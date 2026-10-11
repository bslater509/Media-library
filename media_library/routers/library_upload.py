import logging
from typing import Any

from fastapi import APIRouter, Form, HTTPException, UploadFile, File
from pydantic import BaseModel

from media_library.services.upload_service import (
    cancel_upload,
    finalize_upload,
    handle_upload_chunk,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Library Upload"])


@router.post("/api/backup/upload/chunk")
async def upload_chunk(
    upload_id: str = Form(...),
    chunk_index: int = Form(...),
    total_chunks: int = Form(...),
    chunk: UploadFile = File(...)
) -> dict[str, Any]:
    """Receives a chunk of an uploaded file."""
    try:
        chunk_data = await chunk.read()
        return await handle_upload_chunk(
            upload_id=upload_id,
            chunk_index=chunk_index,
            total_chunks=total_chunks,
            chunk_data=chunk_data,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in upload_chunk: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class FinalizeUploadRequest(BaseModel):
    upload_id: str
    original_filename: str
    relative_path: str
    destination_dir: str
    collision_policy: str = "auto-rename"
    total_size: int = 0
    mtime: int | None = None


@router.post("/api/backup/upload/finalize")
async def finish_upload(req: FinalizeUploadRequest) -> dict[str, Any]:
    """Finalizes an upload by moving it to the destination and indexing it."""
    return await finalize_upload(
        upload_id=req.upload_id,
        original_filename=req.original_filename,
        relative_path=req.relative_path,
        destination_dir=req.destination_dir,
        collision_policy=req.collision_policy,
        mtime=req.mtime,
    )


class CancelUploadRequest(BaseModel):
    upload_id: str


@router.post("/api/backup/upload/cancel")
async def abort_upload(req: CancelUploadRequest) -> dict[str, Any]:
    """Cancels an upload and removes the staging file."""
    return await cancel_upload(upload_id=req.upload_id)
