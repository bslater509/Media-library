"""Video Sorter service for v2.

The sorter is a keyboard-first review queue for the incoming folder: scan it,
play the current clip, move it into a destination folder with one key, and undo
the last move if it was wrong.

Nothing here walks the library: destinations come from the index. Only the
incoming folder is scanned, one level deep, exactly like v1.
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from media_library_v2 import dedupe, writes
from media_library_v2.config import get_settings
from media_library_v2.db import reader
from media_library_v2.paths import (
    format_size,
    quote_path,
    resolve_library_path,
    stable_id,
)

_CACHE_TTL = 1.5
_scan_lock = threading.Lock()
_scan_cache: dict[str, Any] = {"key": None, "at": 0.0, "items": []}

_undo_lock = threading.Lock()
_undo_stack: list[dict[str, Any]] = []
_UNDO_LIMIT = 50

_state_lock = threading.Lock()


def source_dir() -> str:
    return str(get_settings().source_dir)


def _resolve_source_dir() -> str:
    path = source_dir()
    if not os.path.isdir(path):
        raise writes.WriteError("Incoming folder is not available", 404)
    return os.path.abspath(path)


def resolve_source_file(raw: str) -> str:
    root = os.path.abspath(source_dir())
    target = os.path.abspath(os.path.normpath((raw or "").strip()))
    try:
        inside = os.path.commonpath([root, target]) == root
    except ValueError:
        inside = False
    if not inside:
        raise writes.WriteError("Path is outside the incoming folder", 403)
    if not os.path.isfile(target):
        raise writes.WriteError("Incoming file not found", 404)
    if target == root:
        raise writes.WriteError("Invalid path", 400)
    return target


def check_duplicate(path: str) -> dict[str, Any]:
    """Compare an incoming clip against indexed files by quick hash."""
    target = resolve_source_file(path)
    digest = dedupe.quick_hash(target)
    try:
        size = os.path.getsize(target)
    except OSError:
        size = 0
    if not digest:
        return {"path": target, "matched": False, "duplicates": []}
    with reader() as conn:
        rows = conn.execute(
            "SELECT path, name, size_formatted FROM files "
            "WHERE size = ? AND quick_hash = ? LIMIT 20",
            (size, digest),
        ).fetchall()
    return {
        "path": target,
        "matched": bool(rows),
        "duplicates": [
            {
                "path": row["path"],
                "name": row["name"],
                "size_formatted": row["size_formatted"],
            }
            for row in rows
        ],
    }


# ---------------------------------------------------------------------------
# Queue
# ---------------------------------------------------------------------------


def _queue_item(path: str, stat: os.stat_result, ext: str) -> dict[str, Any]:
    settings = get_settings()
    playable = ext in settings.native_video_exts
    quoted = quote_path(path)
    return {
        "id": stable_id(path),
        "name": os.path.basename(path),
        "path": path,
        "size": stat.st_size,
        "size_formatted": format_size(stat.st_size),
        "mtime": stat.st_mtime,
        "ext": ext,
        "playable": playable,
        "needs_transcode": not playable,
        "stream_url": f"/api/v2/sorter/stream?path={quoted}",
        "transcode_url": (
            None if playable else f"/api/v2/sorter/transcode?path={quoted}"
        ),
    }


def scan_queue(force: bool = False) -> list[dict[str, Any]]:
    settings = get_settings()
    root = source_dir()
    now = time.monotonic()
    if (
        not force
        and _scan_cache["key"] == root
        and now - _scan_cache["at"] < _CACHE_TTL
    ):
        return list(_scan_cache["items"])

    if not os.path.isdir(root):
        return []

    items: list[dict[str, Any]] = []
    try:
        with os.scandir(root) as entries:
            for entry in entries:
                if not entry.is_file(follow_symlinks=False):
                    continue
                if entry.name.startswith("."):
                    continue
                ext = os.path.splitext(entry.name)[1].lower()
                if ext not in settings.video_exts:
                    continue
                try:
                    items.append(_queue_item(entry.path, entry.stat(), ext))
                except OSError:
                    continue
    except OSError:
        return []

    items.sort(key=lambda item: item["name"].lower())
    with _scan_lock:
        _scan_cache.update({"key": root, "at": now, "items": items})
    return list(items)


def invalidate_queue() -> None:
    with _scan_lock:
        _scan_cache["at"] = 0.0


# ---------------------------------------------------------------------------
# State (recent destinations)
# ---------------------------------------------------------------------------


def _state_file() -> Path:
    return get_settings().sorter_state_path


def _read_state() -> dict[str, Any]:
    path = _state_file()
    if not path.is_file():
        return {"recent_destinations": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"recent_destinations": []}
    recent = data.get("recent_destinations")
    if not isinstance(recent, list):
        return {"recent_destinations": []}
    return {"recent_destinations": [str(item) for item in recent if item]}


def _write_state(state: dict[str, Any]) -> None:
    path = _state_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".tmp-{uuid.uuid4().hex}-{path.name}")
    temp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    os.replace(temp, path)


def remember_destination(destination: str) -> None:
    with _state_lock:
        state = _read_state()
        recent = [item for item in state["recent_destinations"] if item != destination]
        recent.insert(0, destination)
        _write_state({"recent_destinations": recent[:6]})


def recent_destinations() -> list[dict[str, Any]]:
    with _state_lock:
        paths = _read_state()["recent_destinations"]
    result = []
    for path in paths[:6]:
        if not os.path.isdir(path):
            continue
        result.append({"path": path, "name": os.path.basename(path) or path})
    return result


# ---------------------------------------------------------------------------
# Destinations
# ---------------------------------------------------------------------------


def search_destinations(query: str, limit: int = 30) -> list[dict[str, Any]]:
    words = query.strip().lower().split()
    capped = max(1, min(limit, 50))
    root = resolve_library_path(None)
    where = "fo.is_zip = 0 AND fo.path != ?"
    params: list[Any] = [root]
    for word in words:
        where += " AND LOWER(fo.name) LIKE ?"
        params.append(f"%{word}%")
    with reader() as conn:
        rows = conn.execute(
            f"""
            SELECT fo.path, fo.name, fo.file_count, fo.subfolder_count
            FROM folders fo
            WHERE {where}
            ORDER BY LOWER(fo.name)
            LIMIT ?
            """,
            params + [capped],
        ).fetchall()
    return [
        {
            "path": row["path"],
            "name": row["name"],
            "file_count": row["file_count"] or 0,
            "subfolder_count": row["subfolder_count"] or 0,
        }
        for row in rows
    ]


def create_destination(parent: str, name: str) -> dict[str, Any]:
    created = writes.create_folder(parent, name)
    remember_destination(created["path"])
    return created


# ---------------------------------------------------------------------------
# Moves, undo, rename, delete
# ---------------------------------------------------------------------------


def _queue_payload() -> dict[str, Any]:
    return {
        "source_dir": source_dir(),
        "available": os.path.isdir(source_dir()),
        "items": scan_queue(force=True),
        "undo_count": undo_count(),
    }


def undo_count() -> int:
    with _undo_lock:
        return len(_undo_stack)


def move_to_library(path: str, destination: str) -> dict[str, Any]:
    source = resolve_source_file(path)
    destination_dir = resolve_library_path(destination)
    if not os.path.isdir(destination_dir):
        raise writes.WriteError("Destination folder not found", 404)
    result = writes.import_file(source, destination_dir, "auto_rename")
    if result.get("skipped"):
        raise writes.WriteError("Destination already has a file with that name", 409)
    target = result["target"]
    with _undo_lock:
        _undo_stack.append(
            {
                "source": source,
                "target": target,
                "destination": destination_dir,
                "at": time.time(),
            }
        )
        if len(_undo_stack) > _UNDO_LIMIT:
            _undo_stack.pop(0)
    # Already inside the library root; record it even if the DB write lags.
    remember_destination(destination_dir)
    invalidate_queue()
    payload = _queue_payload()
    payload["moved"] = {"source": source, "target": target}
    return payload


def _unique_restore_path(source: str) -> str:
    if not os.path.exists(source):
        return source
    stem, ext = os.path.splitext(source)
    counter = 1
    while True:
        candidate = f"{stem} (restored {counter}){ext}"
        if not os.path.exists(candidate):
            return candidate
        counter += 1


def undo_last() -> dict[str, Any]:
    with _undo_lock:
        if not _undo_stack:
            raise writes.WriteError("Nothing to undo", 409)
        entry = _undo_stack[-1]
    target = entry["target"]
    if not os.path.isfile(target):
        # Do not consume the entry: the file may be temporarily unreachable,
        # and the user must still be able to retry or undo an older move.
        raise writes.WriteError("The moved file is no longer there", 409)
    restore = _unique_restore_path(entry["source"])
    try:
        shutil.move(target, restore)
    except OSError as exc:
        raise writes.WriteError(f"Could not move the file back: {exc}", 500) from exc
    with _undo_lock:
        if _undo_stack and _undo_stack[-1] is entry:
            _undo_stack.pop()
        else:  # pragma: no cover - another undo changed the stack
            _undo_stack.remove(entry)
    writes.remove_index_entry(target)
    invalidate_queue()
    payload = _queue_payload()
    payload["restored"] = restore
    return payload


def rename_incoming(path: str, name: str) -> dict[str, Any]:
    source = resolve_source_file(path)
    clean = writes.validate_name(name)
    target = os.path.join(os.path.dirname(source), clean)
    if target == source:
        return {"path": source}
    if os.path.exists(target):
        raise writes.WriteError("A file with that name already exists", 409)
    os.rename(source, target)
    invalidate_queue()
    payload = _queue_payload()
    payload["path"] = target
    return payload


def delete_incoming(path: str) -> dict[str, Any]:
    source = resolve_source_file(path)
    try:
        os.remove(source)
    except OSError as exc:
        raise writes.WriteError(f"Could not delete the file: {exc}", 500) from exc
    invalidate_queue()
    return _queue_payload()


def sorter_config() -> dict[str, Any]:
    settings = get_settings()
    return {
        "source_dir": source_dir(),
        "source_available": os.path.isdir(settings.source_dir),
        "library_root": str(settings.library_dir),
        "recent_destinations": recent_destinations(),
        "queue_count": len(scan_queue()),
        "undo_count": undo_count(),
    }
