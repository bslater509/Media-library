"""Background backup watcher automatically triggering clean snapshot backups when workers settle."""

import time
import asyncio
from typing import Optional, Dict, Any

from media_library.workers.base import BaseWorker
from media_library.db.connection import get_db
from media_library.db.indexer import indexer_state
from media_library.services.backup_service import (
    library_backup_state,
    get_existing_backup_meta,
    _create_library_backup_sync,
    _restore_library_backup_sync,
)

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
                library_backup_state["error_message"] = res.get("error", "Unknown error")
            else:
                library_backup_state["status"] = "idle"
                library_backup_state["progress_percent"] = 100.0
                library_backup_state["current_step"] = "Idle"
        except Exception as e:
            library_backup_state["status"] = "error"
            library_backup_state["error_message"] = str(e)
            print(f"[Library Backup] Async backup failed: {e}")

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
                library_backup_state["error_message"] = res.get("error", "Unknown error")
            else:
                library_backup_state["status"] = "idle"
                library_backup_state["progress_percent"] = 100.0
                library_backup_state["current_step"] = "Idle"
        except Exception as e:
            library_backup_state["status"] = "error"
            library_backup_state["error_message"] = str(e)
            print(f"[Library Restore] Async restore failed: {e}")

class BackupWatcherWorker(BaseWorker):
    """Watches system idle state and triggers automatic snapshots to library folder."""

    def __init__(self):
        super().__init__("backup_watcher")

    def get_status(self) -> Dict[str, Any]:
        return library_backup_state

    async def _run_loop(self):
        await asyncio.sleep(20.0)
        idle_since: Optional[float] = None
        DEBOUNCE_COOLDOWN = 60.0

        from media_library.workers.thumbnail_worker import thumbnail_worker_instance
        from media_library.workers.duplicate_hash_worker import duplicate_hash_worker_instance
        from media_library.workers.webp_worker import webp_worker_instance

        while True:
            try:
                await asyncio.sleep(10.0)

                is_crawler_busy = (indexer_state.get("status") == "crawling")
                is_thumb_busy = (thumbnail_worker_instance.state.get("status") == "generating")
                is_hash_busy = (duplicate_hash_worker_instance.state.get("status") == "hashing")
                is_webp_busy = (webp_worker_instance.state.get("status") == "converting")

                all_idle = not (is_crawler_busy or is_thumb_busy or is_hash_busy or is_webp_busy)

                if not all_idle:
                    idle_since = None
                    continue

                if idle_since is None:
                    idle_since = time.time()
                    continue

                if time.time() - idle_since >= DEBOUNCE_COOLDOWN:
                    conn = get_db()
                    try:
                        cur_files = conn.execute("SELECT count(*) FROM files").fetchone()[0]
                        cur_thumbs = conn.execute("SELECT count(*) FROM files WHERE has_thumb = 1").fetchone()[0]
                    finally:
                        conn.close()

                    last_meta = get_existing_backup_meta()
                    needs_backup = False

                    if last_meta is None and cur_files > 0:
                        needs_backup = True
                    elif last_meta is not None:
                        meta_files = last_meta.get("file_count", 0)
                        meta_thumbs = last_meta.get("thumb_count", 0)
                        if abs(cur_files - meta_files) > 0 or abs(cur_thumbs - meta_thumbs) > 0:
                            needs_backup = True

                    if needs_backup and library_backup_state["status"] == "idle":
                        print(f"[Backup Watcher] Settled idle for {DEBOUNCE_COOLDOWN}s with changes detected (files: {cur_files}, thumbs: {cur_thumbs}). Creating library backup...")
                        idle_since = time.time()
                        asyncio.create_task(run_library_backup_async())
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[Backup Watcher] Error in loop: {e}")
                await asyncio.sleep(15.0)

backup_watcher_instance = BackupWatcherWorker()
