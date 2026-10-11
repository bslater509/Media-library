"""Filesystem + index operations backing the Library page's file management.

These helpers are deliberately separated from the HTTP router so the heavy
filesystem work can be executed on a background worker thread (FUSE-safe, never
in a request handler) and unit-tested directly.

All functions follow the repo's FUSE rules:
- writes are atomic (``.tmp_*`` -> ``os.replace``) where a copy is required,
- same-filesystem renames use ``os.rename`` (a single metadata syscall),
- index updates happen via the dedicated helpers in ``db.indexer``.
"""

import logging
import os
import shutil
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from media_library.core.config import DEFAULT_DESTINATION_DIR
from media_library.core.state import get_thumbnail_cache_path, state
from media_library.db.connection import db_conn
from media_library.db.indexer import delete_path_from_index, move_path_in_index
from media_library.services.media_service import validate_backup_path
from media_library.services.zip import get_zip_cache_dir, is_zip_file

logger = logging.getLogger(__name__)

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


def find_empty_folders(base_path: str | None = None) -> dict[str, Any]:
    """Finds all empty folders across the library that have zero files and zero non-empty subfolders.

    Returns candidate list and aggregate statistics.
    Candidates are sorted bottom-up (deepest paths first).
    """
    base_dir = os.path.realpath(
        base_path or state.get("destination_dir") or DEFAULT_DESTINATION_DIR
    )

    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT path, name, parent_path, file_count, subfolder_count, is_zip
            FROM folders
            WHERE (is_zip = 0 OR is_zip IS NULL)
            """
        )
        rows = cur.fetchall()

        cur.execute("SELECT DISTINCT folder_path FROM files")
        folders_with_files = {r[0] for r in cur.fetchall() if r[0]}

        cur.execute("SELECT DISTINCT parent_path FROM folders WHERE is_zip = 1")
        folders_with_zips = {r[0] for r in cur.fetchall() if r[0]}

    all_folders: dict[str, dict[str, Any]] = {}
    children_map: dict[str, list[str]] = {}

    for r in rows:
        path = r[0] if isinstance(r, (list, tuple)) else r["path"]
        name = r[1] if isinstance(r, (list, tuple)) else r["name"]
        parent_path = r[2] if isinstance(r, (list, tuple)) else r["parent_path"]
        file_count = r[3] if isinstance(r, (list, tuple)) else r["file_count"]
        subfolder_count = r[4] if isinstance(r, (list, tuple)) else r["subfolder_count"]

        # Normalize and filter
        norm_path = os.path.realpath(path)
        if norm_path == base_dir:
            continue
        try:
            if os.path.commonpath([base_dir, norm_path]) != base_dir:
                continue
        except ValueError:
            continue
        base_name = name or os.path.basename(norm_path)
        if base_name.startswith("."):
            continue
        if ".media_library_backup" in norm_path:
            continue

        p_parent = (
            os.path.realpath(parent_path)
            if parent_path
            else os.path.dirname(norm_path)
        )
        all_folders[norm_path] = {
            "path": norm_path,
            "name": base_name,
            "parent_path": p_parent,
            "file_count": file_count or 0,
            "subfolder_count": subfolder_count or 0,
        }
        children_map.setdefault(p_parent, []).append(norm_path)

    # Determine which folders in the DB have content (files or zip archives)
    has_content_map: dict[str, bool] = {}

    def has_direct_content(p: str) -> bool:
        if p in folders_with_files:
            return True
        info = all_folders.get(p)
        if info and (info["file_count"] > 0):
            return True
        return p in folders_with_zips

    def folder_has_content(p: str) -> bool:
        if p in has_content_map:
            return has_content_map[p]
        if has_direct_content(p):
            has_content_map[p] = True
            return True
        for child in children_map.get(p, []):
            if folder_has_content(child):
                has_content_map[p] = True
                return True
        has_content_map[p] = False
        return False

    empty_from_db = [p for p in all_folders if not folder_has_content(p)]

    # Sort candidate paths bottom-up (deepest first)
    empty_from_db.sort(
        key=lambda p: (p.count(os.sep), len(p)),
        reverse=True,
    )

    if not empty_from_db:
        return {"total_candidates": 0, "candidates": []}

    # Verify candidate folders on disk (excluding benign OS files):
    # Candidate must not contain any real non-media files, and any subdirectories
    # must themselves be confirmed empty candidates.
    def _inspect_folder(p: str) -> tuple[str, bool, list[str]]:
        has_file = False
        subdirs: list[str] = []
        try:
            if os.path.isdir(p):
                with os.scandir(p) as it:
                    for entry in it:
                        if (
                            entry.name in (".DS_Store", "Thumbs.db", "desktop.ini")
                            or entry.name.startswith(".tmp_")
                        ):
                            continue
                        if entry.is_file():
                            has_file = True
                            break
                        elif entry.is_dir():
                            subdirs.append(os.path.realpath(entry.path))
        except Exception:
            has_file = True
        return p, has_file, subdirs

    max_threads = min(16, max(1, len(empty_from_db)))
    with ThreadPoolExecutor(max_workers=max_threads) as ex:
        scan_results = list(ex.map(_inspect_folder, empty_from_db))

    valid_candidates_set = {
        p for p, has_file, _ in scan_results if not has_file
    }
    subdirs_by_folder = {p: subdirs for p, _, subdirs in scan_results}

    # Bottom-up hierarchical confirmation (deepest first):
    confirmed_empty: list[str] = []
    for p in empty_from_db:
        if p not in valid_candidates_set:
            continue
        # Subdirectories on disk must all be in valid_candidates_set
        if all(sub in valid_candidates_set for sub in subdirs_by_folder.get(p, [])):
            confirmed_empty.append(p)
        else:
            valid_candidates_set.discard(p)

    final_candidates: list[dict[str, Any]] = []
    for p in confirmed_empty:
        p_info = all_folders.get(p)
        name = p_info["name"] if p_info else os.path.basename(p)
        parent_path = p_info["parent_path"] if p_info else os.path.dirname(p)
        rel_path = os.path.relpath(p, base_dir)
        parent_name = (
            os.path.basename(parent_path) if parent_path else ""
        )
        is_leaf = len(children_map.get(p, [])) == 0

        final_candidates.append(
            {
                "path": p,
                "name": name,
                "parent_path": parent_path,
                "parent_name": parent_name,
                "relative_path": rel_path,
                "is_leaf": is_leaf,
            }
        )

    return {
        "total_candidates": len(final_candidates),
        "candidates": final_candidates,
    }


def delete_empty_folder(
    path: str,
    abort_event: threading.Event | None = None,
    base_path: str | None = None,
) -> dict[str, Any]:
    """Safely deletes an empty folder on disk and removes it from the index.

    If the folder contains files, it will NOT be deleted.
    Benign OS metadata (.DS_Store, Thumbs.db, desktop.ini) is removed prior to rmdir.
    """
    if abort_event is not None and abort_event.is_set():
        raise InterruptedError("Delete cancelled by user")

    base_dir = os.path.realpath(
        base_path or state.get("destination_dir") or DEFAULT_DESTINATION_DIR
    )
    real_path = validate_backup_path(path, base_dir)
    if os.path.realpath(real_path) == base_dir:
        raise ValueError("Cannot delete the root library directory")

    if os.path.isdir(real_path):
        # 1. Clean benign OS junk (.DS_Store, Thumbs.db, desktop.ini, .tmp_*)
        for entry in os.scandir(real_path):
            if (
                entry.name in (".DS_Store", "Thumbs.db", "desktop.ini")
                or entry.name.startswith(".tmp_")
            ):
                try:
                    os.remove(entry.path)
                except OSError:
                    pass
            elif entry.is_file():
                raise OSError(f"Folder is not empty: contains file '{entry.name}'")
            elif entry.is_dir():
                raise OSError(f"Folder is not empty: contains subfolder '{entry.name}'")

        # 2. Remove directory
        os.rmdir(real_path)

    # 3. Clean database index
    delete_path_from_index(real_path, "folder")

    # 4. Update parent's subfolder_count in folders table
    parent_path = os.path.dirname(real_path)
    if parent_path:
        try:
            with db_conn() as conn, conn:
                conn.execute(
                    """
                    UPDATE folders
                    SET subfolder_count = (
                        SELECT COUNT(*) FROM folders 
                        WHERE parent_path = ? AND (is_zip = 0 OR is_zip IS NULL)
                    )
                    WHERE path = ?
                    """,
                    (parent_path, parent_path),
                )
        except Exception as e:
            logger.warning(
                f"Error updating parent subfolder_count for {parent_path}: {e}"
            )

    return {"deleted_path": real_path, "kind": "folder"}


def delete_empty_folders(
    paths: list[str] | None = None,
    base_path: str | None = None,
    abort_event: threading.Event | None = None,
) -> dict[str, Any]:
    """Deletes multiple empty folders bottom-up and updates the index."""
    from media_library.workers.library_ops_worker import (
        library_ops_worker_instance,
    )

    base_dir = os.path.realpath(
        base_path or state.get("destination_dir") or DEFAULT_DESTINATION_DIR
    )

    if paths is None:
        candidates_data = find_empty_folders(base_dir)
        target_paths = [c["path"] for c in candidates_data["candidates"]]
    else:
        target_paths = []
        for p in paths:
            try:
                target_paths.append(validate_backup_path(p, base_dir))
            except Exception as e:
                logger.warning(f"Skipping invalid/external path {p}: {e}")

    # Sort bottom-up: deepest paths first
    sorted_paths = sorted(
        target_paths,
        key=lambda p: (p.count(os.sep), len(p)),
        reverse=True,
    )

    deleted = []
    failed = []

    for p in sorted_paths:
        if abort_event is not None and abort_event.is_set():
            break
        try:
            delete_empty_folder(p, abort_event=abort_event, base_path=base_dir)
            deleted.append(p)
            library_ops_worker_instance.history.append(
                {
                    "id": uuid.uuid4().hex[:10],
                    "op": "delete",
                    "source_path": p,
                    "target_path": None,
                    "status": "completed",
                    "error": None,
                }
            )
        except Exception as e:
            logger.warning(f"Failed to delete empty folder {p}: {e}")
            failed.append({"path": p, "error": str(e)})

    return {
        "success": True,
        "deleted_count": len(deleted),
        "deleted_paths": deleted,
        "failed_count": len(failed),
        "failed": failed,
    }

