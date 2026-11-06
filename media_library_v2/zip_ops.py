"""ZIP compression, unpacking and the index rewrite that follows them.

Single-archive operations only: the source/destination is resolved and
validated, the archive is built on local staging and committed atomically, then
the index rows for the affected subtree are replaced in one transaction. A
failure after the filesystem change leaves rows for v1's crawler to reconcile,
which is why the index write is the last step.
"""

from __future__ import annotations

import os
import shutil
import time
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from media_library_v2 import writes
from media_library_v2.config import get_settings
from media_library_v2.db import reader, writer
from media_library_v2.paths import format_size, resolve_library_path
from media_library_v2.zipfs import cache_usage, purge_cache

_JUNK = {".DS_Store", "Thumbs.db", "thumbs.db", "desktop.ini", ".directory"}
_SKIP_DIRS = {"__MACOSX"}


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


def _index_tree(conn, root: str) -> int:
    count = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if name not in _SKIP_DIRS]
        files = [name for name in filenames if name not in _JUNK]
        total = 0
        for name in files:
            path = os.path.join(dirpath, name)
            try:
                stat = os.stat(path)
            except OSError:
                continue
            total += stat.st_size
            ext = os.path.splitext(name)[1].lower()
            file_type = _file_type(ext)
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
                ),
            )
            count += 1
        try:
            dir_mtime = os.stat(dirpath).st_mtime
        except OSError:
            dir_mtime = time.time()
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
                len(files),
                len(dirnames),
                time.time(),
                total,
                dir_mtime,
            ),
        )
    return count


def index_archive(conn, archive: str, infos: list[zipfile.ZipInfo]) -> int:
    """Public wrapper used by the archive WebP rewrite."""
    return _index_archive(conn, archive, infos)


def clear_archive_members(conn, archive: str) -> None:
    conn.execute("DELETE FROM files WHERE folder_path = ?", (archive,))


def check_folder(folder: str) -> dict[str, Any]:
    root = resolve_library_path(folder)
    if not os.path.isdir(root):
        raise writes.WriteError("Folder not found", 404)
    if root == resolve_library_path(None):
        raise writes.WriteError("Refusing to compress the library root", 400)
    prefix = root + os.sep
    with reader() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM files WHERE type = 'video' AND "
            "(folder_path = ? OR substr(folder_path, 1, ?) = ?) "
            "AND substr(folder_path, -4) != '.zip'",
            (root, len(prefix), prefix),
        ).fetchone()
        videos = int(row["n"] or 0)
        images = int(
            conn.execute(
                "SELECT COUNT(*) AS n FROM files WHERE type = 'image' AND "
                "(folder_path = ? OR substr(folder_path, 1, ?) = ?) "
                "AND substr(folder_path, -4) != '.zip'",
                (root, len(prefix), prefix),
            ).fetchone()["n"]
            or 0
        )
        nested_archives = int(
            conn.execute(
                "SELECT COUNT(*) AS n FROM folders WHERE is_zip = 1 AND "
                "(path = ? OR substr(path, 1, ?) = ?)",
                (root, len(prefix), prefix),
            ).fetchone()["n"]
            or 0
        )
    target = root + ".zip"
    reason = ""
    if nested_archives:
        reason = "Contains ZIP archives; unpack or move them first"
    elif videos:
        reason = "Contains videos; ZIP archives are for image folders"
    elif images < 1:
        reason = "No images to archive"
    elif os.path.exists(target):
        reason = "An archive with this name already exists"
    return {
        "path": root,
        "eligible": not reason,
        "images": images,
        "videos": videos,
        "target": target,
        "reason": reason,
    }


def _walk_archive_members(root: str) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            name
            for name in dirnames
            if name not in _SKIP_DIRS and not name.startswith(".")
        )
        for name in sorted(filenames):
            if name in _JUNK or name.startswith("."):
                continue
            full = os.path.join(dirpath, name)
            entries.append((full, os.path.relpath(full, root).replace(os.sep, "/")))
    return entries


def compress_folder(folder: str) -> dict[str, Any]:
    checked = check_folder(folder)
    if not checked["eligible"]:
        raise writes.WriteError(checked["reason"] or "Folder is not eligible", 409)
    source = Path(checked["path"])
    target = Path(checked["target"])

    staging_dir = get_settings().zip_staging_dir
    staging_dir.mkdir(parents=True, exist_ok=True)
    staged = staging_dir / f"{source.name}-{uuid.uuid4().hex}.zip"

    members = _walk_archive_members(str(source))
    if not members:
        raise writes.WriteError("Folder has no files to archive", 400)

    try:
        with zipfile.ZipFile(
            staged, "w", zipfile.ZIP_DEFLATED, compresslevel=6
        ) as archive:
            for full, arcname in members:
                archive.write(full, arcname)
        with zipfile.ZipFile(staged) as archive:
            bad = archive.testzip()
            if bad:
                raise writes.WriteError(f"Archive verification failed at {bad}", 500)
            infos = archive.infolist()
        target_tmp = target.with_name(f".tmp-{uuid.uuid4().hex}-{target.name}")
        shutil.copyfile(staged, target_tmp)
        if target_tmp.stat().st_size != staged.stat().st_size:
            raise writes.WriteError("Archive copy was truncated", 500)
        os.replace(target_tmp, target)
    except OSError as exc:
        raise writes.WriteError(f"Could not create the archive: {exc}", 500) from exc
    finally:
        try:
            staged.unlink(missing_ok=True)
        except OSError:
            pass

    parent = str(source.parent)
    try:
        shutil.rmtree(source)
    except OSError as exc:
        try:
            target.unlink(missing_ok=True)
        except OSError:
            pass
        raise writes.WriteError(
            f"Could not remove the source folder: {exc}", 500
        ) from exc

    with writer() as conn, conn:
        _remove_subtree(conn, str(source))
        count = _index_archive(conn, str(target), infos)
        _update_parent(conn, parent)
    return {
        "archive": str(target),
        "members": count,
        "bytes": target.stat().st_size,
        "size_formatted": format_size(target.stat().st_size),
        "removed": str(source),
    }


def unpack_archive(archive: str) -> dict[str, Any]:
    source = Path(resolve_library_path(archive))
    if not source.is_file() or source.suffix.lower() != ".zip":
        raise writes.WriteError("ZIP archive not found", 404)
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
        from media_library_v2.zipfs import extract_archive

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

    with writer() as conn, conn:
        _remove_subtree(conn, str(source))
        count = _index_tree(conn, str(destination))
        _update_parent(conn, str(destination.parent))
    purge_cache(str(source))
    return {
        "folder": str(destination),
        "files": count,
        "removed": str(source),
    }


def archive_candidates(folder: str) -> list[dict[str, Any]]:
    with reader() as conn:
        rows = conn.execute(
            """
            SELECT fo.path, fo.name, fo.file_count, fo.total_size
            FROM folders fo
            WHERE COALESCE(fo.is_zip, 0) = 0
              AND NOT EXISTS (SELECT 1 FROM folders c WHERE c.parent_path = fo.path)
              AND (SELECT COUNT(*) FROM files x WHERE x.folder_path = fo.path
                   AND x.type = 'image') >= 2
              AND NOT EXISTS (SELECT 1 FROM files v WHERE v.folder_path = fo.path
                   AND v.type = 'video')
            ORDER BY fo.total_size DESC
            LIMIT 200
            """
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
