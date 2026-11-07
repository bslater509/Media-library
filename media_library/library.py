"""Index-only library reads.

Nothing in this module stats a media file: browse, folders, search and counts
all come from SQLite rows, so a remote FUSE library stays instant. Browse and
``list_folders`` check whether the target path is a real directory (or an
indexed archive) so a stale path clamps to its parent; those one or two stats
run in a worker thread, never on the event loop (see ``routers/library.py``).
The only other filesystem access in the request path is inside ``media.py``,
where byte-range streaming genuinely needs the file.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any

from media_library.config import get_settings
from media_library.db import reader
from media_library.paths import (
    breadcrumbs,
    format_size,
    library_root,
    quote_path,
    relative_to_root,
    resolve_library_path,
    stable_id,
)

_SORTS = {
    "name_asc": "LOWER(f.name) ASC",
    "name_desc": "LOWER(f.name) DESC",
    "date_desc": "f.mtime DESC",
    "date_asc": "f.mtime ASC",
    "size_desc": "f.size DESC",
    "size_asc": "f.size ASC",
}


def _thumb_url(path: str, version: float | None) -> str:
    url = f"/api/thumbnails?path={quote_path(path)}"
    if version:
        url += f"&v={int(version)}"
    return url


def _file_entry(row: sqlite3.Row) -> dict[str, Any]:
    settings = get_settings()
    path = row["path"]
    is_video = row["type"] == "video"
    ext = (row["ext"] or "").lower()
    playable = (not is_video) or ext in settings.native_video_exts
    thumb_mtime = row["thumb_mtime"] if "thumb_mtime" in row.keys() else None
    quoted = quote_path(path)
    transcode_url = (
        f"/api/media/transcode?path={quoted}" if is_video and not playable else None
    )
    return {
        "id": stable_id(path),
        "name": row["name"],
        "path": path,
        "folder_path": row["folder_path"],
        "type": row["type"],
        "ext": ext,
        "size": row["size"] or 0,
        "size_formatted": row["size_formatted"] or format_size(row["size"]),
        "mtime": row["mtime"],
        "width": row["width"] if "width" in row.keys() else None,
        "height": row["height"] if "height" in row.keys() else None,
        "duration": row["duration"] if "duration" in row.keys() else None,
        "video_codec": row["video_codec"] if "video_codec" in row.keys() else None,
        "audio_codec": row["audio_codec"] if "audio_codec" in row.keys() else None,
        "fps": row["fps"] if "fps" in row.keys() else None,
        "capture_time": (row["capture_time"] if "capture_time" in row.keys() else None),
        "has_thumb": bool(row["has_thumb"]) if "has_thumb" in row.keys() else False,
        "has_animated": bool(row["has_animated"])
        if "has_animated" in row.keys()
        else False,
        "is_favorite": bool(row["is_favorite"])
        if "is_favorite" in row.keys()
        else False,
        "needs_transcode": bool(row["needs_transcode"]),
        "playable": playable,
        "thumb_url": _thumb_url(path, thumb_mtime),
        "animated_url": (
            f"/api/thumbnails?path={quote_path(path)}&kind=animated"
            if is_video
            else None
        ),
        "media_url": f"/api/media?path={quoted}",
        "transcode_url": transcode_url,
    }


def _folder_entry(row: sqlite3.Row) -> dict[str, Any]:
    is_zip = bool(row["is_zip"]) if "is_zip" in row.keys() else False
    name = row["name"] or ""
    cover = row["cover_path"] if "cover_path" in row.keys() else None
    return {
        "name": Path(name).stem if is_zip else name,
        "raw_name": name,
        "path": row["path"],
        "file_count": row["file_count"] or 0,
        "subfolder_count": row["subfolder_count"] or 0,
        "is_zip": is_zip,
        "total_size": row["total_size"] or 0,
        "size_formatted": format_size(row["total_size"]),
        "cover_thumb_url": (
            _thumb_url(cover, row["cover_mtime"])
            if cover and row["cover_mtime"]
            else None
        ),
        "cover_width": row["cover_w"] if "cover_w" in row.keys() else None,
        "cover_height": row["cover_h"] if "cover_h" in row.keys() else None,
    }


_FOLDER_SQL = """
SELECT fo.path, fo.name, fo.file_count, fo.subfolder_count, fo.is_zip,
       fo.total_size,
       cov.path  AS cover_path,
       cov.mtime AS cover_mtime,
       cov.width AS cover_w,
       cov.height AS cover_h
