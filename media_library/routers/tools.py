"""Downloader tool endpoints: versions, update checks, in-place updates."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Query

from media_library import tools
from media_library.db import writes_available
from media_library.routers.common import as_http_error
from media_library.schemas import (
    ToolStatusResponse,
    ToolUpdateCancelResponse,
    ToolUpdateJob,
    ToolUpdateRequest,
)

router = APIRouter()


@router.get("/tools/status", response_model=ToolStatusResponse)
async def status(refresh: bool = Query(False)) -> dict:
    """Installed versions, latest PyPI releases and the last update job."""
    return await asyncio.to_thread(tools.tools_status, refresh)


@router.post("/tools/update", response_model=ToolUpdateJob)
async def start(payload: ToolUpdateRequest) -> dict:
    if not writes_available():
        raise HTTPException(
            503, "Server updates are disabled while the index is read-only"
        )
    try:
        return await asyncio.to_thread(tools.start_update, payload.tools)
    except Exception as exc:
        raise as_http_error(exc) from exc


@router.post("/tools/update/cancel", response_model=ToolUpdateCancelResponse)
async def cancel() -> dict:
    try:
        return await asyncio.to_thread(tools.cancel_update)
    except Exception as exc:
        raise as_http_error(exc) from exc
