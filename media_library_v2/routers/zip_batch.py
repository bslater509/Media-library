"""Batch ZIP endpoints for v2."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter

from media_library_v2 import zip_batch
from media_library_v2.routers.common import as_http_error
from media_library_v2.schemas import ZipBatchStartRequest, ZipBatchStatus

router = APIRouter()


@router.get("/zip/batch/status", response_model=ZipBatchStatus)
async def status() -> dict:
    return await asyncio.to_thread(zip_batch.runner.status)


@router.post("/zip/batch/start", response_model=ZipBatchStatus)
async def start(payload: ZipBatchStartRequest) -> dict:
    try:
        return await asyncio.to_thread(
            zip_batch.runner.start, payload.mode, payload.paths
        )
    except Exception as exc:
        raise as_http_error(exc) from exc


@router.post("/zip/batch/pause", response_model=ZipBatchStatus)
async def pause() -> dict:
    try:
        return await asyncio.to_thread(zip_batch.runner.pause)
    except Exception as exc:
        raise as_http_error(exc) from exc


@router.post("/zip/batch/resume", response_model=ZipBatchStatus)
async def resume() -> dict:
    try:
        return await asyncio.to_thread(zip_batch.runner.resume)
    except Exception as exc:
        raise as_http_error(exc) from exc


@router.post("/zip/batch/cancel", response_model=ZipBatchStatus)
async def cancel() -> dict:
    try:
        return await asyncio.to_thread(zip_batch.runner.cancel)
    except Exception as exc:
        raise as_http_error(exc) from exc