FROM folders fo
LEFT JOIN files cov ON cov.path = COALESCE(
    (SELECT f.path FROM files f
      WHERE f.folder_path = fo.path AND f.type = 'image' AND f.has_thumb = 1
      ORDER BY LOWER(f.name) LIMIT 1),
    (SELECT f.path FROM files f
      WHERE f.folder_path = fo.path AND f.type = 'video' AND f.has_thumb = 1
      ORDER BY LOWER(f.name) LIMIT 1)
)
WHERE fo.parent_path = ?
ORDER BY LOWER(fo.name)
"""


def _folder_row(conn: sqlite3.Connection, path: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT path, name, file_count, is_zip FROM folders WHERE path = ?", (path,)
    ).fetchone()


def _is_browsable(target: str) -> bool:
    """A directory, or an indexed ZIP archive acting as a virtual folder."""
    if os.path.isdir(target):
        return True
    if target.lower().endswith(".zip"):
        with reader() as conn:
            return _folder_row(conn, target) is not None
    return False


def _clamp_to_directory(target: str) -> str:
    """If ``target`` is not a directory, browse its parent instead."""
    if os.path.isdir(target):
        return target
    parent = os.path.dirname(target)
    root = library_root()
    if parent and parent != target and (parent == root or parent.startswith(root)):
        return parent
    return root


def browse(
    raw_path: str | None,
    offset: int = 0,
    limit: int | None = None,
    type_filter: str | None = None,
    favorites_only: bool = False,
    sort: str = "name_asc",
) -> dict:
    """Return subfolders, media files and breadcrumbs for one folder.

    ``type_filter`` (``video``/``image``), ``favorites_only`` and ``sort``
    narrow the media list only; the folder rows stay the full child listing.
    """
    settings = get_settings()
    root = library_root()
    target = resolve_library_path(raw_path)
    if not _is_browsable(target):
        target = _clamp_to_directory(target)
    capped_limit = max(1, min(limit or settings.browse_limit, 500))
    capped_offset = max(0, offset)

    where = ["f.folder_path = ?"]
    params: list[Any] = [target]
    if type_filter in ("video", "image"):
        where.append("f.type = ?")
        params.append(type_filter)
    if favorites_only:
        where.append("fav.path IS NOT NULL")
    where_sql = " AND ".join(where)
    order_sql = _SORTS.get(sort, _SORTS["name_asc"])

    with reader() as conn:
        folder_row = _folder_row(conn, target)
        folder_rows = conn.execute(_FOLDER_SQL, (target,)).fetchall()
        file_rows = conn.execute(
            f"""
            SELECT f.*, CASE WHEN fav.path IS NOT NULL THEN 1 ELSE 0 END
                   AS is_favorite
            FROM files f
            LEFT JOIN favorites fav ON f.path = fav.path
            WHERE {where_sql}
            ORDER BY {order_sql}
            LIMIT ? OFFSET ?
            """,
            params + [capped_limit + 1, capped_offset],
        ).fetchall()
        total = conn.execute(
            f"""
            SELECT COUNT(*) AS n
            FROM files f
            LEFT JOIN favorites fav ON f.path = fav.path
            WHERE {where_sql}
            """,
            params,
        ).fetchone()["n"]

    has_more = len(file_rows) > capped_limit
    files = file_rows[:capped_limit]

    return {
        "path": target,
        "root": root,
        "name": os.path.basename(target.rstrip("/")) or "Library",
        "breadcrumbs": breadcrumbs(target),
        "folders": [_folder_entry(r) for r in folder_rows],
        "files": [_file_entry(r) for r in files],
        "total": total,
        "offset": capped_offset,
        "limit": capped_limit,
        "next_offset": capped_offset + len(files) if has_more else None,
        "indexed": folder_row is not None,
        "is_zip": bool(folder_row["is_zip"])
        if folder_row
        else target.lower().endswith(".zip"),
    }


def list_folders(raw_path: str | None) -> dict:
    """Return only a folder's immediate children (sidebar tree, FUSE-safe)."""
    root = library_root()
    target = resolve_library_path(raw_path)
    if not _is_browsable(target):
        target = _clamp_to_directory(target)
    with reader() as conn:
        rows = conn.execute(_FOLDER_SQL, (target,)).fetchall()
    return {
        "path": target,
        "root": root,
        "folders": [_folder_entry(r) for r in rows],
    }


def search_folders(query: str, limit: int = 20) -> dict:
    """Find folders by name anywhere in the library (command palette jumps)."""
    words = query.strip().lower().split()
    capped = max(1, min(limit, 50))
    if not words:
        return {"query": query, "results": []}
    where = " AND ".join("LOWER(fo.name) LIKE ?" for _ in words)
    params: list[Any] = [f"%{word}%" for word in words]
    with reader() as conn:
        rows = conn.execute(
            f"""
            SELECT fo.path, fo.name, fo.file_count, fo.subfolder_count,
                   fo.is_zip, fo.total_size,
                   NULL AS cover_path, NULL AS cover_mtime,
                   NULL AS cover_w, NULL AS cover_h
            FROM folders fo
            WHERE {where}
            ORDER BY LOWER(fo.name)
            LIMIT ?
            """,
            params + [capped],
        ).fetchall()
    return {"query": query, "results": [_folder_entry(r) for r in rows]}


