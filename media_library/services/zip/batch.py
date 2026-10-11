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
from .compress import check_folder_compressibility, compress_folder_to_zip, trigger_debounced_sync_back, _execute_sync_back, _execute_sync_back_impl
from .extract import extract_zip_to_cache, unpack_zip_to_folder, _unpack_zip_to_folder_impl
from .optimize import optimize_zip_images_to_webp, _read_zip_member_bytes, _optimize_zip_images_to_webp_impl



# Batch compression state and queue management
batch_zip_state: dict[str, Any] = {
    "status": "idle",  # "idle" | "running" | "paused" | "canceled" | "completed"
    "queue": [],
    "current_index": 0,
    "total_folders": 0,
    "current_folder": "",
    "current_folder_name": "",
    "current_percent": 0.0,
    "overall_percent": 0.0,
    "succeeded": 0,
    "failed": 0,
    "errors": [],
    "is_paused": False,
    "started_at": 0,
    "completed_at": 0,
}
batch_zip_lock = threading.Lock()
batch_zip_pause_event = threading.Event()
batch_zip_pause_event.set()
batch_zip_cancel_event = threading.Event()
batch_zip_thread: threading.Thread | None = None


def find_batch_zip_candidate_folders() -> dict[str, Any]:
    """
    Finds all folders across the entire media library that qualify for batch ZIP compression:
    - is_zip = 0 (or null)
    - subfolder_count = 0 (leaf folder, no child directories)
    - video_count = 0
    - image_count >= 2
    Returns candidate list and aggregate statistics.
    """
    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
        SELECT 
            f.path,
            f.name,
            f.parent_path,
            COUNT(CASE WHEN fi.type = 'image' THEN 1 END) as image_count,
            COUNT(CASE WHEN fi.type = 'video' THEN 1 END) as video_count,
            COUNT(CASE WHEN fi.type NOT IN ('image', 'video') THEN 1 END) as other_count,
            COUNT(fi.path) as total_files,
            COALESCE(SUM(fi.size), 0) as total_size
        FROM folders f
        JOIN files fi ON fi.folder_path = f.path
        WHERE (f.is_zip = 0 OR f.is_zip IS NULL)
          AND (f.subfolder_count = 0 OR f.subfolder_count IS NULL)
        GROUP BY f.path, f.name, f.parent_path
        HAVING video_count = 0 AND image_count >= 2
        ORDER BY LOWER(f.name) ASC
        """)
        rows = cur.fetchall()

        candidates = []
        total_images = 0
        total_size = 0

        for r in rows:
            (
                path,
                name,
                parent_path,
                img_count,
                _vid_count,
                other_count,
                tot_files,
                sz,
            ) = r
            candidates.append(
                {
                    "path": path,
                    "name": name,
                    "parent_path": parent_path,
                    "parent_name": os.path.basename(parent_path) if parent_path else "",
                    "image_count": img_count,
                    "other_count": other_count,
                    "total_files": tot_files,
                    "total_size": sz,
                    "total_size_formatted": format_size(sz),
                    "target_zip": f"{name}.zip",
                }
            )
            total_images += img_count
            total_size += sz

        return {
            "total_candidates": len(candidates),
            "total_images": total_images,
            "total_size": total_size,
            "total_size_formatted": format_size(total_size),
            "candidates": candidates,
        }


def _run_batch_zip_worker():
    with batch_zip_lock:
        queue = list(batch_zip_state["queue"])
        total = len(queue)

    for idx, folder_path in enumerate(queue):
        if batch_zip_cancel_event.is_set():
            break

        batch_zip_pause_event.wait()
        if batch_zip_cancel_event.is_set():
            break

        folder_name = os.path.basename(os.path.normpath(folder_path))
        with batch_zip_lock:
            batch_zip_state["current_index"] = idx + 1
            batch_zip_state["current_folder"] = folder_path
            batch_zip_state["current_folder_name"] = folder_name
            batch_zip_state["current_percent"] = 0.0
            overall = round((idx / max(total, 1)) * 100, 1)
            batch_zip_state["overall_percent"] = overall

        def _folder_progress(cur, tot, file_name, _idx=idx):
            pct = round((cur / max(tot, 1)) * 100, 1)
            with batch_zip_lock:
                batch_zip_state["current_percent"] = pct
                overall_pct = round(
                    ((_idx + (cur / max(tot, 1))) / max(total, 1)) * 100, 1
                )
                batch_zip_state["overall_percent"] = min(overall_pct, 99.9)

        try:
            chk = check_folder_compressibility(folder_path)
            if not chk["eligible"]:
                raise ValueError(chk["reason"])

            compress_folder_to_zip(folder_path, progress_cb=_folder_progress)
            with batch_zip_lock:
                batch_zip_state["succeeded"] += 1
        except Exception as e:
            logger.error(f"[Batch Zip] Error compressing {folder_path}: {e}")
            with batch_zip_lock:
                batch_zip_state["failed"] += 1
                batch_zip_state["errors"].append(
                    {
                        "folder": folder_path,
                        "name": folder_name,
                        "error": str(e),
                    }
                )

    with batch_zip_lock:
        if batch_zip_cancel_event.is_set():
            batch_zip_state["status"] = "canceled"
        else:
            batch_zip_state["status"] = "completed"
            batch_zip_state["overall_percent"] = 100.0
        batch_zip_state["completed_at"] = time.time()


def start_batch_zip(folder_paths: list[str]) -> dict[str, Any]:
    global batch_zip_thread
    with batch_zip_lock:
        if batch_zip_state["status"] == "running":
            raise RuntimeError("Batch ZIP compression is already in progress.")
        if batch_zip_thread is not None and batch_zip_thread.is_alive():
            raise RuntimeError(
                "A previous batch ZIP compression is still finishing. Please retry shortly."
            )

        batch_zip_cancel_event.clear()
        batch_zip_pause_event.set()

        batch_zip_state.update(
            {
                "status": "running",
                "queue": list(folder_paths),
                "current_index": 0,
                "total_folders": len(folder_paths),
                "current_folder": "",
                "current_folder_name": "",
                "current_percent": 0.0,
                "overall_percent": 0.0,
                "succeeded": 0,
                "failed": 0,
                "errors": [],
                "is_paused": False,
                "started_at": time.time(),
                "completed_at": 0,
            }
        )

        batch_zip_thread = threading.Thread(target=_run_batch_zip_worker, daemon=True)
        batch_zip_thread.start()

        return {
            "success": True,
            "message": f"Started batch ZIP compression for {len(folder_paths)} folders.",
            "total_folders": len(folder_paths),
        }


def pause_batch_zip() -> dict[str, Any]:
    with batch_zip_lock:
        if batch_zip_state["status"] == "running":
            batch_zip_pause_event.clear()
            batch_zip_state["status"] = "paused"
            batch_zip_state["is_paused"] = True
            return {
                "success": True,
                "status": "paused",
                "message": "Batch compression paused.",
            }
        elif batch_zip_state["status"] == "paused":
            batch_zip_pause_event.set()
            batch_zip_state["status"] = "running"
            batch_zip_state["is_paused"] = False
            return {
                "success": True,
                "status": "running",
                "message": "Batch compression resumed.",
            }
        return {
            "success": False,
            "status": batch_zip_state["status"],
            "message": f"Cannot pause in status {batch_zip_state['status']}",
        }


def resume_batch_zip() -> dict[str, Any]:
    with batch_zip_lock:
        if batch_zip_state["status"] == "paused":
            batch_zip_pause_event.set()
            batch_zip_state["status"] = "running"
            batch_zip_state["is_paused"] = False
            return {
                "success": True,
                "status": "running",
                "message": "Batch compression resumed.",
            }
        return {
            "success": False,
            "status": batch_zip_state["status"],
            "message": "Batch compression is not paused.",
        }


def cancel_batch_zip() -> dict[str, Any]:
    with batch_zip_lock:
        if batch_zip_state["status"] in ("running", "paused"):
            batch_zip_cancel_event.set()
            batch_zip_pause_event.set()
            batch_zip_state["status"] = "canceled"
            batch_zip_state["is_paused"] = False
            return {
                "success": True,
                "status": "canceled",
                "message": "Batch compression canceled.",
            }
        return {
            "success": False,
            "status": batch_zip_state["status"],
            "message": "No active batch compression to cancel.",
        }


def get_batch_zip_status() -> dict[str, Any]:
    with batch_zip_lock:
        return dict(batch_zip_state)


# Batch unpack (extract-all-archives) state and queue management.
# Mirrors the batch-compress runner above but drives per-archive extraction.
batch_unzip_state: dict[str, Any] = {
    "status": "idle",  # "idle" | "running" | "paused" | "canceled" | "completed"
    "queue": [],
    "current_index": 0,
    "total_zips": 0,
    "current_zip": "",
    "current_zip_name": "",
    "current_percent": 0.0,
    "overall_percent": 0.0,
    "succeeded": 0,
    "failed": 0,
    "errors": [],
    "is_paused": False,
    "started_at": 0,
    "completed_at": 0,
}
batch_unzip_lock = threading.Lock()
batch_unzip_pause_event = threading.Event()
batch_unzip_pause_event.set()
batch_unzip_cancel_event = threading.Event()
batch_unzip_thread: threading.Thread | None = None


def find_batch_unzip_candidate_zips() -> dict[str, Any]:
    """
    Finds all indexed ZIP archives across the entire media library
    (folders with is_zip = 1) that are candidates for extraction back into
    regular folders. Index-only and FUSE-safe: never walks the filesystem.
    """
    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
        SELECT f.path, f.name, f.parent_path,
               COUNT(fi.path) AS total_files,
               COALESCE(SUM(fi.size), 0) AS total_size
        FROM folders f
        LEFT JOIN files fi ON fi.folder_path = f.path
        WHERE f.is_zip = 1
        GROUP BY f.path, f.name, f.parent_path
        ORDER BY LOWER(f.name) ASC
        """)
        rows = cur.fetchall()

        candidates = []
        total_files = 0
        total_size = 0

        for r in rows:
            path, name, parent_path, file_count, sz = r
            candidates.append(
                {
                    "path": path,
                    "name": name,
                    "parent_path": parent_path,
                    "parent_name": os.path.basename(parent_path) if parent_path else "",
                    "total_files": file_count,
                    "total_size": sz,
                    "total_size_formatted": format_size(sz),
                }
            )
            total_files += file_count
            total_size += sz

        return {
            "total_candidates": len(candidates),
            "total_files": total_files,
            "total_size": total_size,
            "total_size_formatted": format_size(total_size),
            "candidates": candidates,
        }


