"""Background backup watcher automatically triggering clean snapshot backups when workers settle."""

import asyncio
import logging
import time
from typing import Any

from media_library.core.config import (
    LIBRARY_BACKUP_AUTO_ENABLED,
    LIBRARY_BACKUP_AUTO_INTERVAL_HOURS,
)
from media_library.db.connection import db_conn
from media_library.db.indexer import indexer_state
from media_library.services.backup_service import (
    _create_library_backup_sync,
    _restore_library_backup_sync,
    get_existing_backup_meta,
    library_backup_state,
)
from media_library.workers.base import BaseWorker

logger = logging.getLogger(__name__)

library_backup_lock = asyncio.Lock()


async def run_library_backup_async():
    if library_backup_lock.locked():
        return
    async with library_backup_lock:
        library_backup_state["status"] = "backing_up"
        library_backup_state["progress_percent"] = 5.0
        library_backup_state["current_step"] = "Preparing snapshot..."
        library_backup_state["error_message"] = None

        def progress_cb(step: str, pct: float):
            library_backup_state["current_step"] = step
            library_backup_state["progress_percent"] = round(pct, 1)

        try:
            res = await asyncio.to_thread(_create_library_backup_sync, progress_cb)
            if not res.get("success"):
                library_backup_state["status"] = "error"
                library_backup_state["error_message"] = res.get(
                    "error", "Unknown error"
                )
            else:
                library_backup_state["status"] = "idle"
                library_backup_state["progress_percent"] = 100.0
                library_backup_state["current_step"] = "Idle"
        except Exception as e:
            library_backup_state["status"] = "error"
            library_backup_state["error_message"] = str(e)
            logger.error(f"[Library Backup] Async backup failed: {e}")


async def run_library_restore_async():
    if library_backup_lock.locked():
        return
    async with library_backup_lock:
        library_backup_state["status"] = "restoring"
        library_backup_state["progress_percent"] = 5.0
        library_backup_state["current_step"] = "Restoring database and thumbnails..."
        library_backup_state["error_message"] = None

        def progress_cb(step: str, pct: float):
            library_backup_state["current_step"] = step
            library_backup_state["progress_percent"] = round(pct, 1)

        try:
            res = await asyncio.to_thread(_restore_library_backup_sync, progress_cb)
            if not res.get("success"):
                library_backup_state["status"] = "error"
                library_backup_state["error_message"] = res.get(
                    "error", "Unknown error"
                )
            else:
                library_backup_state["status"] = "idle"
                library_backup_state["progress_percent"] = 100.0
                library_backup_state["current_step"] = "Idle"
        except Exception as e:
            library_backup_state["status"] = "error"
            library_backup_state["error_message"] = str(e)
            logger.error(f"[Library Restore] Async restore failed: {e}")


