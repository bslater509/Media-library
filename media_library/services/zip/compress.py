import hashlib
import logging
import os
import shutil
import threading
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any
from media_library.core.config import (
    THUMBNAIL_CACHE_DIR,
    WEBP_SKIP_LARGER,
    ZIP_CACHE_DIR,
    ZIP_CACHE_MAX_SIZE_GB,
    ZIP_STAGING_DIR,
)
from media_library.core.constants import IMAGE_EXTENSIONS, VIDEO_EXTENSIONS
from media_library.core.state import format_size
from media_library.db.connection import db_conn
from .core import logger, zip_tasks_state, zip_lock, _archive_op_locks, _archive_op_locks_guard, _get_archive_lock, _get_common_root_prefix, split_zip_path, is_zip_file, get_zip_cache_dir, is_zip_extracted, resolve_zip_file_path, get_zip_metadata, index_zip_archive, manage_lru_cache



def check_folder_compressibility(folder_path: str) -> dict[str, Any]:
    """
    Evaluates whether a folder is eligible for compression to ZIP:
    Requires at least 1 image file and 0 video files.
    """
    if not os.path.exists(folder_path) or not os.path.isdir(folder_path):
        return {"eligible": False, "reason": "Path is not a valid directory"}

    image_files = []
    video_files = []
    other_files = []
    total_size = 0

    for root, _, filenames in os.walk(folder_path):
        for fname in filenames:
            if fname.startswith("."):
                continue
            full_p = os.path.join(root, fname)
            ext = os.path.splitext(fname)[1].lower()
            try:
                st = os.stat(full_p)
                total_size += st.st_size
            except OSError:
                continue

            if ext in VIDEO_EXTENSIONS:
                video_files.append(fname)
            elif ext in IMAGE_EXTENSIONS:
                image_files.append(fname)
            else:
                other_files.append(fname)

    if video_files:
        return {
            "eligible": False,
            "reason": f"Folder contains {len(video_files)} video file(s). Only folders without videos can be compressed to ZIP.",
            "image_count": len(image_files),
            "video_count": len(video_files),
        }

    if not image_files:
        return {
            "eligible": False,
            "reason": "Folder contains no image files to compress.",
            "image_count": 0,
            "video_count": 0,
        }

    return {
        "eligible": True,
        "reason": "Eligible for compression",
        "image_count": len(image_files),
        "video_count": 0,
        "other_count": len(other_files),
        "total_files": len(image_files) + len(other_files),
        "total_size": total_size,
        "total_size_formatted": format_size(total_size),
    }


