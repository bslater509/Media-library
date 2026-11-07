"""WebP optimisation endpoints"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter

from media_library import webp
from media_library.routers.common import as_http_error
from media_library.schemas import (
    WebpOptimizeRequest,
    WebpOptimizeResponse,
    WebpStatus,
)

router = APIRouter()


@router.get("/webp/status", response_model=WebpStatus)
async def status() -> dict:
    return await asyncio.to_thread(webp.status)


@router.post("/webp/optimize", response_model=WebpOptimizeResponse)
async def optimize(payload: WebpOptimizeRequest) -> dict:
    try:
        return await asyncio.to_thread(
            webp.optimize_batch, payload.limit, payload.folder, payload.paths
        )
    except Exception as exc:
        raise as_http_error(exc) from exc
