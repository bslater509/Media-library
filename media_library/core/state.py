"""Application state, formatters, and metadata builders."""

import hashlib
import logging
import os
import threading
from pathlib import Path
from typing import Any

from media_library.core.config import (
    DEFAULT_DESTINATION_DIR,
    THUMBNAIL_CACHE_DIR,
    load_config_data,
    save_config_data,
)
from media_library.core.constants import NATIVE_EXTENSIONS

logger = logging.getLogger(__name__)

saved_cfg = load_config_data()


class AppState:
    """Thread-safe container for the process-global sorter/library state.

    All read-modify-write sequences on the shared lists (video queue, undo
    stack, recent destinations) go through the intent-revealing locked methods
    below; plain reads keep working via ``state["key"]`` / ``state.get(...)``.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._data: dict[str, Any] = {
            "source_dir": saved_cfg.get("source_dir", ""),
            "destination_dir": DEFAULT_DESTINATION_DIR,
            "video_queue": [],  # List of dicts: { id, filename, path, size, mtime, ext, needs_transcode }
            "undo_stack": [],  # List of dicts: { type, task_id, source_path, target_path, filename, destination_folder }
            "recent_destinations": saved_cfg.get("recent_destinations", []),
        }

    # -- generic dict-compatible accessors (reads only) --------------------
    def __getitem__(self, key: str) -> Any:
        with self._lock:
            return self._data[key]

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._data.get(key, default)

    def __setitem__(self, key: str, value: Any) -> None:
        with self._lock:
            self._data[key] = value

    def snapshot(self) -> dict[str, Any]:
        """Return a shallow copy of the state for config persistence/reads."""
        with self._lock:
            return {
                k: list(v) if isinstance(v, list) else v for k, v in self._data.items()
            }

    # -- source / destination ----------------------------------------------
    def set_source_dir(self, path: str) -> None:
        with self._lock:
            self._data["source_dir"] = path

    def set_destination_dir(self, path: str) -> None:
        with self._lock:
            self._data["destination_dir"] = path

    # -- video queue -------------------------------------------------------
    def set_queue(self, items: list[Any]) -> None:
        with self._lock:
            self._data["video_queue"] = list(items)

    def update_queue(self, updater) -> None:
        """Apply ``updater(list) -> list`` to the video queue under the lock."""
        with self._lock:
            self._data["video_queue"] = updater(self._data["video_queue"])

    def push_queue_head(self, item: Any) -> None:
        with self._lock:
            self._data["video_queue"].insert(0, item)

    # -- undo stack --------------------------------------------------------
    def push_undo(self, item: dict[str, Any]) -> None:
        with self._lock:
            self._data["undo_stack"].append(item)

    def pop_undo(self) -> dict[str, Any] | None:
        with self._lock:
            if not self._data["undo_stack"]:
                return None
            return self._data["undo_stack"].pop()

    # -- recent destinations ------------------------------------------------
    def push_recent(self, name: str) -> None:
        """Move ``name`` to the front of the recent-destinations list."""
        with self._lock:
            recent = self._data["recent_destinations"]
            if name in recent:
                recent.remove(name)
            recent.insert(0, name)

    def set_recent(self, items: list[str]) -> None:
        with self._lock:
            self._data["recent_destinations"] = list(items)

    def reset_runtime(self) -> None:
        """Clear runtime-only mutable lists (queue, undo, recents) for tests."""
        with self._lock:
            self._data["video_queue"] = []
            self._data["undo_stack"] = []
            self._data["recent_destinations"] = []


# Shared application state
state = AppState()


def get_app_state() -> AppState:
    return state


def persist_config():
    save_config_data(state.snapshot())


def format_size(size_bytes: float) -> str:
    try:
        size_val = float(size_bytes)
    except (TypeError, ValueError):
        return "0 B"
    is_neg = size_val < 0
    size_val = abs(size_val)
    prefix = "-" if is_neg else ""
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size_val < 1024.0:
            return f"{prefix}{size_val:.1f} {unit}"
        size_val /= 1024.0
    return f"{prefix}{size_val:.1f} PB"


_file_stat_cache: dict[str, tuple[float, int]] = {}
_file_stat_cache_lock = threading.Lock()


def update_file_stat_cache(file_path: str, mtime: float, size: int):
    """Stores known file stat in memory to avoid remote disk stats."""
    with _file_stat_cache_lock:
        if len(_file_stat_cache) > 100000:
            _file_stat_cache.clear()
        _file_stat_cache[file_path] = (float(mtime), int(size))


def get_thumbnail_cache_path(
    file_path: str, mtime: float | None = None, size: int | None = None
) -> Path:
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
                from media_library.db.connection import db_conn

                with db_conn() as conn:
                    row = conn.execute(
                        "SELECT mtime, size FROM files WHERE path = ? LIMIT 1",
                        (file_path,),
                    ).fetchone()
                    if row and row["mtime"] is not None and row["size"] is not None:
                        db_mtime = row["mtime"]
                        db_size = row["size"]
            except Exception:
                # Best-effort: DB may not exist yet (fresh install); the caller
                # falls back to os.stat below.
                logger.debug(
                    "Could not read thumbnail stat from DB for %s",
                    file_path,
                    exc_info=True,
                )

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


def make_video_meta(path: str) -> dict[str, Any] | None:
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
    except OSError:
        # Path is unreachable right now (e.g. FUSE mount hiccup); the caller
        # treats the file as absent rather than crashing the request.
        return None
