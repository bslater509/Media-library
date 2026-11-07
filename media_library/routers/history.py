"""Server-side watch history: record a viewing, list recent and in-progress.

``POST /history/record`` is a mutation, so it runs off the event loop
(``asyncio.to_thread``, AGENTS.md Rule 3); the two listings are index-only
reads and go through a thread as well, matching the rest of the read routers.
"""

from __future__ import annotations

import asyncio
from typing import Literal

from fastapi import APIRouter, Query

from media_library import library, writes
from media_library.routers.common import as_http_error
from media_library.schemas import (
    ViewRecordRequest,
    ViewRecordResponse,
    WatchedListResponse,
)

router = APIRouter()


@router.post("/history/record", response_model=ViewRecordResponse)
async def record_view(payload: ViewRecordRequest) -> dict:
    """Persist one viewing of an indexed file as a per-path rollup."""
    try:
        return await asyncio.to_thread(
            writes.record_view, payload.path, payload.seconds, payload.bump
        )
    except Exception as exc:
        raise as_http_error(exc) from exc


@router.get("/history/recent", response_model=WatchedListResponse)
async def watched_recent(
    type: Literal["video", "image"] | None = Query(default=None),
    limit: int | None = None,
    offset: int = 0,
) -> dict:
    """Most recently viewed files, newest view first."""
    return await asyncio.to_thread(library.watched_recent, type, limit, offset)


@router.get("/history/resume", response_model=WatchedListResponse)
async def watched_resume(limit: int | None = None, offset: int = 0) -> dict:
    """Videos still in progress: started but not yet at the last half-minute."""
    return await asyncio.to_thread(library.watched_resume, limit, offset)


@router.get("/history/unwatched", response_model=WatchedListResponse)
async def watched_unseen(
    type: Literal["video", "image"] | None = Query(default=None),
    limit: int | None = None,
    offset: int = 0,
) -> dict:
    """Indexed media never opened yet."""
    return await asyncio.to_thread(library.watched_unseen, type, limit, offset)
