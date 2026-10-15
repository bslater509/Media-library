"""FastAPI router for the Video Sorter: moves, undo, transfers, rename, and delete."""

import asyncio
import logging
import os
import re
import shutil
import threading
import time
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from media_library.core.config import save_config_data
from media_library.core.state import format_size, make_video_meta, state
from media_library.services.media_service import resolve_video_path
from media_library.services.status import transfers_payload
from media_library.workers.transcode_worker import transcode_worker_instance
from media_library.workers.transfer_worker import transfer_worker_instance

router = APIRouter()

logger = logging.getLogger(__name__)


class MoveRequest(BaseModel):
    file_id: str
    destination_folder: str
    is_favorite: bool = False


class RenameRequest(BaseModel):
    file_id: str
    new_name: str


class DeleteRequest(BaseModel):
    file_id: str


class CancelTransferRequest(BaseModel):
    task_id: str


@router.post("/api/move")
def move_video(req: MoveRequest) -> dict:
    if not state["source_dir"]:
        raise HTTPException(status_code=400, detail="No source directory configured")

    source_path = resolve_video_path(
        req.file_id, transcode_worker_instance.transcode_items_map
    )
    if not source_path or not os.path.exists(source_path):
        raise HTTPException(status_code=404, detail="Source file not found")

    dest_folder = req.destination_folder.strip()
    if not dest_folder:
        raise HTTPException(
            status_code=400, detail="Destination folder cannot be empty"
        )

    target_dir = os.path.join(state["destination_dir"], dest_folder)
    try:
        os.makedirs(target_dir, exist_ok=True)
    except Exception:
        logger.exception("Failed to prepare destination %s", target_dir)
        raise HTTPException(status_code=500, detail="Failed to prepare destination")

    filename = os.path.basename(source_path)
    base, ext = os.path.splitext(filename)

    occupied_names = set()
    if os.path.exists(target_dir):
        try:
            occupied_names = set(os.listdir(target_dir))
        except OSError:
            # Listing failed (e.g. FUSE timeout): treat destination as empty;
            # the mover re-checks collisions on the transfer side anyway.
            logger.debug(
                "Could not list destination %s for occupancy check",
                target_dir,
                exc_info=True,
            )

    for t in transfer_worker_instance.transfer_items_map.values():
        if (
            t.get("status") in ("queued", "transferring")
            and t.get("destination_folder") == dest_folder
        ):
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
        if (t.get("filename") in (filename, req.file_id)) and t.get("status") in (
            "queued",
            "transcoding",
        ):
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
        "is_favorite": req.is_favorite,
        "abort_event": threading.Event(),
    }

    transfer_worker_instance.enqueue(transfer_item)

    state.push_recent(dest_folder)
    save_config_data(state)

    state.push_undo(
        {
            "type": "move",
            "task_id": task_id,
            "source_path": source_path,
            "target_path": target_path,
            "filename": filename,
            "destination_folder": dest_folder,
        }
    )

    state.update_queue(
        lambda q: [v for v in q if v["id"] not in (req.file_id, filename)]
    )

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
async def undo_last_move() -> dict:
    if not state["undo_stack"]:
        raise HTTPException(status_code=400, detail="Nothing to undo")

    last_op = state.pop_undo()
    if last_op is None:
        raise HTTPException(status_code=400, detail="Nothing to undo")
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
            state.push_queue_head(meta)
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
            state.push_queue_head(meta)
        return {
            "success": True,
            "action": "aborted_active",
            "restored_filename": filename,
            "message": f"Aborted active transfer of {filename}",
            "remaining_undo_count": len(state["undo_stack"]),
        }

    if not os.path.exists(target_path):
        raise HTTPException(
            status_code=404, detail="File no longer exists at destination"
        )

    restore_target = source_path
    if os.path.exists(restore_target):
        base, ext = os.path.splitext(source_path)
        counter = 1
        while os.path.exists(f"{base}_restored_{counter}{ext}"):
            counter += 1
        restore_target = f"{base}_restored_{counter}{ext}"

    try:
        await asyncio.to_thread(shutil.move, target_path, restore_target)
    except Exception:
        logger.exception("Failed to restore file %s", target_path)
        state.push_undo(last_op)
        raise HTTPException(status_code=500, detail="Failed to restore file")

    meta = make_video_meta(restore_target)
    if meta:
        state.push_queue_head(meta)

    return {
        "success": True,
        "action": "restored",
        "restored_filename": os.path.basename(restore_target),
        "restored_path": restore_target,
        "remaining_undo_count": len(state["undo_stack"]),
    }