def _run_batch_unzip_worker():
    with batch_unzip_lock:
        queue = list(batch_unzip_state["queue"])
        total = len(queue)

    for idx, zip_path in enumerate(queue):
        if batch_unzip_cancel_event.is_set():
            break

        batch_unzip_pause_event.wait()
        if batch_unzip_cancel_event.is_set():
            break

        zip_name = os.path.basename(os.path.normpath(zip_path))
        with batch_unzip_lock:
            batch_unzip_state["current_index"] = idx + 1
            batch_unzip_state["current_zip"] = zip_path
            batch_unzip_state["current_zip_name"] = zip_name
            batch_unzip_state["current_percent"] = 0.0
            overall = round((idx / max(total, 1)) * 100, 1)
            batch_unzip_state["overall_percent"] = overall

        def _unpack_progress(cur, tot, file_name, _idx=idx):
            pct = round((cur / max(tot, 1)) * 100, 1)
            with batch_unzip_lock:
                batch_unzip_state["current_percent"] = pct
                overall_pct = round(
                    ((_idx + (cur / max(tot, 1))) / max(total, 1)) * 100, 1
                )
                batch_unzip_state["overall_percent"] = min(overall_pct, 99.9)

        try:
            # Existing unpack handles conflict auto-rename, ZIP deletion, DB
            # re-index, and per-archive progress in zip_tasks_state. Failures
            # (missing/corrupt archives) are captured and skipped so the batch
            # keeps going.
            unpack_zip_to_folder(zip_path, progress_cb=_unpack_progress)
            with batch_unzip_lock:
                batch_unzip_state["succeeded"] += 1
        except Exception as e:
            logger.error(f"[Batch Unpack] Error extracting {zip_path}: {e}")
            with batch_unzip_lock:
                batch_unzip_state["failed"] += 1
                batch_unzip_state["errors"].append(
                    {
                        "zip": zip_path,
                        "name": zip_name,
                        "error": str(e),
                    }
                )

    with batch_unzip_lock:
        if batch_unzip_cancel_event.is_set():
            batch_unzip_state["status"] = "canceled"
        else:
            batch_unzip_state["status"] = "completed"
            batch_unzip_state["overall_percent"] = 100.0
        batch_unzip_state["completed_at"] = time.time()


