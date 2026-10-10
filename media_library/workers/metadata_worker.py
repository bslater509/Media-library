"""Background media metadata worker.

Probes indexed videos and images (via FFprobe, plus EXIF for image capture
times) so the Library can render natural-aspect-ratio tiles and the transcode
path can avoid re-probing. Each file is probed at most once: successful results
are stored in the ``files`` probe columns (``width``/``height``/``duration``/
``video_codec``/``audio_codec``/``fps``/``capture_time``); files that cannot be
probed are recorded as ``0 x 0`` so they are not retried until the indexer sees
them change.

Only real files are probed. Images inside ZIP archives (virtual folders) are
skipped here — probing them would require extracting the archive over FUSE — and
are instead corrected client-side when their thumbnail image loads.
"""

import asyncio
import logging
import os
import time
from typing import Any

from PIL import Image

from media_library.core.state import get_thumbnail_cache_path
from media_library.db.connection import db_conn
from media_library.services.ffmpeg_service import probe_media_metadata
from media_library.workers.base import BaseWorker

logger = logging.getLogger(__name__)

# ZIP members have no cheap source access (probing would force archive extraction
# over FUSE), so their dimensions are read from the already-cached local
# thumbnail instead. The sweep runs at most once per interval.
_ZIP_BATCH = 200
_ZIP_SWEEP_INTERVAL = 60.0


def _read_image_size(path: str):
    """Reads an image file's pixel size (header-only) or returns None."""
    try:
        with Image.open(path) as img:
            return img.width, img.height
    except Exception:
        return None