def search(
    query: str,
    type_filter: str | None = None,
    sort: str = "name_asc",
    limit: int | None = None,
    offset: int = 0,
) -> dict:
    """Multi-word search over filenames and library-relative folder paths."""
    settings = get_settings()
    root = library_root()
    capped_limit = max(1, min(limit or settings.search_limit, 2000))
    capped_offset = max(0, offset)
    words = query.strip().lower().split()
    if not words:
        return {
            "query": query,
            "total": 0,
            "limit": capped_limit,
            "offset": capped_offset,
            "truncated": False,
            "results": [],
        }

    rel_offset = len(root) + 1
    clauses: list[str] = []
    for _ in words:
        clauses.append(
            "(LOWER(f.name) LIKE ? OR LOWER(substr(f.folder_path, ?)) LIKE ?)"
        )
    where_sql = " AND ".join(clauses)
    word_params: list[Any] = []
    for word in words:
        word_params.extend([f"%{word}%", rel_offset, f"%{word}%"])

    type_sql = ""
    if type_filter in ("video", "image"):
        type_sql = " AND f.type = ?"
    order_sql = _SORTS.get(sort, _SORTS["name_asc"])

    base_from = f"FROM files f LEFT JOIN favorites fav ON f.path = fav.path WHERE {where_sql}{type_sql}"
    count_params = word_params + ([type_filter] if type_sql else [])

    with reader() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) AS n {base_from}", count_params
        ).fetchone()["n"]
        rows = conn.execute(
            f"""
            SELECT f.*, CASE WHEN fav.path IS NOT NULL THEN 1 ELSE 0 END
                   AS is_favorite
            {base_from}
            ORDER BY {order_sql}
            LIMIT ? OFFSET ?
            """,
            count_params + [capped_limit, capped_offset],
        ).fetchall()

    results = []
    for row in rows:
        entry = _file_entry(row)
        folder_path = row["folder_path"]
        entry["folder_name"] = os.path.basename(folder_path)
        entry["rel_folder"] = relative_to_root(folder_path) or "/"
        results.append(entry)

    return {
        "query": query,
        "total": total,
        "limit": capped_limit,
        "offset": capped_offset,
        "truncated": total > capped_offset + len(results),
        "results": results,
    }


def counts() -> dict[str, int]:
    """Global library totals from one aggregate plus two small counts."""
    with reader() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS files,
                   COALESCE(SUM(type = 'video'), 0) AS videos,
                   COALESCE(SUM(type = 'image'), 0) AS images
            FROM files
            """
        ).fetchone()
        folders = conn.execute("SELECT COUNT(*) AS n FROM folders").fetchone()["n"]
        favorites = conn.execute("SELECT COUNT(*) AS n FROM favorites").fetchone()["n"]
    return {
        "files": row["files"] or 0,
        "folders": folders or 0,
        "videos": row["videos"] or 0,
        "images": row["images"] or 0,
        "favorites": favorites or 0,
    }


def favorites(type_filter: str | None = None) -> dict:
    """List favorited media, newest star first, plus global counts."""
    counts_payload = counts()
    type_sql = ""
    params: list[Any] = []
    if type_filter in ("video", "image"):
        type_sql = " AND f.type = ?"
        params.append(type_filter)
    with reader() as conn:
        rows = conn.execute(
            f"""
            SELECT f.*, 1 AS is_favorite
            FROM favorites fav
            JOIN files f ON f.path = fav.path
            WHERE 1=1{type_sql}
            ORDER BY fav.created_at DESC
            """,
            params,
        ).fetchall()
    return {
        "counts": counts_payload,
        "files": [_file_entry(r) for r in rows],
    }


def shape_file_row(row) -> dict:
    """Public row shaper (analytics reuses the browse entry shape)."""
    return _file_entry(row)


def get_file(path: str) -> dict | None:
    """One shaped file entry by path, or None when it is not indexed."""
    with reader() as conn:
        row = conn.execute(
            """
            SELECT f.*, CASE WHEN fav.path IS NOT NULL THEN 1 ELSE 0 END
                   AS is_favorite
            FROM files f
            LEFT JOIN favorites fav ON f.path = fav.path
            WHERE f.path = ?
            """,
            (path,),
        ).fetchone()
    return _file_entry(row) if row else None