def compress_folder_to_zip(folder_path: str, progress_cb=None) -> dict[str, Any]:
    """
    Compresses an eligible image folder into a ZIP archive,
    verifies archive CRC with testzip(), deletes the source folder,
    and indexes the newly created virtual folder.
    """
    check = check_folder_compressibility(folder_path)
    if not check["eligible"]:
        raise ValueError(check["reason"])

    folder_name = os.path.basename(os.path.normpath(folder_path))
    parent_dir = os.path.dirname(os.path.normpath(folder_path))
    target_zip = os.path.join(parent_dir, f"{folder_name}.zip")

    # If target zip already exists, choose unique name
    if os.path.exists(target_zip):
        cnt = 1
        while os.path.exists(os.path.join(parent_dir, f"{folder_name}_{cnt}.zip")):
            cnt += 1
        target_zip = os.path.join(parent_dir, f"{folder_name}_{cnt}.zip")

    # Stage ZIP creation on fast local disk to avoid remote FUSE latency and slow CRC verification
    local_temp_zip = str(
        ZIP_STAGING_DIR
        / f".tmp_{folder_name}_{int(time.time())}_{uuid.uuid4().hex[:6]}.zip"
    )
    remote_temp_zip = os.path.join(
        parent_dir, f".tmp_transfer_{folder_name}_{int(time.time())}.zip"
    )

    with zip_lock:
        zip_tasks_state["active_tasks"][folder_path] = {
            "type": "compress",
            "status": "running",
            "percent": 0.0,
            "current": 0,
            "total": check["total_files"],
            "message": f"Compressing {check['total_files']} files...",
            "started_at": time.time(),
        }

    try:
        # Collect all files
        all_files = []
        for root, _, filenames in os.walk(folder_path):
            for fname in filenames:
                if fname.startswith("."):
                    continue
                full_p = os.path.join(root, fname)
                rel_p = os.path.relpath(full_p, folder_path)
                all_files.append((full_p, rel_p))

        total_files = len(all_files)

        with zipfile.ZipFile(
            local_temp_zip, "w", compression=zipfile.ZIP_DEFLATED
        ) as zf:
            for i, (full_path, arc_name) in enumerate(all_files):
                ext = os.path.splitext(arc_name)[1].lower()
                if ext in IMAGE_EXTENSIONS and ext != ".webp":
                    try:
                        from media_library.services.ffmpeg_service import (
                            convert_image_to_webp_bytes,
                        )

                        webp_bytes = convert_image_to_webp_bytes(full_path)
                        webp_arc_name = os.path.splitext(arc_name)[0] + ".webp"
                        zf.writestr(webp_arc_name, webp_bytes)
                    except Exception as ce:
                        logger.warning(
                            f"Warning: could not convert {arc_name} to WebP: {ce}"
                        )
                        zf.write(full_path, arc_name)
                else:
                    zf.write(full_path, arc_name)

                pct = round(((i + 1) / max(total_files, 1)) * 100, 1)

                with zip_lock:
                    task = zip_tasks_state["active_tasks"].get(folder_path)
                    if task:
                        task["percent"] = pct
                        task["current"] = i + 1
                        task["message"] = f"Compressing {i + 1}/{total_files} ({pct}%)"

                if progress_cb:
                    progress_cb(i + 1, total_files, arc_name)

        # Integrity verification on fast local disk
        with zip_lock:
            task = zip_tasks_state["active_tasks"].get(folder_path)
            if task:
                task["message"] = "Verifying archive CRC integrity..."

        with zipfile.ZipFile(local_temp_zip, "r") as zf:
            corrupted = zf.testzip()
            if corrupted:
                raise RuntimeError(
                    f"ZIP archive integrity check failed on member: {corrupted}"
                )

        # Transfer verified archive to destination in a single sequential copy
        with zip_lock:
            task = zip_tasks_state["active_tasks"].get(folder_path)
            if task:
                task["message"] = "Transferring archive to destination..."

        shutil.copyfile(local_temp_zip, remote_temp_zip)
        os.replace(remote_temp_zip, target_zip)

        if os.path.exists(local_temp_zip):
            try:
                os.remove(local_temp_zip)
            except OSError:
                pass

        # Clear any stale extraction cache so the new WebP files are extracted on demand
        cache_dir = get_zip_cache_dir(target_zip)
        if cache_dir.exists():
            shutil.rmtree(cache_dir, ignore_errors=True)

        # Safe removal of source directory on pCloud
        shutil.rmtree(folder_path)

        # Remove deleted source folder from SQLite index
        with db_conn() as conn, conn:
            conn.execute("DELETE FROM folders WHERE path = ?", (folder_path,))
            conn.execute("DELETE FROM files WHERE folder_path = ?", (folder_path,))
            conn.execute("DELETE FROM folders WHERE parent_path = ?", (folder_path,))

        # Update SQLite index with the new ZIP archive as virtual folder
        index_zip_archive(target_zip, parent_path=parent_dir)

        with zip_lock:
            task = zip_tasks_state["active_tasks"].get(folder_path)
            if task:
                task["status"] = "completed"
                task["percent"] = 100.0
                task["message"] = "Compression complete"

        return {
            "success": True,
            "zip_path": target_zip,
            "folder_name": folder_name,
            "clean_name": Path(target_zip).stem,
            "total_files": total_files,
        }

    except Exception as e:
        for p in (local_temp_zip, remote_temp_zip):
            if p and os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass
        with zip_lock:
            task = zip_tasks_state["active_tasks"].get(folder_path)
            if task:
                task["status"] = "failed"
                task["error"] = str(e)
                task["message"] = f"Compression failed: {e}"
        raise


