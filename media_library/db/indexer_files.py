"""Per-file index mutations (add, update, move, remove)."""

import asyncio
import hashlib
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from media_library.core.config import (
    DEFAULT_DESTINATION_DIR,
    THUMBNAIL_CACHE_DIR,
)
from media_library.core.constants import (
    IMAGE_EXTENSIONS,
    NATIVE_EXTENSIONS,
    VIDEO_EXTENSIONS,
)
from media_library.core.state import (
    format_size,
    get_thumbnail_cache_path,
    state,
)
from media_library.db.connection import db_conn

logger = logging.getLogger(__name__)

from media_library.db.indexer_state import indexer_lock, indexer_state  # noqa: F401


def add_file_to_index(file_path: str):
    try:
        if not os.path.exists(file_path) or not os.path.isfile(file_path):
            return
        ext = os.path.splitext(file_path)[1].lower()
        is_vid = ext in VIDEO_EXTENSIONS
        is_img = ext in IMAGE_EXTENSIONS
        if not is_vid and not is_img:
            return
        folder_path = os.path.dirname(file_path)
        fname = os.path.basename(file_path)
        st = os.stat(file_path)

        with db_conn() as conn, conn:
            has_thumb = 1 if get_thumbnail_cache_path(file_path).exists() else 0
            conn.execute(
                """
            INSERT OR REPLACE INTO files (path, name, folder_path, type, ext, size, size_formatted, mtime, needs_transcode, has_thumb)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    file_path,
                    fname,
                    folder_path,
                    "video" if is_vid else "image",
                    ext,
                    st.st_size,
                    format_size(st.st_size),
                    st.st_mtime,
                    1 if (is_vid and ext not in NATIVE_EXTENSIONS) else 0,
                    has_thumb,
                ),
            )
            cur = conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(size), 0) FROM files WHERE folder_path = ?",
                (folder_path,),
            )
            cnt, total = cur.fetchone()
            conn.execute(
                "UPDATE folders SET file_count = ?, total_size = ?, last_scanned = ? WHERE path = ?",
                (cnt, total, time.time(), folder_path),
            )
    except Exception as e:
        logger.error(f"Error adding {file_path} to index: {e}")

def remove_file_from_index(file_path: str):
    try:
        folder_path = os.path.dirname(file_path)
        with db_conn() as conn, conn:
            conn.execute("DELETE FROM files WHERE path = ?", (file_path,))
            conn.execute(
                "DELETE FROM ignored_duplicates WHERE path1 = ? OR path2 = ?",
                (file_path, file_path),
            )
            cur = conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(size), 0) FROM files WHERE folder_path = ?",
                (folder_path,),
            )
            cnt, total = cur.fetchone()
            conn.execute(
                "UPDATE folders SET file_count = ?, total_size = ?, last_scanned = ? WHERE path = ?",
                (cnt, total, time.time(), folder_path),
            )
    except Exception as e:
        logger.error(f"Error removing {file_path} from index: {e}")

def update_file_in_index(
    old_path: str,
    new_path: str,
    new_name: str,
    new_size: int | None = None,
    new_ext: str | None = None,
    preserve_hashes: bool = True,
):
    try:
        folder_path = os.path.dirname(new_path)
        old_folder = os.path.dirname(old_path)
        ext = new_ext or os.path.splitext(new_name)[1].lower()
        is_vid = ext in VIDEO_EXTENSIONS
        disk_path = new_path
        if not os.path.exists(disk_path):
            try:
                from media_library.services.zip import resolve_zip_file_path

                res = resolve_zip_file_path(new_path)
                if res and os.path.exists(res):
                    disk_path = res
            except Exception:
                pass
        st_size = (
            new_size
            if new_size is not None
            else (os.path.getsize(disk_path) if os.path.exists(disk_path) else 0)
        )
        mtime = (
            os.path.getmtime(disk_path) if os.path.exists(disk_path) else time.time()
        )

        with db_conn() as conn:
            quick_hash = None
            thumb_hash = None
            webp_optimized = 0
            width = None
            height = None
            duration = None
            video_codec = None
            audio_codec = None
            fps = None
            capture_time = None
            if preserve_hashes:
                try:
                    row = conn.execute(
                        "SELECT quick_hash, thumb_hash, webp_optimized, width, height, "
                        "duration, video_codec, audio_codec, fps, capture_time "
                        "FROM files WHERE path = ?",
                        (old_path,),
                    ).fetchone()
                    if row:
                        quick_hash = row["quick_hash"]
                        thumb_hash = row["thumb_hash"]
                        webp_optimized = row["webp_optimized"] or 0
                        width = row["width"]
                        height = row["height"]
                        duration = row["duration"]
                        video_codec = row["video_codec"]
                        audio_codec = row["audio_codec"]
                        fps = row["fps"]
                        capture_time = row["capture_time"]
                except Exception:
                    quick_hash = None
                    thumb_hash = None
                    webp_optimized = 0
                    width = None
                    height = None
                    duration = None
                    video_codec = None
                    audio_codec = None
                    fps = None
                    capture_time = None

            with conn:
                conn.execute("DELETE FROM files WHERE path = ?", (old_path,))
                has_thumb = 1 if get_thumbnail_cache_path(new_path).exists() else 0
                conn.execute(
                    """
                INSERT OR REPLACE INTO files (path, name, folder_path, type, ext, size, size_formatted, mtime, needs_transcode, has_thumb, quick_hash, thumb_hash, webp_optimized, width, height, duration, video_codec, audio_codec, fps, capture_time)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        new_path,
                        new_name,
                        folder_path,
                        "video" if is_vid else "image",
                        ext,
                        st_size,
                        format_size(st_size),
                        mtime,
                        1 if (is_vid and ext not in NATIVE_EXTENSIONS) else 0,
                        has_thumb,
                        quick_hash,
                        thumb_hash,
                        webp_optimized,
                        width,
                        height,
                        duration,
                        video_codec,
                        audio_codec,
                        fps,
                        capture_time,
                    ),
                )
                cur = conn.execute(
                    "SELECT COUNT(*), COALESCE(SUM(size), 0) FROM files WHERE folder_path = ?",
                    (folder_path,),
                )
                cnt, total = cur.fetchone()
                conn.execute(
                    "UPDATE folders SET file_count = ?, total_size = ?, last_scanned = ? WHERE path = ?",
                    (cnt, total, time.time(), folder_path),
                )
                if old_folder != folder_path:
                    cur_old = conn.execute(
                        "SELECT COUNT(*), COALESCE(SUM(size), 0) FROM files WHERE folder_path = ?",
                        (old_folder,),
                    )
                    old_cnt, old_total = cur_old.fetchone()
                    conn.execute(
                        "UPDATE folders SET file_count = ?, total_size = ?, last_scanned = ? WHERE path = ?",
                        (old_cnt, old_total, time.time(), old_folder),
                    )
    except Exception as e:
        logger.error(f"Error updating {old_path} -> {new_path} in index: {e}")

