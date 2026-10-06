"""FastAPI router for Incoming Video Sorter workflows, browse navigation, moves, undo, and streaming."""

import os
import re
import time
import uuid
import shutil
import asyncio
import threading
import mimetypes
import urllib.parse
from typing import Optional, List, Dict, Any
from pathlib import Path
from pydantic import BaseModel
from fastapi import APIRouter, Request, HTTPException, Query, Response
from fastapi.responses import StreamingResponse

from media_library.core.constants import VIDEO_EXTENSIONS
from media_library.core.config import DEFAULT_DESTINATION_DIR, save_config_data
from media_library.core.state import state, format_size, make_video_meta
from media_library.db.connection import get_db
from media_library.services.media_service import (
    get_lan_ip,
    scan_videos,
    resolve_video_path,
)
from media_library.services.deduplication_service import compute_quick_hash
from media_library.workers.transfer_worker import transfer_worker_instance
from media_library.workers.transcode_worker import (
    transcode_worker_instance,
    enqueue_pretranscode_for_source,
)

router = APIRouter()

_last_queue_scan_time = 0.0

class ScanSourceRequest(BaseModel):
    source_dir: str

class MoveRequest(BaseModel):
    file_id: str
    destination_folder: str

class RenameRequest(BaseModel):
    file_id: str
    new_name: str

class DeleteRequest(BaseModel):
    file_id: str

class CreateFolderRequest(BaseModel):
    folder_name: str

class CancelTransferRequest(BaseModel):
    task_id: str

@router.get("/api/config")
async def get_config():
    lan_ip = get_lan_ip()
    queued_count = sum(1 for t in transfer_worker_instance.transfer_items_map.values() if t.get("status") == "queued")
    active_count = 1 if transfer_worker_instance.active_transfer else 0
    return {
        "lan_ip": lan_ip,
        "source_dir": state["source_dir"],
        "destination_dir": state["destination_dir"],
        "recent_destinations": state["recent_destinations"][:6],
        "queue_count": len(state["video_queue"]),
        "can_undo": len(state["undo_stack"]) > 0,
        "last_undo_item": state["undo_stack"][-1]["filename"] if state["undo_stack"] else None,
        "transfers_in_progress": active_count + queued_count,
    }

@router.post("/api/source/scan")
async def scan_source(req: ScanSourceRequest):
    global _last_queue_scan_time
    source_path = os.path.abspath(req.source_dir.strip())
    if not os.path.exists(source_path):
        raise HTTPException(status_code=400, detail=f"Directory does not exist: {source_path}")
    if not os.path.isdir(source_path):
        raise HTTPException(status_code=400, detail=f"Path is not a directory: {source_path}")

    state["source_dir"] = source_path
    save_config_data(state)

    in_flight_names = {
        t["filename"] for t in transfer_worker_instance.transfer_items_map.values()
        if t.get("status") in ("queued", "transferring") and t.get("source_path", "").startswith(source_path)
    }

    all_videos = await asyncio.to_thread(scan_videos, source_path)
    state["video_queue"] = [v for v in all_videos if v["filename"] not in in_flight_names]
    _last_queue_scan_time = time.time()

    asyncio.create_task(asyncio.to_thread(enqueue_pretranscode_for_source, source_path))

    return {
        "success": True,
        "source_dir": source_path,
        "count": len(state["video_queue"]),
        "videos": state["video_queue"],
    }

@router.get("/api/queue")
async def get_queue(refresh: Optional[bool] = Query(False)):
    global _last_queue_scan_time
    now = time.time()
    in_flight_names = {
        t["filename"] for t in transfer_worker_instance.transfer_items_map.values()
        if t.get("status") in ("queued", "transferring")
    }

    if state["source_dir"] and os.path.isdir(state["source_dir"]):
        if refresh or not state["video_queue"] or (now - _last_queue_scan_time > 8.0):
            all_videos = await asyncio.to_thread(scan_videos, state["source_dir"])
            state["video_queue"] = [v for v in all_videos if v["filename"] not in in_flight_names]
            _last_queue_scan_time = now
        else:
            state["video_queue"] = [v for v in state["video_queue"] if v["filename"] not in in_flight_names]

    return {
        "source_dir": state["source_dir"],
        "count": len(state["video_queue"]),
        "videos": state["video_queue"],
    }

