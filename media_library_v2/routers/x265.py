"""In-place H.265 conversion endpoints for v2."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Query

from media_library_v2.routers.common import as_http_error
from media_library_v2.schemas import (
    X265CancelRequest,
    X265Job,
    X265Request,
    X265Status,
)
from media_library_v2.x265 import X265Manager

router = APIRouter()
manager = X265Manager()


@router.post("/transcode/x265", response_model=X265Job)
async def start(payload: X265Request) -> dict:
    try:
        return await manager.enqueue(payload.path)
    except Exception as exc:
        raise as_http_error(exc) from exc


@router.get("/transcode/x265/status", response_model=X265Status)
async def status(path: str | None = Query(None)) -> dict:
    return await asyncio.to_thread(manager.status, path)


@router.post("/transcode/x265/cancel")
async def cancel(payload: X265CancelRequest) -> dict:
    try:
        return await asyncio.to_thread(manager.cancel, payload.job_id)
    except Exception as exc:
        raise as_http_error(exc) from exc
