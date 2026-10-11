import hashlib
import logging
import os
import shutil
import threading
import time
import zipfile
from pathlib import Path
from typing import Any

from media_library.core.config import (
    THUMBNAIL_CACHE_DIR,
    ZIP_CACHE_DIR,
    ZIP_CACHE_MAX_SIZE_GB,
    ZIP_STAGING_DIR,
)
from media_library.core.constants import IMAGE_EXTENSIONS, VIDEO_EXTENSIONS
from media_library.core.state import format_size
from media_library.db.connection import db_conn

logger = logging.getLogger(__name__)

# In-memory tracking of ongoing zip operations
zip_tasks_state: dict[str, Any] = {
    # path -> {"type": "compress"|"extract"|"sync"|"unpack", "status": "running"|"completed"|"failed", "percent": 0.0, "message": "...", "started_at": ts}
    "active_tasks": {},
    # path -> threading.Timer
    "sync_timers": {},
}
zip_lock = threading.Lock()
# Per-archive operation locks. Serialize every operation that reads, rewrites,
# deletes or unpacks a given archive (extract / unpack / sync-back / WebP rewrite)
# so two of them can never interleave on the same remote file.
_archive_op_locks: dict[str, threading.Lock] = {}
_archive_op_locks_guard = threading.Lock()


def _get_archive_lock(zip_path: str) -> threading.Lock:
    """Returns a per-archive lock serializing concurrent archive operations."""
    norm = os.path.abspath(zip_path)
    with _archive_op_locks_guard:
        if norm not in _archive_op_locks:
            _archive_op_locks[norm] = threading.Lock()
        return _archive_op_locks[norm]


def _get_common_root_prefix(members_list: list[zipfile.ZipInfo]) -> str:
    """If all non-system, non-dir members share a common top-level directory, return it with trailing slash."""
    non_sys = [
        m.filename
        for m in members_list
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
        return next(iter(first_parts)) + "/"
    return ""


def split_zip_path(path_str: str) -> tuple[str | None, str]:
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


def resolve_zip_file_path(path_str: str) -> str | None:
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

    resolved_path: Path | None = None

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


def get_zip_metadata(zip_path: str) -> dict[str, Any]:
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
            files.append(
                {
                    "name": base_name,
                    "inner_path": name,
                    "size": info.file_size,
                    "size_formatted": format_size(info.file_size),
                    "is_image": is_img,
                    "is_video": is_vid,
                    "ext": ext,
                }
            )

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


def index_zip_archive(zip_path: str, parent_path: str | None = None):
    """Indexes a ZIP file as a virtual folder and its files into SQLite."""
    if not os.path.exists(zip_path):
        return

    meta = get_zip_metadata(zip_path)
    clean_name = meta["clean_name"]
    parent = parent_path or os.path.dirname(zip_path)

    with db_conn() as conn, conn:
        zip_total_size = sum(f["size"] for f in meta["files"])
        try:
            zip_mtime = os.stat(zip_path).st_mtime
        except OSError:
            zip_mtime = None
        conn.execute(
            """
        INSERT INTO folders (path, name, parent_path, file_count, subfolder_count, last_scanned, is_zip, total_size, dir_mtime)
        VALUES (?, ?, ?, ?, 0, ?, 1, ?, ?)
        ON CONFLICT(path) DO UPDATE SET
            name=excluded.name,
            parent_path=excluded.parent_path,
            file_count=excluded.file_count,
            subfolder_count=0,
            last_scanned=excluded.last_scanned,
            is_zip=1,
            total_size=excluded.total_size,
            dir_mtime=excluded.dir_mtime
        """,
            (
                zip_path,
                clean_name,
                parent,
                meta["total_files"],
                time.time(),
                zip_total_size,
                zip_mtime,
            ),
        )

        conn.execute("DELETE FROM files WHERE folder_path = ?", (zip_path,))

        file_rows = []
        for f in meta["files"]:
            full_vpath = os.path.join(zip_path, f["inner_path"])
            ftype = (
                "image" if f["is_image"] else ("video" if f["is_video"] else "other")
            )
            # Thumbnail hash check if thumb exists
            key = f"{full_vpath}_{f['size']}"
            h = hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
            has_thumb = 1 if (THUMBNAIL_CACHE_DIR / f"thumb_{h}.jpg").exists() else 0
            webp_opt = 1 if f["ext"] == ".webp" else 0

            file_rows.append(
                (
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
                )
            )

        if file_rows:
            conn.executemany(
                """
            INSERT INTO files (path, name, folder_path, type, ext, size, size_formatted, mtime, needs_transcode, has_thumb, webp_optimized)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                file_rows,
            )


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
        active_paths = {
            p
            for p, t in zip_tasks_state["active_tasks"].items()
            if t.get("status") in ("running", "queued")
        }
        # Prune old completed/failed tasks from active_tasks to prevent unbounded memory/dict growth
        if len(zip_tasks_state["active_tasks"]) > 100:
            finished_keys = [
                p
                for p, t in zip_tasks_state["active_tasks"].items()
                if t.get("status") not in ("running", "queued")
            ]
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
            logger.error(f"Error evicting cache dir {dir_path}: {e}")