class BackupWatcherWorker(BaseWorker):
    """Watches system idle state and triggers automatic snapshots to library folder."""

    def __init__(self):
        super().__init__("backup_watcher")

    def get_status(self) -> dict[str, Any]:
        return library_backup_state

    async def _run_loop(self):
        await asyncio.sleep(20.0)
        idle_since: float | None = None
        DEBOUNCE_COOLDOWN = 120.0

        from media_library.services.zip import (
            batch_unzip_state,
            batch_zip_state,
            zip_tasks_state,
        )
        from media_library.workers.animated_thumb_worker import (
            animated_thumb_worker_instance,
        )
        from media_library.workers.duplicate_hash_worker import (
            duplicate_hash_worker_instance,
            thumb_hash_worker_instance,
        )
        from media_library.workers.h265_worker import h265_worker_instance
        from media_library.workers.library_ops_worker import (
            library_ops_worker_instance,
        )
        from media_library.workers.metadata_worker import metadata_worker_instance
        from media_library.workers.thumbnail_worker import thumbnail_worker_instance
        from media_library.workers.transcode_worker import transcode_worker_instance
        from media_library.workers.transfer_worker import transfer_worker_instance
        from media_library.workers.url_download_worker import (
            url_download_worker_instance,
        )
        from media_library.workers.webp_worker import webp_worker_instance

        while True:
            try:
                await asyncio.sleep(10.0)

                if not self._pause_event.is_set():
                    idle_since = None
                    await self._pause_event.wait()

                if not LIBRARY_BACKUP_AUTO_ENABLED:
                    idle_since = None
                    continue

                is_crawler_busy = indexer_state.get("status") == "crawling"
                is_thumb_busy = (
                    thumbnail_worker_instance.state.get("status") == "generating"
                )
                is_hash_busy = (
                    duplicate_hash_worker_instance.state.get("status") == "hashing"
                    or thumb_hash_worker_instance.state.get("status")
                    in ("hashing", "migrating")
                )
                is_webp_busy = webp_worker_instance.state.get("status") == "converting"
                is_transfer_busy = (
                    transfer_worker_instance.active_transfer is not None
                ) or (transfer_worker_instance.transfer_queue.qsize() > 0)
                is_transcode_busy = (
                    transcode_worker_instance.active_transcode is not None
                ) or (transcode_worker_instance.transcode_queue.qsize() > 0)
                is_lib_ops_busy = (
                    library_ops_worker_instance.active_op is not None
                ) or (library_ops_worker_instance.ops_queue.qsize() > 0)
                is_metadata_busy = (
                    metadata_worker_instance.state.get("status") == "probing"
                )
                is_anim_busy = (
                    animated_thumb_worker_instance.active_task is not None
                    or not animated_thumb_worker_instance.priority_queue.empty()
                    or not animated_thumb_worker_instance.batch_queue.empty()
                )
                is_url_dl_busy = (
                    bool(url_download_worker_instance.active_jobs)
                    or url_download_worker_instance.jobs_queue.qsize() > 0
                )
                is_h265_busy = (
                    h265_worker_instance.active is not None
                    or h265_worker_instance.queue.qsize() > 0
                )
                is_batch_zip_busy = batch_zip_state.get("status") in (
                    "running",
                    "paused",
                )
                is_batch_unzip_busy = batch_unzip_state.get("status") in (
                    "running",
                    "paused",
                )
                is_zip_busy = any(
                    t.get("status") == "running"
                    for t in zip_tasks_state.get("active_tasks", {}).values()
                )
                is_backup_busy = (
                    library_backup_state.get("status") in ("backing_up", "restoring")
                ) or library_backup_lock.locked()

                all_idle = not (
                    is_crawler_busy
                    or is_thumb_busy
                    or is_hash_busy
                    or is_webp_busy
                    or is_transfer_busy
                    or is_transcode_busy
                    or is_lib_ops_busy
                    or is_metadata_busy
                    or is_anim_busy
                    or is_url_dl_busy
                    or is_h265_busy
                    or is_batch_zip_busy
                    or is_batch_unzip_busy
                    or is_zip_busy
                    or is_backup_busy
                )

                if not all_idle:
                    idle_since = None
                    continue

                if idle_since is None:
                    idle_since = time.time()
                    continue

                if time.time() - idle_since >= DEBOUNCE_COOLDOWN:
                    with db_conn() as conn:
                        cur_files = conn.execute(
                            "SELECT count(*) FROM files"
                        ).fetchone()[0]
                        cur_thumbs = conn.execute(
                            "SELECT count(*) FROM files WHERE has_thumb = 1"
                        ).fetchone()[0]
                        cur_max_mtime = conn.execute(
                            "SELECT COALESCE(MAX(mtime), 0) FROM files"
                        ).fetchone()[0]
                        cur_total_size = conn.execute(
                            "SELECT COALESCE(SUM(size), 0) FROM files"
                        ).fetchone()[0]

                    last_meta = await asyncio.to_thread(get_existing_backup_meta)
                    needs_backup = False

                    if last_meta is None and cur_files > 0:
                        needs_backup = True
                    elif last_meta is not None:
                        meta_files = last_meta.get("file_count", 0)
                        meta_thumbs = last_meta.get("thumb_count", 0)
                        meta_max_mtime = float(last_meta.get("max_mtime", 0) or 0)
                        meta_total_size = int(last_meta.get("total_size", 0) or 0)

                        min_interval_sec = max(
                            0.0, LIBRARY_BACKUP_AUTO_INTERVAL_HOURS * 3600.0
                        )
                        last_ts = float(last_meta.get("timestamp", 0) or 0)
                        elapsed = time.time() - last_ts

                        counts_changed = (
                            abs(cur_files - meta_files) > 0
                            or abs(cur_thumbs - meta_thumbs) > 0
                        )
                        content_changed = (
                            cur_max_mtime > meta_max_mtime
                            or cur_total_size != meta_total_size
                        )
                        if (counts_changed or content_changed) and (
                            min_interval_sec <= 0 or elapsed >= min_interval_sec
                        ):
                            needs_backup = True

                    if needs_backup and library_backup_state["status"] == "idle":
                        logger.info(
                            f"[Backup Watcher] Settled idle for {DEBOUNCE_COOLDOWN}s with changes detected (files: {cur_files}, thumbs: {cur_thumbs}). Creating library backup..."
                        )
                        idle_since = None
                        asyncio.create_task(run_library_backup_async())
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[Backup Watcher] Error in loop: {e}")
                idle_since = None
                await asyncio.sleep(15.0)


backup_watcher_instance = BackupWatcherWorker()
