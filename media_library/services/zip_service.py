"""ZIP archive compression, extraction cache, and virtual folder management."""

import os
import time
import uuid
import shutil
import hashlib
import zipfile
import threading
import asyncio
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple

from media_library.core.constants import IMAGE_EXTENSIONS, VIDEO_EXTENSIONS
from media_library.core.config import (
    ZIP_CACHE_DIR,
    ZIP_CACHE_MAX_SIZE_GB,
    THUMBNAIL_CACHE_DIR,
    ZIP_STAGING_DIR,
)
from media_library.core.state import format_size
from media_library.db.connection import get_db

# In-memory tracking of ongoing zip operations
zip_tasks_state: Dict[str, Any] = {
    # path -> {"type": "compress"|"extract"|"sync"|"unpack", "status": "running"|"completed"|"failed", "percent": 0.0, "message": "...", "started_at": ts}
    "active_tasks": {},
    # path -> threading.Timer
    "sync_timers": {},
}
zip_lock = threading.Lock()
_extract_locks: Dict[str, threading.Lock] = {}
_extract_locks_guard = threading.Lock()


def _get_extract_lock(zip_path: str) -> threading.Lock:
    norm = os.path.abspath(zip_path)
    with _extract_locks_guard:
        if norm not in _extract_locks:
            _extract_locks[norm] = threading.Lock()
        return _extract_locks[norm]


def _get_common_root_prefix(members_list: List[zipfile.ZipInfo]) -> str:
    """If all non-system, non-dir members share a common top-level directory, return it with trailing slash."""
    non_sys = [
        m.filename for m in members_list
        if not os.path.basename(m.filename).startswith(".")
        and "__MACOSX" not in m.filename
        and not m.is_dir()
        and not m.filename.endswith("/")
    ]
    if not non_sys:
        return ""
    first_parts = set()
    for name in non_sys:
        parts = Path(name).parts
        if len(parts) > 1:
            first_parts.add(parts[0])
        else:
            return ""
    if len(first_parts) == 1:
        return list(first_parts)[0] + "/"
    return ""


def split_zip_path(path_str: str) -> Tuple[Optional[str], str]:
    """
    Checks if path_str references a .zip archive or an item inside a .zip archive.
    Returns (zip_path, inner_relative_path).
    Example:
      /media/album.zip -> ('/media/album.zip', '')
      /media/album.zip/sub/img.jpg -> ('/media/album.zip', 'sub/img.jpg')
      /media/regular_folder -> (None, '')
    """
    norm = os.path.normpath(path_str)
    parts = Path(norm).parts
    for i, part in enumerate(parts):
        if part.lower().endswith(".zip"):
            zip_p = str(Path(*parts[: i + 1]))
            inner_p = str(Path(*parts[i + 1 :])) if i + 1 < len(parts) else ""
            if inner_p == ".":
                inner_p = ""
            return zip_p, inner_p
    return None, ""


def is_zip_file(path_str: str) -> bool:
    """Checks whether the path points to an existing .zip file."""
    try:
        return os.path.isfile(path_str) and path_str.lower().endswith(".zip")
    except (OSError, ValueError):
        return False


def get_zip_cache_dir(zip_path: str) -> Path:
    """Returns deterministic directory path for extracted contents in local cache."""
    real_path = os.path.abspath(zip_path)
    hash_key = hashlib.sha256(real_path.encode("utf-8")).hexdigest()[:16]
    stem = Path(zip_path).stem
    return ZIP_CACHE_DIR / f"{stem}_{hash_key}"


def is_zip_extracted(zip_path: str) -> bool:
    """Checks if the zip has already been extracted to local cache."""
    cache_dir = get_zip_cache_dir(zip_path)
    marker = cache_dir / ".extracted_done"
    return cache_dir.is_dir() and marker.exists()


