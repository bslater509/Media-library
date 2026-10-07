"""Application state, formatters, and metadata builders."""

import os
import hashlib
import threading
from typing import Optional, Dict, Any, Union, Tuple
from pathlib import Path

from media_library.core.constants import NATIVE_EXTENSIONS
from media_library.core.config import (
    load_config_data,
    save_config_data,
    DEFAULT_DESTINATION_DIR,
    THUMBNAIL_CACHE_DIR,
)

saved_cfg = load_config_data()

# Shared application state
state: Dict[str, Any] = {
    "source_dir": saved_cfg.get("source_dir", ""),
    "destination_dir": DEFAULT_DESTINATION_DIR,
    "video_queue": [],  # List of dicts: { id, filename, path, size, mtime, ext, needs_transcode }
    "undo_stack": [],   # List of dicts: { type, task_id, source_path, target_path, filename, destination_folder }
    "recent_destinations": saved_cfg.get("recent_destinations", []),
}

def get_app_state() -> Dict[str, Any]:
    return state

def persist_config():
    save_config_data(state)

def format_size(size_bytes: Union[int, float]) -> str:
    try:
        size_val = float(size_bytes)
    except (TypeError, ValueError):
        return "0 B"
    is_neg = size_val < 0
    size_val = abs(size_val)
    prefix = "-" if is_neg else ""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size_val < 1024.0:
            return f"{prefix}{size_val:.1f} {unit}"
        size_val /= 1024.0
    return f"{prefix}{size_val:.1f} PB"

_file_stat_cache: Dict[str, tuple[float, int]] = {}
_file_stat_cache_lock = threading.Lock()

def update_file_stat_cache(file_path: str, mtime: float, size: int):
    """Stores known file stat in memory to avoid remote disk stats."""
    with _file_stat_cache_lock:
        if len(_file_stat_cache) > 100000:
            _file_stat_cache.clear()
        _file_stat_cache[file_path] = (float(mtime), int(size))

def get_thumbnail_cache_path(file_path: str, mtime: Optional[float] = None, size: Optional[int] = None) -> Path:
    if mtime is not None and size is not None:
        key = f"{file_path}_{mtime}_{size}"
        update_file_stat_cache(file_path, mtime, size)
    else:
        cached_stat = None
        with _file_stat_cache_lock:
            cached_stat = _file_stat_cache.get(file_path)

        if cached_stat is not None:
            key = f"{file_path}_{cached_stat[0]}_{cached_stat[1]}"
        else:
            db_mtime = None
            db_size = None
            try:
                from media_library.db.connection import get_db
                conn = get_db()
                row = conn.execute("SELECT mtime, size FROM files WHERE path = ? LIMIT 1", (file_path,)).fetchone()
                if row and row["mtime"] is not None and row["size"] is not None:
                    db_mtime = row["mtime"]
                    db_size = row["size"]
                conn.close()
            except Exception:
                pass

            if db_mtime is not None and db_size is not None:
                key = f"{file_path}_{db_mtime}_{db_size}"
                update_file_stat_cache(file_path, db_mtime, db_size)
            else:
                try:
                    st = os.stat(file_path)
                    key = f"{file_path}_{st.st_mtime}_{st.st_size}"
                    update_file_stat_cache(file_path, st.st_mtime, st.st_size)
                except OSError:
                    key = file_path

    h = hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
    return THUMBNAIL_CACHE_DIR / f"thumb_{h}.jpg"

def make_video_meta(path: str) -> Optional[Dict[str, Any]]:
    try:
        if not os.path.exists(path):
            return None
        name = os.path.basename(path)
        ext = os.path.splitext(name)[1].lower()
        stat = os.stat(path)
        return {
            "id": name,
            "filename": name,
            "path": path,
            "size": stat.st_size,
            "size_formatted": format_size(stat.st_size),
            "mtime": stat.st_mtime,
            "ext": ext,
            "needs_transcode": ext not in NATIVE_EXTENSIONS,
        }
    except Exception:
        return None
