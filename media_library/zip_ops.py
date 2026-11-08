"""ZIP unpacking and the index rewrite that follows it.

Unpacking is a single-archive operation: the destination is resolved and
validated, the archive is extracted into a temporary sibling and renamed into
place, then the index rows for the affected subtree are replaced in one
transaction. A failure after the filesystem change leaves rows for the crawler
to reconcile, which is why the index write is the last step.
"""

from __future__ import annotations

import os
import shutil
import threading
import time
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from media_library import writes
from media_library.config import get_settings
from media_library.db import reader, writer
from media_library.paths import format_size, resolve_library_path
from media_library.zipfs import cache_usage, purge_cache

_JUNK = {".DS_Store", "Thumbs.db", "thumbs.db", "desktop.ini", ".directory"}
_SKIP_DIRS = {"__MACOSX"}

# One operation at a time per archive file (unpack and the member-cache purge
# both touch the same file). Keyed by normalized path; entries are small and
# never pruned — a library has finite archives.
_archive_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def archive_lock(path: str) -> threading.Lock:
    """Serialise every mutation of one archive file across the ZIP modules."""
    key = os.path.normpath(os.path.abspath(path)).lower()
    with _locks_guard:
        return _archive_locks.setdefault(key, threading.Lock())


def _file_type(ext: str) -> str:
    settings = get_settings()
    if ext in settings.video_exts:
        return "video"
    if ext in settings.image_exts:
        return "image"
    return "other"


def _needs_transcode(ext: str, file_type: str) -> int:
    return int(file_type == "video" and ext not in get_settings().native_video_exts)


def _zip_mtime(info: zipfile.ZipInfo) -> float:
    try:
        return datetime(*info.date_time, tzinfo=timezone.utc).timestamp()
    except (ValueError, OverflowError):
        return time.time()


def _remove_subtree(conn, target: str) -> None:
    prefix = target + os.sep
    length = len(prefix)
    conn.execute(
        "DELETE FROM folders WHERE path = ? OR substr(path, 1, ?) = ?",
        (target, length, prefix),
    )
    conn.execute(
        "DELETE FROM files WHERE folder_path = ? OR substr(folder_path, 1, ?) = ? "
        "OR path = ? OR substr(path, 1, ?) = ?",
        (target, length, prefix, target, length, prefix),
    )
    conn.execute(
        "DELETE FROM favorites WHERE path = ? OR substr(path, 1, ?) = ?",
        (target, length, prefix),
    )
    conn.execute(
        "DELETE FROM ignored_duplicates WHERE path1 = ? OR path2 = ? "
        "OR substr(path1, 1, ?) = ? OR substr(path2, 1, ?) = ?",
        (target, target, length, prefix, length, prefix),
    )
    conn.execute(
        "DELETE FROM visual_frames WHERE path = ? OR substr(path, 1, ?) = ?",
        (target, length, prefix),
    )


def _index_archive(conn, archive: str, infos: list[zipfile.ZipInfo]) -> int:
    members = [
        info
        for info in infos
        if not info.is_dir()
        and not info.filename.startswith("__MACOSX/")
        and os.path.basename(info.filename) not in _JUNK
    ]
    total = sum(info.file_size for info in members)
    parent = os.path.dirname(archive)
    name = os.path.basename(archive)
    conn.execute(
        """
        INSERT INTO folders
            (path, name, parent_path, file_count, subfolder_count,
             last_scanned, is_zip, total_size, dir_mtime)
        VALUES (?, ?, ?, ?, 0, ?, 1, ?, ?)
        ON CONFLICT(path) DO UPDATE SET
            name = excluded.name, parent_path = excluded.parent_path,
            file_count = excluded.file_count, last_scanned = excluded.last_scanned,
            is_zip = 1, total_size = excluded.total_size,
            dir_mtime = excluded.dir_mtime
        """,
        (archive, name, parent, len(members), time.time(), total, time.time()),
    )
    for info in members:
        member = info.filename.replace("\\", "/")
        ext = os.path.splitext(member)[1].lower()
        file_type = _file_type(ext)
        conn.execute(
            """
            INSERT INTO files
                (path, name, folder_path, type, ext, size, size_formatted,
                 mtime, needs_transcode, has_thumb, width, height)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, NULL, NULL)
            ON CONFLICT(path) DO UPDATE SET
                size = excluded.size, size_formatted = excluded.size_formatted,
                mtime = excluded.mtime, type = excluded.type, ext = excluded.ext,
                needs_transcode = excluded.needs_transcode
            """,
            (
                f"{archive}/{member}",
                os.path.basename(member),
                archive,
                file_type,
                ext,
                info.file_size,
                format_size(info.file_size),
                _zip_mtime(info),
                _needs_transcode(ext, file_type),
            ),
        )
    return len(members)


def _update_parent(conn, parent: str) -> None:
    if not parent:
        return
    conn.execute(
        "UPDATE folders SET subfolder_count = ("
        "SELECT COUNT(*) FROM folders WHERE parent_path = ?), "
        "file_count = (SELECT COUNT(*) FROM files WHERE folder_path = ?), "
        "total_size = (SELECT COALESCE(SUM(size), 0) FROM files WHERE folder_path = ?) "
        "WHERE path = ?",
        (parent, parent, parent, parent),
    )