@router.get("/api/browse")
def browse_directory(path: Optional[str] = Query(None)):
    target_path = os.path.abspath(path.strip()) if path and path.strip() else (state["source_dir"] or state["destination_dir"] or "/")
    if not os.path.exists(target_path):
        target_path = "/"
    if not os.path.isdir(target_path):
        target_path = os.path.dirname(target_path) or "/"

    folders = []
    video_count = 0
    try:
        with os.scandir(target_path) as entries:
            for entry in entries:
                try:
                    if entry.is_dir(follow_symlinks=False):
                        if not entry.name.startswith("."):
                            folders.append({
                                "name": entry.name,
                                "path": entry.path,
                            })
                    elif entry.is_file(follow_symlinks=False):
                        ext = os.path.splitext(entry.name)[1].lower()
                        if ext in VIDEO_EXTENSIONS:
                            video_count += 1
                except (PermissionError, OSError):
                    continue
    except PermissionError:
        raise HTTPException(status_code=403, detail="Permission denied")
    except OSError as e:
        print(f"Warning: could not scan {target_path}: {e}")

    folders.sort(key=lambda x: x["name"].lower())
    parent_path = str(Path(target_path).parent) if target_path != "/" else None

    return {
        "current_path": target_path,
        "parent_path": parent_path,
        "folders": folders,
        "video_count_here": video_count,
    }

@router.get("/api/quick-nav")
def get_quick_nav():
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
        except Exception:
            pass

    try:
        home_path = str(Path.home())
        add_shortcut("Home", home_path)
    except Exception:
        pass

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
def list_destinations():
    dest_dir = state["destination_dir"]
    if not os.path.exists(dest_dir):
        return {
            "base_path": dest_dir,
            "folders": [],
            "recent": state["recent_destinations"][:6],
        }

    subfolders = []
    try:
        conn = get_db()
        cur = conn.execute("SELECT name FROM folders WHERE parent_path = ? ORDER BY LOWER(name)", (dest_dir,))
        subfolders = [r["name"] for r in cur.fetchall()]
        conn.close()
    except Exception as e:
        print(f"Error reading destinations from DB: {e}")

    if not subfolders:
        try:
            with os.scandir(dest_dir) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False) and not entry.name.startswith("."):
                            subfolders.append(entry.name)
                    except (PermissionError, OSError):
                        continue
            subfolders.sort(key=lambda x: x.lower())
        except Exception as e:
            print(f"Error reading destinations from {dest_dir}: {e}")

    return {
        "base_path": dest_dir,
        "folders": subfolders,
        "recent": state["recent_destinations"][:6],
    }

@router.post("/api/destinations/create")
def create_destination_subfolder(req: CreateFolderRequest):
    folder_name = req.folder_name.strip()
    if not folder_name:
        raise HTTPException(status_code=400, detail="Folder name cannot be empty")

    sanitized = re.sub(r'[\/\\:\*\?"<>\|]', '', folder_name).strip()
    if not sanitized or sanitized.startswith("."):
        raise HTTPException(status_code=400, detail="Invalid folder name")

    dest_dir = state["destination_dir"]
    new_folder_path = os.path.join(dest_dir, sanitized)

    try:
        os.makedirs(new_folder_path, exist_ok=True)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create folder: {str(e)}")

    try:
        conn = get_db()
        with conn:
            conn.execute(
                "INSERT OR IGNORE INTO folders (path, name, parent_path, file_count, subfolder_count, last_scanned) VALUES (?, ?, ?, 0, 0, ?)",
                (new_folder_path, sanitized, dest_dir, time.time())
            )
        conn.close()
    except Exception as e:
        print(f"Error adding new folder to DB index: {e}")

    if sanitized not in state["recent_destinations"]:
        state["recent_destinations"].insert(0, sanitized)
        save_config_data(state)

    return {
        "success": True,
        "folder_name": sanitized,
        "folder_path": new_folder_path,
    }

