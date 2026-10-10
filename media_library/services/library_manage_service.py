"""Filesystem + index operations backing the Library page's file management.

These helpers are deliberately separated from the HTTP router so the heavy
filesystem work can be executed on a background worker thread (FUSE-safe, never
in a request handler) and unit-tested directly.

All functions follow the repo's FUSE rules:
- writes are atomic (``.tmp_*`` -> ``os.replace``) where a copy is required,
- same-filesystem renames use ``os.rename`` (a single metadata syscall),
- index updates happen via the dedicated helpers in ``db.indexer``.
"""

import os
import shutil
import threading
import time
from typing import Any

from media_library.core.state import get_thumbnail_cache_path
from media_library.db.connection import db_conn
from media_library.db.indexer import delete_path_from_index, move_path_in_index
from media_library.services.media_service import validate_backup_path
from media_library.services.zip_service import get_zip_cache_dir, is_zip_file

# ---------------------------------------------------------------------------
# Path classification & conflict detection (no heavy I/O)
# ---------------------------------------------------------------------------


def classify_library_path(path: str) -> str:
    """Returns 'file', 'folder', or 'zip' for a validated library path."""
    if is_zip_file(path):
        return "zip"
    if os.path.isdir(path):
        return "folder"
    return "file"


def compute_move_targets(
    paths: list[str],
    destination_folder: str,
    conflict_policy: str = "auto_rename",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Resolves final targets for a set of move operations.

    Returns ``(plans, collisions)`` where ``plans`` is a list of
    ``{source_path, target_path, kind, conflict}`` dicts and ``collisions`` the
    subset of plans whose target already exists on disk. With
    ``conflict_policy="skip"`` colliding items are dropped from ``plans``; with
    ``"auto_rename"`` they receive a numeric ``(1)`` suffix instead.
    """
    destination_folder = os.path.normpath(destination_folder)
    plans: list[dict[str, Any]] = []
    collisions: list[dict[str, Any]] = []

    # Track names this batch will already occupy at the destination.
    occupied: dict[str, str] = {}
    for p in paths:
        source = validate_backup_path(p)
        kind = classify_library_path(source)
        base_name = os.path.basename(source.rstrip(os.sep))

        candidate = os.path.join(destination_folder, base_name)
        final = candidate
        conflict = False

        if os.path.exists(candidate) or candidate in occupied.values():
            conflict = True

        if conflict and conflict_policy == "skip":
            collisions.append(
                {"source_path": source, "target_path": candidate, "kind": kind}
            )
            continue

        if conflict and conflict_policy == "auto_rename":
            stem, ext = os.path.splitext(base_name)
            counter = 1
            while True:
                final = os.path.join(destination_folder, f"{stem} ({counter}){ext}")
                if not os.path.exists(final) and final not in occupied.values():
                    break
                counter += 1

        occupied[source] = final
        plan = {
            "source_path": source,
            "target_path": final,
            "kind": kind,
            "conflict": conflict,
        }
        plans.append(plan)
        if conflict:
            collisions.append(plan)

    return plans, collisions


# ---------------------------------------------------------------------------
# Index-preserving filesystem mutations
# ---------------------------------------------------------------------------


def _clear_thumb_for(path: str):
    """Removes the cached poster for a single file path (best effort)."""
    try:
        thumb = get_thumbnail_cache_path(path)
        if thumb.exists():
            thumb.unlink(missing_ok=True)
    except OSError:
        pass


def move_library_path(
    source_path: str,
    target_path: str,
    kind: str,
    abort_event: threading.Event | None = None,
):
    """Moves a file, folder, or ZIP archive within the library and updates the index.

    Raises ``OSError`` on failure so the worker can mark the task failed.
    """
    if abort_event is not None and abort_event.is_set():
        raise InterruptedError("Move cancelled by user")

    if not os.path.exists(source_path):
        raise FileNotFoundError(f"Source not found: {source_path}")

    target_dir = os.path.dirname(target_path)
    # Only issue a mkdir when the parent is actually missing — on remote FUSE
    # mounts a fresh `mkdir` is a slow API round trip, and bulk moves already
    # ran N copies of this. An `isdir` stat is cached and effectively free.
    if not os.path.isdir(target_dir):
        os.makedirs(target_dir, exist_ok=True)

    # shutil.move already handles cross-device copy + delete within the worker;
    # any failure propagates so the caller can mark the task failed.
    shutil.move(source_path, target_path)

    # Purge the extraction cache for a moved ZIP archive (its cache key embeds
    # the old path), so the next browse re-extracts at the new location.
    if kind == "zip":
        try:
            old_cache = get_zip_cache_dir(source_path)
            if old_cache.exists():
                shutil.rmtree(old_cache, ignore_errors=True)
        except OSError:
            pass

    move_path_in_index(source_path, target_path, kind)

    if kind == "file":
        _clear_thumb_for(source_path)

    return {"source_path": source_path, "target_path": target_path, "kind": kind}


def delete_library_path(
    path: str, kind: str, abort_event: threading.Event | None = None
):
    """Deletes a file, folder (recursive), or ZIP archive and updates the index."""
    if abort_event is not None and abort_event.is_set():
        raise InterruptedError("Delete cancelled by user")

    if kind == "zip":
        try:
            cache_dir = get_zip_cache_dir(path)
            if cache_dir.exists():
                shutil.rmtree(cache_dir, ignore_errors=True)
        except OSError:
            pass
        if os.path.exists(path):
            os.remove(path)
        delete_path_from_index(path, kind)
        return {"deleted_path": path, "kind": kind}

    if kind == "folder":
        if os.path.isdir(path):
            shutil.rmtree(path)
        delete_path_from_index(path, kind)
        return {"deleted_path": path, "kind": kind}

    # Single file
    _clear_thumb_for(path)
    if os.path.exists(path):
        os.remove(path)
    delete_path_from_index(path, kind)
    return {"deleted_path": path, "kind": kind}


def rename_library_path(path: str, new_name: str) -> dict[str, Any]:
    """Renames a file, folder, or ZIP archive in place (same parent) and updates the index."""
    real_path = validate_backup_path(path)
    new_name = new_name.strip()
    if not new_name or "/" in new_name or "\\" in new_name or new_name.startswith("."):
        raise ValueError("Invalid filename")

    kind = classify_library_path(real_path)
    parent = os.path.dirname(real_path)
    target = os.path.join(parent, new_name)

    if target == real_path:
        return {
            "success": True,
            "old_path": real_path,
            "new_path": target,
            "kind": kind,
        }

    if os.path.exists(target):
        raise FileExistsError("A file or folder with this name already exists")

    os.rename(real_path, target)

    if kind == "zip":
        try:
            old_cache = get_zip_cache_dir(real_path)
            if old_cache.exists():
                shutil.rmtree(old_cache, ignore_errors=True)
        except OSError:
            pass

    move_path_in_index(real_path, target, kind)

    if kind == "file":
        _clear_thumb_for(real_path)

    return {"success": True, "old_path": real_path, "new_path": target, "kind": kind}


# ---------------------------------------------------------------------------
# Bulk rename
# ---------------------------------------------------------------------------


def _apply_rename_rule(
    base: str, index: int, total: int, mode: str, params: dict[str, Any]
) -> str:
    """Applies a single bulk-rename rule to a base filename (extension excluded)."""
    if mode == "prefix":
        return f"{params.get('prefix', '')}{base}{params.get('suffix', '')}"
    if mode == "case":
        case = params.get("case", "lower")
        if case == "upper":
            return base.upper()
        if case == "title":
            return base.title()
        return base.lower()
    if mode == "find_replace":
        find = params.get("find", "")
        if not find:
            raise ValueError("Find text cannot be empty")
        return base.replace(find, params.get("replace", ""))
    if mode == "numbering":
        start = int(params.get("start", 1))
        step = int(params.get("step", 1))
        width = int(params.get("padding", 0))
        num = start + index * step
        if width > 0:
            num_str = str(num).zfill(width)
        else:
            num_str = str(num)
        if params.get("position") == "prefix":
            return f"{num_str}{base}"
        return f"{base}{num_str}"
    if mode == "base_counter":
        base_name = params.get("base", "").strip()
        if not base_name:
            raise ValueError("Base name cannot be empty")
        start = int(params.get("start", 1))
        width = int(params.get("padding", 0)) or len(str(total))
        num = start + index
        return f"{base_name}{str(num).zfill(width)}"
    raise ValueError(f"Unknown rename mode: {mode}")


def compute_bulk_rename(
    paths: list[str], mode: str, params: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """Computes target names for a bulk rename without touching disk.

    Returns a list of ``{path, name, new_name, kind, conflict}`` dicts. The
    extension is always preserved (rules operate on the base name only).
    """
    params = params or {}
    items: list[dict[str, Any]] = []
    for i, p in enumerate(paths):
        real = validate_backup_path(p)
        kind = classify_library_path(real)
        name = os.path.basename(real.rstrip(os.sep))
        if kind == "file":
            base, ext = os.path.splitext(name)
        else:
            base, ext = name, ""
        new_base = _apply_rename_rule(base, i, len(paths), mode, params)
        new_name = new_base + ext
        items.append(
            {
                "path": real,
                "name": name,
                "new_name": new_name,
                "kind": kind,
                "conflict": False,
            }
        )

    # Flag collisions within the batch (same parent + same new name).
    seen: dict[str, str] = {}
    for item in items:
        key = os.path.join(os.path.dirname(item["path"]), item["new_name"]).lower()
        if key in seen:
            item["conflict"] = True
            seen[key]["conflict"] = True
        else:
            seen[key] = item
    return items


def create_library_folder(parent_path: str, name: str) -> dict[str, Any]:
    """Creates a single real folder inside ``parent_path`` and inserts it into the index."""
    parent = validate_backup_path(parent_path)
    name = name.strip()
    if not name or "/" in name or "\\" in name or name.startswith("."):
        raise ValueError("Invalid folder name")

    target = os.path.join(parent, name)
    if os.path.exists(target):
        raise FileExistsError("A folder with this name already exists")

    os.makedirs(target, exist_ok=True)

    with db_conn() as conn, conn:
        conn.execute(
            """
                INSERT INTO folders (path, name, parent_path, file_count, subfolder_count, last_scanned, is_zip)
                VALUES (?, ?, ?, 0, 0, ?, 0)
                ON CONFLICT(path) DO UPDATE SET last_scanned = excluded.last_scanned
                """,
            (target, name, parent, time.time()),
        )

    return {"success": True, "path": target, "name": name}