class MediaMetadataWorker(BaseWorker):
    """Worker probing source dimensions for the natural-ratio Library grid."""

    def __init__(self):
        super().__init__("metadata_worker")
        self.state: dict[str, Any] = {
            "status": "idle",  # "probing" | "idle" | "paused"
            "total_files": 0,
            "pending_count": 0,
            "zip_pending_count": 0,
            "processed_count": 0,
            "percent": 100.0,
            "current_file": "",
        }
        self._zip_cursor = ""
        self._zip_sweep_at = 0.0
        self._stats_at = 0.0

    def pause(self):
        super().pause()
        self.state["status"] = "paused"

    def resume(self):
        super().resume()
        self.state["status"] = "probing"

    def get_status(self) -> dict[str, Any]:
        return self.state

    def _refresh_stats(self):
        """Recomputes pending/total counts from the index for accurate progress."""
        now = time.time()
        if now - self._stats_at < 2.0:
            return
        self._stats_at = now
        try:
            with db_conn() as conn:
                total = conn.execute(
                    "SELECT count(*) FROM files f LEFT JOIN folders fo ON f.folder_path = fo.path "
                    "WHERE COALESCE(fo.is_zip, 0) = 0"
                ).fetchone()[0]
                pending = conn.execute(
                    "SELECT count(*) FROM files f LEFT JOIN folders fo ON f.folder_path = fo.path "
                    "WHERE COALESCE(fo.is_zip, 0) = 0 AND f.width IS NULL"
                ).fetchone()[0]
                zip_pending = conn.execute(
                    "SELECT count(*) FROM files f LEFT JOIN folders fo ON f.folder_path = fo.path "
                    "WHERE COALESCE(fo.is_zip, 0) = 1 AND f.width IS NULL"
                ).fetchone()[0]
            self.state["total_files"] = total
            self.state["pending_count"] = pending
            self.state["zip_pending_count"] = zip_pending
            self.state["percent"] = (
                round(((total - pending) / total * 100.0), 1) if total > 0 else 100.0
            )
        except Exception as e:
            logger.error(f"Error refreshing metadata stats: {e}")

    async def _backfill_zip_batch(self) -> bool:
        """Backfills dimensions for a batch of ZIP members from cached thumbnails.

        Returns True while a sweep is in progress (more batches may remain), and
        False when the sweep is complete or still in its cooldown window. Members
        without a cached thumbnail are skipped and re-checked on the next sweep.
        """
        now = time.time()
        if self._zip_cursor == "" and (now - self._zip_sweep_at) < _ZIP_SWEEP_INTERVAL:
            return False

        rows = []
        try:
            with db_conn() as conn:
                cur = conn.execute(
                    "SELECT f.path, f.mtime, f.size FROM files f "
                    "LEFT JOIN folders fo ON f.folder_path = fo.path "
                    "WHERE COALESCE(fo.is_zip, 0) = 1 AND f.width IS NULL AND f.path > ? "
                    "ORDER BY f.path LIMIT ?",
                    (self._zip_cursor, _ZIP_BATCH),
                )
                rows = cur.fetchall()
        except Exception as e:
            logger.error(f"Error fetching zip metadata batch: {e}")

        if not rows:
            # Sweep complete: wait before re-checking for newly generated thumbnails.
            self._zip_cursor = ""
            self._zip_sweep_at = time.time()
            return False

        self.state["status"] = "probing"
        updates = []
        for r in rows:
            self.state["current_file"] = os.path.basename(r["path"])
            thumb = get_thumbnail_cache_path(r["path"], r["mtime"], r["size"])
            try:
                if not (thumb.exists() and thumb.stat().st_size > 0):
                    continue
            except OSError:
                continue
            dims = await asyncio.to_thread(_read_image_size, str(thumb))
            if dims and dims[0] > 0 and dims[1] > 0:
                updates.append((dims[0], dims[1], r["path"]))
        self._zip_cursor = rows[-1]["path"]

        if updates:
            try:
                with db_conn() as conn, conn:
                    conn.executemany(
                        "UPDATE files SET width = ?, height = ? WHERE path = ?", updates
                    )
                    self.state["processed_count"] += len(updates)
            except Exception as e:
                logger.error(f"Error updating zip media dimensions: {e}")
        return True

    async def _run_loop(self):
        self._refresh_stats()
        concurrency_sem = asyncio.Semaphore(4)

        async def process_item(item: dict[str, Any]) -> tuple:
            async with concurrency_sem:
                fpath = item["path"]
                self.state["current_file"] = item.get("name", os.path.basename(fpath))
                if not os.path.exists(fpath):
                    # Missing / unreadable source: mark attempted (width 0) so it
                    # is not retried until the indexer sees the file change.
                    return (fpath, 0, 0, None, None, None, None, None)
                meta = await probe_media_metadata(fpath)
                if meta:
                    is_image = item.get("type") == "image"
                    return (
                        fpath,
                        meta["width"],
                        meta["height"],
                        None if is_image else meta["duration"],
                        None if is_image else meta["video_codec"],
                        None if is_image else meta["audio_codec"],
                        None if is_image else meta["fps"],
                        meta["capture_time"],
                    )
                return (fpath, 0, 0, None, None, None, None, None)

        async def execute_batch(items: list[dict[str, Any]]) -> int:
            if not items:
                return 0
            self.state["status"] = "probing"
            results = await asyncio.gather(
                *[process_item(it) for it in items], return_exceptions=True
            )
            rows = []
            count = 0
            for res in results:
                if isinstance(res, tuple):
                    rows.append(res)
                    count += 1
                elif isinstance(res, Exception):
                    logger.error(f"Exception probing media metadata: {res}")

            if rows:
                try:
                    with db_conn() as conn, conn:
                        conn.executemany(
                            "UPDATE files SET width = ?, height = ?, duration = ?, "
                            "video_codec = ?, audio_codec = ?, fps = ?, capture_time = ? "
                            "WHERE path = ?",
                            [
                                (w, h, dur, vc, ac, fps, ct, p)
                                for (p, w, h, dur, vc, ac, fps, ct) in rows
                            ],
                        )
                        self.state["processed_count"] += len(rows)
                        self.state["pending_count"] = max(
                            0, self.state["pending_count"] - len(rows)
                        )
                        total = self.state["total_files"]
                        pending = self.state["pending_count"]
                        if total > 0:
                            self.state["percent"] = round(
                                ((total - pending) / total * 100.0), 1
                            )
                except Exception as e:
                    logger.error(f"Error updating media dimensions: {e}")
            return count

        while True:
            try:
                if not self._pause_event.is_set():
                    self.state["status"] = "paused"
                    await self._pause_event.wait()
                    self.state["status"] = "probing"

                self._refresh_stats()

                # ZIP members resolve from local thumbnails cheaply, so sweep
                # them alongside (not after) the slower FUSE source probing.
                await self._backfill_zip_batch()

                batch = []
                try:
                    with db_conn() as conn:
                        cur = conn.execute(
                            "SELECT f.path, f.name, f.type FROM files f "
                            "LEFT JOIN folders fo ON f.folder_path = fo.path "
                            "WHERE COALESCE(fo.is_zip, 0) = 0 AND f.width IS NULL LIMIT 25"
                        )
                        for r in cur.fetchall():
                            batch.append(
                                {
                                    "path": r["path"],
                                    "name": r["name"],
                                    "type": r["type"],
                                }
                            )
                except Exception as e:
                    logger.error(f"Error fetching metadata batch: {e}")

                if not batch:
                    self.state["status"] = "idle"
                    self.state["current_file"] = ""
                    self.state["pending_count"] = 0
                    self.state["percent"] = 100.0
                    await asyncio.sleep(6.0)
                    continue

                await execute_batch(batch)
                await asyncio.sleep(0.04)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in metadata worker: {e}")
                await asyncio.sleep(2.0)


metadata_worker_instance = MediaMetadataWorker()
