"""Empty-folder cleanup endpoints"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter

from media_library import cleanup
from media_library.routers.common import as_http_error
from media_library.schemas import (
    EmptyDeleteRequest,
    EmptyDeleteResponse,
    EmptyFoldersResponse,
)

router = APIRouter()


@router.get("/folders/empty", response_model=EmptyFoldersResponse)
async def empty_folders() -> dict:
    return await asyncio.to_thread(cleanup.candidates)


@router.post("/folders/empty/delete", response_model=EmptyDeleteResponse)
async def delete_empty(payload: EmptyDeleteRequest) -> dict:
    try:
        return await asyncio.to_thread(cleanup.delete_empty, payload.paths)
    except Exception as exc:
        raise as_http_error(exc) from exc