@router.post("/api/move")
def move_video(req: MoveRequest):
    if not state["source_dir"]:
        raise HTTPException(status_code=400, detail="No source directory configured")

    source_path = resolve_video_path(req.file_id, transcode_worker_instance.transcode_items_map)
    if not source_path or not os.path.exists(source_path):
        raise HTTPException(status_code=404, detail="Source file not found")

    dest_folder = req.destination_folder.strip()
    if not dest_folder:
        raise HTTPException(status_code=400, detail="Destination folder cannot be empty")

    target_dir = os.path.join(state["destination_dir"], dest_folder)
    try:
        os.makedirs(target_dir, exist_ok=True)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to prepare destination: {e}")

    filename = os.path.basename(source_path)
    base, ext = os.path.splitext(filename)

    occupied_names = set()
    if os.path.exists(target_dir):
        try:
            occupied_names = set(os.listdir(target_dir))
        except Exception:
            pass

    for t in transfer_worker_instance.transfer_items_map.values():
        if t.get("status") in ("queued", "transferring") and t.get("destination_folder") == dest_folder:
            occupied_names.add(os.path.basename(t["target_path"]))

    final_name = filename
    collision_renamed = False
    if final_name in occupied_names:
        collision_renamed = True
        counter = 1
        while f"{base}_{counter}{ext}" in occupied_names:
            counter += 1
        final_name = f"{base}_{counter}{ext}"

    target_path = os.path.join(target_dir, final_name)
    file_size = os.path.getsize(source_path)

    waiting_transcode_id = None
    for tid, t in transcode_worker_instance.transcode_items_map.items():
        if (t.get("filename") in (filename, req.file_id)) and t.get("status") in ("queued", "transcoding"):
            waiting_transcode_id = tid
            break

    task_id = uuid.uuid4().hex[:10]
    transfer_item = {
        "id": task_id,
        "file_id": req.file_id,
        "filename": filename,
        "final_filename": final_name,
        "source_path": source_path,
        "target_path": target_path,
        "destination_folder": dest_folder,
        "total_bytes": file_size,
        "bytes_copied": 0,
        "percent": 0.0,
        "size_formatted": format_size(file_size),
        "status": "queued",
        "created_at": time.time(),
        "started_at": None,
        "completed_at": None,
        "error": None,
        "collision_renamed": collision_renamed,
        "waiting_on_transcode": waiting_transcode_id,
        "abort_event": threading.Event(),
    }

    transfer_worker_instance.enqueue(transfer_item)

    if dest_folder in state["recent_destinations"]:
        state["recent_destinations"].remove(dest_folder)
    state["recent_destinations"].insert(0, dest_folder)
    save_config_data(state)

    state["undo_stack"].append({
        "type": "move",
        "task_id": task_id,
        "source_path": source_path,
        "target_path": target_path,
        "filename": filename,
        "destination_folder": dest_folder,
    })

    state["video_queue"] = [v for v in state["video_queue"] if v["id"] not in (req.file_id, filename)]

    return {
        "success": True,
        "task_id": task_id,
        "original_file": filename,
        "final_filename": final_name,
        "target_path": target_path,
        "destination_folder": dest_folder,
        "collision_renamed": collision_renamed,
        "remaining_count": len(state["video_queue"]),
        "status": "queued",
    }

