"""Index-only analytics endpoints for the dashboard."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Query

from media_library import analytics, growth
from media_library.routers.common import as_http_error
from media_library.schemas import AnalyticsGrowth, AnalyticsOverview, GrowthPoint

router = APIRouter()


@router.get("/analytics/overview", response_model=AnalyticsOverview)
async def overview(
    folder_limit: int = Query(15, ge=1, le=100),
    file_limit: int = Query(25, ge=1, le=100),
    refresh: bool = Query(False),
) -> dict:
    """Totals, folder rollups, quality, reclaimable space and health.

    Computed from the index alone; memoised for 30 seconds unless ``refresh``
    is set. The app owns the crawler, so there is no revision counter to key
    on — the TTL is the staleness bound.
    """
    return await asyncio.to_thread(
        analytics.overview, folder_limit, file_limit, refresh
    )


@router.get("/analytics/growth", response_model=AnalyticsGrowth)
async def growth_history(
    days: int = Query(30, ge=1, le=3650),
) -> dict:
    """Recorded daily rollups, oldest first, capped to ``days``.

    Index-only read (AGENTS.md Rule 2), off the event loop like the other
    listings. ``today`` carries the current live counts so a chart's final
    point is fresh even before the worker upserts again.
    """
    payload = await asyncio.to_thread(growth.read_history, days)
    status = growth.worker_status()
    if status is not None:
        payload["worker"] = status
    return payload


@router.post("/analytics/growth/refresh", response_model=GrowthPoint)
async def refresh_growth() -> dict:
    """Recompute and upsert today's rollup row now.

    The write runs off the event loop (AGENTS.md Rule 3); a read-only index
    answers 503, exactly like the other mutation routes.
    """
    try:
        return await asyncio.to_thread(growth.record_today)
    except Exception as exc:
        raise as_http_error(exc) from exc