def trigger_debounced_sync_back(zip_path: str, delay_seconds: float = 4.0):
    """
    Schedules debounced background re-compression of modified local cache back to pCloud.
    Batches consecutive file deletions and renames.
    """
    with zip_lock:
        existing_timer = zip_tasks_state["sync_timers"].get(zip_path)
        if existing_timer:
            existing_timer.cancel()

        def _do_sync():
            try:
                _execute_sync_back(zip_path)
            finally:
                with zip_lock:
                    zip_tasks_state["sync_timers"].pop(zip_path, None)

        timer = threading.Timer(delay_seconds, _do_sync)
        zip_tasks_state["sync_timers"][zip_path] = timer
        timer.daemon = True
        timer.start()


def _execute_sync_back(zip_path: str):
    """Re-packs the extracted cache directory and updates the ZIP on storage.

    Serialized per archive via ``_get_archive_lock`` so the rewrite cannot race a
    concurrent unpack or WebP rewrite of the same archive.
    """
    with _get_archive_lock(zip_path):
        # Re-check under the lock: an unpack/compress may have consumed the archive
        # and purged its cache while this sync was queued.
        cache_dir = get_zip_cache_dir(zip_path)
        if not cache_dir.exists() or not os.path.exists(zip_path):
            return
        _execute_sync_back_impl(zip_path)


def _execute_sync_back_impl(zip_path: str):
    cache_dir = get_zip_cache_dir(zip_path)
    parent_dir = os.path.dirname(zip_path)
    local_temp_zip = str(
        ZIP_STAGING_DIR
        / f".tmp_sync_{Path(zip_path).stem}_{int(time.time())}_{uuid.uuid4().hex[:6]}.zip"
    )
    remote_temp_zip = os.path.join(
        parent_dir, f".tmp_transfer_sync_{Path(zip_path).stem}_{int(time.time())}.zip"
    )

    with zip_lock:
        zip_tasks_state["active_tasks"][zip_path] = {
            "type": "sync",
            "status": "running",
            "percent": 0.0,
            "message": f"Syncing updates to {os.path.basename(zip_path)}...",
            "started_at": time.time(),
        }

    try:
        files = []
        for root, _, filenames in os.walk(cache_dir):
            for fname in filenames:
                if fname.startswith(".") or fname == ".extracted_done":
                    continue
                full_p = os.path.join(root, fname)
                rel_p = os.path.relpath(full_p, cache_dir)
                files.append((full_p, rel_p))

        with zipfile.ZipFile(
            local_temp_zip, "w", compression=zipfile.ZIP_DEFLATED
        ) as zf:
            for i, (full_p, arc_name) in enumerate(files):
                zf.write(full_p, arc_name)
                pct = round(((i + 1) / max(len(files), 1)) * 100, 1)
                with zip_lock:
                    task = zip_tasks_state["active_tasks"].get(zip_path)
                    if task:
                        task["percent"] = pct

        with zipfile.ZipFile(local_temp_zip, "r") as zf:
            corrupted = zf.testzip()
            if corrupted:
                raise RuntimeError(f"Integrity check failed: {corrupted}")

        shutil.copyfile(local_temp_zip, remote_temp_zip)
        os.replace(remote_temp_zip, zip_path)

        if os.path.exists(local_temp_zip):
            try:
                os.remove(local_temp_zip)
            except OSError:
                pass

        # Refresh index
        index_zip_archive(zip_path, parent_path=parent_dir)

        with zip_lock:
            task = zip_tasks_state["active_tasks"].get(zip_path)
            if task and task.get("type") == "sync":
                task["status"] = "completed"
                task["percent"] = 100.0
                task["message"] = "Archive synced successfully"

    except Exception as e:
        for p in (local_temp_zip, remote_temp_zip):
            if p and os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass
        with zip_lock:
            task = zip_tasks_state["active_tasks"].get(zip_path)
            if task and task.get("type") == "sync":
                task["status"] = "failed"
                task["error"] = str(e)
                task["message"] = f"Sync failed: {e}"
        logger.error(f"Error syncing archive back {zip_path}: {e}")