@router.post("/api/undo")
async def undo_last_move():
    if not state["undo_stack"]:
        raise HTTPException(status_code=400, detail="Nothing to undo")

    last_op = state["undo_stack"].pop()
    task_id = last_op.get("task_id")
    source_path = last_op["source_path"]
    target_path = last_op["target_path"]
    filename = last_op["filename"]

    item = transfer_worker_instance.transfer_items_map.get(task_id) if task_id else None

    if item and item.get("status") == "queued":
        item["status"] = "cancelled"
        cur_src = item.get("source_path") or source_path
        meta = make_video_meta(cur_src) or make_video_meta(source_path)
        if meta:
            state["video_queue"].insert(0, meta)
        return {
            "success": True,
            "action": "cancelled_queued",
            "restored_filename": filename,
            "message": f"Instantly cancelled transfer of {filename}",
            "remaining_undo_count": len(state["undo_stack"]),
        }

    if item and item.get("status") == "transferring":
        if item.get("abort_event"):
            item["abort_event"].set()
        cur_src = item.get("source_path") or source_path
        meta = make_video_meta(cur_src) or make_video_meta(source_path)
        if meta:
            state["video_queue"].insert(0, meta)
        return {
            "success": True,
            "action": "aborted_active",
            "restored_filename": filename,
            "message": f"Aborted active transfer of {filename}",
            "remaining_undo_count": len(state["undo_stack"]),
        }

    if not os.path.exists(target_path):
        raise HTTPException(status_code=404, detail="File no longer exists at destination")

    restore_target = source_path
    if os.path.exists(restore_target):
        base, ext = os.path.splitext(source_path)
        counter = 1
        while os.path.exists(f"{base}_restored_{counter}{ext}"):
            counter += 1
        restore_target = f"{base}_restored_{counter}{ext}"

    try:
        await asyncio.to_thread(shutil.move, target_path, restore_target)
    except Exception as e:
        state["undo_stack"].append(last_op)
        raise HTTPException(status_code=500, detail=f"Failed to restore file: {str(e)}")

    meta = make_video_meta(restore_target)
    if meta:
        state["video_queue"].insert(0, meta)

    return {
        "success": True,
        "action": "restored",
        "restored_filename": os.path.basename(restore_target),
        "restored_path": restore_target,
        "remaining_undo_count": len(state["undo_stack"]),
    }

@router.get("/api/transfers")
async def get_transfers_status():
    t_status = transfer_worker_instance.get_status()
    tc_status = transcode_worker_instance.get_status()

    active_data = None
    if t_status["active"]:
        act = t_status["active"]
        active_data = {
            "id": act["id"],
            "filename": act["filename"],
            "destination_folder": act["destination_folder"],
            "bytes_copied": act.get("bytes_copied", 0),
            "total_bytes": act.get("total_bytes", 0),
            "percent": act.get("percent", 0.0),
            "status": "transferring",
        }

    queued_list = []
    for t in transfer_worker_instance.transfer_items_map.values():
        if t.get("status") == "queued":
            queued_list.append({
                "id": t["id"],
                "filename": t["filename"],
                "destination_folder": t["destination_folder"],
                "size_formatted": t.get("size_formatted", ""),
                "status": "queued",
                "created_at": t.get("created_at"),
            })
    queued_list.sort(key=lambda x: x.get("created_at", 0))

    recent_history = []
    for t in reversed(transfer_worker_instance.transfers_history[-20:]):
        if t.get("status") in ("completed", "failed", "cancelled"):
            recent_history.append({
                "id": t["id"],
                "filename": t["filename"],
                "destination_folder": t["destination_folder"],
                "status": t["status"],
                "error": t.get("error"),
                "completed_at": t.get("completed_at"),
            })

    total_pending = (1 if active_data else 0) + len(queued_list)

    transcode_active_data = None
    if tc_status["active"]:
        act_tc = tc_status["active"]
        transcode_active_data = {
            "id": act_tc["id"],
            "filename": act_tc["filename"],
            "percent": act_tc.get("percent", 0.0),
            "fps": act_tc.get("fps", 0.0),
            "speed": act_tc.get("speed", ""),
            "eta": act_tc.get("eta", ""),
            "method": act_tc.get("method", "reencode"),
            "status": "transcoding",
        }

    transcode_queued_list = []
    for t in transcode_worker_instance.transcode_items_map.values():
        if t.get("status") == "queued":
            transcode_queued_list.append({
                "id": t["id"],
                "filename": t["filename"],
                "status": "queued",
                "created_at": t.get("created_at"),
            })
    transcode_queued_list.sort(key=lambda x: x.get("created_at", 0))

    transcode_history_list = []
    for t in reversed(transcode_worker_instance.transcode_history[-15:]):
        if t.get("status") in ("completed", "failed", "cancelled"):
            transcode_history_list.append({
                "id": t["id"],
                "filename": t["filename"],
                "target_filename": os.path.basename(t["target_path"]) if t.get("target_path") else None,
                "status": t["status"],
                "error": t.get("error"),
                "method": t.get("method"),
                "completed_at": t.get("completed_at"),
            })

    return {
        "active": active_data,
        "queued": queued_list,
        "history": recent_history,
        "active_count": 1 if active_data else 0,
        "queued_count": len(queued_list),
        "total_pending": total_pending,
        "transcode": {
            "active": transcode_active_data,
            "queued": transcode_queued_list,
            "queued_count": len(transcode_queued_list),
            "history": transcode_history_list,
            "is_paused": transcode_worker_instance.transcode_paused,
            "total_pending": (1 if transcode_active_data else 0) + len(transcode_queued_list),
        }
    }

