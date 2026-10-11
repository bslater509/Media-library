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



def extract_zip_to_cache(zip_path: str, progress_cb=None) -> Path:
    """
    Extracts a ZIP archive to the local fast cache directory.
    Safe against concurrent extraction races, path traversal, strips redundant common root prefixes,
    and updates LRU metadata.
    """
    cache_dir = get_zip_cache_dir(zip_path)
    marker = cache_dir / ".extracted_done"

    if cache_dir.is_dir() and marker.exists():
        try:
            marker.touch()
        except OSError:
            pass
        return cache_dir

    lock = _get_archive_lock(zip_path)
    with lock:
        # Check again under lock in case another concurrent request just completed extraction
        if cache_dir.is_dir() and marker.exists():
            try:
                marker.touch()
            except OSError:
                pass
            return cache_dir

        # The archive may have been consumed by a concurrent unpack/compress (or
        # removed externally) while we waited on the lock. There is nothing left to
        # extract, so drop any stale task entry instead of recording a bogus failure.
        if not os.path.exists(zip_path):
            with zip_lock:
                task = zip_tasks_state["active_tasks"].get(zip_path)
                if task and task.get("type") == "extract":
                    zip_tasks_state["active_tasks"].pop(zip_path, None)
            return None

        # Ensure cache quota is respected before extraction
        manage_lru_cache()

        staging_dir = (
            cache_dir.parent / f"{cache_dir.name}_staging_{uuid.uuid4().hex[:8]}"
        )
        if staging_dir.exists():
            shutil.rmtree(staging_dir, ignore_errors=True)
        staging_dir.mkdir(parents=True, exist_ok=True)

        with zip_lock:
            zip_tasks_state["active_tasks"][zip_path] = {
                "type": "extract",
                "status": "running",
                "percent": 0.0,
                "current": 0,
                "total": 0,
                "message": "Starting extraction...",
                "started_at": time.time(),
            }

        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                members = [m for m in zf.infolist() if not m.is_dir()]
                total_members = len(members)

                with zip_lock:
                    task = zip_tasks_state["active_tasks"].get(zip_path)
                    if task and task.get("type") == "extract":
                        task["total"] = total_members

                common_prefix = _get_common_root_prefix(members)

                for i, member in enumerate(members):
                    base_name = os.path.basename(member.filename)
                    if base_name.startswith(".") or "__MACOSX" in member.filename:
                        continue

                    rel_name = member.filename
                    if common_prefix and rel_name.startswith(common_prefix):
                        rel_name = rel_name[len(common_prefix) :]
                    if not rel_name:
                        continue

                    # Guard against path traversal attacks (Zip Slip)
                    target_path = Path(staging_dir) / rel_name
                    try:
                        target_path.resolve().relative_to(staging_dir.resolve())
                    except ValueError:
                        continue  # Malicious path outside staging directory

                    target_path.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(member) as source, open(target_path, "wb") as dest:
                        shutil.copyfileobj(source, dest)

                    pct = round(((i + 1) / max(total_members, 1)) * 100, 1)
                    with zip_lock:
                        task = zip_tasks_state["active_tasks"].get(zip_path)
                        if task and task.get("type") == "extract":
                            task["percent"] = pct
                            task["current"] = i + 1
                            task["message"] = (
                                f"Extracting {i + 1}/{total_members} files ({pct}%)"
                            )

                    if progress_cb:
                        progress_cb(i + 1, total_members, member.filename)

            # Mark finished
            (staging_dir / ".extracted_done").touch()

            # Atomically replace or move into place
            if cache_dir.exists():
                shutil.rmtree(cache_dir, ignore_errors=True)
            staging_dir.rename(cache_dir)

            with zip_lock:
                task = zip_tasks_state["active_tasks"].get(zip_path)
                if task and task.get("type") == "extract":
                    task["status"] = "completed"
                    task["percent"] = 100.0
                    task["message"] = "Extraction complete"

            # Check LRU cache limit
            manage_lru_cache()
            return cache_dir

        except Exception as e:
            if staging_dir.exists():
                shutil.rmtree(staging_dir, ignore_errors=True)
            with zip_lock:
                task = zip_tasks_state["active_tasks"].get(zip_path)
                if task and task.get("type") == "extract":
                    task["status"] = "failed"
                    task["error"] = str(e)
                    task["message"] = f"Extraction failed: {e}"
            raise