def resolve_zip_file_path(path_str: str) -> Optional[str]:
    """
    If path_str points to an inner file inside a .zip archive,
    resolves and returns the real path in the local extracted cache if it exists.
    Supports flat extraction, nested directories, and existing subfolder caches.
    """
    zip_path, inner_rel = split_zip_path(path_str)
    if not zip_path or not inner_rel:
        return None
    cache_dir = get_zip_cache_dir(zip_path)
    if not cache_dir.exists() or not cache_dir.is_dir():
        return None

    resolved_path: Optional[Path] = None

    # 1. Direct path check
    target = cache_dir / inner_rel
    if target.exists() and target.is_file():
        resolved_path = target
    else:
        # 2. Check immediate subdirectories (e.g. common root directory preserved in cache)
        try:
            for item in cache_dir.iterdir():
                if item.is_dir() and not item.name.startswith("."):
                    candidate = item / inner_rel
                    if candidate.exists() and candidate.is_file():
                        resolved_path = candidate
                        break
        except OSError:
            pass

    # 3. Fallback: match by file basename inside cache_dir (e.g. nested subdirectories)
    if resolved_path is None:
        base_name = Path(inner_rel).name
        try:
            for root, _, files in os.walk(cache_dir):
                if base_name in files:
                    cand = Path(root) / base_name
                    if cand.is_file():
                        resolved_path = cand
                        break
        except OSError:
            pass

    if resolved_path is not None and resolved_path.is_file():
        # Touch marker for LRU tracking
        try:
            marker = cache_dir / ".extracted_done"
            if marker.exists():
                marker.touch()
        except OSError:
            pass
        return str(resolved_path)

    return None


def get_zip_metadata(zip_path: str) -> Dict[str, Any]:
    """
    Quickly inspects ZIP central directory headers (<50ms)
    without extracting file contents.
    """
    if not os.path.exists(zip_path):
        raise FileNotFoundError(f"ZIP file not found: {zip_path}")

    files = []
    total_size = 0
    image_count = 0
    video_count = 0

    with zipfile.ZipFile(zip_path, "r") as zf:
        for info in zf.infolist():
            # Skip directory markers and hidden/system files
            if info.is_dir():
                continue
            name = info.filename
            base_name = os.path.basename(name)
            if base_name.startswith(".") or "__MACOSX" in name:
                continue

            ext = os.path.splitext(base_name)[1].lower()
            is_img = ext in IMAGE_EXTENSIONS
            is_vid = ext in VIDEO_EXTENSIONS

            if is_img:
                image_count += 1
            elif is_vid:
                video_count += 1

            total_size += info.file_size
            files.append({
                "name": base_name,
                "inner_path": name,
                "size": info.file_size,
                "size_formatted": format_size(info.file_size),
                "is_image": is_img,
                "is_video": is_vid,
                "ext": ext,
            })

    files.sort(key=lambda x: x["name"].lower())
    return {
        "zip_path": zip_path,
        "zip_name": os.path.basename(zip_path),
        "clean_name": Path(zip_path).stem,
        "total_files": len(files),
        "image_count": image_count,
        "video_count": video_count,
        "total_size": total_size,
        "total_size_formatted": format_size(total_size),
        "files": files,
    }


def check_folder_compressibility(folder_path: str) -> Dict[str, Any]:
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

    lock = _get_extract_lock(zip_path)
    with lock:
        # Check again under lock in case another concurrent request just completed extraction
        if cache_dir.is_dir() and marker.exists():
            try:
                marker.touch()
            except OSError:
                pass
            return cache_dir

        # Ensure cache quota is respected before extraction
        manage_lru_cache()

        staging_dir = cache_dir.parent / f"{cache_dir.name}_staging_{uuid.uuid4().hex[:8]}"
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
                    if task:
                        task["total"] = total_members

                common_prefix = _get_common_root_prefix(members)

                for i, member in enumerate(members):
                    base_name = os.path.basename(member.filename)
                    if base_name.startswith(".") or "__MACOSX" in member.filename:
                        continue

                    rel_name = member.filename
                    if common_prefix and rel_name.startswith(common_prefix):
                        rel_name = rel_name[len(common_prefix):]
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
                        if task:
                            task["percent"] = pct
                            task["current"] = i + 1
                            task["message"] = f"Extracting {i + 1}/{total_members} files ({pct}%)"

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
                if task:
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
                if task:
                    task["status"] = "failed"
                    task["error"] = str(e)
                    task["message"] = f"Extraction failed: {e}"
            raise e