@router.post("/api/transfers/cancel")
async def cancel_transfer(req: CancelTransferRequest):
    item = transfer_worker_instance.transfer_items_map.get(req.task_id)
    if not item:
        raise HTTPException(status_code=404, detail="Transfer task not found")

    if item.get("status") == "queued":
        item["status"] = "cancelled"
        if state["source_dir"] and item["source_path"].startswith(state["source_dir"]):
            meta = make_video_meta(item["source_path"])
            if meta:
                state["video_queue"].insert(0, meta)
        return {"success": True, "action": "cancelled"}

    if item.get("status") == "transferring":
        if item.get("abort_event"):
            item["abort_event"].set()
        if state["source_dir"] and item["source_path"].startswith(state["source_dir"]):
            meta = make_video_meta(item["source_path"])
            if meta:
                state["video_queue"].insert(0, meta)
        return {"success": True, "action": "aborted"}

    return {"success": False, "detail": f"Cannot cancel transfer in state {item.get('status')}"}

@router.post("/api/rename")
def rename_video(req: RenameRequest):
    if not state["source_dir"]:
        raise HTTPException(status_code=400, detail="No source directory configured")

    source_path = resolve_video_path(req.file_id, transcode_worker_instance.transcode_items_map)
    if not source_path or not os.path.exists(source_path):
        raise HTTPException(status_code=404, detail="File not found")

    actual_file_id = os.path.basename(source_path)
    new_name = req.new_name.strip()
    if not new_name:
        raise HTTPException(status_code=400, detail="New name cannot be empty")

    _, orig_ext = os.path.splitext(actual_file_id)
    _, new_ext = os.path.splitext(new_name)
    if not new_ext:
        new_name += orig_ext

    sanitized_name = re.sub(r'[\/\\:\*\?"<>\|]', '', new_name).strip()
    target_path = os.path.join(state["source_dir"], sanitized_name)

    if os.path.exists(target_path) and target_path != source_path:
        raise HTTPException(status_code=400, detail="A file with this name already exists")

    for t in transcode_worker_instance.transcode_items_map.values():
        if t.get("filename") in (req.file_id, actual_file_id) and t.get("status") in ("queued", "transcoding"):
            if t.get("abort_event"):
                t["abort_event"].set()
            t["status"] = "cancelled"

    try:
        os.rename(source_path, target_path)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to rename file: {str(e)}")

    meta = make_video_meta(target_path)
    state["video_queue"] = [meta if v["id"] in (req.file_id, actual_file_id) else v for v in state["video_queue"]]

    return {
        "success": True,
        "old_id": req.file_id,
        "new_id": sanitized_name,
        "new_filename": sanitized_name,
    }