@router.get("/api/transfers")
async def get_transfers_status() -> dict:
    return transfers_payload()


@router.post("/api/transfers/cancel")
async def cancel_transfer(req: CancelTransferRequest) -> dict:
    item = transfer_worker_instance.transfer_items_map.get(req.task_id)
    if not item:
        raise HTTPException(status_code=404, detail="Transfer task not found")

    if item.get("status") == "queued":
        item["status"] = "cancelled"
        if state["source_dir"] and item["source_path"].startswith(state["source_dir"]):
            meta = make_video_meta(item["source_path"])
            if meta:
                state.push_queue_head(meta)
        return {"success": True, "action": "cancelled"}

    if item.get("status") == "transferring":
        if item.get("abort_event"):
            item["abort_event"].set()
        if state["source_dir"] and item["source_path"].startswith(state["source_dir"]):
            meta = make_video_meta(item["source_path"])
            if meta:
                state.push_queue_head(meta)
        return {"success": True, "action": "aborted"}

    return {
        "success": False,
        "detail": f"Cannot cancel transfer in state {item.get('status')}",
    }


@router.post("/api/rename")
def rename_video(req: RenameRequest) -> dict:
    if not state["source_dir"]:
        raise HTTPException(status_code=400, detail="No source directory configured")

    source_path = resolve_video_path(
        req.file_id, transcode_worker_instance.transcode_items_map
    )
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

    sanitized_name = re.sub(r'[\/\\:\*\?"<>\|]', "", new_name).strip()
    target_path = os.path.join(state["source_dir"], sanitized_name)

    if os.path.exists(target_path) and target_path != source_path:
        raise HTTPException(
            status_code=400, detail="A file with this name already exists"
        )

    for t in transcode_worker_instance.transcode_items_map.values():
        if t.get("filename") in (req.file_id, actual_file_id) and t.get("status") in (
            "queued",
            "transcoding",
        ):
            if t.get("abort_event"):
                t["abort_event"].set()
            t["status"] = "cancelled"

    try:
        os.rename(source_path, target_path)
    except Exception:
        logger.exception("Failed to rename file %s to %s", source_path, target_path)
        raise HTTPException(status_code=500, detail="Failed to rename file")

    meta = make_video_meta(target_path)
    if meta is not None:
        state.update_queue(
            lambda q: [
                meta if v["id"] in (req.file_id, actual_file_id) else v for v in q
            ]
        )
    else:
        # Metadata rebuild failed after rename; drop the stale entry so the
        # queue never references a path that no longer exists.
        state.update_queue(
            lambda q: [v for v in q if v["id"] not in (req.file_id, actual_file_id)]
        )

    return {
        "success": True,
        "old_id": req.file_id,
        "new_id": sanitized_name,
        "new_filename": sanitized_name,
    }


@router.post("/api/delete")
def delete_video(req: DeleteRequest) -> dict:
    if not state["source_dir"]:
        raise HTTPException(status_code=400, detail="No source directory configured")

    source_path = resolve_video_path(
        req.file_id, transcode_worker_instance.transcode_items_map
    )
    if not source_path or not os.path.exists(source_path):
        raise HTTPException(status_code=404, detail="File not found")

    actual_file_id = os.path.basename(source_path)

    for t in transcode_worker_instance.transcode_items_map.values():
        if t.get("filename") in (req.file_id, actual_file_id) and t.get("status") in (
            "queued",
            "transcoding",
        ):
            if t.get("abort_event"):
                t["abort_event"].set()
            t["status"] = "cancelled"

    try:
        os.remove(source_path)
    except Exception:
        logger.exception("Failed to delete file %s", source_path)
        raise HTTPException(status_code=500, detail="Failed to delete file")

    state.update_queue(
        lambda q: [v for v in q if v["id"] not in (req.file_id, actual_file_id)]
    )

    return {
        "success": True,
        "deleted_file": req.file_id,
        "remaining_count": len(state["video_queue"]),
    }