def start_batch_unzip(zip_paths: list[str]) -> dict[str, Any]:
    global batch_unzip_thread
    with batch_unzip_lock:
        if batch_unzip_state["status"] == "running":
            raise RuntimeError("Batch ZIP extraction is already in progress.")
        if batch_unzip_thread is not None and batch_unzip_thread.is_alive():
            raise RuntimeError(
                "A previous batch ZIP extraction is still finishing. Please retry shortly."
            )

        batch_unzip_cancel_event.clear()
        batch_unzip_pause_event.set()

        batch_unzip_state.update(
            {
                "status": "running",
                "queue": list(zip_paths),
                "current_index": 0,
                "total_zips": len(zip_paths),
                "current_zip": "",
                "current_zip_name": "",
                "current_percent": 0.0,
                "overall_percent": 0.0,
                "succeeded": 0,
                "failed": 0,
                "errors": [],
                "is_paused": False,
                "started_at": time.time(),
                "completed_at": 0,
            }
        )

        batch_unzip_thread = threading.Thread(
            target=_run_batch_unzip_worker, daemon=True
        )
        batch_unzip_thread.start()

        return {
            "success": True,
            "message": f"Started batch ZIP extraction for {len(zip_paths)} archives.",
            "total_zips": len(zip_paths),
        }


