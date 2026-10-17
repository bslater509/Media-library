"""Filesystem scanning: folder entries, ZIP archives and folder indexing."""

import hashlib
import logging
import os
import time
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
    state,
)
from media_library.db.connection import db_conn

logger = logging.getLogger(__name__)

from media_library.db.indexer_state import indexer_lock, indexer_state  # noqa: F401


def _scan_root_dirs(path: str) -> list[str]:
    root_dirs = []
    try:
        with os.scandir(path) as entries:
            for entry in entries:
                if entry.name.startswith("."):
                    continue
                if (
                    entry.is_dir(follow_symlinks=False)
                    or entry.is_file(follow_symlinks=False)
                    and entry.name.lower().endswith(".zip")
                ):
                    root_dirs.append(entry.path)
        root_dirs.sort(key=lambda x: os.path.basename(x).lower())
    except Exception as e:
        logger.error(f"Error scanning root dirs from {path}: {e}")
    return root_dirs


def _index_zip_archive(real_folder: str, base_dir: str) -> dict[str, Any]:
    try:
        from media_library.services.zip import (
            get_zip_metadata,
            index_zip_archive,
        )

        parent_p = os.path.dirname(real_folder) if real_folder != base_dir else None
        index_zip_archive(real_folder, parent_path=parent_p)
        meta = get_zip_metadata(real_folder)
        return {
            "file_count": meta["total_files"],
            "subfolder_count": 0,
            "subfolders": [],
        }
    except Exception as ze:
        logger.error(f"Error indexing zip archive {real_folder}: {ze}")
        return {"file_count": 0, "subfolder_count": 0, "subfolders": []}


def _scan_folder_entries(real_folder: str) -> tuple[list[dict[str, Any]], list[tuple]]:
    subfolders = []
    files = []
    try:
        with os.scandir(real_folder) as entries:
            for entry in entries:
                if entry.name.startswith("."):
                    continue
                try:
                    if entry.is_dir(follow_symlinks=False):
                        subfolders.append({"path": entry.path, "name": entry.name})
                    elif entry.is_file(
                        follow_symlinks=False
                    ) and entry.name.lower().endswith(".zip"):
                        try:
                            from media_library.services.zip import index_zip_archive

                            index_zip_archive(entry.path, parent_path=real_folder)
                            subfolders.append({"path": entry.path, "name": entry.name})
                        except Exception as ze:
                            logger.error(
                                f"Error indexing nested zip {entry.path}: {ze}"
                            )
                    elif entry.is_file(follow_symlinks=False):
                        ext = os.path.splitext(entry.name)[1].lower()
                        is_vid = ext in VIDEO_EXTENSIONS
                        is_img = ext in IMAGE_EXTENSIONS
                        if is_vid or is_img:
                            st = entry.stat()
                            key = f"{entry.path}_{st.st_mtime}_{st.st_size}"
                            h = hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
                            has_thumb = (
                                1
                                if (THUMBNAIL_CACHE_DIR / f"thumb_{h}.jpg").exists()
                                else 0
                            )
                            files.append(
                                (
                                    entry.path,
                                    entry.name,
                                    real_folder,
                                    "video" if is_vid else "image",
                                    ext,
                                    st.st_size,
                                    format_size(st.st_size),
                                    st.st_mtime,
                                    1
                                    if (is_vid and ext not in NATIVE_EXTENSIONS)
                                    else 0,
                                    has_thumb,
                                )
                            )
                except (PermissionError, OSError):
                    continue
    except Exception as e:
        logger.error(f"Error scanning folder for index {real_folder}: {e}")
    return subfolders, files