def unpack_zip_to_folder(zip_path: str, progress_cb=None) -> dict[str, Any]:
    """
    Reverts a ZIP archive back into a regular directory on storage/pCloud,
    verifies files, deletes the ZIP file, updates DB index, and purges local cache.

    Serialized per archive via ``_get_archive_lock`` so an in-flight cache
    extraction can never race the archive being unpacked and deleted.
    """
    if not os.path.exists(zip_path) or not zip_path.lower().endswith(".zip"):
        raise ValueError(f"Path is not a valid ZIP file: {zip_path}")

    with _get_archive_lock(zip_path):
        # Re-check under the operation lock: a concurrent unpack/compress may have
        # consumed the archive while we were waiting.
        if not os.path.exists(zip_path):
            raise FileNotFoundError(f"Archive no longer exists: {zip_path}")
        return _unpack_zip_to_folder_impl(zip_path, progress_cb)


def _unpack_zip_to_folder_impl(zip_path: str, progress_cb=None) -> dict[str, Any]:
    """Unlocked implementation of :func:`unpack_zip_to_folder`."""
    parent_dir = os.path.dirname(os.path.normpath(zip_path))
    folder_name = Path(zip_path).stem
    target_folder = os.path.join(parent_dir, folder_name)

    if os.path.exists(target_folder):
        cnt = 1
        while os.path.exists(os.path.join(parent_dir, f"{folder_name}_{cnt}")):
            cnt += 1
        target_folder = os.path.join(parent_dir, f"{folder_name}_{cnt}")

    temp_unpack = os.path.join(
        parent_dir, f".tmp_unpack_{folder_name}_{int(time.time())}"
    )
    os.makedirs(temp_unpack, exist_ok=True)

    with zip_lock:
        zip_tasks_state["active_tasks"][zip_path] = {
            "type": "unpack",
            "status": "running",
            "percent": 0.0,
            "current": 0,
            "total": 0,
            "message": f"Unpacking {os.path.basename(zip_path)}...",
            "started_at": time.time(),
        }

    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            members = [m for m in zf.infolist() if not m.is_dir()]
            total_members = len(members)

            with zip_lock:
                zip_tasks_state["active_tasks"][zip_path]["total"] = total_members

            common_prefix = _get_common_root_prefix(members)

            for i, member in enumerate(members):
                base_name = os.path.basename(member.filename)
                if base_name.startswith(".") or "__MACOSX" in member.filename:
                    continue

                rel_name = member.filename
                if common_prefix and rel_name.startswith(common_prefix):
                    rel_name = rel_name[len(common_prefix) :]
                if not rel_name:
                    continue

                target_p = Path(temp_unpack) / rel_name
                try:
                    target_p.resolve().relative_to(Path(temp_unpack).resolve())
                except ValueError:
                    continue

                target_p.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(member) as src, open(target_p, "wb") as dst:
                    shutil.copyfileobj(src, dst)

                pct = round(((i + 1) / max(total_members, 1)) * 100, 1)
                with zip_lock:
                    task = zip_tasks_state["active_tasks"].get(zip_path)
                    if task:
                        task["percent"] = pct
                        task["current"] = i + 1
                        task["message"] = (
                            f"Unpacking {i + 1}/{total_members} files ({pct}%)"
                        )

                if progress_cb:
                    progress_cb(i + 1, total_members, member.filename)

        # Move unpacked directory into final location
        os.rename(temp_unpack, target_folder)

        # Delete the ZIP file from storage
        os.remove(zip_path)

        # Clean up local cache directory
        cache_dir = get_zip_cache_dir(zip_path)
        if cache_dir.exists():
            shutil.rmtree(cache_dir, ignore_errors=True)

        # Re-index SQLite
        with db_conn() as conn, conn:
            conn.execute("DELETE FROM folders WHERE path = ?", (zip_path,))
            conn.execute("DELETE FROM files WHERE folder_path = ?", (zip_path,))

        from media_library.db.indexer import index_folder

        index_folder(target_folder)

        with zip_lock:
            task = zip_tasks_state["active_tasks"].get(zip_path)
            if task and task.get("type") == "unpack":
                task["status"] = "completed"
                task["percent"] = 100.0
                task["message"] = "Unpack complete"

        return {
            "success": True,
            "folder_path": target_folder,
            "folder_name": os.path.basename(target_folder),
        }

    except Exception as e:
        if os.path.exists(temp_unpack):
            shutil.rmtree(temp_unpack, ignore_errors=True)
        with zip_lock:
            task = zip_tasks_state["active_tasks"].get(zip_path)
            if task and task.get("type") == "unpack":
                task["status"] = "failed"
                task["error"] = str(e)
                task["message"] = f"Unpack failed: {e}"
        raise