@router.post("/api/delete")
def delete_video(req: DeleteRequest):
    if not state["source_dir"]:
        raise HTTPException(status_code=400, detail="No source directory configured")

    source_path = resolve_video_path(req.file_id, transcode_worker_instance.transcode_items_map)
    if not source_path or not os.path.exists(source_path):
        raise HTTPException(status_code=404, detail="File not found")

    actual_file_id = os.path.basename(source_path)

    for t in transcode_worker_instance.transcode_items_map.values():
        if t.get("filename") in (req.file_id, actual_file_id) and t.get("status") in ("queued", "transcoding"):
            if t.get("abort_event"):
                t["abort_event"].set()
            t["status"] = "cancelled"

    try:
        os.remove(source_path)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete file: {str(e)}")

    state["video_queue"] = [v for v in state["video_queue"] if v["id"] not in (req.file_id, actual_file_id)]

    return {
        "success": True,
        "deleted_file": req.file_id,
        "remaining_count": len(state["video_queue"]),
    }

@router.api_route("/api/stream/{file_id}", methods=["GET", "HEAD"])
async def stream_video(file_id: str, request: Request):
    if not state["source_dir"]:
        raise HTTPException(status_code=400, detail="No source directory configured")

    file_path = resolve_video_path(file_id, transcode_worker_instance.transcode_items_map)
    if not file_path:
        raise HTTPException(status_code=404, detail="Video file not found")

    file_exists = await asyncio.to_thread(os.path.exists, file_path)
    if not file_exists:
        raise HTTPException(status_code=404, detail="Video file not found")

    file_size = await asyncio.to_thread(os.path.getsize, file_path)
    mime_type, _ = mimetypes.guess_type(file_path)
    if not mime_type:
        mime_type = "video/mp4"

    if request.method == "HEAD":
        headers = {
            "Content-Length": str(file_size),
            "Content-Type": mime_type,
            "Accept-Ranges": "bytes",
        }
        return Response(status_code=200, headers=headers)

    range_header = request.headers.get("Range")

    if not range_header:
        async def full_file_sender():
            def _open():
                return open(file_path, "rb")
            f = await asyncio.to_thread(_open)
            try:
                while True:
                    chunk = await asyncio.to_thread(f.read, 1024 * 1024)
                    if not chunk:
                        break
                    yield chunk
            finally:
                await asyncio.to_thread(f.close)

        headers = {
            "Content-Length": str(file_size),
            "Content-Type": mime_type,
            "Accept-Ranges": "bytes",
        }
        return StreamingResponse(full_file_sender(), status_code=200, headers=headers)

    try:
        range_val = range_header.strip().replace("bytes=", "")
        parts = range_val.split("-")
        start = int(parts[0]) if parts[0] else 0
        end = int(parts[1]) if len(parts) > 1 and parts[1] else file_size - 1

        if start >= file_size or end >= file_size or start > end:
            return Response(
                status_code=416,
                headers={"Content-Range": f"bytes */{file_size}"}
            )

        chunk_max = 2 * 1024 * 1024
        if (end - start + 1) > chunk_max:
            end = start + chunk_max - 1

        content_length = end - start + 1

        async def chunk_generator(filepath, start_pos, length):
            def _open_and_seek():
                f = open(filepath, "rb")
                f.seek(start_pos)
                return f

            f = await asyncio.to_thread(_open_and_seek)
            try:
                bytes_left = length
                while bytes_left > 0:
                    read_size = min(bytes_left, 128 * 1024)
                    data = await asyncio.to_thread(f.read, read_size)
                    if not data:
                        break
                    bytes_left -= len(data)
                    yield data
            finally:
                await asyncio.to_thread(f.close)

        headers = {
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Accept-Ranges": "bytes",
            "Content-Length": str(content_length),
            "Content-Type": mime_type,
        }
        return StreamingResponse(
            chunk_generator(file_path, start, content_length),
            status_code=206,
            headers=headers
        )
    except Exception as e:
        print(f"Error handling Range request for {file_id}: {e}")
        raise HTTPException(status_code=500, detail="Error streaming video range")

