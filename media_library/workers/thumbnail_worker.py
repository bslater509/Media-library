"""Background thumbnail generation worker managing batch FFmpeg poster extraction."""

import asyncio
import logging
import os
from typing import Any

from media_library.db.connection import db_conn
from media_library.services.backup_service import sync_thumbnail_db_cache
from media_library.services.ffmpeg_service import generate_thumbnail
from media_library.workers.base import BaseWorker

logger = logging.getLogger(__name__)

# Images inside ZIP archives are deferred until the WebP optimizer converts them;
# a thumbnail made for a pre-conversion member would be orphaned by the re-index
# (the thumb key is derived from the file path + size, both of which change).
# Videos are never converted, so they are always thumbnailed.
_DEFERRED_ZIP_IMAGE_PREDICATE = (
    "(COALESCE(fo.is_zip, 0) = 1 AND f.type = 'image' AND f.ext != '.webp')"
)


class ThumbnailWorker(BaseWorker):
    """Worker proactively extracting video poster frames and updating SQLite index."""

    def __init__(self):
        super().__init__("thumbnail_worker")
        self.priority_queue: asyncio.Queue = asyncio.Queue()
        self.state: dict[str, Any] = {
            "status": "idle",  # "generating" | "idle" | "paused"
            "total_files": 0,
            "cached_count": 0,
            "pending_count": 0,
            "percent": 0.0,
            "current_file": "",
            "current_folder": "",
            "processed_count": 0,
        }

    def pause(self):
        super().pause()
        self.state["status"] = "paused"

    def resume(self):
        super().resume()
        self.state["status"] = "generating"

    def enqueue_folder(self, folder_path: str):
        try:
            with db_conn() as conn:
                cur = conn.execute(
                    f"SELECT f.path, f.type, f.name, f.folder_path FROM files f "
                    f"LEFT JOIN folders fo ON f.folder_path = fo.path "
                    f"WHERE f.folder_path = ? AND f.has_thumb = 0 AND NOT {_DEFERRED_ZIP_IMAGE_PREDICATE}",
                    (folder_path,),
                )
                rows = cur.fetchall()
            for r in rows:
                self.enqueue_nowait(
                    self.priority_queue,
                    {
                        "path": r["path"],
                        "type": r["type"],
                        "name": r["name"],
                        "folder_path": r["folder_path"],
                    },
                )
        except Exception as e:
            logger.error(f"Error enqueuing folder thumbnails: {e}")

    def get_status(self) -> dict[str, Any]:
        return self.state

    def _refresh_stats(self):
        """Recomputes thumbnail totals/pending/cached from the DB.

        The deferred set (non-WebP images inside ZIP archives) shrinks as the WebP
        optimizer converts archives, so counters are refreshed each loop iteration
        rather than maintained only at startup.
        """
        try:
            with db_conn() as conn:
                c_tot = conn.execute(
                    f"SELECT count(*) FROM files f LEFT JOIN folders fo ON f.folder_path = fo.path "
                    f"WHERE NOT {_DEFERRED_ZIP_IMAGE_PREDICATE}"
                ).fetchone()[0]
                c_pending = conn.execute(
                    f"SELECT count(*) FROM files f LEFT JOIN folders fo ON f.folder_path = fo.path "
                    f"WHERE f.has_thumb = 0 AND NOT {_DEFERRED_ZIP_IMAGE_PREDICATE}"
                ).fetchone()[0]
                c_cached = conn.execute(
                    f"SELECT count(*) FROM files f LEFT JOIN folders fo ON f.folder_path = fo.path "
                    f"WHERE f.has_thumb = 1 AND NOT {_DEFERRED_ZIP_IMAGE_PREDICATE}"
                ).fetchone()[0]
            self.state["total_files"] = c_tot
            self.state["cached_count"] = c_cached
            self.state["pending_count"] = c_pending
            self.state["percent"] = (
                round(((c_tot - c_pending) / c_tot * 100.0), 1) if c_tot > 0 else 100.0
            )
        except Exception as e:
            logger.error(f"Error refreshing thumbnail stats: {e}")

    async def _run_loop(self):
        await asyncio.to_thread(sync_thumbnail_db_cache)
        self._refresh_stats()

        concurrency_sem = asyncio.Semaphore(5)

        async def process_item(item: dict[str, Any]) -> tuple:
            async with concurrency_sem:
                fpath = item["path"]
                is_vid = item["type"] == "video"
                self.state["current_file"] = item.get("name", os.path.basename(fpath))
                self.state["current_folder"] = os.path.basename(
                    item.get("folder_path", os.path.dirname(fpath))
                )
                ok = await generate_thumbnail(fpath, is_video=is_vid, update_db=False)
                return fpath, bool(ok)

        async def execute_batch(items: list[dict[str, Any]]):
            if not items:
                return
            self.state["status"] = "generating"
            results = await asyncio.gather(
                *[process_item(it) for it in items], return_exceptions=True
            )

            successes = []
            failures = []
            for res in results:
                if isinstance(res, tuple):
                    fpath, ok = res
                    if ok:
                        successes.append((fpath,))
                    else:
                        failures.append((fpath,))
                elif isinstance(res, Exception):
                    logger.error(f"Exception processing thumbnail item: {res}")

            if successes or failures:
                try:
                    with db_conn() as conn, conn:
                        if successes:
                            conn.executemany(
                                "UPDATE files SET has_thumb = 1 WHERE path = ?",
                                successes,
                            )
                            self.state["processed_count"] += len(successes)
                            self.state["cached_count"] += len(successes)
                            self.state["pending_count"] = max(
                                0, self.state["pending_count"] - len(successes)
                            )
                        if failures:
                            conn.executemany(
                                "UPDATE files SET has_thumb = 2 WHERE path = ?",
                                failures,
                            )
                            self.state["pending_count"] = max(
                                0, self.state["pending_count"] - len(failures)
                            )
                        tot = self.state["total_files"]
                        pend = self.state["pending_count"]
                        if tot > 0:
                            self.state["percent"] = round(
                                ((tot - pend) / tot * 100.0), 1
                            )
                except Exception as e:
                    logger.error(f"Error bulk updating thumbnail status: {e}")

        while True:
            try:
                if not self._pause_event.is_set():
                    self.state["status"] = "paused"
                    await self._pause_event.wait()
                    self.state["status"] = "generating"

                self._refresh_stats()

                priority_items = []
                while len(priority_items) < 25:
                    try:
                        p_item = self.priority_queue.get_nowait()
                        priority_items.append(p_item)
                    except asyncio.QueueEmpty:
                        break

                if priority_items:
                    await execute_batch(priority_items)
                    for _ in priority_items:
                        self.priority_queue.task_done()
                    continue

                batch = []
                try:
                    with db_conn() as conn:
                        cur = conn.execute(
                            f"SELECT f.path, f.type, f.name, f.folder_path FROM files f "
                            f"LEFT JOIN folders fo ON f.folder_path = fo.path "
                            f"WHERE f.has_thumb = 0 AND NOT {_DEFERRED_ZIP_IMAGE_PREDICATE} LIMIT 25"
                        )
                        for r in cur.fetchall():
                            batch.append(
                                {
                                    "path": r["path"],
                                    "type": r["type"],
                                    "name": r["name"],
                                    "folder_path": r["folder_path"],
                                }
                            )
                except Exception as e:
                    logger.error(f"Error fetching thumbnail batch: {e}")

                if not batch:
                    self.state["status"] = "idle"
                    self.state["current_file"] = ""
                    self.state["current_folder"] = ""
                    self.state["pending_count"] = 0
                    self.state["percent"] = 100.0
                    await asyncio.sleep(4.0)
                    continue

                await execute_batch(batch)
                await asyncio.sleep(0.04)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in thumbnail worker: {e}")
                await asyncio.sleep(2.0)


thumbnail_worker_instance = ThumbnailWorker()
