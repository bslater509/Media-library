"""Index-only analytics endpoint for the dashboard."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Query

from media_library import analytics
from media_library.schemas import AnalyticsOverview

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
