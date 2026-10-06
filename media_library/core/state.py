"""Application state, formatters, and metadata builders."""

import os
import hashlib
from typing import Optional, Dict, Any, Union
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

def get_thumbnail_cache_path(file_path: str, mtime: Optional[float] = None, size: Optional[int] = None) -> Path:
    if mtime is not None and size is not None:
        key = f"{file_path}_{mtime}_{size}"
    else:
        try:
            st = os.stat(file_path)
            key = f"{file_path}_{st.st_mtime}_{st.st_size}"
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