def _update_folder_in_db(
    real_folder: str,
    folder_name: str,
    parent_path: str | None,
    subfolders: list[dict[str, Any]],
    files: list[tuple],
) -> None:
    try:
        with db_conn() as conn:
            existing = {}
            try:
                cur = conn.execute(
                    "SELECT path, webp_optimized, quick_hash, thumb_hash, size, mtime, "
                    "width, height, duration, video_codec, audio_codec, fps, capture_time "
                    "FROM files WHERE folder_path = ?",
                    (real_folder,),
                )
                for r in cur.fetchall():
                    existing[r["path"]] = r
            except Exception:
                pass

            folder_total_size = sum(f[5] for f in files)

            try:
                dir_mtime = os.stat(real_folder).st_mtime
            except OSError:
                dir_mtime = None

            with conn:
                conn.execute(
                    """
                INSERT INTO folders (path, name, parent_path, file_count, subfolder_count, last_scanned, total_size, dir_mtime)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(path) DO UPDATE SET
                    name=excluded.name,
                    parent_path=excluded.parent_path,
                    file_count=excluded.file_count,
                    subfolder_count=excluded.subfolder_count,
                    last_scanned=excluded.last_scanned,
                    total_size=excluded.total_size,
                    dir_mtime=excluded.dir_mtime
                """,
                    (
                        real_folder,
                        folder_name,
                        parent_path,
                        len(files),
                        len(subfolders),
                        time.time(),
                        folder_total_size,
                        dir_mtime,
                    ),
                )

                conn.execute("DELETE FROM files WHERE folder_path = ?", (real_folder,))
                if files:
                    file_rows = []
                    for f in files:
                        (
                            fpath,
                            fname,
                            fdir,
                            ftype,
                            fext,
                            fsize,
                            fsize_fmt,
                            fmtime,
                            ftrans,
                            fthumb,
                        ) = f
                        prev = existing.get(fpath)
                        unchanged = (
                            prev is not None
                            and prev["size"] == fsize
                            and prev["mtime"] == fmtime
                        )
                        if unchanged:
                            prev_opt = prev["webp_optimized"]
                            opt_val = (
                                prev_opt
                                if (prev_opt not in (None, 0))
                                else (1 if fext == ".webp" else 0)
                            )
                            quick_hash = prev["quick_hash"]
                            thumb_hash = prev["thumb_hash"]
                            width = prev["width"]
                            height = prev["height"]
                            duration = prev["duration"]
                            video_codec = prev["video_codec"]
                            audio_codec = prev["audio_codec"]
                            fps = prev["fps"]
                            capture_time = prev["capture_time"]
                        else:
                            opt_val = 1 if fext == ".webp" else 0
                            quick_hash = None
                            thumb_hash = None
                            width = None
                            height = None
                            duration = None
                            video_codec = None
                            audio_codec = None
                            fps = None
                            capture_time = None
                        file_rows.append(
                            (
                                fpath,
                                fname,
                                fdir,
                                ftype,
                                fext,
                                fsize,
                                fsize_fmt,
                                fmtime,
                                ftrans,
                                fthumb,
                                opt_val,
                                quick_hash,
                                thumb_hash,
                                width,
                                height,
                                duration,
                                video_codec,
                                audio_codec,
                                fps,
                                capture_time,
                            )
                        )

                    conn.executemany(
                        """
                    INSERT INTO files (path, name, folder_path, type, ext, size, size_formatted, mtime, needs_transcode, has_thumb, webp_optimized, quick_hash, thumb_hash, width, height, duration, video_codec, audio_codec, fps, capture_time)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                        file_rows,
                    )
    except Exception as e:
        logger.error(f"DB error indexing {real_folder}: {e}")


def index_folder(folder_path: str, base_dir: str | None = None) -> dict[str, Any]:
    if not base_dir:
        base_dir = os.path.realpath(
            state.get("destination_dir") or DEFAULT_DESTINATION_DIR
        )
    real_folder = os.path.realpath(folder_path)

    if os.path.isfile(real_folder) and real_folder.lower().endswith(".zip"):
        return _index_zip_archive(real_folder, base_dir)

    if not os.path.exists(real_folder) or not os.path.isdir(real_folder):
        return {"file_count": 0, "subfolder_count": 0, "subfolders": []}

    folder_name = os.path.basename(real_folder)
    parent_path = os.path.dirname(real_folder) if real_folder != base_dir else None

    subfolders, files = _scan_folder_entries(real_folder)
    _update_folder_in_db(real_folder, folder_name, parent_path, subfolders, files)

    return {
        "file_count": len(files),
        "subfolder_count": len(subfolders),
        "subfolders": subfolders,
    }
