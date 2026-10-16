"""Library analytics API — the index-only overview powering ``/analytics``.

Read-only by design: the dashboard links out to the Library's existing panels
for anything destructive (duplicate resolution, WebP, ZIP, empty-folder
cleanup), so this router owns no mutations.
"""

from typing import Any

from fastapi import APIRouter, Query

from media_library.services.analytics_service import (
    DEFAULT_FILE_LIMIT,
    DEFAULT_FOLDER_LIMIT,
    MAX_LIMIT,
    get_analytics_overview,
    invalidate_analytics_cache,
)

router = APIRouter(tags=["analytics"])


@router.get("/api/analytics/overview")
async def analytics_overview(
    folder_limit: int = Query(
        DEFAULT_FOLDER_LIMIT,
        ge=1,
        le=MAX_LIMIT,
        description="How many ranked folders to return.",
    ),
    file_limit: int = Query(
        DEFAULT_FILE_LIMIT,
        ge=1,
        le=MAX_LIMIT,
        description="How many largest files to return.",
    ),
    refresh: bool = Query(
        False,
        description="Bypass the 30s memoized payload for an explicit manual reload.",
    ),
) -> dict[str, Any]:
    """Aggregate storage, folder, quality and housekeeping totals from the index."""
    if refresh:
        invalidate_analytics_cache()
    return get_analytics_overview(folder_limit=folder_limit, file_limit=file_limit)
