"""FastAPI router for ZIP compression, extraction cache, and archive folder management."""

import asyncio
import logging
import os
import threading
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from media_library.services.media_service import validate_backup_path
from media_library.services.status import zip_status_payload
from media_library.services.zip import (
    cancel_batch_unzip,
    cancel_batch_zip,
    check_folder_compressibility,
    compress_folder_to_zip,
    find_batch_unzip_candidate_zips,
    find_batch_zip_candidate_folders,
    get_batch_unzip_status,
    get_batch_zip_status,
    is_zip_file,
    pause_batch_unzip,
    pause_batch_zip,
    resume_batch_unzip,
    resume_batch_zip,
    start_batch_unzip,
    start_batch_zip,
    unpack_zip_to_folder,
)

router = APIRouter(tags=["zip"])

logger = logging.getLogger(__name__)


class StartBatchZipRequest(BaseModel):
    folder_paths: list[str]


class StartBatchUnpackRequest(BaseModel):
    zip_paths: list[str]


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
async def get_zip_status() -> dict:
    """Returns active and recent ZIP operations for the Background Tasks panel and progress overlay."""
    return zip_status_payload()


@router.post("/api/backup/zip/compress")
async def compress_folder_endpoint(req: CompressFolderRequest):
    """
    Compresses an eligible image folder into a ZIP archive,
    verifies integrity, deletes source folder, and auto-indexes.
    """
    real_path = validate_backup_path(req.folder_path)
    check = await asyncio.to_thread(check_folder_compressibility, real_path)
    if not check["eligible"]:
        raise HTTPException(status_code=400, detail=check["reason"])

    # Run compression on a daemon thread; compress_folder_to_zip registers and
    # completes/fails its own zip_tasks_state entry, so /api/backup/zip/status
    # reflects progress and errors without a BackgroundTasks wrapper.
    def _run_compress():
        try:
            compress_folder_to_zip(real_path)
        except Exception:
            logger.exception("Error compressing folder %s", real_path)

    threading.Thread(target=_run_compress, daemon=True).start()
    folder_name = os.path.basename(real_path)
    target_zip = os.path.join(os.path.dirname(real_path), f"{folder_name}.zip")
    return {
        "success": True,
        "target_zip": target_zip,
        "message": f"Compressing {folder_name} to ZIP in background...",
    }


@router.post("/api/backup/zip/unpack")
async def unpack_zip_endpoint(req: UnpackZipRequest):
    """
    Unpacks a ZIP archive back into a regular directory,
    verifies files, deletes the ZIP file, and updates index.
    """
    real_path = validate_backup_path(req.zip_path)
    if not is_zip_file(real_path):
        raise HTTPException(status_code=400, detail="Specified path is not a ZIP file")

    # Run unpacking on a daemon thread; unpack_zip_to_folder registers and
    # completes/fails its own zip_tasks_state entry so /api/backup/zip/status
    # reflects progress and errors without a BackgroundTasks wrapper.
    def _run_unpack():
        try:
            unpack_zip_to_folder(real_path)
        except Exception:
            logger.exception("Error unpacking zip %s", real_path)

    threading.Thread(target=_run_unpack, daemon=True).start()
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
        except Exception as e:
            logger.warning(f"Skipping invalid batch-compress path {fp}: {e}")
            continue

    if not valid_paths:
        raise HTTPException(
            status_code=400, detail="No valid folders provided for batch compression."
        )

    try:
        res = start_batch_zip(valid_paths)
        return res
    except RuntimeError:
        raise HTTPException(
            status_code=409, detail="Batch ZIP compression is already in progress"
        )


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


@router.get("/api/backup/zip/batch-unpack/candidates")
async def get_batch_unpack_candidates():
    """Finds all indexed ZIP archives eligible for batch extraction to folders."""
    res = await asyncio.to_thread(find_batch_unzip_candidate_zips)
    return res


@router.post("/api/backup/zip/batch-unpack/start")
async def start_batch_unpack_endpoint(req: StartBatchUnpackRequest):
    """Starts background batch extraction for the specified ZIP archives."""
    valid_paths = []
    for zp in req.zip_paths:
        try:
            rp = validate_backup_path(zp)
            valid_paths.append(rp)
        except Exception as e:
            logger.warning(f"Skipping invalid batch-unpack path {zp}: {e}")
            continue

    if not valid_paths:
        raise HTTPException(
            status_code=400,
            detail="No valid ZIP archives provided for batch extraction.",
        )

    try:
        res = start_batch_unzip(valid_paths)
        return res
    except RuntimeError:
        raise HTTPException(
            status_code=409, detail="Batch ZIP extraction is already in progress"
        )


@router.post("/api/backup/zip/batch-unpack/pause")
async def pause_batch_unpack_endpoint():
    """Toggles pause/resume on the active batch extraction."""
    return pause_batch_unzip()


@router.post("/api/backup/zip/batch-unpack/resume")
async def resume_batch_unpack_endpoint():
    """Resumes paused batch extraction."""
    return resume_batch_unzip()


@router.post("/api/backup/zip/batch-unpack/cancel")
async def cancel_batch_unpack_endpoint():
    """Cancels active batch extraction."""
    return cancel_batch_unzip()


@router.get("/api/backup/zip/batch-unpack/status")
async def get_batch_unpack_status_endpoint():
    """Returns current batch extraction status and progress metrics."""
    return get_batch_unzip_status()