def compress_folder_to_zip(folder_path: str, progress_cb=None) -> Dict[str, Any]:
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
    local_temp_zip = str(ZIP_STAGING_DIR / f".tmp_{folder_name}_{int(time.time())}_{uuid.uuid4().hex[:6]}.zip")
    remote_temp_zip = os.path.join(parent_dir, f".tmp_transfer_{folder_name}_{int(time.time())}.zip")

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

        with zipfile.ZipFile(local_temp_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for i, (full_path, arc_name) in enumerate(all_files):
                ext = os.path.splitext(arc_name)[1].lower()
                if ext in IMAGE_EXTENSIONS and ext != ".webp":
                    try:
                        from media_library.services.ffmpeg_service import convert_image_to_webp_bytes
                        webp_bytes = convert_image_to_webp_bytes(full_path)
                        webp_arc_name = os.path.splitext(arc_name)[0] + ".webp"
                        zf.writestr(webp_arc_name, webp_bytes)
                    except Exception as ce:
                        print(f"Warning: could not convert {arc_name} to WebP: {ce}")
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
                raise RuntimeError(f"ZIP archive integrity check failed on member: {corrupted}")

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
        conn = get_db()
        try:
            with conn:
                conn.execute("DELETE FROM folders WHERE path = ?", (folder_path,))
                conn.execute("DELETE FROM files WHERE folder_path = ?", (folder_path,))
                conn.execute("DELETE FROM folders WHERE parent_path = ?", (folder_path,))
        finally:
            conn.close()

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
        raise e


def unpack_zip_to_folder(zip_path: str, progress_cb=None) -> Dict[str, Any]:
    """
    Reverts a ZIP archive back into a regular directory on storage/pCloud,
    verifies files, deletes the ZIP file, updates DB index, and purges local cache.
    """
    if not os.path.exists(zip_path) or not zip_path.lower().endswith(".zip"):
        raise ValueError(f"Path is not a valid ZIP file: {zip_path}")

    parent_dir = os.path.dirname(os.path.normpath(zip_path))
    folder_name = Path(zip_path).stem
    target_folder = os.path.join(parent_dir, folder_name)

    if os.path.exists(target_folder):
        cnt = 1
        while os.path.exists(os.path.join(parent_dir, f"{folder_name}_{cnt}")):
            cnt += 1
        target_folder = os.path.join(parent_dir, f"{folder_name}_{cnt}")

    temp_unpack = os.path.join(parent_dir, f".tmp_unpack_{folder_name}_{int(time.time())}")
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
                    rel_name = rel_name[len(common_prefix):]
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
                        task["message"] = f"Unpacking {i + 1}/{total_members} files ({pct}%)"

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
        conn = get_db()
        try:
            with conn:
                conn.execute("DELETE FROM folders WHERE path = ?", (zip_path,))
                conn.execute("DELETE FROM files WHERE folder_path = ?", (zip_path,))
        finally:
            conn.close()

        from media_library.db.indexer import index_folder
        index_folder(target_folder)

        with zip_lock:
            task = zip_tasks_state["active_tasks"].get(zip_path)
            if task:
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
            if task:
                task["status"] = "failed"
                task["error"] = str(e)
                task["message"] = f"Unpack failed: {e}"
        raise e


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
    """Re-packs the extracted cache directory and updates the ZIP on storage."""
    cache_dir = get_zip_cache_dir(zip_path)
    if not cache_dir.exists():
        return

    parent_dir = os.path.dirname(zip_path)
    local_temp_zip = str(ZIP_STAGING_DIR / f".tmp_sync_{Path(zip_path).stem}_{int(time.time())}_{uuid.uuid4().hex[:6]}.zip")
    remote_temp_zip = os.path.join(parent_dir, f".tmp_transfer_sync_{Path(zip_path).stem}_{int(time.time())}.zip")

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

        with zipfile.ZipFile(local_temp_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
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
            if task:
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
            if task:
                task["status"] = "failed"
                task["error"] = str(e)
                task["message"] = f"Sync failed: {e}"
        print(f"Error syncing archive back {zip_path}: {e}")


def optimize_zip_images_to_webp(zip_path: str, quality: Optional[int] = None) -> Dict[str, Any]:
    """
    Scans a ZIP archive and converts all non-WebP image members to .webp format.
    Rebuilds archive atomically, verifies CRC, purges stale extraction cache,
    re-indexes in SQLite, and updates webp_stats.
    """
    if not os.path.exists(zip_path) or not zip_path.lower().endswith(".zip"):
        return {"status": "error", "error": f"Invalid zip path: {zip_path}", "converted": 0}

    manage_lru_cache()
    orig_zip_size = os.path.getsize(zip_path)
    parent_dir = os.path.dirname(os.path.normpath(zip_path))
    local_temp_zip = str(ZIP_STAGING_DIR / f".tmp_webp_opt_{int(time.time())}_{uuid.uuid4().hex[:6]}.zip")
    remote_temp_zip = os.path.join(parent_dir, f".tmp_transfer_webp_{Path(zip_path).stem}_{int(time.time())}.zip")

    with zip_lock:
        task = zip_tasks_state["active_tasks"].get(zip_path)
        if not task:
            zip_tasks_state["active_tasks"][zip_path] = {
                "type": "webp_optimize",
                "status": "running",
                "percent": 0.0,
                "current": 0,
                "total": 0,
                "message": "Scanning ZIP for non-WebP images...",
                "started_at": time.time(),
            }

    try:
        from media_library.services.ffmpeg_service import convert_image_to_webp_bytes

        with zipfile.ZipFile(zip_path, "r") as zf_in:
            infolist = zf_in.infolist()
            convert_targets = []
            for info in infolist:
                if info.is_dir():
                    continue
                ext = os.path.splitext(info.filename)[1].lower()
                if ext in IMAGE_EXTENSIONS and ext != ".webp":
                    convert_targets.append(info.filename)

            if not convert_targets:
                return {
                    "status": "already_webp",
                    "zip_path": zip_path,
                    "converted": 0,
                    "message": "All images in ZIP are already WebP"
                }

            total_items = len(infolist)
            converted_count = 0

            with zipfile.ZipFile(local_temp_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf_out:
                for idx, info in enumerate(infolist):
                    if info.is_dir():
                        zf_out.writestr(info, "")
                        continue

                    raw_data = zf_in.read(info.filename)
                    ext = os.path.splitext(info.filename)[1].lower()

                    if ext in IMAGE_EXTENSIONS and ext != ".webp":
                        try:
                            webp_bytes = convert_image_to_webp_bytes(raw_data, quality=quality)
                            new_name = os.path.splitext(info.filename)[0] + ".webp"
                            new_info = zipfile.ZipInfo(new_name, date_time=info.date_time)
                            new_info.compress_type = zipfile.ZIP_DEFLATED
                            zf_out.writestr(new_info, webp_bytes)
                            converted_count += 1
                        except Exception as conv_err:
                            print(f"Warning: could not convert {info.filename} in {zip_path}: {conv_err}")
                            zf_out.writestr(info, raw_data)
                    else:
                        zf_out.writestr(info, raw_data)

                    pct = round(((idx + 1) / max(total_items, 1)) * 100, 1)
                    with zip_lock:
                        t = zip_tasks_state["active_tasks"].get(zip_path)
                        if t:
                            t["percent"] = pct
                            t["current"] = idx + 1
                            t["message"] = f"Converting images in ZIP to WebP ({pct}%)..."

        # Verify integrity on local disk
        with zipfile.ZipFile(local_temp_zip, "r") as zf_test:
            corrupt = zf_test.testzip()
            if corrupt:
                raise RuntimeError(f"Optimized zip verification failed on member: {corrupt}")

        # Transfer verified archive to destination and atomically replace
        shutil.copyfile(local_temp_zip, remote_temp_zip)
        os.replace(remote_temp_zip, zip_path)

        if os.path.exists(local_temp_zip):
            try:
                os.remove(local_temp_zip)
            except OSError:
                pass

        new_zip_size = os.path.getsize(zip_path)
        saved_bytes = max(0, orig_zip_size - new_zip_size)

        # Invalidate extraction cache if exists
        cache_dir = get_zip_cache_dir(zip_path)
        if cache_dir.exists():
            shutil.rmtree(cache_dir, ignore_errors=True)

        # Re-index ZIP in SQLite
        index_zip_archive(zip_path, parent_path=parent_dir)

        # Update webp_stats
        conn = get_db()
        try:
            with conn:
                conn.execute("""
                    UPDATE webp_stats 
                    SET saved_bytes = saved_bytes + ?, converted_count = converted_count + ?
                    WHERE id = 1
                """, (saved_bytes, converted_count))
        except Exception as se:
            print(f"Error updating webp_stats for zip: {se}")
        finally:
            conn.close()

        with zip_lock:
            t = zip_tasks_state["active_tasks"].get(zip_path)
            if t:
                t["status"] = "completed"
                t["percent"] = 100.0
                t["message"] = f"Converted {converted_count} images to WebP"

        return {
            "status": "converted",
            "zip_path": zip_path,
            "converted_count": converted_count,
            "orig_size": orig_zip_size,
            "new_size": new_zip_size,
            "saved_bytes": saved_bytes,
        }

    except Exception as e:
        for p in (local_temp_zip, remote_temp_zip):
            if p and os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass
        with zip_lock:
            t = zip_tasks_state["active_tasks"].get(zip_path)
            if t:
                t["status"] = "failed"
                t["error"] = str(e)
                t["message"] = f"WebP optimization of ZIP failed: {e}"
        raise e


def index_zip_archive(zip_path: str, parent_path: Optional[str] = None):
    """Indexes a ZIP file as a virtual folder and its files into SQLite."""
    if not os.path.exists(zip_path):
        return

    meta = get_zip_metadata(zip_path)
    clean_name = meta["clean_name"]
    parent = parent_path or os.path.dirname(zip_path)

    conn = get_db()
    try:
        with conn:
            conn.execute("""
            INSERT INTO folders (path, name, parent_path, file_count, subfolder_count, last_scanned, is_zip)
            VALUES (?, ?, ?, ?, 0, ?, 1)
            ON CONFLICT(path) DO UPDATE SET
                name=excluded.name,
                parent_path=excluded.parent_path,
                file_count=excluded.file_count,
                subfolder_count=0,
                last_scanned=excluded.last_scanned,
                is_zip=1
            """, (zip_path, clean_name, parent, meta["total_files"], time.time()))

            conn.execute("DELETE FROM files WHERE folder_path = ?", (zip_path,))

            file_rows = []
            for f in meta["files"]:
                full_vpath = os.path.join(zip_path, f["name"])
                ftype = "image" if f["is_image"] else ("video" if f["is_video"] else "other")
                # Thumbnail hash check if thumb exists
                key = f"{full_vpath}_{f['size']}"
                h = hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
                has_thumb = 1 if (THUMBNAIL_CACHE_DIR / f"thumb_{h}.jpg").exists() else 0
                webp_opt = 1 if f["ext"] == ".webp" else 0

                file_rows.append((
                    full_vpath,
                    f["name"],
                    zip_path,
                    ftype,
                    f["ext"],
                    f["size"],
                    f["size_formatted"],
                    time.time(),
                    0,
                    has_thumb,
                    webp_opt,
                ))

            if file_rows:
                conn.executemany("""
                INSERT INTO files (path, name, folder_path, type, ext, size, size_formatted, mtime, needs_transcode, has_thumb, webp_optimized)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, file_rows)
    finally:
        conn.close()


def manage_lru_cache():
    """
    Evicts oldest extracted ZIP caches if total cache size exceeds ZIP_CACHE_MAX_SIZE_GB.
    Never evicts caches with active sync or extraction tasks.
    Also cleans up orphaned staging folders and temporary files older than 10 minutes.
    """
    if not ZIP_CACHE_DIR.exists():
        return

    now = time.time()
    # 1. Clean up stale staging directories and temporary files older than 10 minutes
    try:
        for item in ZIP_CACHE_DIR.iterdir():
            if item.is_dir() and "_staging_" in item.name:
                try:
                    if now - item.stat().st_mtime > 600:
                        shutil.rmtree(item, ignore_errors=True)
                except OSError:
                    pass
    except OSError:
        pass

    if ZIP_STAGING_DIR.exists():
        try:
            for p in ZIP_STAGING_DIR.glob(".tmp_*"):
                try:
                    if now - p.stat().st_mtime > 600:
                        if p.is_dir():
                            shutil.rmtree(p, ignore_errors=True)
                        else:
                            p.unlink(missing_ok=True)
                except OSError:
                    pass
        except OSError:
            pass

    max_bytes = int(ZIP_CACHE_MAX_SIZE_GB * 1024 * 1024 * 1024)
    total_size = 0
    cache_dirs = []

    try:
        dir_entries = list(ZIP_CACHE_DIR.iterdir())
    except OSError:
        return

    for item in dir_entries:
        if not item.is_dir() or item.name.startswith(".") or "_staging_" in item.name:
            continue
        dir_size = 0
        try:
            latest_atime = item.stat().st_mtime
        except OSError:
            latest_atime = 0.0
        marker = item / ".extracted_done"
        if marker.exists():
            try:
                latest_atime = max(latest_atime, marker.stat().st_mtime)
            except OSError:
                pass
        try:
            for root, _, files in os.walk(item):
                for f in files:
                    fp = os.path.join(root, f)
                    try:
                        st = os.stat(fp)
                        dir_size += st.st_size
                        latest_atime = max(latest_atime, st.st_atime, st.st_mtime)
                    except OSError:
                        pass
        except OSError:
            pass
        total_size += dir_size
        cache_dirs.append((item, dir_size, latest_atime))

    if total_size <= max_bytes:
        return

    # Sort oldest accessed first
    cache_dirs.sort(key=lambda x: x[2])

    with zip_lock:
        active_paths = {p for p, t in zip_tasks_state["active_tasks"].items() if t.get("status") in ("running", "queued")}
        # Prune old completed/failed tasks from active_tasks to prevent unbounded memory/dict growth
        if len(zip_tasks_state["active_tasks"]) > 100:
            finished_keys = [p for p, t in zip_tasks_state["active_tasks"].items() if t.get("status") not in ("running", "queued")]
            for k in finished_keys[:-50]:
                zip_tasks_state["active_tasks"].pop(k, None)

    # Active cache dirs matching any actively running or queued task
    active_cache_dirs = {get_zip_cache_dir(p) for p in active_paths}

    for dir_path, dir_size, _ in cache_dirs:
        # Check if actively running
        if dir_path in active_cache_dirs:
            continue
        try:
            shutil.rmtree(dir_path, ignore_errors=True)
            total_size -= dir_size
            if total_size <= max_bytes:
                break
        except Exception as e:
            print(f"Error evicting cache dir {dir_path}: {e}")


# Batch compression state and queue management
batch_zip_state: Dict[str, Any] = {
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
batch_zip_thread: Optional[threading.Thread] = None


def find_batch_zip_candidate_folders() -> Dict[str, Any]:
    """
    Finds all folders across the entire media library that qualify for batch ZIP compression:
    - is_zip = 0 (or null)
    - subfolder_count = 0 (leaf folder, no child directories)
    - video_count = 0
    - image_count >= 2
    Returns candidate list and aggregate statistics.
    """
    conn = get_db()
    try:
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
            path, name, parent_path, img_count, vid_count, other_count, tot_files, sz = r
            candidates.append({
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
            })
            total_images += img_count
            total_size += sz

        return {
            "total_candidates": len(candidates),
            "total_images": total_images,
            "total_size": total_size,
            "total_size_formatted": format_size(total_size),
            "candidates": candidates,
        }
    finally:
        conn.close()


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

        def _folder_progress(cur, tot, file_name):
            pct = round((cur / max(tot, 1)) * 100, 1)
            with batch_zip_lock:
                batch_zip_state["current_percent"] = pct
                overall_pct = round(((idx + (cur / max(tot, 1))) / max(total, 1)) * 100, 1)
                batch_zip_state["overall_percent"] = min(overall_pct, 99.9)

        try:
            chk = check_folder_compressibility(folder_path)
            if not chk["eligible"]:
                raise ValueError(chk["reason"])

            compress_folder_to_zip(folder_path, progress_cb=_folder_progress)
            with batch_zip_lock:
                batch_zip_state["succeeded"] += 1
        except Exception as e:
            print(f"[Batch Zip] Error compressing {folder_path}: {e}")
            with batch_zip_lock:
                batch_zip_state["failed"] += 1
                batch_zip_state["errors"].append({
                    "folder": folder_path,
                    "name": folder_name,
                    "error": str(e),
                })

    with batch_zip_lock:
        if batch_zip_cancel_event.is_set():
            batch_zip_state["status"] = "canceled"
        else:
            batch_zip_state["status"] = "completed"
            batch_zip_state["overall_percent"] = 100.0
        batch_zip_state["completed_at"] = time.time()


def start_batch_zip(folder_paths: List[str]) -> Dict[str, Any]:
    global batch_zip_thread
    with batch_zip_lock:
        if batch_zip_state["status"] == "running":
            raise RuntimeError("Batch ZIP compression is already in progress.")

        batch_zip_cancel_event.clear()
        batch_zip_pause_event.set()

        batch_zip_state.update({
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
        })

        batch_zip_thread = threading.Thread(target=_run_batch_zip_worker, daemon=True)
        batch_zip_thread.start()

        return {
            "success": True,
            "message": f"Started batch ZIP compression for {len(folder_paths)} folders.",
            "total_folders": len(folder_paths),
        }


def pause_batch_zip() -> Dict[str, Any]:
    with batch_zip_lock:
        if batch_zip_state["status"] == "running":
            batch_zip_pause_event.clear()
            batch_zip_state["status"] = "paused"
            batch_zip_state["is_paused"] = True
            return {"success": True, "status": "paused", "message": "Batch compression paused."}
        elif batch_zip_state["status"] == "paused":
            batch_zip_pause_event.set()
            batch_zip_state["status"] = "running"
            batch_zip_state["is_paused"] = False
            return {"success": True, "status": "running", "message": "Batch compression resumed."}
        return {"success": False, "status": batch_zip_state["status"], "message": f"Cannot pause in status {batch_zip_state['status']}"}


def resume_batch_zip() -> Dict[str, Any]:
    with batch_zip_lock:
        if batch_zip_state["status"] == "paused":
            batch_zip_pause_event.set()
            batch_zip_state["status"] = "running"
            batch_zip_state["is_paused"] = False
            return {"success": True, "status": "running", "message": "Batch compression resumed."}
        return {"success": False, "status": batch_zip_state["status"], "message": "Batch compression is not paused."}


def cancel_batch_zip() -> Dict[str, Any]:
    with batch_zip_lock:
        if batch_zip_state["status"] in ("running", "paused"):
            batch_zip_cancel_event.set()
            batch_zip_pause_event.set()
            batch_zip_state["status"] = "canceled"
            batch_zip_state["is_paused"] = False
            return {"success": True, "status": "canceled", "message": "Batch compression canceled."}
        return {"success": False, "status": batch_zip_state["status"], "message": "No active batch compression to cancel."}


def get_batch_zip_status() -> Dict[str, Any]:
    with batch_zip_lock:
        return dict(batch_zip_state)
