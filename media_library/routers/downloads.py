"""URL / playlist download endpoints"""

from __future__ import annotations

from fastapi import APIRouter, Query

from media_library.downloads import DownloadManager
from media_library.routers.common import as_http_error
from media_library.schemas import (
    CancelResponse,
    DownloadClearResponse,
    DownloadEnqueueRequest,
    DownloadEnqueueResponse,
    DownloadHistoryResponse,
    DownloadJobRequest,
    DownloadRetryResponse,
    DownloadStatusResponse,
)

router = APIRouter()

manager = DownloadManager()


@router.get("/downloads/status", response_model=DownloadStatusResponse)
async def status() -> dict:
    return manager.status()


@router.post("/downloads/queue", response_model=DownloadEnqueueResponse)
async def enqueue(payload: DownloadEnqueueRequest) -> dict:
    try:
        return await manager.enqueue(payload.urls, payload.destination)
    except Exception as exc:
        raise as_http_error(exc) from exc


@router.get("/downloads/history", response_model=DownloadHistoryResponse)
async def history(
    limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0)
) -> dict:
    return manager.history_page(limit=limit, offset=offset)


@router.post("/downloads/cancel", response_model=CancelResponse)
async def cancel(payload: DownloadJobRequest) -> dict:
    try:
        return manager.cancel(payload.job_id)
    except Exception as exc:
        raise as_http_error(exc) from exc


@router.post("/downloads/retry", response_model=DownloadRetryResponse)
async def retry(payload: DownloadJobRequest) -> dict:
    try:
        return await manager.retry(payload.job_id)
    except Exception as exc:
        raise as_http_error(exc) from exc


@router.post("/downloads/clear-history", response_model=DownloadClearResponse)
async def clear_history() -> dict:
    return manager.clear_history()
