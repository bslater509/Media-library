"""Media library filesystem scanning, folder indexing, and pruning."""

import asyncio
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
    get_thumbnail_cache_path,
    state,
)
from media_library.db.connection import db_conn

logger = logging.getLogger(__name__)

# Indexer State & Background Crawler
indexer_state: dict[str, Any] = {
    "status": "idle",  # "crawling" | "idle"
    "scanned_folders": 0,
    "skipped_folders": 0,
    "total_folders": 0,
    "scanned_files": 0,
    "total_files": 0,
    "current_folder": "",
    "last_synced": None,
}
indexer_lock = asyncio.Lock()


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

def _update_folder_in_db(real_folder: str, folder_name: str, parent_path: str | None, subfolders: list[dict[str, Any]], files: list[tuple]) -> None:
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
                    "SELECT COUNT(*) FROM files WHERE folder_path = ?", (folder_path,)
                )
                cnt = cur.fetchone()[0]
                conn.execute(
                    "UPDATE folders SET file_count = ?, last_scanned = ? WHERE path = ?",
                    (cnt, time.time(), folder_path),
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


def _path_confirmed_gone(path: str, attempts: int = 3, delay: float = 0.15) -> bool:
    """Return True only when a path is consistently reported missing on disk.

    Remote FUSE mounts (pCloud, rclone, NFS) can transiently fail ``stat`` calls
    while reconnecting. A single failed existence check must never be treated as
    "the file was deleted", otherwise a brief mount hiccup wipes the index (this
    is exactly what emptied ~48k files once). Retry a few times before
    concluding the path is gone.
    """
    for attempt in range(attempts):
        try:
            if os.path.isdir(path) or os.path.isfile(path):
                return False
        except OSError:
            pass
        if attempt < attempts - 1:
            time.sleep(delay)
    return True


def _get_stale_folders(db_folders: list[str], resolved_base: str, visited_folders: set[str] | None) -> tuple[list[str], list[str]]:
    candidate_stale = []
    under_base_folders = []
    for fp in db_folders:
        is_under_base = fp == resolved_base or fp.startswith(
            resolved_base + os.sep
        )
        if not is_under_base:
            continue
        under_base_folders.append(fp)
        if visited_folders is not None and fp in visited_folders:
            continue
        try:
            appears_gone = not (os.path.isdir(fp) or os.path.isfile(fp))
        except OSError:
            appears_gone = True
        if appears_gone:
            candidate_stale.append(fp)
    return candidate_stale, under_base_folders

def _remove_stale_folders(conn: Any, stale_folders: list[str]) -> tuple[int, int]:
    pruned_folders = 0
    pruned_files = 0
    thumb_dir = THUMBNAIL_CACHE_DIR
    for sf in stale_folders:
        cur = conn.execute(
            "SELECT path, mtime, size FROM files WHERE folder_path = ?", (sf,)
        )
        rows = cur.fetchall()
        for r in rows:
            k1 = f"{r['path']}_{r['mtime']}_{r['size']}"
            h1 = hashlib.sha256(k1.encode("utf-8")).hexdigest()[:24]
            try:
                (thumb_dir / f"thumb_{h1}.jpg").unlink(missing_ok=True)
            except OSError:
                pass

            k2 = r["path"]
            h2 = hashlib.sha256(k2.encode("utf-8")).hexdigest()[:24]
            try:
                (thumb_dir / f"thumb_{h2}.jpg").unlink(missing_ok=True)
            except OSError:
                pass

        with conn:
            conn.execute("DELETE FROM files WHERE folder_path = ?", (sf,))
            conn.execute("DELETE FROM folders WHERE path = ?", (sf,))
        pruned_folders += 1
        pruned_files += len(rows)
    return pruned_folders, pruned_files

def _clean_orphaned_thumbnails(conn: Any, thumb_dir: Any) -> int:
    pruned_orphaned_thumbs = 0
    if thumb_dir.exists():
        cur = conn.execute("SELECT path, mtime, size FROM files")
        valid_thumb_names = set()
        for r in cur.fetchall():
            p, m, s = r["path"], r["mtime"], r["size"]
            k1 = f"{p}_{m}_{s}"
            h1 = hashlib.sha256(k1.encode("utf-8")).hexdigest()[:24]
            valid_thumb_names.add(f"thumb_{h1}.jpg")
            k2 = p
            h2 = hashlib.sha256(k2.encode("utf-8")).hexdigest()[:24]
            valid_thumb_names.add(f"thumb_{h2}.jpg")

        for t_file in thumb_dir.glob("thumb_*.jpg"):
            if t_file.name not in valid_thumb_names:
                try:
                    t_file.unlink(missing_ok=True)
                    pruned_orphaned_thumbs += 1
                except OSError:
                    pass
    return pruned_orphaned_thumbs

def prune_stale_index_entries(
    base_dir: str | None = None,
    visited_folders: set[str] | None = None,
    clean_orphaned_thumbnails: bool = True,
    force: bool = False,
) -> dict[str, Any]:
    resolved_base = os.path.realpath(
        base_dir or state.get("destination_dir") or DEFAULT_DESTINATION_DIR
    )
    try:
        base_accessible = os.path.isdir(resolved_base)
    except OSError:
        base_accessible = False
    if not base_accessible:
        logger.warning(
            f"[Prune] Base directory {resolved_base} is not accessible. Aborting prune to prevent accidental data loss."
        )
        return {
            "pruned_folders": 0,
            "pruned_files": 0,
            "pruned_orphaned_thumbs": 0,
            "pruned_stale_duplicates": 0,
            "aborted": True,
            "reason": "Base directory unmounted or missing",
        }

    pruned_folders = 0
    pruned_files = 0
    pruned_orphaned_thumbs = 0
    pruned_stale_duplicates = 0

    try:
        with db_conn() as conn:
            cur = conn.execute("SELECT path FROM folders")
            db_folders = [r["path"] for r in cur.fetchall()]

            candidate_stale, under_base_folders = _get_stale_folders(db_folders, resolved_base, visited_folders)

            max_safe_prune = max(25, int(len(under_base_folders) * 0.10))
            if len(candidate_stale) > max_safe_prune and not force:
                logger.warning(
                    f"[Prune] Aborting: {len(candidate_stale)} of {len(under_base_folders)} folders reported missing "
                    f"(safety limit {max_safe_prune}). Suspected transient mount failure; index left intact."
                )
                return {
                    "pruned_folders": 0,
                    "pruned_files": 0,
                    "pruned_orphaned_thumbs": 0,
                    "pruned_stale_duplicates": 0,
                    "aborted": True,
                    "reason": f"Too many folders reported missing ({len(candidate_stale)}); aborted to protect the index",
                    "candidate_folders": len(candidate_stale),
                    "total_folders": len(under_base_folders),
                }

            stale_folders = [fp for fp in candidate_stale if _path_confirmed_gone(fp)]

            pruned_folders, pruned_files = _remove_stale_folders(conn, stale_folders)

            with conn:
                cur = conn.execute("""
                    DELETE FROM ignored_duplicates
                    WHERE path1 NOT IN (SELECT path FROM files)
                       OR path2 NOT IN (SELECT path FROM files)
                """)
                pruned_stale_duplicates = cur.rowcount

            if clean_orphaned_thumbnails:
                pruned_orphaned_thumbs = _clean_orphaned_thumbnails(conn, THUMBNAIL_CACHE_DIR)

            try:
                from media_library.services.deduplication_service import (
                    invalidate_visual_cache,
                )
                invalidate_visual_cache()
            except ImportError:
                pass

            cur = conn.execute("SELECT COUNT(*) FROM files")
            indexer_state["total_files"] = cur.fetchone()[0]
            cur_folders = conn.execute("SELECT COUNT(*) FROM folders")
            indexer_state["total_folders"] = cur_folders.fetchone()[0]

            if (
                pruned_folders > 0
                or pruned_files > 0
                or pruned_orphaned_thumbs > 0
                or pruned_stale_duplicates > 0
            ):
                logger.info(
                    f"[Prune] Removed {pruned_folders} deleted folders, {pruned_files} files, {pruned_orphaned_thumbs} orphaned thumbnails, and {pruned_stale_duplicates} stale duplicate records."
                )

    except Exception as e:
        logger.error(f"Error in prune_stale_index_entries: {e}")

    return {
        "pruned_folders": pruned_folders,
        "pruned_files": pruned_files,
        "pruned_orphaned_thumbs": pruned_orphaned_thumbs,
        "pruned_stale_duplicates": pruned_stale_duplicates,
    }


async def run_background_crawler(force_full: bool = False):
    async with indexer_lock:
        indexer_state["status"] = "crawling"
        indexer_state["scanned_folders"] = 0
        indexer_state["skipped_folders"] = 0
        indexer_state["scanned_files"] = 0
        base_dir = os.path.realpath(
            state.get("destination_dir") or DEFAULT_DESTINATION_DIR
        )
        if not os.path.exists(base_dir):
            indexer_state["status"] = "idle"
            return

        try:
            root_dirs = await asyncio.to_thread(_scan_root_dirs, base_dir)
            # The FUSE mount may still be settling right after boot (rclone/NFS).
            # An empty listing for an existing, non-empty library is almost always
            # a not-yet-ready mount, so retry briefly before giving up.
            attempts = 0
            while not root_dirs and attempts < 5 and os.path.isdir(base_dir):
                attempts += 1
                logger.warning(
                    f"[Crawler] No entries found under {base_dir}; waiting for mount (attempt {attempts}/5)..."
                )
                await asyncio.sleep(2.0)
                root_dirs = await asyncio.to_thread(_scan_root_dirs, base_dir)
            indexer_state["total_folders"] = len(root_dirs)

            # Snapshot the previously recorded per-folder mtimes and the known
            # parent -> children map. A folder whose directory mtime still matches
            # what we stored last scan is unchanged, so it can be skipped without
            # re-stat-ing/rewriting its files — while still recursing into its
            # known children so deep changes (and pruning) stay correct.
            known_dir_mtimes: dict[str, float | None] = {}
            known_children: dict[str, list[str]] = {}
            with db_conn() as conn:
                for r in conn.execute(
                    "SELECT path, parent_path, dir_mtime FROM folders"
                ):
                    known_dir_mtimes[r["path"]] = r["dir_mtime"]
                    if r["parent_path"]:
                        known_children.setdefault(r["parent_path"], []).append(
                            r["path"]
                        )

            dirs_queue = list(root_dirs)
            visited_folders: set[str] = set()
            crawler_sem = asyncio.Semaphore(4)

            async def _crawl_single(folder_path: str) -> dict[str, Any]:
                async with crawler_sem:
                    indexer_state["current_folder"] = os.path.basename(folder_path)
                    try:
                        st = await asyncio.to_thread(os.stat, folder_path)
                    except FileNotFoundError:
                        # Really gone: don't protect it, let the prune remove it.
                        return {"gone": True, "subfolders": [], "file_count": 0}
                    except OSError:
                        # Transient mount error: keep the indexed subtree intact
                        # rather than risk the prune deleting it.
                        return {
                            "subfolders": [
                                {"path": c, "name": os.path.basename(c)}
                                for c in known_children.get(folder_path, [])
                            ],
                            "file_count": 0,
                            "skipped": True,
                        }

                    prev_mtime = known_dir_mtimes.get(folder_path)
                    is_zip_file = folder_path.lower().endswith(".zip")
                    unchanged = (
                        not force_full
                        and prev_mtime is not None
                        and abs(st.st_mtime - prev_mtime) < 1e-6
                    )
                    if unchanged:
                        # Directory entries are unchanged: reuse the index and
                        # recurse into the known children only.
                        indexer_state["skipped_folders"] += 1
                        indexer_state["scanned_folders"] += 1
                        children = (
                            [] if is_zip_file else known_children.get(folder_path, [])
                        )
                        return {
                            "subfolders": [
                                {"path": c, "name": os.path.basename(c)}
                                for c in children
                            ],
                            "file_count": 0,
                            "skipped": True,
                        }

                    res = await asyncio.to_thread(index_folder, folder_path, base_dir)
                    indexer_state["scanned_folders"] += 1
                    if isinstance(res, dict):
                        indexer_state["scanned_files"] += res.get("file_count", 0)
                        return res
                    return {"file_count": 0, "subfolders": []}

            while dirs_queue:
                batch = []
                while dirs_queue and len(batch) < 8:
                    batch.append(dirs_queue.pop(0))

                results = await asyncio.gather(
                    *[_crawl_single(f) for f in batch], return_exceptions=True
                )
                for f, res in zip(batch, results):
                    if not isinstance(res, dict):
                        # Unexpected error: protect this folder from pruning.
                        visited_folders.add(os.path.realpath(f))
                        continue
                    if res.get("gone"):
                        continue
                    visited_folders.add(os.path.realpath(f))
                    for sub in res.get("subfolders") or []:
                        dirs_queue.append(sub["path"])
                        indexer_state["total_folders"] += 1

                await asyncio.sleep(0.04)

            # Auto-prune stale entries, deleted folders & orphaned thumbnails.
            # Only prune when the crawl actually enumerated the tree; a partial
            # crawl (e.g. the FUSE mount was still coming up at boot) must never
            # be allowed to delete index rows.
            if visited_folders:
                indexer_state["current_folder"] = "Pruning deleted folders & cache..."
                await asyncio.to_thread(
                    prune_stale_index_entries, base_dir, visited_folders, True
                )
            else:
                logger.warning(
                    "[Crawler] No folders were visited; skipping prune to protect the index."
                )

            with db_conn() as conn:
                cur = conn.execute("SELECT COUNT(*) FROM files")
                indexer_state["total_files"] = cur.fetchone()[0]
                cur_folders = conn.execute("SELECT COUNT(*) FROM folders")
                indexer_state["total_folders"] = cur_folders.fetchone()[0]

            indexer_state["last_synced"] = time.time()
        except Exception as e:
            logger.error(f"Error in background crawler: {e}")
        finally:
            indexer_state["status"] = "idle"
            indexer_state["current_folder"] = ""

def deep_clean_database():
    import os
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from media_library.db.connection import db_conn
    from media_library.core.config import DEFAULT_DESTINATION_DIR
    from media_library.core.state import state
    from media_library.db.indexer import _path_confirmed_gone, remove_file_from_index, prune_stale_index_entries, indexer_state
    
    logger.info("[Deep Clean] Starting deep scan of database for missing files...")
    indexer_state["status"] = "crawling"
    indexer_state["current_folder"] = "Deep cleaning DB (checking missing files)..."
    try:
        with db_conn() as conn:
            rows = conn.execute("SELECT path FROM files").fetchall()
            paths = [r["path"] for r in rows]

        logger.info(f"[Deep Clean] Checking existence of {len(paths)} files concurrently...")
        missing = []
        checked = 0
        
        def _check(p):
            nonlocal checked
            if not os.path.exists(p):
                missing.append(p)
            checked += 1
            if checked % 1000 == 0:
                indexer_state["current_folder"] = f"Deep cleaning DB (checked {checked}/{len(paths)})..."
                
        with ThreadPoolExecutor(max_workers=16) as ex:
            ex.map(_check, paths)
            
        logger.info(f"[Deep Clean] Found {len(missing)} potentially missing files.")
        indexer_state["current_folder"] = f"Deep cleaning DB (verifying {len(missing)} missing files)..."
        
        removed = 0
        for i, p in enumerate(missing):
            if _path_confirmed_gone(p, attempts=2, delay=0.1):
                remove_file_from_index(p)
                removed += 1
            if i % 50 == 0:
                indexer_state["current_folder"] = f"Deep cleaning DB (verified {i}/{len(missing)} missing files)..."
                
        logger.info(f"[Deep Clean] Removed {removed} confirmed missing files. Running stale index prune...")
        indexer_state["current_folder"] = "Deep cleaning DB (pruning stale entries)..."
        prune_stale_index_entries(clean_orphaned_thumbnails=True, force=True)
        logger.info("[Deep Clean] Deep clean finished.")
    finally:
        indexer_state["status"] = "idle"
        indexer_state["current_folder"] = ""