def _gather_tree(root: str) -> dict[str, dict[str, object]]:
    """Read-only walk of a folder tree: stats happen before any write txn.

    Returns ``{dirpath: {"files": [(path, name, dirpath, ext, type, size,
    formatted, mtime, needs_transcode)], "subfolders": n, "total": bytes,
    "mtime": float, "names": [file names]}}``.
    """
    plan: dict[str, dict[str, object]] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if name not in _SKIP_DIRS]
        names = [name for name in filenames if name not in _JUNK]
        rows = []
        total = 0
        for name in names:
            path = os.path.join(dirpath, name)
            try:
                stat = os.stat(path)
            except OSError:
                continue
            total += stat.st_size
            ext = os.path.splitext(name)[1].lower()
            file_type = _file_type(ext)
            rows.append(
                (
                    path,
                    name,
                    dirpath,
                    file_type,
                    ext,
                    stat.st_size,
                    format_size(stat.st_size),
                    stat.st_mtime,
                    _needs_transcode(ext, file_type),
                )
            )
        try:
            dir_mtime: float = os.stat(dirpath).st_mtime
        except OSError:
            dir_mtime = time.time()
        plan[dirpath] = {
            "rows": rows,
            "names": [row[1] for row in rows],
            "subfolders": len(dirnames),
            "total": total,
            "mtime": dir_mtime,
        }
    return plan


def _index_tree(conn, root: str, plan=None) -> int:
    """Apply a gathered tree plan to the index (SQL only)."""
    if plan is None:
        plan = _gather_tree(root)
    count = 0
    for dirpath, info in plan.items():
        rows = info["rows"]
        total = info["total"]
        names = info["names"]
        dirnames = info["subfolders"]
        dir_mtime = info["mtime"]
        for row in rows:
            conn.execute(
                """
                INSERT INTO files
                    (path, name, folder_path, type, ext, size, size_formatted,
                     mtime, needs_transcode, has_thumb, width, height)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, NULL, NULL)
                ON CONFLICT(path) DO UPDATE SET
                    size = excluded.size, size_formatted = excluded.size_formatted,
                    mtime = excluded.mtime
                """,
                row,
            )
            count += 1
        conn.execute(
            """
            INSERT INTO folders
                (path, name, parent_path, file_count, subfolder_count,
                 last_scanned, is_zip, total_size, dir_mtime)
            VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)
            ON CONFLICT(path) DO UPDATE SET
                file_count = excluded.file_count,
                subfolder_count = excluded.subfolder_count,
                last_scanned = excluded.last_scanned, total_size = excluded.total_size,
                dir_mtime = excluded.dir_mtime
            """,
            (
                dirpath,
                os.path.basename(dirpath) or dirpath,
                os.path.dirname(dirpath) if dirpath != root else os.path.dirname(root),
                len(names),
                dirnames,
                time.time(),
                total,
                dir_mtime,
            ),
        )
    return count


def index_archive(conn, archive: str, infos: list[zipfile.ZipInfo]) -> int:
    """Re-index an archive subtree; the indexer uses this when a zip file changes."""
    return _index_archive(conn, archive, infos)


def clear_archive_members(conn, archive: str) -> None:
    conn.execute("DELETE FROM files WHERE folder_path = ?", (archive,))
    conn.execute(
        "DELETE FROM visual_frames WHERE substr(path, 1, ?) = ?",
        (len(archive) + 1, archive + os.sep),
    )


def unpack_archive(archive: str) -> dict[str, Any]:
    source = Path(resolve_library_path(archive))
    if not source.is_file() or source.suffix.lower() != ".zip":
        raise writes.WriteError("ZIP archive not found", 404)
    with archive_lock(str(source)):
        return _unpack_locked(source)


def _unpack_locked(source: Path) -> dict[str, Any]:
    destination = source.with_suffix("")
    if destination.exists():
        counter = 1
        while True:
            candidate = source.with_name(f"{source.stem} ({counter})")
            if not candidate.exists():
                destination = candidate
                break
            counter += 1
    temp = destination.with_name(f".tmp-{uuid.uuid4().hex}-{destination.name}")
    try:
        from media_library.zipfs import extract_archive

        extract_archive(str(source), str(temp))
    except (zipfile.BadZipFile, OSError) as exc:
        shutil.rmtree(temp, ignore_errors=True)
        raise writes.WriteError(f"Could not unpack the archive: {exc}", 500) from exc
    try:
        os.rename(temp, destination)
    except OSError:
        shutil.rmtree(temp, ignore_errors=True)
        raise

    try:
        source.unlink()
    except OSError:
        pass

    plan = _gather_tree(str(destination))
    with writer() as conn, conn:
        _remove_subtree(conn, str(source))
        count = _index_tree(conn, str(destination), plan)
        _update_parent(conn, str(destination.parent))
    purge_cache(str(source))
    return {
        "folder": str(destination),
        "files": count,
        "removed": str(source),
    }


def archives() -> list[dict[str, Any]]:
    with reader() as conn:
        rows = conn.execute(
            "SELECT path, name, file_count, total_size FROM folders "
            "WHERE is_zip = 1 ORDER BY LOWER(name) LIMIT 300"
        ).fetchall()
    return [
        {
            "path": row["path"],
            "name": row["name"],
            "file_count": row["file_count"] or 0,
            "size_formatted": format_size(row["total_size"] or 0),
        }
        for row in rows
    ]


def cache_info() -> dict[str, Any]:
    usage = cache_usage()
    return {**usage, "size_formatted": format_size(usage["bytes"])}
