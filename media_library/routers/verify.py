"""Verification queue endpoints"""

from __future__ import annotations

import asyncio
from typing import Literal

from fastapi import APIRouter, HTTPException, Query

from media_library import paths, verify
from media_library.config import get_settings
from media_library.routers.common import as_http_error
from media_library.schemas import (
    VerifyDismissRequest,
    VerifyDismissResponse,
    VerifyIssuesResponse,
    VerifyRecheckRequest,
    VerifyRecheckResponse,
    VerifyRescanResponse,
    VerifyStatusResponse,
)

router = APIRouter()

IssueFilter = Literal["zero_size", "probe_failed", "thumb_failed"]


@router.get("/verify/status", response_model=VerifyStatusResponse)
async def status() -> dict:
    """Worker state plus how many issues of each kind are open."""
    counts = await asyncio.to_thread(verify.issue_counts)
    totals = await asyncio.to_thread(verify.totals)
    return {
        "owned": get_settings().own_workers,
        **verify.worker_state(),
        "counts": counts,
        "open": totals["open"],
        "dismissed": totals["dismissed"],
    }


@router.get("/verify/issues", response_model=VerifyIssuesResponse)
async def issues(
    issue: IssueFilter | None = Query(default=None),
    include_dismissed: bool = Query(False),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict:
    """Flagged files, newest detection first, joined with their files row."""
    return await asyncio.to_thread(
        verify.list_issues, issue, include_dismissed, limit, offset
    )


@router.post("/verify/rescan", response_model=VerifyRescanResponse)
async def rescan() -> dict:
    """Schedule another full verification pass on the owned worker."""
    if not get_settings().own_workers:
        raise HTTPException(
            status_code=409,
            detail="The owned runtime is off. Set OWN_WORKERS=true to let it verify.",
        )
    verify.request_rescan()
    return {"started": True, "rescan_pending": verify.rescan_pending()}


@router.post("/verify/recheck", response_model=VerifyRecheckResponse)
async def recheck(payload: VerifyRecheckRequest) -> dict:
    """Re-derive one file's issues from the filesystem and the index."""
    try:
        # 403 outside the library root, like every mutation route.
        path = paths.resolve_library_path(payload.path)
        return await asyncio.to_thread(verify.recheck_file, path)
    except Exception as exc:
        raise as_http_error(exc) from exc


@router.post("/verify/dismiss", response_model=VerifyDismissResponse)
async def dismiss(payload: VerifyDismissRequest) -> dict:
    """Dismiss one issue, or all issues of a path, from the queue."""
    try:
        # 403 outside the library root, like every mutation route.
        path = paths.resolve_library_path(payload.path)
        return await asyncio.to_thread(verify.dismiss_issues, path, payload.issue)
    except Exception as exc:
        raise as_http_error(exc) from exc
