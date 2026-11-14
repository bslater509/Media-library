"""Animated WebP video previews.

One generation runs at a time on a daemon thread. ffmpeg samples about 24 frames
across the clip (never more than 2 fps) and writes a looping WebP next to the
posters, so the existing cache key/move/delete logic treats it like any other
artwork. A small queue feeds it from tile hovers, a folder batch or the whole
library; failures are marked ``has_animated = -1`` so a broken file is not
retried forever.
"""

from __future__ import annotations

import os
import subprocess
import threading
import uuid
from collections import deque
from pathlib import Path
from typing import Any

from media_library import writes
from media_library.cache import animated_path
from media_library.db import reader, writer
from media_library.paths import is_zip_member


def _duration(path: str) -> float | None:
    with reader() as conn:
        row = conn.execute(
            "SELECT duration FROM files WHERE path = ?", (path,)
        ).fetchone()
    if row is not None and row["duration"]:
        return float(row["duration"])
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=nw=1:nk=1",
                path,
            ],
            capture_output=True,
            timeout=20,
            check=False,
        )
        return float(result.stdout.decode().strip())
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def generate(path: str) -> Path:
    """Build (or reuse) one animated preview and mark the row."""
    with reader() as conn:
        row = conn.execute(
            "SELECT mtime, size, type FROM files WHERE path = ?", (path,)
        ).fetchone()
    if row is None:
        raise writes.WriteError("Not indexed", 404)
    if row["type"] != "video":
        raise writes.WriteError("Animated previews are for videos", 400)
    mtime = float(row["mtime"] or 0)
    size = int(row["size"] or 0)
    target = animated_path(path, mtime, size)
    if target.is_file():
        with writer() as conn, conn:
            conn.execute("UPDATE files SET has_animated = 1 WHERE path = ?", (path,))
        return target

    source = path
    if is_zip_member(path):
        raise writes.WriteError("ZIP members have no animated preview", 400)
    if not os.path.isfile(source):
        raise writes.WriteError("File not found", 404)

    duration = _duration(path)
    if not duration or duration <= 0.05:
        raise writes.WriteError("Could not read the video duration", 500)

    rate = min(2.0, 24.0 / duration)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f"anim_{uuid.uuid4().hex}.tmp.webp")
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        source,
        "-vf",
        f"fps={rate:.6f},scale=480:-2:flags=fast_bilinear",
        "-loop",
        "0",
        "-r",
        "2",
        "-c:v",
        "libwebp",
        "-quality",
        "70",
        "-preset",
        "picture",
        "-an",
        "-y",
        str(temp),
    ]
    try:
        result = subprocess.run(command, capture_output=True, timeout=180, check=False)
        if result.returncode != 0 or not temp.is_file() or temp.stat().st_size == 0:
            message = result.stderr.decode("utf-8", "replace")[-300:]
            raise writes.WriteError(
                f"Could not build an animated preview: {message.strip()}", 500
            )
        os.replace(temp, target)
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass

    with writer() as conn, conn:
        conn.execute("UPDATE files SET has_animated = 1 WHERE path = ?", (path,))
    return target


# --- queue -----------------------------------------------------------------

_state: dict[str, Any] = {
    "status": "idle",
    "active": None,
    "queued": 0,
    "completed": 0,
    "failed": 0,
}
_queue: deque[str] = deque()
_seen: set[str] = set()
_lock = threading.Lock()
_thread: threading.Thread | None = None


def _update(**changes: Any) -> None:
    with _lock:
        _state.update(changes)


def activity_status() -> dict[str, Any]:
    """Memory-only queue telemetry, without the library-wide SQL count."""
    with _lock:
        return dict(_state)


def status() -> dict[str, Any]:
    with reader() as conn:
        counts = conn.execute(
            "SELECT "
            "COALESCE(SUM(type = 'video'), 0) AS total, "
            "COALESCE(SUM(type = 'video' AND COALESCE(has_animated, 0) = 0), 0) AS pending, "
            "COALESCE(SUM(type = 'video' AND has_animated = 1), 0) AS ready, "
            "COALESCE(SUM(type = 'video' AND has_animated = -1), 0) AS failed "
            "FROM files"
        ).fetchone()
    with _lock:
        state = dict(_state)
    return {
        **state,
        "total": int(counts["total"] or 0),
        "pending": int(counts["pending"] or 0),
        "ready": int(counts["ready"] or 0),
        "failed_total": int(counts["failed"] or 0),
    }


def enqueue(paths: list[str]) -> int:
    """Queue videos that do not have a preview yet; returns the new count."""
    added = 0
    queued_len = 0
    with _lock:
        for path in paths:
            if path in _seen:
                continue
            _seen.add(path)
            _queue.append(path)
            added += 1
        queued_len = len(_queue)
        state = dict(_state)
    if added:
        _update(status="working" if state["active"] else "queued", queued=queued_len)
        _ensure_thread()
    return added


def enqueue_batch(
    folder: str | None = None, all_videos: bool = False, limit: int = 500
) -> int:
    sql = (
        "SELECT path FROM files WHERE type = 'video' AND COALESCE(has_animated, 0) = 0"
    )
    params: list[Any] = []
    if folder:
        sql += " AND folder_path = ?"
        params.append(folder)
    elif not all_videos:
        raise writes.WriteError("Choose a folder or all videos", 400)
    sql += " ORDER BY size DESC LIMIT ?"
    params.append(max(1, min(5000, limit)))
    with reader() as conn:
        rows = conn.execute(sql, params).fetchall()
    return enqueue([row["path"] for row in rows])


def cancel() -> int:
    with _lock:
        dropped = len(_queue)
        _queue.clear()
        _seen.clear()
        _state.update(queued=0, status="working" if _state["active"] else "idle")
    return dropped


def _ensure_thread() -> None:
    global _thread
    with _lock:
        if _thread and _thread.is_alive():
            return
        _thread = threading.Thread(target=_loop, name="anim-previews", daemon=True)
        _thread.start()


def _loop() -> None:
    from media_library import activity

    global _thread
    while True:
        activity.wait_turn("animated", lambda: not _queue)
        with _lock:
            if not _queue:
                _state.update(status="idle", active=None, queued=0)
                _seen.clear()
                # Mark the worker as gone *before* releasing the lock: a caller
                # that appends right after this block must start a new thread
                # rather than observe the dying one as still alive and return.
                _thread = None
                return
            path = _queue.popleft()
            _state.update(status="working", active=path, queued=len(_queue))
        try:
            generate(path)
            with _lock:
                _state["completed"] += 1
        except Exception:
            with _lock:
                _state["failed"] += 1
            try:
                with writer() as conn, conn:
                    conn.execute(
                        "UPDATE files SET has_animated = -1 WHERE path = ?", (path,)
                    )
            except Exception:
                pass
        finally:
            with _lock:
                _seen.discard(path)


def start() -> None:
    """Compatibility no-op: the thread starts with the first queued job."""


def stop() -> None:
    cancel()
