"""The write path: favorites, rename, move, delete, folders and uploads.

Every function here is synchronous and is invoked from a router via
``asyncio.to_thread`` so the SQLite write lock is never held on the event loop
(AGENTS.md Rule 3). Filesystem operations happen before the index update, with
the move/rename rolled back if the index write fails; a crash between the two
leaves a row the crawler reconciles on its next pass.

There is one index and one app that owns it, so an upload, rename or move is
picked up by the metadata prober and the poster sweep without a second crawler
on the mount. ``DB_READ_ONLY=true`` (or ``WRITES=false``) turns every mutation
into a 503.
"""

from __future__ import annotations

import os
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from media_library.cache import animated_path, thumbnail_path
from media_library.config import get_settings
from media_library.db import reader, writer
from media_library.paths import format_size, resolve_library_path, stable_id

_UPLOAD_ID_CHARS = set("abcdefghijklmnopqrstuvwxyz0123456789-_")

_gc_lock = threading.Lock()
# None means "never checked". A zero timestamp would compare badly against
# time.monotonic() on a machine whose uptime is shorter than the interval.
_gc_checked_at: float | None = None
_GC_INTERVAL_SECONDS = 3600


class WriteError(Exception):
    """A rejected mutation, carrying the HTTP status the router should use."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


def _validate_name(name: str) -> str:
    clean = (name or "").strip()
    if (
        not clean
        or clean in (".", "..")
        or "/" in clean
        or "\\" in clean
        or "\x00" in clean
        or any(ord(ch) < 32 or ord(ch) == 127 for ch in clean)
        or clean.startswith(".")
        or len(clean) > 255
    ):
        raise WriteError("Invalid file or folder name", 400)
    return clean


def validate_name(name: str) -> str:
    """Public wrapper so the sorter can validate a new filename."""
    return _validate_name(name)


def _resolve_file(raw: str) -> str:
    target = resolve_library_path(raw)
    if not os.path.isfile(target):
        raise WriteError("File not found", 404)
    return target


def _resolve_dir(raw: str) -> str:
    target = resolve_library_path(raw)
    if not os.path.isdir(target):
        raise WriteError("Destination folder not found", 404)
    return target


def _thumb_paths(path: str, mtime: float, size: int) -> tuple[Path, Path]:
    return thumbnail_path(path, mtime, size), animated_path(path, mtime, size)


def _move_thumbs(old: str, new: str, mtime: float, size: int) -> None:
    """Move cached poster/preview files to the new key so a move keeps its art."""
    old_thumb, old_anim = _thumb_paths(old, mtime, size)
    new_thumb, new_anim = _thumb_paths(new, mtime, size)
    for source, target in ((old_thumb, new_thumb), (old_anim, new_anim)):
        if not source.is_file():
            continue
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(source, target)
        except OSError:
            # A cache miss only costs a regeneration; never fail the mutation.
            pass


def _remove_thumbs(path: str, mtime: float, size: int) -> None:
    for candidate in _thumb_paths(path, mtime, size):
        try:
            candidate.unlink(missing_ok=True)
        except OSError:
            pass


def _file_stats(path: str) -> tuple[float, int]:
    stat = os.stat(path)
    return stat.st_mtime, stat.st_size


def recompute_folder(conn, folder: str) -> None:
    """Public wrapper for callers that update files outside this module."""
    _recompute_folder(conn, folder)


def _recompute_folder(conn, folder: str) -> None:
    row = conn.execute(
        "SELECT COUNT(*) AS n, COALESCE(SUM(size), 0) AS total FROM files "
        "WHERE folder_path = ?",
        (folder,),
    ).fetchone()
    conn.execute(
        "UPDATE folders SET file_count = ?, total_size = ?, last_scanned = ? "
        "WHERE path = ?",
        (row["n"], row["total"], time.time(), folder),
    )


def _apply_file_move(conn, old: str, new: str) -> None:
    settings = get_settings()
    ext = os.path.splitext(new)[1].lower()
    if ext in settings.video_exts:
        file_type = "video"
    elif ext in settings.image_exts:
        file_type = "image"
    else:
        file_type = "other"
    needs_transcode = int(
        file_type == "video" and ext not in settings.native_video_exts
    )
    conn.execute(
        "UPDATE files SET path = ?, name = ?, folder_path = ?, ext = ?, "
        "type = ?, needs_transcode = ? WHERE path = ?",
        (
            new,
            os.path.basename(new),
            os.path.dirname(new),
            ext,
            file_type,
            needs_transcode,
            old,
        ),
    )
    conn.execute("UPDATE favorites SET path = ? WHERE path = ?", (new, old))
    conn.execute("DELETE FROM visual_frames WHERE path = ?", (old,))
    _recompute_folder(conn, os.path.dirname(old))
    if os.path.dirname(new) != os.path.dirname(old):
        _recompute_folder(conn, os.path.dirname(new))


def _favorite_counts(conn) -> dict[str, int]:
    row = conn.execute(
        "SELECT COUNT(*) AS all_count, "
        "COALESCE(SUM(f.type = 'video'), 0) AS videos, "
        "COALESCE(SUM(f.type = 'image'), 0) AS images "
        "FROM favorites fav JOIN files f ON f.path = fav.path"
    ).fetchone()
    return {
        "all": row["all_count"] or 0,
        "videos": row["videos"] or 0,
        "images": row["images"] or 0,
    }


def toggle_favorite(path: str) -> dict[str, Any]:
    target = resolve_library_path(path)
    with writer() as conn, conn:
        exists = conn.execute(
            "SELECT 1 FROM files WHERE path = ?", (target,)
        ).fetchone()
        if exists is None:
            raise WriteError("Not indexed", 404)
        favorite = conn.execute(
            "SELECT 1 FROM favorites WHERE path = ?", (target,)
        ).fetchone()
        if favorite is None:
            conn.execute(
                "INSERT INTO favorites (path, created_at) VALUES (?, ?)",
                (target, time.time()),
            )
            is_favorite = True
        else:
            conn.execute("DELETE FROM favorites WHERE path = ?", (target,))
            is_favorite = False
        counts = _favorite_counts(conn)
    return {"path": target, "is_favorite": is_favorite, "counts": counts}


def set_favorites(paths: list[str], action: str = "toggle") -> dict[str, Any]:
    """Star, unstar or toggle many files in one transaction."""
    if action not in ("add", "remove", "toggle"):
        raise WriteError("Unknown favorite action", 400)
    resolved: list[str] = []
    failed: list[dict[str, str]] = []
    for raw in paths:
        try:
            resolved.append(resolve_library_path(raw))
        except Exception as exc:
            failed.append({"path": raw, "error": str(exc)})
    updated = 0
    with writer() as conn, conn:
        if resolved:
            placeholders = ",".join("?" for _ in resolved)
            indexed = {
                row["path"]
                for row in conn.execute(
                    f"SELECT path FROM files WHERE path IN ({placeholders})", resolved
                ).fetchall()
            }
            existing = {
                row["path"]
                for row in conn.execute(
                    f"SELECT path FROM favorites WHERE path IN ({placeholders})",
                    resolved,
                ).fetchall()
            }
            now = time.time()
            for path in resolved:
                if path not in indexed:
                    failed.append({"path": path, "error": "Not indexed"})
                    continue
                wants = action == "add" or (action == "toggle" and path not in existing)
                if wants and path not in existing:
                    conn.execute(
                        "INSERT INTO favorites (path, created_at) VALUES (?, ?)",
                        (path, now),
                    )
                elif not wants and path in existing:
                    conn.execute("DELETE FROM favorites WHERE path = ?", (path,))
                updated += 1
        counts = _favorite_counts(conn)
    return {"updated": updated, "counts": counts, "failed": failed}


def bulk_rename(items: list[dict[str, str]]) -> dict[str, Any]:
    """Rename many files, reporting each failure independently."""
    renamed: list[str] = []
    failed: list[dict[str, str]] = []
    for item in items:
        path = str(item.get("path", ""))
        try:
            renamed.append(rename_file(path, str(item.get("name", "")))["path"])
        except Exception as exc:
            failed.append({"path": path, "error": str(exc)})
    return {"renamed": renamed, "failed": failed}


def rename_file(path: str, name: str) -> dict[str, Any]:
    target = _resolve_file(path)
    new_name = _validate_name(name)
    new_path = os.path.join(os.path.dirname(target), new_name)
    if new_path == target:
        return {"path": target}
    if os.path.exists(new_path):
        raise WriteError("A file with that name already exists", 409)
    mtime, size = _file_stats(target)
    os.rename(target, new_path)
    try:
        with writer() as conn, conn:
            _apply_file_move(conn, target, new_path)
    except Exception:
        os.rename(new_path, target)
        raise
    _move_thumbs(target, new_path, mtime, size)
    return {"path": new_path}


def delete_files(paths: list[str]) -> dict[str, Any]:
    """Delete files and/or folders (recursive), updating the index.

    Folders remove their whole indexed subtree, the cached posters of the files
    they contained, and any favorites pointing inside them.
    """
    if not paths:
        raise WriteError("No files selected", 400)
    if len(paths) > 500:
        raise WriteError("Too many items in one request (max 500)", 400)

    root = resolve_library_path(None)
    failed: list[dict[str, str]] = []
    prepared: list[tuple[str, str, float, int]] = []

    for raw in paths:
        try:
            target = resolve_library_path(raw)
            if target == root:
                raise WriteError("Refusing to delete the library root", 400)
            if os.path.isdir(target):
                prepared.append((target, "folder", 0.0, 0))
            elif os.path.isfile(target):
                mtime, size = _file_stats(target)
                kind = "zip" if target.lower().endswith(".zip") else "file"
                prepared.append((target, kind, mtime, size))
            else:
                raise WriteError("Not found", 404)
        except WriteError as exc:
            failed.append({"path": raw, "error": exc.message})

    deleted: list[tuple[str, str, float, int]] = []
    for target, kind, mtime, size in prepared:
        try:
            if kind == "folder":
                shutil.rmtree(target)
            else:
                os.remove(target)
            deleted.append((target, kind, mtime, size))
        except OSError as exc:
            failed.append({"path": target, "error": str(exc)})

    if not deleted:
        return {"deleted": [], "failed": failed}

    doomed_thumbs: list[tuple[str, float, int]] = []
    parent_folders: set[str] = set()

    with writer() as conn, conn:
        for target, kind, mtime, size in deleted:
            parent = os.path.dirname(target)
            if parent and parent != target:
                parent_folders.add(parent)
            if kind in ("folder", "zip"):
                prefix = target + os.sep
                rows = conn.execute(
                    "SELECT path, mtime, size FROM files WHERE folder_path = ? "
                    "OR substr(folder_path, 1, ?) = ? OR path = ? "
                    "OR substr(path, 1, ?) = ?",
                    (target, len(prefix), prefix, target, len(prefix), prefix),
                ).fetchall()
                doomed_thumbs.extend(
                    (row["path"], float(row["mtime"] or 0), int(row["size"] or 0))
                    for row in rows
                )
                conn.execute(
                    "DELETE FROM folders WHERE path = ? OR substr(path, 1, ?) = ?",
                    (target, len(prefix), prefix),
                )
                conn.execute(
                    "DELETE FROM files WHERE folder_path = ? "
                    "OR substr(folder_path, 1, ?) = ? OR path = ? "
                    "OR substr(path, 1, ?) = ?",
                    (target, len(prefix), prefix, target, len(prefix), prefix),
                )
                conn.execute(
                    "DELETE FROM favorites WHERE path = ? OR substr(path, 1, ?) = ?",
                    (target, len(prefix), prefix),
                )
                conn.execute(
                    "DELETE FROM ignored_duplicates WHERE path1 = ? OR path2 = ? "
                    "OR substr(path1, 1, ?) = ? OR substr(path2, 1, ?) = ?",
                    (target, target, len(prefix), prefix, len(prefix), prefix),
                )
                conn.execute(
                    "DELETE FROM visual_frames WHERE path = ? "
                    "OR substr(path, 1, ?) = ?",
                    (target, len(prefix), prefix),
                )
                if kind == "zip":
                    # The archive's own row holds the virtual folder; members
                    # were removed by the prefix clauses above.
                    conn.execute("DELETE FROM folders WHERE path = ?", (target,))
            else:
                doomed_thumbs.append((target, mtime, size))
                conn.execute("DELETE FROM files WHERE path = ?", (target,))
                conn.execute("DELETE FROM favorites WHERE path = ?", (target,))
                conn.execute(
                    "DELETE FROM ignored_duplicates WHERE path1 = ? OR path2 = ?",
                    (target, target),
                )
                conn.execute("DELETE FROM visual_frames WHERE path = ?", (target,))
        for folder in parent_folders:
            _recompute_folder(conn, folder)
            conn.execute(
                "UPDATE folders SET subfolder_count = ("
                "SELECT COUNT(*) FROM folders WHERE parent_path = ?) WHERE path = ?",
                (folder, folder),
            )

    for path, mtime, size in doomed_thumbs:
        _remove_thumbs(path, mtime, size)

    return {"deleted": [target for target, *_rest in deleted], "failed": failed}


def _entry_rows(path: str):
    """Rows and ZIP flag for a folder/archive about to be relocated."""
    prefix = path + os.sep
    cut = len(prefix)
    with reader() as conn:
        rows = conn.execute(
            "SELECT path, mtime, size FROM files "
            "WHERE path = ? OR substr(path, 1, ?) = ?",
            (path, cut, prefix),
        ).fetchall()
        row = conn.execute(
            "SELECT is_zip FROM folders WHERE path = ?", (path,)
        ).fetchone()
    return rows, bool(row is not None and row["is_zip"])


def _relocate_entry(source: str, target: str, is_zip: bool) -> None:
    """Rewrite the index subtree after ``source`` moved to ``target``.

    The caller has already moved the entry on disk and is responsible for
    undoing that move if this raises.
    """
    from media_library import zipfs

    rows, zip_indexed = _entry_rows(source)
    zip_entry = is_zip or zip_indexed
    prefix = source + os.sep
    cut = len(prefix)
    name = os.path.basename(target)
    parent = os.path.dirname(target)
    with writer() as conn, conn:
        conn.execute(
            "UPDATE folders SET path = ?, name = ?, parent_path = ? WHERE path = ?",
            (target, name, parent, source),
        )
        # Prefix tests use ``substr(...) = ?`` rather than ``LIKE``: a folder
        # name may contain ``_`` or ``%``, which LIKE would treat as wildcards
        # and rewrite a sibling subtree ("my_folder" also matching "myXfolder").
        if zip_entry:
            conn.execute(
                "UPDATE files SET folder_path = ? WHERE folder_path = ?",
                (target, source),
            )
            conn.execute(
                "UPDATE files SET path = ? || substr(path, ?) "
                "WHERE substr(path, 1, ?) = ?",
                (target, cut, cut, prefix),
            )
            conn.execute(
                "UPDATE favorites SET path = ? || substr(path, ?) "
                "WHERE substr(path, 1, ?) = ?",
                (target, cut, cut, prefix),
            )
            conn.execute(
                "UPDATE visual_frames SET path = ? || substr(path, ?) "
                "WHERE substr(path, 1, ?) = ?",
                (target, cut, cut, prefix),
            )
            conn.execute(
                "UPDATE ignored_duplicates SET "
                "path1 = CASE WHEN substr(path1, 1, ?) = ? "
                "THEN ? || substr(path1, ?) ELSE path1 END, "
                "path2 = CASE WHEN substr(path2, 1, ?) = ? "
                "THEN ? || substr(path2, ?) ELSE path2 END "
                "WHERE substr(path1, 1, ?) = ? OR substr(path2, 1, ?) = ?",
                (
                    cut,
                    prefix,
                    target,
                    cut,
                    cut,
                    prefix,
                    target,
                    cut,
                    cut,
                    prefix,
                    cut,
                    prefix,
                ),
            )
        else:
            conn.execute(
                "UPDATE folders SET path = ? || substr(path, ?) "
                "WHERE substr(path, 1, ?) = ?",
                (target, cut, cut, prefix),
            )
            conn.execute(
                "UPDATE folders SET parent_path = ? || substr(parent_path, ?) "
                "WHERE parent_path = ? OR substr(parent_path, 1, ?) = ?",
                (target, cut, source, cut, prefix),
            )
            conn.execute(
                "UPDATE files SET path = ? || substr(path, ?) "
                "WHERE path = ? OR substr(path, 1, ?) = ?",
                (target, cut, source, cut, prefix),
            )
            conn.execute(
                "UPDATE files SET folder_path = ? || substr(folder_path, ?) "
                "WHERE folder_path = ? OR substr(folder_path, 1, ?) = ?",
                (target, cut, source, cut, prefix),
            )
            conn.execute(
                "UPDATE favorites SET path = ? || substr(path, ?) "
                "WHERE path = ? OR substr(path, 1, ?) = ?",
                (target, cut, source, cut, prefix),
            )
            conn.execute(
                "UPDATE visual_frames SET path = ? || substr(path, ?) "
                "WHERE path = ? OR substr(path, 1, ?) = ?",
                (target, cut, source, cut, prefix),
            )
            conn.execute(
                "UPDATE ignored_duplicates SET "
                "path1 = CASE WHEN substr(path1, 1, ?) = ? "
                "THEN ? || substr(path1, ?) ELSE path1 END, "
                "path2 = CASE WHEN substr(path2, 1, ?) = ? "
                "THEN ? || substr(path2, ?) ELSE path2 END "
                "WHERE path1 = ? OR path2 = ? "
                "OR substr(path1, 1, ?) = ? OR substr(path2, 1, ?) = ?",
                (
                    cut,
                    prefix,
                    target,
                    cut,
                    cut,
                    prefix,
                    target,
                    cut,
                    source,
                    source,
                    cut,
                    prefix,
                    cut,
                    prefix,
                ),
            )
        _recompute_folder(conn, os.path.dirname(source))
        _recompute_folder(conn, parent)

    for row in rows:
        suffix = row["path"][len(source) :]
        _move_thumbs(
            row["path"],
            target + suffix,
            float(row["mtime"] or 0),
            int(row["size"] or 0),
        )

    if zip_entry:
        try:
            zipfs.purge_cache(source)
        except OSError:
            pass


def rename_folder(raw_path: str, name: str) -> dict[str, Any]:
    """Rename a real folder or a ZIP archive, rewriting the index subtree.

    Cached posters and animated previews are moved to the new cache keys, so a
    renamed folder keeps its artwork instead of regenerating every poster.
    """
    target = resolve_library_path(raw_path)
    is_zip = os.path.isfile(target) and target.lower().endswith(".zip")
    if not is_zip and not os.path.isdir(target):
        raise WriteError("Only folders or ZIP archives can be renamed here", 400)
    clean = _validate_name(name)
    new_path = os.path.join(os.path.dirname(target), clean)
    if new_path == target:
        return {"path": target, "name": clean}
    if os.path.exists(new_path):
        raise WriteError("An item with that name already exists", 409)

    os.rename(target, new_path)
    try:
        _relocate_entry(target, new_path, is_zip)
    except Exception:
        os.rename(new_path, target)
        raise
    return {"path": new_path, "name": clean}


def create_folder(parent: str, name: str) -> dict[str, Any]:
    parent_dir = _resolve_dir(parent)
    clean = _validate_name(name)
    target = os.path.join(parent_dir, clean)
    if os.path.exists(target):
        raise WriteError("A file or folder with that name already exists", 409)
    os.mkdir(target)
    try:
        mtime, _size = _file_stats(target)
    except OSError:
        mtime = time.time()
    with writer() as conn, conn:
        conn.execute(
            """
            INSERT INTO folders
                (path, name, parent_path, file_count, subfolder_count,
                 last_scanned, is_zip, total_size, dir_mtime)
            VALUES (?, ?, ?, 0, 0, ?, 0, 0, ?)
            ON CONFLICT(path) DO UPDATE SET name = excluded.name,
                parent_path = excluded.parent_path, dir_mtime = excluded.dir_mtime
            """,
            (target, clean, parent_dir, time.time(), mtime),
        )
        conn.execute(
            "UPDATE folders SET subfolder_count = ("
            "SELECT COUNT(*) FROM folders WHERE parent_path = ?) WHERE path = ?",
            (parent_dir, parent_dir),
        )
    return {"path": target, "name": clean}


def unique_target(destination: str, name: str, occupied: set[str] | None = None) -> str:
    """Public wrapper for callers that need collision-free naming."""
    return _unique_target(destination, name, occupied or set())


def _unique_target(destination: str, name: str, occupied: set[str]) -> str:
    stem, ext = os.path.splitext(name)
    counter = 1
    while True:
        candidate = os.path.join(destination, f"{stem} ({counter}){ext}")
        if not os.path.exists(candidate) and candidate not in occupied:
            return candidate
        counter += 1


def move_files(
    paths: list[str], destination: str, conflict: str = "auto_rename"
) -> dict[str, Any]:
    if conflict not in ("auto_rename", "skip", "error"):
        raise WriteError("Unknown conflict policy", 400)
    dest = _resolve_dir(destination)
    moved: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
    failed: list[dict[str, str]] = []
    occupied: set[str] = set()

    for raw in paths[:500]:
        try:
            source = resolve_library_path(raw)
            if not os.path.exists(source):
                raise WriteError("File or folder not found", 404)
        except WriteError as exc:
            failed.append({"path": raw, "error": exc.message})
            continue

        is_entry = os.path.isdir(source) or (
            os.path.isfile(source) and source.lower().endswith(".zip")
        )
        if is_entry and (dest == source or dest.startswith(source + os.sep)):
            failed.append({"path": source, "error": "Cannot move a folder into itself"})
            continue
        if os.path.dirname(source) == dest:
            skipped.append({"path": source, "reason": "Already in this folder"})
            continue

        candidate = os.path.join(dest, os.path.basename(source))
        name_taken = os.path.exists(candidate) or candidate in occupied
        if name_taken and conflict == "skip":
            skipped.append({"path": source, "reason": "Name already exists"})
            continue
        if name_taken and conflict == "error":
            raise WriteError(f"Destination already exists: {candidate}", 409)
        target = (
            _unique_target(dest, os.path.basename(source), occupied)
            if name_taken
            else candidate
        )

        try:
            shutil.move(source, target)
        except OSError as exc:
            failed.append({"path": source, "error": str(exc)})
            continue

        if is_entry:
            try:
                _relocate_entry(source, target, source.lower().endswith(".zip"))
            except Exception:
                try:
                    shutil.move(target, source)
                except OSError:
                    pass
                failed.append({"path": source, "error": "Index update failed"})
                continue
            occupied.add(target)
            moved.append({"source": source, "target": target})
            continue

        mtime, size = _file_stats(target)
        try:
            with writer() as conn, conn:
                _apply_file_move(conn, source, target)
        except Exception:
            try:
                shutil.move(target, source)
            except OSError:
                pass
            failed.append({"path": source, "error": "Index update failed"})
            continue
        _move_thumbs(source, target, mtime, size)
        occupied.add(target)
        moved.append({"source": source, "target": target})

    return {"moved": moved, "skipped": skipped, "failed": failed}


# ---------------------------------------------------------------------------
# Resumable uploads
# ---------------------------------------------------------------------------


def _gc_stale_staging() -> None:
    """Drop abandoned staged uploads, at most once an hour.

    A closed tab leaves its ``.part`` file behind. The sweep runs from the
    write path (throttled) rather than only at startup, so a long-running server
    also reclaims the space. ``UPLOAD_STAGING_TTL_HOURS`` bounds how long an
    interrupted upload can still resume.
    """
    global _gc_checked_at
    now = time.monotonic()
    if _gc_checked_at is not None and now - _gc_checked_at < _GC_INTERVAL_SECONDS:
        return
    if not _gc_lock.acquire(blocking=False):
        return
    try:
        if _gc_checked_at is not None and now - _gc_checked_at < _GC_INTERVAL_SECONDS:
            return
        _gc_checked_at = now
        staging = get_settings().upload_staging_dir
        if not staging.is_dir():
            return
        cutoff = time.time() - (get_settings().upload_staging_ttl_hours * 3600)
        for entry in staging.glob("*.part"):
            try:
                if entry.stat().st_mtime < cutoff:
                    entry.unlink(missing_ok=True)
            except OSError:
                continue
    finally:
        _gc_lock.release()


def _staging_file(upload_id: str) -> Path:
    clean = (upload_id or "").strip().lower()
    if not 6 <= len(clean) <= 80 or any(ch not in _UPLOAD_ID_CHARS for ch in clean):
        raise WriteError("Invalid upload id", 400)
    _gc_stale_staging()
    staging = get_settings().upload_staging_dir
    staging.mkdir(parents=True, exist_ok=True)
    return staging / f"{clean}.part"


def staged_size(upload_id: str) -> dict[str, Any]:
    path = _staging_file(upload_id)
    return {
        "upload_id": upload_id,
        "exists": path.is_file(),
        "received_bytes": path.stat().st_size if path.is_file() else 0,
    }


def stage_chunk(upload_id: str, offset: int, data: bytes) -> dict[str, Any]:
    path = _staging_file(upload_id)
    existing = path.stat().st_size if path.is_file() else 0
    if offset > existing:
        raise WriteError(
            f"Chunk starts past the staged data ({offset} > {existing})", 409
        )
    if offset + len(data) <= existing:
        return {"received_bytes": existing, "duplicate": True}
    with open(path, "ab") as handle:
        if offset < existing:
            handle.truncate(offset)
        handle.write(data)
    return {"received_bytes": offset + len(data), "duplicate": False}


def _index_new_file(conn, path: str) -> None:
    settings = get_settings()
    stat = os.stat(path)
    ext = os.path.splitext(path)[1].lower()
    if ext in settings.video_exts:
        file_type = "video"
    elif ext in settings.image_exts:
        file_type = "image"
    else:
        file_type = "other"
    needs_transcode = int(
        file_type == "video" and ext not in settings.native_video_exts
    )
    conn.execute(
        """
        INSERT INTO files
            (path, name, folder_path, type, ext, size, size_formatted, mtime,
             needs_transcode, has_thumb, width, height)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, NULL, NULL)
        ON CONFLICT(path) DO UPDATE SET
            size = excluded.size, size_formatted = excluded.size_formatted,
            mtime = excluded.mtime, needs_transcode = excluded.needs_transcode,
            has_thumb = 0, width = NULL, height = NULL
        """,
        (
            path,
            os.path.basename(path),
            os.path.dirname(path),
            file_type,
            ext,
            stat.st_size,
            format_size(stat.st_size),
            stat.st_mtime,
            needs_transcode,
        ),
    )


def finalize_upload(
    upload_id: str,
    name: str,
    folder: str,
    total_size: int | None = None,
    conflict: str = "auto_rename",
) -> dict[str, Any]:
    if conflict not in ("auto_rename", "skip", "error"):
        raise WriteError("Unknown conflict policy", 400)
    staging = _staging_file(upload_id)
    if not staging.is_file():
        raise WriteError("No staged upload for this id", 404)
    actual = staging.stat().st_size
    if total_size is not None and actual != total_size:
        raise WriteError(
            f"Upload incomplete: {actual} of {total_size} bytes staged", 400
        )
    if actual == 0:
        raise WriteError("Upload is empty", 400)

    destination = _resolve_dir(folder)
    clean = _validate_name(name)
    target = os.path.join(destination, clean)
    if os.path.exists(target):
        if conflict == "skip":
            staging.unlink(missing_ok=True)
            return {"skipped": True, "path": target}
        if conflict == "error":
            raise WriteError("A file with that name already exists", 409)
        target = _unique_target(destination, clean, set())

    temp = os.path.join(destination, f".mllibtmp-{uuid.uuid4().hex}-{clean}")
    try:
        shutil.copyfile(staging, temp)
        if os.path.getsize(temp) != actual:
            raise WriteError("Staged copy was truncated", 500)
        os.replace(temp, target)
    except OSError as exc:
        try:
            os.unlink(temp)
        except OSError:
            pass
        raise WriteError(f"Could not write the file: {exc}", 500) from exc

    try:
        with writer() as conn, conn:
            _index_new_file(conn, target)
            _recompute_folder(conn, destination)
    finally:
        staging.unlink(missing_ok=True)
    return {"skipped": False, "path": target}


def cancel_upload(upload_id: str) -> dict[str, Any]:
    path = _staging_file(upload_id)
    removed = path.is_file()
    path.unlink(missing_ok=True)
    return {"cancelled": removed}


def index_external_file(path: str) -> None:
    """Index a file that arrived from outside the library (sorter import)."""
    with writer() as conn, conn:
        _index_new_file(conn, path)
        _recompute_folder(conn, os.path.dirname(path))


def remove_index_entry(path: str) -> None:
    """Drop one file from the index and its cached artwork (sorter undo)."""
    try:
        mtime, size = _file_stats(path)
    except OSError:
        mtime, size = 0.0, 0
    with writer() as conn, conn:
        row = conn.execute(
            "SELECT mtime, size FROM files WHERE path = ?", (path,)
        ).fetchone()
        if row is not None:
            mtime = float(row["mtime"] or 0)
            size = int(row["size"] or 0)
        conn.execute("DELETE FROM files WHERE path = ?", (path,))
        conn.execute("DELETE FROM favorites WHERE path = ?", (path,))
        conn.execute("DELETE FROM visual_frames WHERE path = ?", (path,))
        _recompute_folder(conn, os.path.dirname(path))
    _remove_thumbs(path, mtime, size)


def import_file(
    source: str, destination: str, conflict: str = "auto_rename"
) -> dict[str, Any]:
    """Move an external file into the library and index it (sorter moves)."""
    if conflict not in ("auto_rename", "skip", "error"):
        raise WriteError("Unknown conflict policy", 400)
    if not os.path.isfile(source):
        raise WriteError("Source file not found", 404)
    dest = _resolve_dir(destination)
    name = os.path.basename(source)
    target = os.path.join(dest, name)
    if os.path.exists(target):
        if conflict == "skip":
            return {"skipped": True, "source": source, "target": target}
        if conflict == "error":
            raise WriteError("Destination already exists", 409)
        target = _unique_target(dest, name, set())
    try:
        shutil.move(source, target)
    except OSError as exc:
        raise WriteError(f"Could not move the file: {exc}", 500) from exc
    try:
        index_external_file(target)
    except Exception:
        try:
            shutil.move(target, source)
        except OSError:
            pass
        raise
    return {"skipped": False, "source": source, "target": target}


def file_id(path: str) -> str:
    return stable_id(path)