@router.get("/api/sorter/check-duplicate")
def check_sorter_duplicate(file_id: Optional[str] = Query(None), path: Optional[str] = Query(None)):
    target_path = None
    if path:
        target_path = os.path.abspath(path.strip())
    elif file_id:
        target_path = resolve_video_path(file_id, transcode_worker_instance.transcode_items_map)

    if not target_path or not os.path.exists(target_path) or not os.path.isfile(target_path):
        return {"is_duplicate": False, "matches": []}

    try:
        incoming_size = os.path.getsize(target_path)
        incoming_name = os.path.basename(target_path)
        if incoming_size == 0:
            return {"is_duplicate": False, "matches": []}

        conn = get_db()
        base_dir = os.path.realpath(state.get("destination_dir") or DEFAULT_DESTINATION_DIR)

        size_matches = conn.execute("""
            SELECT path, name, folder_path, type, ext, size, size_formatted, mtime, has_thumb, quick_hash
            FROM files
            WHERE size = ?
        """, (incoming_size,)).fetchall()

        matched_content = []
        if size_matches:
            incoming_hash = compute_quick_hash(target_path)
            if incoming_hash:
                for sm in size_matches:
                    cand_path = sm["path"]
                    cand_hash = sm["quick_hash"]
                    if not cand_hash:
                        cand_hash = compute_quick_hash(cand_path)
                        if cand_hash:
                            with conn:
                                conn.execute("UPDATE files SET quick_hash = ? WHERE path = ?", (cand_hash, cand_path))
                    if cand_hash and cand_hash == incoming_hash:
                        try:
                            rel_folder = os.path.relpath(sm["folder_path"], base_dir)
                        except Exception:
                            rel_folder = sm["folder_path"]
                        if rel_folder == ".":
                            rel_folder = "Root"
                        matched_content.append({
                            "path": cand_path,
                            "name": sm["name"],
                            "folder_path": sm["folder_path"],
                            "folder_display": rel_folder,
                            "size": sm["size"],
                            "size_formatted": sm["size_formatted"],
                            "match_reason": f"Exact content match ({sm['size_formatted']})",
                            "media_url": f"/api/backup/media?path={urllib.parse.quote(cand_path)}",
                            "thumb_url": f"/api/backup/thumbnail?path={urllib.parse.quote(cand_path)}",
                        })

        name_matches = conn.execute("""
            SELECT path, name, folder_path, type, ext, size, size_formatted, mtime, has_thumb
            FROM files
            WHERE name = ?
        """, (incoming_name,)).fetchall()

        matched_names = []
        existing_paths = {m["path"] for m in matched_content}
        for nm in name_matches:
            if nm["path"] not in existing_paths:
                try:
                    rel_folder = os.path.relpath(nm["folder_path"], base_dir)
                except Exception:
                    rel_folder = nm["folder_path"]
                if rel_folder == ".":
                    rel_folder = "Root"
                matched_names.append({
                    "path": nm["path"],
                    "name": nm["name"],
                    "folder_path": nm["folder_path"],
                    "folder_display": rel_folder,
                    "size": nm["size"],
                    "size_formatted": nm["size_formatted"],
                    "match_reason": f"Exact filename match (in {rel_folder})",
                    "media_url": f"/api/backup/media?path={urllib.parse.quote(nm['path'])}",
                    "thumb_url": f"/api/backup/thumbnail?path={urllib.parse.quote(nm['path'])}",
                })

        conn.close()

        if matched_content:
            return {
                "is_duplicate": True,
                "match_type": "content",
                "incoming_file": {
                    "filename": incoming_name,
                    "size": incoming_size,
                    "size_formatted": format_size(incoming_size),
                },
                "matches": matched_content
            }
        elif matched_names:
            return {
                "is_duplicate": True,
                "match_type": "name",
                "incoming_file": {
                    "filename": incoming_name,
                    "size": incoming_size,
                    "size_formatted": format_size(incoming_size),
                },
                "matches": matched_names
            }
        else:
            return {
                "is_duplicate": False,
                "matches": []
            }
    except Exception as e:
        print(f"Error checking duplicate for {target_path}: {e}")
        return {"is_duplicate": False, "matches": [], "error": str(e)}
