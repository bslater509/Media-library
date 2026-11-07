"""Resumable chunked uploads.

The browser stages bytes with explicit offsets, so a dropped connection or a
closed tab resumes instead of restarting. Finalize verifies the staged size
before committing with an atomic replace, then indexes the new file.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile

from media_library import library, writes
from media_library.routers.common import as_http_error
from media_library.schemas import (
    UploadCancelRequest,
    UploadCancelResponse,
    UploadChunkResponse,
    UploadFinalizeRequest,
    UploadFinalizeResponse,
    UploadStatusResponse,
)

router = APIRouter()

_MAX_CHUNK = 64 * 1024 * 1024


@router.get("/uploads/status", response_model=UploadStatusResponse)
async def upload_status(upload_id: str = Query(..., min_length=6)) -> dict:
    """Bytes already staged for this upload id."""
    try:
        return await asyncio.to_thread(writes.staged_size, upload_id)
    except Exception as exc:
        raise as_http_error(exc) from exc


@router.post("/uploads/chunk", response_model=UploadChunkResponse)
async def upload_chunk(
    upload_id: str = Form(...),
    offset: int = Form(0),
    file: UploadFile = File(...),
) -> dict:
    """Append one chunk at its explicit byte offset."""
    data = await file.read(_MAX_CHUNK + 1)
    if len(data) > _MAX_CHUNK:
        raise HTTPException(status_code=413, detail="Chunk too large")
    if offset < 0:
        raise HTTPException(status_code=400, detail="Offset must be positive")
    try:
        return await asyncio.to_thread(writes.stage_chunk, upload_id, offset, data)
    except Exception as exc:
        raise as_http_error(exc) from exc


@router.post("/uploads/finalize", response_model=UploadFinalizeResponse)
async def upload_finalize(payload: UploadFinalizeRequest) -> dict:
    """Commit a fully staged upload into its target folder."""
    try:
        result = await asyncio.to_thread(
            writes.finalize_upload,
            payload.upload_id,
            payload.name,
            payload.folder,
            payload.total_size,
            payload.conflict,
        )
        entry = await asyncio.to_thread(library.get_file, result["path"])
    except Exception as exc:
        raise as_http_error(exc) from exc
    return {**result, "file": entry}


@router.post("/uploads/cancel", response_model=UploadCancelResponse)
async def upload_cancel(payload: UploadCancelRequest) -> dict:
    try:
        return await asyncio.to_thread(writes.cancel_upload, payload.upload_id)
    except Exception as exc:
        raise as_http_error(exc) from exc