def pause_batch_unzip() -> dict[str, Any]:
    with batch_unzip_lock:
        if batch_unzip_state["status"] == "running":
            batch_unzip_pause_event.clear()
            batch_unzip_state["status"] = "paused"
            batch_unzip_state["is_paused"] = True
            return {
                "success": True,
                "status": "paused",
                "message": "Batch extraction paused.",
            }
        elif batch_unzip_state["status"] == "paused":
            batch_unzip_pause_event.set()
            batch_unzip_state["status"] = "running"
            batch_unzip_state["is_paused"] = False
            return {
                "success": True,
                "status": "running",
                "message": "Batch extraction resumed.",
            }
        return {
            "success": False,
            "status": batch_unzip_state["status"],
            "message": f"Cannot pause in status {batch_unzip_state['status']}",
        }


def resume_batch_unzip() -> dict[str, Any]:
    with batch_unzip_lock:
        if batch_unzip_state["status"] == "paused":
            batch_unzip_pause_event.set()
            batch_unzip_state["status"] = "running"
            batch_unzip_state["is_paused"] = False
            return {
                "success": True,
                "status": "running",
                "message": "Batch extraction resumed.",
            }
        return {
            "success": False,
            "status": batch_unzip_state["status"],
            "message": "Batch extraction is not paused.",
        }


def cancel_batch_unzip() -> dict[str, Any]:
    with batch_unzip_lock:
        if batch_unzip_state["status"] in ("running", "paused"):
            batch_unzip_cancel_event.set()
            batch_unzip_pause_event.set()
            batch_unzip_state["status"] = "canceled"
            batch_unzip_state["is_paused"] = False
            return {
                "success": True,
                "status": "canceled",
                "message": "Batch extraction canceled.",
            }
        return {
            "success": False,
            "status": batch_unzip_state["status"],
            "message": "No active batch extraction to cancel.",
        }


def get_batch_unzip_status() -> dict[str, Any]:
    with batch_unzip_lock:
        return dict(batch_unzip_state)
