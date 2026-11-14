"""Explicit, serial poster regeneration; old artwork survives failed extraction."""

from __future__ import annotations

import threading
from collections import deque
from typing import Any

from media_library import activity, thumbnails, writes
from media_library.db import reader
from media_library.paths import resolve_library_path

_lock = threading.Lock()
_queue: deque[str] = deque()
_seen: set[str] = set()
_thread: threading.Thread | None = None
_state: dict[str, Any] = {
    "status": "idle",
    "active": None,
    "queued": 0,
    "completed": 0,
    "failed": 0,
    "error": None,
}


def status() -> dict[str, Any]:
    with _lock:
        return dict(_state)


def enqueue(paths: list[str] | None, folder: str | None) -> dict[str, Any]:
    """Resolve scope using the index only, before starting any work."""
    global _thread
    if (paths is None) == (folder is None):
        raise writes.WriteError("Supply either paths or folder", 422)
    if paths is not None:
        selected = list(dict.fromkeys(resolve_library_path(p) for p in paths))
        with reader() as conn:
            videos = [
                p
                for p in selected
                if conn.execute(
                    "SELECT 1 FROM files WHERE path = ? AND type = 'video'", (p,)
                ).fetchone()
            ]
    else:
        folder = resolve_library_path(folder)
        with reader() as conn:
            videos = [
                r["path"]
                for r in conn.execute(
                    "SELECT path FROM files WHERE folder_path = ? AND type = 'video'",
                    (folder,),
                )
            ]
        videos = [resolve_library_path(p) for p in videos]
    with _lock:
        added = 0
        if _state["status"] == "idle":
            _state.update(completed=0, failed=0, error=None)
        for path in videos:
            if path not in _seen:
                _seen.add(path)
                _queue.append(path)
                added += 1
        _state["queued"] = len(_queue)
        if _queue and (_thread is None or not _thread.is_alive()):
            _state["status"] = "running"
            _thread = threading.Thread(
                target=_run, name="poster-regeneration", daemon=True
            )
            _thread.start()
        return {**_state, "added": added}


def _run() -> None:
    global _thread
    while True:
        with _lock:
            if not _queue:
                _state.update(status="idle", active=None)
                _thread = None
                return
            path = _queue.popleft()
            _state.update(active=path, queued=len(_queue))
        activity.wait_turn("poster-regeneration")
        try:
            _mtime, _size, duration = thumbnails._video_row(path)
            duration = duration or thumbnails._probe_duration(
                thumbnails._video_source(path)
            )
            if not duration or duration <= 0:
                raise writes.WriteError("Could not read video duration", 500)
            thumbnails.set_poster(path, duration * 0.2)
        except Exception as exc:
            with _lock:
                _state["failed"] += 1
                _state["error"] = str(exc)
        else:
            with _lock:
                _state["completed"] += 1
        finally:
            with _lock:
                _seen.discard(path)
