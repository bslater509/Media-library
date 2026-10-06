"""Background thumbnail generation worker managing batch FFmpeg poster extraction."""

import os
import time
import asyncio
from typing import Dict, Any, List

from media_library.workers.base import BaseWorker
from media_library.db.connection import get_db
from media_library.services.backup_service import sync_thumbnail_db_cache
from media_library.services.ffmpeg_service import generate_thumbnail

class ThumbnailWorker(BaseWorker):
    """Worker proactively extracting video poster frames and updating SQLite index."""

    def __init__(self):
        super().__init__("thumbnail_worker")
        self.priority_queue: asyncio.Queue = asyncio.Queue()
        self.state: Dict[str, Any] = {
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
            conn = get_db()
            cur = conn.execute(
                "SELECT path, type, name, folder_path FROM files WHERE folder_path = ? AND has_thumb = 0",
                (folder_path,)
            )
            rows = cur.fetchall()
            conn.close()
            for r in rows:
                self.priority_queue.put_nowait({
                    "path": r["path"],
                    "type": r["type"],
                    "name": r["name"],
                    "folder_path": r["folder_path"]
                })
        except Exception as e:
            print(f"Error enqueuing folder thumbnails: {e}")

    def get_status(self) -> Dict[str, Any]:
        return self.state

    async def _run_loop(self):
        await asyncio.to_thread(sync_thumbnail_db_cache)
        try:
            conn = get_db()
            c_tot = conn.execute("SELECT count(*) FROM files").fetchone()[0]
            c_pending = conn.execute("SELECT count(*) FROM files WHERE has_thumb = 0").fetchone()[0]
            c_cached = conn.execute("SELECT count(*) FROM files WHERE has_thumb = 1").fetchone()[0]
            conn.close()
            self.state["total_files"] = c_tot
            self.state["cached_count"] = c_cached
            self.state["pending_count"] = c_pending
            self.state["percent"] = round(((c_tot - c_pending) / c_tot * 100.0), 1) if c_tot > 0 else 100.0
        except Exception as e:
            print(f"Error initializing thumbnail stats: {e}")

        concurrency_sem = asyncio.Semaphore(5)

        async def process_item(item: Dict[str, Any]) -> tuple:
            async with concurrency_sem:
                fpath = item["path"]
                is_vid = (item["type"] == "video")
                self.state["current_file"] = item.get("name", os.path.basename(fpath))
                self.state["current_folder"] = os.path.basename(item.get("folder_path", os.path.dirname(fpath)))
                ok = await generate_thumbnail(fpath, is_video=is_vid, update_db=False)
                return fpath, bool(ok)

        async def execute_batch(items: List[Dict[str, Any]]):
            if not items:
                return
            self.state["status"] = "generating"
            results = await asyncio.gather(*[process_item(it) for it in items], return_exceptions=True)

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
                    print(f"Exception processing thumbnail item: {res}")

            if successes or failures:
                conn = get_db()
                try:
                    with conn:
                        if successes:
                            conn.executemany("UPDATE files SET has_thumb = 1 WHERE path = ?", successes)
                            self.state["processed_count"] += len(successes)
                            self.state["cached_count"] += len(successes)
                            self.state["pending_count"] = max(0, self.state["pending_count"] - len(successes))
                        if failures:
                            conn.executemany("UPDATE files SET has_thumb = 2 WHERE path = ?", failures)
                            self.state["pending_count"] = max(0, self.state["pending_count"] - len(failures))
                    tot = self.state["total_files"]
                    pend = self.state["pending_count"]
                    if tot > 0:
                        self.state["percent"] = round(((tot - pend) / tot * 100.0), 1)
                except Exception as e:
                    print(f"Error bulk updating thumbnail status: {e}")
                finally:
                    conn.close()

        while True:
            try:
                if not self._pause_event.is_set():
                    self.state["status"] = "paused"
                    await self._pause_event.wait()
                    self.state["status"] = "generating"

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

                conn = get_db()
                batch = []
                try:
                    cur = conn.execute(
                        "SELECT path, type, name, folder_path FROM files WHERE has_thumb = 0 LIMIT 25"
                    )
                    for r in cur.fetchall():
                        batch.append({
                            "path": r["path"],
                            "type": r["type"],
                            "name": r["name"],
                            "folder_path": r["folder_path"]
                        })
                except Exception as e:
                    print(f"Error fetching thumbnail batch: {e}")
                finally:
                    conn.close()

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
                print(f"Error in thumbnail worker: {e}")
                await asyncio.sleep(2.0)

thumbnail_worker_instance = ThumbnailWorker()
