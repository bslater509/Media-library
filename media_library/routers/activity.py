"""Global memory-only activity summary and existing worker controls."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException

from media_library import activity, animated, tools, zip_batch
from media_library.routers.common import as_http_error
from media_library.routers.downloads import manager as downloads
from media_library.routers.x265 import manager as x265
from media_library.schemas import (
    ActivityActionRequest,
    ActivityPriorityRequest,
    ActivityResponse,
)

router = APIRouter()


@router.get("/system/activity", response_model=ActivityResponse)
async def status() -> dict:
    return activity.summary()


@router.post("/system/activity/priority", response_model=ActivityResponse)
async def priority(payload: ActivityPriorityRequest) -> dict:
    activity.set_priority(payload.prioritize_browsing)
    return activity.summary()


@router.post("/system/activity/action", response_model=ActivityResponse)
async def action(payload: ActivityActionRequest) -> dict:
    entry = next(
        (row for row in activity.summary()["items"] if row["id"] == payload.id), None
    )
    if entry is None:
        raise HTTPException(404, "Activity not found")
    if payload.action not in entry["controls"]:
        raise HTTPException(409, "This action is not available for this activity")
    try:
        if payload.id.startswith("download:"):
            if payload.action == "retry":
                await downloads.retry(entry["job_id"])
            else:
                downloads.cancel(entry["job_id"])
        elif payload.id.startswith("x265:"):
            x265.cancel(entry["job_id"])
        elif payload.id == "zip":
            await asyncio.to_thread(getattr(zip_batch.runner, payload.action))
        elif payload.id == "animated":
            animated.cancel()
        elif payload.id == "tool-update":
            await asyncio.to_thread(tools.cancel_update)
    except Exception as exc:
        raise as_http_error(exc) from exc
    return activity.summary()
