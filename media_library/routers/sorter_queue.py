"""FastAPI router for the Video Sorter: config, source scanning, queue, browsing, and destinations."""

import asyncio
import logging
import os
import re
import stat as _stat
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from media_library.core.config import save_config_data
from media_library.core.constants import QUEUE_REFRESH_INTERVAL_S, VIDEO_EXTENSIONS
from media_library.core.state import state
from media_library.db.connection import db_conn
from media_library.services.media_service import get_lan_ip, scan_videos
from media_library.workers.transcode_worker import enqueue_pretranscode_for_source
from media_library.workers.transfer_worker import transfer_worker_instance

router = APIRouter()

logger = logging.getLogger(__name__)

# Timestamp of the last incoming-folder queue rescan (atomic float write, safe
# under the GIL; only touched by the sorter queue handlers below).
_last_queue_scan_time = 0.0


class ScanSourceRequest(BaseModel):
    source_dir: str


class CreateFolderRequest(BaseModel):
    folder_name: str


def _resolve_directory_tolerant(path_str: str) -> str:
    """Resolve a directory path, tolerating trailing whitespace in the final
    component (e.g. a folder literally named 'Movies ')."""
    if not path_str or not path_str.strip():
        return os.path.abspath(path_str or "/")

    target = os.path.abspath(path_str)
    try:
        if os.path.isdir(target):
            return target
    except OSError:
        # Transient FUSE stat failure; fall through to the caller's error path
        # rather than treating the directory as definitely absent.
        pass

    parent = os.path.dirname(target)
    base = os.path.basename(target)
    if not base or not os.path.isdir(parent):
        return target  # let the caller surface the real error

    # Exact path isn't a directory: try to match a sibling whose name differs
    # only by trailing whitespace.
    try:
        matches = []
        with os.scandir(parent) as entries:
            for entry in entries:
                if (
                    entry.is_dir(follow_symlinks=True)
                    and entry.name.rstrip() == base.rstrip()
                ):
                    matches.append(entry.path)
        if len(matches) == 1:
            return matches[0]
    except OSError:
        # Parent unreadable right now: return the exact path and let the
        # handler surface the real error.
        pass
    return target


@router.get("/api/config")
async def get_config() -> dict:
    lan_ip = get_lan_ip()
    queued_count = sum(
        1
        for t in transfer_worker_instance.transfer_items_map.values()
        if t.get("status") == "queued"
    )
    active_count = 1 if transfer_worker_instance.active_transfer else 0
    return {
        "lan_ip": lan_ip,
        "source_dir": state["source_dir"],
        "destination_dir": state["destination_dir"],
        "recent_destinations": state["recent_destinations"][:6],
        "queue_count": len(state["video_queue"]),
        "can_undo": len(state["undo_stack"]) > 0,
        "last_undo_item": state["undo_stack"][-1]["filename"]
        if state["undo_stack"]
        else None,
        "transfers_in_progress": active_count + queued_count,
    }


@router.post("/api/source/scan")
async def scan_source(req: ScanSourceRequest) -> dict:
    global _last_queue_scan_time
    source_path = _resolve_directory_tolerant(req.source_dir)
    if not os.path.exists(source_path):
        raise HTTPException(
            status_code=400, detail=f"Directory does not exist: {source_path}"
        )
    if not os.path.isdir(source_path):
        raise HTTPException(
            status_code=400, detail=f"Path is not a directory: {source_path}"
        )

    state.set_source_dir(source_path)
    save_config_data(state)

    in_flight_names = {
        t["filename"]
        for t in transfer_worker_instance.transfer_items_map.values()
        if t.get("status") in ("queued", "transferring")
        and t.get("source_path", "").startswith(source_path)
    }

    all_videos = await asyncio.to_thread(scan_videos, source_path)
    state.set_queue([v for v in all_videos if v["filename"] not in in_flight_names])
    _last_queue_scan_time = time.time()

    asyncio.create_task(asyncio.to_thread(enqueue_pretranscode_for_source, source_path))

    return {
        "success": True,
        "source_dir": source_path,
        "count": len(state["video_queue"]),
        "videos": state["video_queue"],
    }


@router.get("/api/queue")
async def get_queue(refresh: bool | None = Query(False)) -> dict:
    global _last_queue_scan_time
    now = time.time()
    in_flight_names = {
        t["filename"]
        for t in transfer_worker_instance.transfer_items_map.values()
        if t.get("status") in ("queued", "transferring")
    }

    if state["source_dir"] and os.path.isdir(state["source_dir"]):
        if (
            refresh
            or not state["video_queue"]
            or (now - _last_queue_scan_time > QUEUE_REFRESH_INTERVAL_S)
        ):
            all_videos = await asyncio.to_thread(scan_videos, state["source_dir"])
            state.set_queue(
                [v for v in all_videos if v["filename"] not in in_flight_names]
            )
            _last_queue_scan_time = now
        else:
            state.update_queue(
                lambda q: [v for v in q if v["filename"] not in in_flight_names]
            )

    return {
        "source_dir": state["source_dir"],
        "count": len(state["video_queue"]),
        "videos": state["video_queue"],
    }


