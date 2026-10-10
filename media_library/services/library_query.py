"""Index-only data shaping for Media Library browsing.

Holds the row -> dict builders that turn indexed ``files``/``folders`` rows into
the payload shapes the frontend consumes, shared by the library browse and
folder-tree endpoints. All reads are index-only (never stats remote files).
"""

import logging
import urllib.parse
from pathlib import Path
from typing import Any

from media_library.core.constants import NATIVE_EXTENSIONS, SQL_IN_CHUNK

logger = logging.getLogger(__name__)


def _build_file_entry(r: Any) -> dict[str, Any]:
    ext = r["ext"]
    is_vid = r["type"] == "video"
    is_native = ext in NATIVE_EXTENSIONS
    fpath = r["path"]
    media_url = (
        f"/api/backup/media?path={urllib.parse.quote(fpath)}"
        if (not is_vid or is_native)
        else f"/api/backup/transcode_stream?path={urllib.parse.quote(fpath)}"
    )
    fmtime = r["mtime"]
    fsize = r["size"]
    thumb_query = f"?path={urllib.parse.quote(fpath)}"
    if fmtime is not None and fsize is not None:
        thumb_query += f"&mtime={fmtime}&size={fsize}"
    return {
        "name": r["name"],
        "path": fpath,
        "type": r["type"],
        "ext": ext,
        "size": fsize,
        "size_formatted": r["size_formatted"],
        "mtime": fmtime,
        "needs_transcode": bool(r["needs_transcode"]),
        "has_thumb": bool(r["has_thumb"]) if "has_thumb" in r.keys() else False,
        "width": r["width"] if "width" in r.keys() else None,
        "height": r["height"] if "height" in r.keys() else None,
        "duration": r["duration"] if "duration" in r.keys() else None,
        "video_codec": r["video_codec"] if "video_codec" in r.keys() else None,
        "audio_codec": r["audio_codec"] if "audio_codec" in r.keys() else None,
        "fps": r["fps"] if "fps" in r.keys() else None,
        "capture_time": r["capture_time"] if "capture_time" in r.keys() else None,
        "thumb_url": f"/api/backup/thumbnail{thumb_query}",
        "media_url": media_url,
    }


def _read_indexed_files(conn, folder_path: str) -> list[dict[str, Any]]:
    cur = conn.execute(
        "SELECT * FROM files WHERE folder_path = ? ORDER BY LOWER(name) ASC",
        (folder_path,),
    )
    return [_build_file_entry(r) for r in cur.fetchall()]


def _read_indexed_subfolders(conn, folder_path: str) -> list[dict[str, Any]]:
    subfolders = []
    cur = conn.execute(
        "SELECT path, name, is_zip, file_count, subfolder_count, total_size FROM folders "
        "WHERE parent_path = ? ORDER BY LOWER(name) ASC",
        (folder_path,),
    )
    for sr in cur.fetchall():
        is_z = (
            bool(sr["is_zip"])
            if "is_zip" in sr.keys()
            else sr["path"].lower().endswith(".zip")
        )
        clean_n = Path(sr["name"]).stem if is_z else sr["name"]
        subfolders.append(
            {
                "name": clean_n,
                "raw_name": sr["name"],
                "path": sr["path"],
                "is_zip": is_z,
                "file_count": sr["file_count"] or 0,
                "subfolder_count": sr["subfolder_count"] or 0,
                "total_size": sr["total_size"] if "total_size" in sr.keys() else 0,
            }
        )
    return subfolders


def _query_first_child_cover(
    conn, folder_paths: list[str], media_type: str
) -> dict[str, Any]:
    """Return ``{folder_path: row}`` for each folder's alphabetically-first child
    of ``media_type``, read index-only (never stats remote files)."""
    covers: dict[str, Any] = {}
    # Chunk to stay well under SQLite's bound-variable limit.
    for start in range(0, len(folder_paths), SQL_IN_CHUNK):
        chunk = folder_paths[start : start + SQL_IN_CHUNK]
        placeholders = ",".join("?" for _ in chunk)
        try:
            cur = conn.execute(
                f"""
                SELECT f.folder_path, f.path, f.mtime, f.size, f.has_thumb, f.width, f.height
                FROM files f
                WHERE f.type = ? AND f.folder_path IN ({placeholders})
                  AND LOWER(f.name) = (
                      SELECT MIN(LOWER(f2.name)) FROM files f2
                      WHERE f2.folder_path = f.folder_path AND f2.type = ?
                  )
                """,
                [media_type, *chunk, media_type],
            )
            for r in cur.fetchall():
                covers.setdefault(r["folder_path"], r)
        except Exception as e:
            logger.error(f"Error reading folder covers ({media_type}): {e}")
    return covers


def _attach_folder_covers(conn, folders: list[dict[str, Any]]) -> None:
    """Attach a representative cover thumbnail to each folder entry.

    The cover is the alphabetically-first direct-child image (``LOWER(name)``
    ASC); when a folder has no image at all, the first direct-child video's
    poster frame is used instead. All reads are index-only. Folders with no media
    — or not yet indexed — get ``thumb_url = None`` / ``has_thumb = False`` and
    fall back to an icon on the client. Mutates ``folders`` in place; FUSE-safe
    (never stats remote files).
    """
    paths = [f.get("path") for f in folders if f.get("path")]
    if not paths:
        for f in folders:
            f["thumb_url"] = None
            f["has_thumb"] = False
            f["width"] = None
            f["height"] = None
        return

    covers = _query_first_child_cover(conn, paths, "image")
    missing = [p for p in paths if p not in covers]
    if missing:
        covers.update(_query_first_child_cover(conn, missing, "video"))

    for f in folders:
        r = covers.get(f.get("path"))
        if not r:
            f["thumb_url"] = None
            f["has_thumb"] = False
            f["width"] = None
            f["height"] = None
            continue
        q = f"?path={urllib.parse.quote(r['path'])}"
        if r["mtime"] is not None and r["size"] is not None:
            q += f"&mtime={r['mtime']}&size={r['size']}"
        f["thumb_url"] = f"/api/backup/thumbnail{q}"
        f["has_thumb"] = bool(r["has_thumb"]) if "has_thumb" in r.keys() else False
        f["width"] = r["width"] if "width" in r.keys() else None
        f["height"] = r["height"] if "height" in r.keys() else None
