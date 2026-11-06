"""Favorite toggling for v2."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter

from media_library_v2 import library, writes
from media_library_v2.routers.common import as_http_error
from media_library_v2.schemas import FavoriteRequest, FavoriteResponse

router = APIRouter()


@router.post("/favorites/toggle", response_model=FavoriteResponse)
async def toggle_favorite(payload: FavoriteRequest) -> dict:
    """Star or unstar one indexed file."""
    try:
        result = await asyncio.to_thread(writes.toggle_favorite, payload.path)
    except Exception as exc:
        raise as_http_error(exc) from exc
    return result


@router.get("/favorites/counts", response_model=dict)
async def favorite_counts() -> dict:
    return library.counts()