@router.get("/api/browse")
def browse_directory(path: str | None = Query(None)):
    raw = path if path and path.strip() else None
    requested = os.path.abspath(raw) if raw else None
    target_path = (
        _resolve_directory_tolerant(raw)
        if raw
        else (state["source_dir"] or state["destination_dir"] or "/")
    )

    # Validate that the path exists and is a directory.
    # os.path.exists() silently returns False on OSError (e.g. FUSE timeouts), so we
    # call os.stat() explicitly to surface the real error to the caller.
    try:
        st = os.stat(target_path)
        if not _stat.S_ISDIR(st.st_mode):
            target_path = os.path.dirname(target_path) or "/"
            os.stat(target_path)  # confirm parent is reachable too
    except FileNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=f"Directory does not exist: {requested or target_path}",
        )
    except OSError:
        logger.exception("Cannot access path %s", target_path)
        raise HTTPException(status_code=503, detail="Cannot access path")

    folders = []
    video_count = 0
    try:
        with os.scandir(target_path) as entries:
            for entry in entries:
                try:
                    if entry.is_dir(follow_symlinks=False):
                        if not entry.name.startswith("."):
                            folders.append(
                                {
                                    "name": entry.name,
                                    "path": entry.path,
                                }
                            )
                    elif entry.is_file(follow_symlinks=False):
                        ext = os.path.splitext(entry.name)[1].lower()
                        if ext in VIDEO_EXTENSIONS:
                            video_count += 1
                except (PermissionError, OSError):
                    continue
    except PermissionError:
        raise HTTPException(status_code=403, detail="Permission denied")
    except OSError:
        logger.exception("Cannot read directory %s", target_path)
        raise HTTPException(status_code=503, detail="Cannot read directory")

    folders.sort(key=lambda x: x["name"].lower())
    parent_path = str(Path(target_path).parent) if target_path != "/" else None

    return {
        "current_path": target_path,
        "parent_path": parent_path,
        "folders": folders,
        "video_count_here": video_count,
    }


@router.get("/api/quick-nav")
def get_quick_nav() -> dict:
    shortcuts = []
    seen = set()

    def add_shortcut(label: str, path_str: str):
        if not path_str:
            return
        try:
            p = os.path.abspath(os.path.expanduser(path_str.strip()))
            if p not in seen and os.path.exists(p) and os.path.isdir(p):
                seen.add(p)
                shortcuts.append({"label": label, "path": p})
        except (OSError, RuntimeError, KeyError):
            # Best-effort shortcut: a broken env var/home must not break quick-nav.
            logger.debug(
                "Could not resolve quick-nav shortcut %r", path_str, exc_info=True
            )

    try:
        home_path = str(Path.home())
        add_shortcut("Home", home_path)
    except (RuntimeError, OSError, KeyError):
        logger.debug(
            "Could not resolve home directory quick-nav shortcut", exc_info=True
        )

    if state.get("destination_dir"):
        add_shortcut("Media Library", state["destination_dir"])

    if state.get("source_dir"):
        add_shortcut("Incoming", state["source_dir"])

    env_quick = os.environ.get("QUICK_NAV_DIRS", "")
    if env_quick:
        for item in env_quick.split(","):
            item = item.strip()
            if item:
                label = os.path.basename(item) or item
                add_shortcut(label, item)

    for sys_path in ["/media", "/mnt", "/Volumes"]:
        if os.path.exists(sys_path) and os.path.isdir(sys_path):
            add_shortcut(sys_path, sys_path)

    if "/" not in seen:
        shortcuts.append({"label": "Root", "path": "/"})

    return {"shortcuts": shortcuts}


@router.get("/api/destinations")
def list_destinations() -> dict:
    dest_dir = state["destination_dir"]
    if not os.path.exists(dest_dir):
        return {
            "base_path": dest_dir,
            "folders": [],
            "recent": state["recent_destinations"][:6],
        }

    subfolders = []
    try:
        with db_conn() as conn:
            cur = conn.execute(
                "SELECT name FROM folders WHERE parent_path = ? ORDER BY LOWER(name)",
                (dest_dir,),
            )
            subfolders = [r["name"] for r in cur.fetchall()]
    except Exception as e:
        logger.error(f"Error reading destinations from DB: {e}")

    if not subfolders:
        try:
            with os.scandir(dest_dir) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(
                            follow_symlinks=False
                        ) and not entry.name.startswith("."):
                            subfolders.append(entry.name)
                    except (PermissionError, OSError):
                        continue
            subfolders.sort(key=lambda x: x.lower())
        except Exception as e:
            logger.error(f"Error reading destinations from {dest_dir}: {e}")

    return {
        "base_path": dest_dir,
        "folders": subfolders,
        "recent": state["recent_destinations"][:6],
    }


@router.post("/api/destinations/create")
def create_destination_subfolder(req: CreateFolderRequest) -> dict:
    folder_name = req.folder_name.strip()
    if not folder_name:
        raise HTTPException(status_code=400, detail="Folder name cannot be empty")

    sanitized = re.sub(r'[\/\\:\*\?"<>\|]', "", folder_name).strip()
    if not sanitized or sanitized.startswith("."):
        raise HTTPException(status_code=400, detail="Invalid folder name")

    dest_dir = state["destination_dir"]
    new_folder_path = os.path.join(dest_dir, sanitized)

    try:
        os.makedirs(new_folder_path, exist_ok=True)
    except Exception:
        logger.exception("Failed to create folder %s", new_folder_path)
        raise HTTPException(status_code=500, detail="Failed to create folder")

    try:
        with db_conn() as conn, conn:
            conn.execute(
                "INSERT OR IGNORE INTO folders (path, name, parent_path, file_count, subfolder_count, last_scanned) VALUES (?, ?, ?, 0, 0, ?)",
                (new_folder_path, sanitized, dest_dir, time.time()),
            )
    except Exception as e:
        logger.error(f"Error adding new folder to DB index: {e}")

    if sanitized not in state["recent_destinations"]:
        state.push_recent(sanitized)
        save_config_data(state)

    return {
        "success": True,
        "folder_name": sanitized,
        "folder_path": new_folder_path,
    }
