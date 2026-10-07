"""FastAPI router for ZIP compression, extraction cache, and archive folder management."""

import os
import asyncio
from typing import Optional, Dict, Any
from pathlib import Path
from pydantic import BaseModel
from fastapi import APIRouter, HTTPException, Query, BackgroundTasks

from media_library.services.media_service import validate_backup_path
from media_library.services.zip_service import (
    check_folder_compressibility,
    compress_folder_to_zip,
    unpack_zip_to_folder,
    get_zip_metadata,
    is_zip_file,
    is_zip_extracted,
    extract_zip_to_cache,
    zip_tasks_state,
    zip_lock,
    find_batch_zip_candidate_folders,
    start_batch_zip,
    pause_batch_zip,
    resume_batch_zip,
    cancel_batch_zip,
    get_batch_zip_status,
)

router = APIRouter(tags=["zip"])


class StartBatchZipRequest(BaseModel):
    folder_paths: list[str]


class CompressFolderRequest(BaseModel):
    folder_path: str


class UnpackZipRequest(BaseModel):
    zip_path: str


@router.get("/api/backup/zip/check")
async def check_folder_compression_eligibility(folder_path: str = Query(...)):
    """Checks if a folder is eligible for compression to ZIP (contains images, 0 videos)."""
    real_path = validate_backup_path(folder_path)
    res = await asyncio.to_thread(check_folder_compressibility, real_path)
    return res


@router.get("/api/backup/zip/status")
async def get_zip_status():
    """Returns active and recent ZIP operations for the Background Tasks panel and progress overlay."""
    with zip_lock:
        active = dict(zip_tasks_state["active_tasks"])

    tasks_list = []
    for path_key, info in active.items():
        tasks_list.append({
            "path": path_key,
            "filename": os.path.basename(path_key),
            "type": info.get("type", "zip"),
            "status": info.get("status", "running"),
            "percent": info.get("percent", 0.0),
            "current": info.get("current", 0),
            "total": info.get("total", 0),
            "message": info.get("message", ""),
            "started_at": info.get("started_at", 0),
        })

    tasks_list.sort(key=lambda x: x["started_at"], reverse=True)
    return {
        "tasks": tasks_list,
        "is_active": any(t["status"] == "running" for t in tasks_list),
    }


@router.post("/api/backup/zip/compress")
async def compress_folder_endpoint(req: CompressFolderRequest, background_tasks: BackgroundTasks):
    """
    Compresses an eligible image folder into a ZIP archive,
    verifies integrity, deletes source folder, and auto-indexes.
    """
    real_path = validate_backup_path(req.folder_path)
    check = await asyncio.to_thread(check_folder_compressibility, real_path)
    if not check["eligible"]:
        raise HTTPException(status_code=400, detail=check["reason"])

    # Run compression in thread
    def _run_compress():
        try:
            compress_folder_to_zip(real_path)
        except Exception as e:
            print(f"Error compressing folder {real_path}: {e}")

    background_tasks.add_task(_run_compress)
    folder_name = os.path.basename(real_path)
    target_zip = os.path.join(os.path.dirname(real_path), f"{folder_name}.zip")
    return {
        "success": True,
        "target_zip": target_zip,
        "message": f"Compressing {folder_name} to ZIP in background...",
    }


@router.post("/api/backup/zip/unpack")
async def unpack_zip_endpoint(req: UnpackZipRequest, background_tasks: BackgroundTasks):
    """
    Unpacks a ZIP archive back into a regular directory,
    verifies files, deletes the ZIP file, and updates index.
    """
    real_path = validate_backup_path(req.zip_path)
    if not is_zip_file(real_path):
        raise HTTPException(status_code=400, detail="Specified path is not a ZIP file")

    def _run_unpack():
        try:
            unpack_zip_to_folder(real_path)
        except Exception as e:
            print(f"Error unpacking zip {real_path}: {e}")

    background_tasks.add_task(_run_unpack)
    folder_name = Path(real_path).stem
    target_folder = os.path.join(os.path.dirname(real_path), folder_name)
    return {
        "success": True,
        "target_folder": target_folder,
        "message": f"Unpacking {os.path.basename(real_path)} into folder in background...",
    }


@router.get("/api/backup/zip/batch/candidates")
async def get_batch_zip_candidates():
    """Finds all candidate folders eligible for batch ZIP compression across the library."""
    res = await asyncio.to_thread(find_batch_zip_candidate_folders)
    return res


@router.post("/api/backup/zip/batch/start")
async def start_batch_zip_endpoint(req: StartBatchZipRequest):
    """Starts background batch compression for the specified candidate folders."""
    valid_paths = []
    for fp in req.folder_paths:
        try:
            rp = validate_backup_path(fp)
            valid_paths.append(rp)
        except Exception:
            continue

    if not valid_paths:
        raise HTTPException(status_code=400, detail="No valid folders provided for batch compression.")

    try:
        res = start_batch_zip(valid_paths)
        return res
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.post("/api/backup/zip/batch/pause")
async def pause_batch_zip_endpoint():
    """Toggles pause/resume on the active batch compression."""
    return pause_batch_zip()


@router.post("/api/backup/zip/batch/resume")
async def resume_batch_zip_endpoint():
    """Resumes paused batch compression."""
    return resume_batch_zip()


@router.post("/api/backup/zip/batch/cancel")
async def cancel_batch_zip_endpoint():
    """Cancels active batch compression."""
    return cancel_batch_zip()


@router.get("/api/backup/zip/batch/status")
async def get_batch_zip_status_endpoint():
    """Returns current batch compression status and progress metrics."""
    return get_batch_zip_status()