def move_path_in_index(old_path: str, new_path: str, kind: str):
    """Rewrites the SQLite index when a library path is moved or renamed.

    ``kind`` is one of:

    - ``"file"``   — a single media file (delegates to ``update_file_in_index``).
    - ``"zip"``    — a ZIP archive (a virtual folder in the index) plus its members.
    - ``"folder"`` — a real directory plus all descendant folders/files.

    Uses prefix replacement on the ``path`` / ``folder_path`` / ``parent_path``
    columns so a whole subtree can be relocated without rescanning the (possibly
    remote) filesystem.
    """
    try:
        with db_conn() as conn, conn:
            if kind == "file":
                update_file_in_index(old_path, new_path, os.path.basename(new_path))
                return

            old_prefix = old_path + os.sep
            old_len = len(old_path) + 1
            new_name = os.path.basename(new_path)
            new_parent = os.path.dirname(new_path)

            # The path's own folder row (for both zip and real folders).
            conn.execute(
                "UPDATE folders SET path = ?, name = ?, parent_path = ? WHERE path = ?",
                (new_path, new_name, new_parent, old_path),
            )

            if kind == "zip":
                # Members are stored with folder_path == zip path and path == "zip/inner".
                conn.execute(
                    "UPDATE files SET folder_path = ? WHERE folder_path = ?",
                    (new_path, old_path),
                )
                conn.execute(
                    "UPDATE files SET path = ? || substr(path, ?) WHERE path LIKE ?",
                    (new_path, old_len, old_prefix + "%"),
                )
            elif kind == "folder":
                # Descendant folder rows and their parent pointers.
                conn.execute(
                    "UPDATE folders SET path = ? || substr(path, ?) WHERE path LIKE ?",
                    (new_path, old_len, old_prefix + "%"),
                )
                conn.execute(
                    "UPDATE folders SET parent_path = ? || substr(parent_path, ?) "
                    "WHERE parent_path = ? OR parent_path LIKE ?",
                    (new_path, old_len, old_path, old_prefix + "%"),
                )
                # All files under the moved subtree.
                conn.execute(
                    "UPDATE files SET path = ? || substr(path, ?) WHERE path = ? OR path LIKE ?",
                    (new_path, old_len, old_path, old_prefix + "%"),
                )
                conn.execute(
                    "UPDATE files SET folder_path = ? || substr(folder_path, ?) "
                    "WHERE folder_path = ? OR folder_path LIKE ?",
                    (new_path, old_len, old_path, old_prefix + "%"),
                )
    except Exception as e:
        logger.error(f"Error moving {old_path} -> {new_path} in index: {e}")

def delete_path_from_index(path: str, kind: str):
    """Removes a path (and, for folders/ZIPs, its contents) from the SQLite index."""
    try:
        with db_conn() as conn, conn:
            if kind == "file":
                conn.execute("DELETE FROM files WHERE path = ?", (path,))
                conn.execute(
                    "DELETE FROM ignored_duplicates WHERE path1 = ? OR path2 = ?",
                    (path, path),
                )
                folder_path = os.path.dirname(path)
                cur = conn.execute(
                    "SELECT COUNT(*), COALESCE(SUM(size), 0) FROM files WHERE folder_path = ?",
                    (folder_path,),
                )
                cnt, total_size = cur.fetchone()
                conn.execute(
                    "UPDATE folders SET file_count = ?, total_size = ?, last_scanned = ? WHERE path = ?",
                    (cnt, total_size, time.time(), folder_path),
                )
            elif kind == "zip":
                conn.execute("DELETE FROM folders WHERE path = ?", (path,))
                conn.execute("DELETE FROM files WHERE folder_path = ?", (path,))
            elif kind == "folder":
                prefix = path + os.sep
                conn.execute(
                    "DELETE FROM folders WHERE path = ? OR path LIKE ?",
                    (path, prefix + "%"),
                )
                conn.execute(
                    "DELETE FROM files WHERE folder_path = ? OR folder_path LIKE ?",
                    (path, prefix + "%"),
                )
    except Exception as e:
        logger.error(f"Error deleting {path} from index: {e}")
