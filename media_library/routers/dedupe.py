"""Deduplication endpoints"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Query

from media_library import dedupe
from media_library.routers.common import as_http_error
from media_library.schemas import (
    DedupeGroups,
    DedupeHashRequest,
    DedupeHashResponse,
    DedupeIgnoreRequest,
    DedupeIgnoreResponse,
    DedupeResetResponse,
    DedupeResolveRequest,
    DedupeResolveResponse,
    DedupeStatus,
    DedupeUnignoreRequest,
    DedupeUnignoreResponse,
    DedupeVerification,
    DedupeVerifyRequest,
    DedupeVerifyResponse,
    DedupeVisualVerifyRequest,
    IgnoredPairsPage,
)

router = APIRouter()


@router.get("/dedupe/status", response_model=DedupeStatus)
async def status() -> dict:
    return await asyncio.to_thread(dedupe.status)


@router.get("/dedupe/groups", response_model=DedupeGroups)
async def groups(
    mode: str = Query("all"),
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> dict:
    if mode not in ("all", "content", "visual"):
        from fastapi import HTTPException

        raise HTTPException(status_code=400, detail="Unknown match mode")
    worker = {
        "all": dedupe.all_groups,
        "content": dedupe.content_groups,
        "visual": dedupe.visual_groups,
    }[mode]
    return await asyncio.to_thread(worker, limit, offset)


@router.post("/dedupe/verify-visual", response_model=DedupeVerification)
async def verify_visual(payload: DedupeVisualVerifyRequest) -> dict:
    try:
        return await asyncio.to_thread(dedupe.verify_visual_group, payload.paths)
    except Exception as exc:
        raise as_http_error(exc) from exc


@router.post("/dedupe/reset", response_model=DedupeResetResponse)
async def reset() -> dict:
    try:
        return await asyncio.to_thread(dedupe.reset_duplicates)
    except Exception as exc:
        raise as_http_error(exc) from exc


@router.post("/dedupe/hash", response_model=DedupeHashResponse)
async def hash_files(payload: DedupeHashRequest) -> dict:
    if payload.mode not in ("content", "visual"):
        from fastapi import HTTPException

        raise HTTPException(status_code=400, detail="Unknown match mode")
    worker = dedupe.hash_content if payload.mode == "content" else dedupe.hash_visual
    try:
        return await asyncio.to_thread(worker, payload.limit)
    except Exception as exc:
        raise as_http_error(exc) from exc


@router.post("/dedupe/verify", response_model=DedupeVerifyResponse)
async def verify(payload: DedupeVerifyRequest) -> dict:
    try:
        return await asyncio.to_thread(dedupe.verify_pair, payload.keep, payload.path)
    except Exception as exc:
        raise as_http_error(exc) from exc


@router.post("/dedupe/resolve", response_model=DedupeResolveResponse)
async def resolve(payload: DedupeResolveRequest) -> dict:
    try:
        return await asyncio.to_thread(
            dedupe.resolve, payload.keep, payload.delete, payload.mode
        )
    except Exception as exc:
        raise as_http_error(exc) from exc


@router.post("/dedupe/ignore", response_model=DedupeIgnoreResponse)
async def ignore(payload: DedupeIgnoreRequest) -> dict:
    try:
        return await asyncio.to_thread(
            dedupe.ignore_pair, payload.path1, payload.path2, payload.match_type
        )
    except Exception as exc:
        raise as_http_error(exc) from exc


@router.get("/dedupe/ignored", response_model=IgnoredPairsPage)
async def ignored(
    limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)
) -> dict:
    return await asyncio.to_thread(dedupe.ignored_page, limit, offset)


@router.post("/dedupe/unignore", response_model=DedupeUnignoreResponse)
async def unignore(payload: DedupeUnignoreRequest) -> dict:
    try:
        return await asyncio.to_thread(
            dedupe.unignore_pair, payload.path1, payload.path2
        )
    except Exception as exc:
        raise as_http_error(exc) from exc
