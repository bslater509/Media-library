"""ZIP compression/unpacking endpoints"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Query

from media_library import zip_ops, zip_webp
from media_library.routers.common import as_http_error
from media_library.schemas import (
    ZipCheck,
    ZipCompressRequest,
    ZipCompressResponse,
    ZipOverview,
    ZipUnpackRequest,
    ZipUnpackResponse,
    ZipWebpRequest,
    ZipWebpResponse,
)

router = APIRouter()


@router.get("/zip/check", response_model=ZipCheck)
async def check(path: str = Query(...)) -> dict:
    try:
        return await asyncio.to_thread(zip_ops.check_folder, path)
    except Exception as exc:
        raise as_http_error(exc) from exc


@router.post("/zip/compress", response_model=ZipCompressResponse)
async def compress(payload: ZipCompressRequest) -> dict:
    try:
        return await asyncio.to_thread(zip_ops.compress_folder, payload.path)
    except Exception as exc:
        raise as_http_error(exc) from exc


@router.post("/zip/unpack", response_model=ZipUnpackResponse)
async def unpack(payload: ZipUnpackRequest) -> dict:
    try:
        return await asyncio.to_thread(zip_ops.unpack_archive, payload.path)
    except Exception as exc:
        raise as_http_error(exc) from exc


@router.post("/zip/webp/optimize", response_model=ZipWebpResponse)
async def optimize_archive(payload: ZipWebpRequest) -> dict:
    try:
        return await asyncio.to_thread(zip_webp.optimize_archive, payload.path)
    except Exception as exc:
        raise as_http_error(exc) from exc


@router.get("/zip/overview", response_model=ZipOverview)
async def overview() -> dict:
    def build() -> dict:
        return {
            "folders": zip_ops.archive_candidates(""),
            "archives": zip_ops.archives(),
            "cache": zip_ops.cache_info(),
        }

    return await asyncio.to_thread(build)
