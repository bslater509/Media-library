"""ZIP unpacking endpoints (archives themselves stay browsable in the Library)"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter

from media_library import zip_ops
from media_library.routers.common import as_http_error
from media_library.schemas import ZipOverview, ZipUnpackRequest, ZipUnpackResponse

router = APIRouter()


@router.post("/zip/unpack", response_model=ZipUnpackResponse)
async def unpack(payload: ZipUnpackRequest) -> dict:
    try:
        return await asyncio.to_thread(zip_ops.unpack_archive, payload.path)
    except Exception as exc:
        raise as_http_error(exc) from exc


@router.get("/zip/overview", response_model=ZipOverview)
async def overview() -> dict:
    def build() -> dict:
        return {
            "archives": zip_ops.archives(),
            "cache": zip_ops.cache_info(),
        }

    return await asyncio.to_thread(build)
