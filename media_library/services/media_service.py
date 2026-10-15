"""Media file system operations, queue scanning, and transfer execution."""

import logging
import os
import socket
import threading
import time
import urllib.parse
from typing import Any

from fastapi import HTTPException

from media_library.core.config import DEFAULT_DESTINATION_DIR, SEARCH_LIMIT
from media_library.core.constants import NATIVE_EXTENSIONS, VIDEO_EXTENSIONS
from media_library.core.state import format_size, state
from media_library.db.connection import db_conn
from media_library.db.indexer import add_file_to_index

logger = logging.getLogger(__name__)


def get_lan_ip() -> str:
    """Detects primary local network IP address."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        s.connect(("1.1.1.1", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def validate_backup_path(path_str: str | None, base_dir: str | None = None) -> str:
    """Validates that a path resides strictly within the destination directory."""
    target_base = os.path.abspath(
        os.path.normpath(base_dir or state.get("destination_dir") or DEFAULT_DESTINATION_DIR)
    )
    if not path_str or not path_str.strip():
        return target_base
    clean_path = urllib.parse.unquote(path_str.strip())
    norm_path = os.path.abspath(os.path.normpath(clean_path))

    # If path points to base_dir, its parent directory (e.g. /mnt/pcloud), or root, clamp safely to base_dir
    if (
        norm_path == target_base
        or norm_path == os.path.dirname(target_base)
        or norm_path == "/"
    ):
        return target_base

    try:
        common = os.path.commonpath([target_base, norm_path])
        if common != target_base:
            raise HTTPException(
                status_code=403,
                detail="Access denied: path is outside Backup directory",
            )
    except ValueError:
        raise HTTPException(status_code=403, detail="Invalid path")
    return norm_path


def scan_videos(directory: str) -> list[dict[str, Any]]:
    """Scans incoming directory for valid video files."""
    if not os.path.isdir(directory):
        return []

    videos = []
    fav_paths = set()
    try:
        with db_conn() as conn:
            rows = conn.execute("SELECT path FROM favorites").fetchall()
            fav_paths = {r[0] for r in rows}
    except Exception:
        pass

    try:
        with os.scandir(directory) as entries:
            for entry in entries:
                if entry.is_file():
                    if entry.name.startswith("."):
                        continue
                    ext = os.path.splitext(entry.name)[1].lower()
                    if ext in VIDEO_EXTENSIONS:
                        try:
                            stat = entry.stat()
                            needs_transcode = ext not in NATIVE_EXTENSIONS
                            videos.append(
                                {
                                    "id": entry.name,
                                    "filename": entry.name,
                                    "path": entry.path,
                                    "size": stat.st_size,
                                    "size_formatted": format_size(stat.st_size),
                                    "mtime": stat.st_mtime,
                                    "ext": ext,
                                    "needs_transcode": needs_transcode,
                                    "is_favorite": entry.path in fav_paths,
                                }
                            )
                        except Exception as e:
                            logger.warning(
                                f"Skipping unreadable video entry {entry.path}: {e}"
                            )
                            continue
    except Exception as e:
        logger.error(f"Error scanning directory {directory}: {e}")
        return []

    videos.sort(key=lambda x: x["filename"].lower())
    return videos


def resolve_video_path(
    filename: str, transcode_items_map: dict[str, Any] | None = None
) -> str | None:
    """Resolves a video file path in incoming directory or transcode cache."""
    if not state.get("source_dir") or not os.path.isdir(state["source_dir"]):
        return None

    clean_filename = urllib.parse.unquote(filename).strip()
    candidates_to_try = [clean_filename]
    if clean_filename != filename:
        candidates_to_try.append(filename)

    for fname in candidates_to_try:
        # 1. Exact match in source directory
        direct_path = os.path.join(state["source_dir"], fname)
        if os.path.exists(direct_path) and not os.path.basename(direct_path).startswith(
            "."
        ):
            return direct_path

        # 2. Check transcode items map for completed target paths
        if transcode_items_map:
            for t in transcode_items_map.values():
                if (
                    t.get("filename") == fname
                    and t.get("target_path")
                    and os.path.exists(t["target_path"])
                ):
                    return t["target_path"]

        # 3. Check if file was converted from an older container to .mp4
        base, _ = os.path.splitext(fname)
        mp4_name = f"{base}.mp4"
        mp4_path = os.path.join(state["source_dir"], mp4_name)
        if os.path.exists(mp4_path):
            return mp4_path

        # 4. Check if file was renamed with a converted counter
        try:
            with os.scandir(state["source_dir"]) as entries:
                for entry in entries:
                    if (
                        entry.is_file()
                        and not entry.name.startswith(".")
                        and entry.name.startswith(base)
                        and entry.name.endswith(".mp4")
                    ):
                        return entry.path
        except Exception:
            pass

    return None


def search_backup_index(
    query: str,
    type_filter: str | None = None,
    sort_by: str = "name_asc",
    limit: int | None = None,
    return_meta: bool = False,
) -> list[dict[str, Any]] | tuple[list[dict[str, Any]], bool]:
    """Performs multi-token search in indexed SQLite library."""
    effective_limit = limit if (limit is not None and limit > 0) else SEARCH_LIMIT
    with db_conn() as conn:
        try:
            words = query.strip().lower().split()
            if not words:
                return ([], False) if return_meta else []

            where_clauses = ["LOWER(f.name) LIKE ?" for _ in words]
            sql = (
                "SELECT f.*, CASE WHEN fav.path IS NOT NULL THEN 1 ELSE 0 END AS is_favorite "
                f"FROM files f LEFT JOIN favorites fav ON f.path = fav.path WHERE {' AND '.join(where_clauses)}"
            )
            params: list[Any] = [f"%{w}%" for w in words]

            if type_filter and type_filter in ("video", "image"):
                sql += " AND f.type = ?"
                params.append(type_filter)

            sort_sql = "f.name ASC"
            if sort_by == "name_desc":
                sort_sql = "f.name DESC"
            elif sort_by == "date_desc":
                sort_sql = "f.mtime DESC"
            elif sort_by == "date_asc":
                sort_sql = "f.mtime ASC"
            elif sort_by == "size_desc":
                sort_sql = "f.size DESC"
            elif sort_by == "size_asc":
                sort_sql = "f.size ASC"

            # Query limit + 1 rows to detect truncation without an extra COUNT(*) query
            sql += f" ORDER BY {sort_sql} LIMIT ?"
            params.append(effective_limit + 1)

            cur = conn.execute(sql, params)
            rows = cur.fetchall()

            truncated = len(rows) > effective_limit
            if truncated:
                rows = rows[:effective_limit]

            results = []
            base_dir = os.path.realpath(
                state.get("destination_dir") or DEFAULT_DESTINATION_DIR
            )
            for r in rows:
                fpath = r["path"]
                fname = r["name"]
                folder_path = r["folder_path"]
                rel_folder = os.path.relpath(folder_path, base_dir)
                folder_name = os.path.basename(folder_path)
                is_vid = r["type"] == "video"
                ext = r["ext"]
                is_native = ext in NATIVE_EXTENSIONS

                media_url = (
                    f"/api/backup/media?path={urllib.parse.quote(fpath)}"
                    if (not is_vid or is_native)
                    else f"/api/backup/transcode_stream?path={urllib.parse.quote(fpath)}"
                )

                results.append(
                    {
                        "name": fname,
                        "path": fpath,
                        "folder_name": folder_name,
                        "folder_path": folder_path,
                        "rel_folder": rel_folder,
                        "type": r["type"],
                        "ext": ext,
                        "size": r["size"],
                        "size_formatted": r["size_formatted"],
                        "mtime": r["mtime"],
                        "needs_transcode": bool(r["needs_transcode"]),
                        "has_thumb": bool(r["has_thumb"])
                        if "has_thumb" in r.keys()
                        else False,
                        "width": r["width"] if "width" in r.keys() else None,
                        "height": r["height"] if "height" in r.keys() else None,
                        "duration": r["duration"] if "duration" in r.keys() else None,
                        "video_codec": r["video_codec"]
                        if "video_codec" in r.keys()
                        else None,
                        "audio_codec": r["audio_codec"]
                        if "audio_codec" in r.keys()
                        else None,
                        "fps": r["fps"] if "fps" in r.keys() else None,
                        "capture_time": r["capture_time"]
                        if "capture_time" in r.keys()
                        else None,
                        "is_favorite": bool(r["is_favorite"])
                        if "is_favorite" in r.keys()
                        else False,
                        "thumb_url": (
                            f"/api/backup/thumbnail?path={urllib.parse.quote(fpath)}"
                            + (f"&mtime={r['mtime']}&size={r['size']}" if (r["mtime"] is not None and r["size"] is not None) else "")
                            + (f"&v={int(r['thumb_mtime'])}" if ("thumb_mtime" in r.keys() and r["thumb_mtime"]) else (f"&v={r['thumb_hash']}" if ("thumb_hash" in r.keys() and r["thumb_hash"]) else ""))
                        ),
                        "media_url": media_url,
                    }
                )
            if return_meta:
                return results, truncated
            return results
        except Exception as e:
            logger.error(f"Error querying backup search: {e}")
            return ([], False) if return_meta else []


def do_transfer(
    item: dict[str, Any], transcode_items_map: dict[str, Any] | None = None
):
    """Executes a move/copy transfer item from queue with chunking and fallback."""
    source_path = item["source_path"]
    target_path = item["target_path"]
    abort_event: threading.Event = item.get("abort_event") or threading.Event()

    # Wait on pre-transcode if linked
    wait_transcode_id = item.get("waiting_on_transcode")
    if (
        wait_transcode_id
        and transcode_items_map
        and wait_transcode_id in transcode_items_map
    ):
        t_task = transcode_items_map[wait_transcode_id]
        while t_task.get("status") in ("queued", "transcoding"):
            if abort_event.is_set():
                item["status"] = "cancelled"
                return
            time.sleep(0.5)

        if (
            t_task.get("status") == "completed"
            and t_task.get("target_path")
            and os.path.exists(t_task["target_path"])
        ):
            source_path = t_task["target_path"]
            item["source_path"] = source_path
            converted_name = os.path.basename(source_path)
            item["filename"] = converted_name
            target_dir = os.path.dirname(target_path)
            c_base, c_ext = os.path.splitext(converted_name)
            final_dest_name = converted_name
            cnt = 1
            while os.path.exists(os.path.join(target_dir, final_dest_name)):
                final_dest_name = f"{c_base}_{cnt}{c_ext}"
                cnt += 1
            target_path = os.path.join(target_dir, final_dest_name)
            item["target_path"] = target_path
            item["final_filename"] = final_dest_name
            try:
                item["total_bytes"] = os.path.getsize(source_path)
            except Exception:
                pass

    if not os.path.exists(source_path):
        item["status"] = "failed"
        item["error"] = "Source file not found"
        return

    target_dir = os.path.dirname(target_path)
    os.makedirs(target_dir, exist_ok=True)

    # Check same filesystem for atomic fast move
    try:
        s_stat = os.stat(source_path)
        d_stat = os.stat(target_dir)
        if s_stat.st_dev == d_stat.st_dev:
            os.rename(source_path, target_path)
            item["bytes_copied"] = item.get("total_bytes", s_stat.st_size)
            item["percent"] = 100.0
            item["status"] = "completed"
            item["completed_at"] = time.time()
            add_file_to_index(target_path)
            if item.get("is_favorite"):
                try:
                    with db_conn() as conn, conn:
                        conn.execute(
                            "INSERT OR REPLACE INTO favorites (path, created_at) VALUES (?, ?)",
                            (target_path, time.time()),
                        )
                except Exception as e:
                    logger.warning(f"Could not record favorite for {target_path}: {e}")
            return
    except Exception:
        pass

    # Across-filesystem chunked copy with cancel support
    temp_target = target_path + f".tmp.{item['id']}"
    total_size = item.get("total_bytes") or (os.path.getsize(source_path) if os.path.exists(source_path) else 0)
    bytes_copied = 0
    chunk_size = 4 * 1024 * 1024

    try:
        with open(source_path, "rb") as fsrc, open(temp_target, "wb") as fdst:
            while True:
                if abort_event.is_set():
                    raise InterruptedError("Transfer cancelled by user")
                chunk = fsrc.read(chunk_size)
                if not chunk:
                    break
                fdst.write(chunk)
                bytes_copied += len(chunk)
                item["bytes_copied"] = bytes_copied
                if total_size > 0:
                    item["percent"] = round((bytes_copied / total_size) * 100.0, 1)

        os.replace(temp_target, target_path)
        try:
            os.remove(source_path)
        except Exception as e:
            logger.warning(f"Warning: Failed to delete source file {source_path}: {e}")

        item["status"] = "completed"
        item["completed_at"] = time.time()
        item["percent"] = 100.0
        add_file_to_index(target_path)
        if item.get("is_favorite"):
            try:
                with db_conn() as conn, conn:
                    conn.execute(
                        "INSERT OR REPLACE INTO favorites (path, created_at) VALUES (?, ?)",
                        (target_path, time.time()),
                    )
            except Exception as e:
                logger.warning(f"Could not record favorite for {target_path}: {e}")
    except InterruptedError:
        if os.path.exists(temp_target):
            try:
                os.remove(temp_target)
            except Exception:
                pass
        item["status"] = "cancelled"
    except Exception as e:
        if os.path.exists(temp_target):
            try:
                os.remove(temp_target)
            except Exception:
                pass
        item["status"] = "failed"
        item["error"] = str(e)
        logger.error(f"Transfer failed for {item['filename']}: {e}")
