"""System, status and health endpoints"""

from __future__ import annotations

import asyncio
import os
import sqlite3

from fastapi import APIRouter, HTTPException, Response

from media_library import __version__, build_info, indexer, library, runtime
from media_library.config import get_settings
from media_library.db import is_available, reader, writes_available
from media_library.schemas import (
    HealthResponse,
    IndexStatusResponse,
    IndexSyncResponse,
    InfoResponse,
    OnboardingInfo,
    StatusResponse,
)

router = APIRouter()


def _onboarding_index_facts() -> dict:
    """counts() plus the root folder row, read off the event loop."""
    settings = get_settings()
    totals = library.counts()
    with reader() as conn:
        root_row = conn.execute(
            "SELECT last_scanned FROM folders WHERE path = ?",
            (str(settings.library_dir),),
        ).fetchone()
    return {"totals": totals, "root_row": root_row}


@router.get("/system/info", response_model=InfoResponse)
async def info() -> dict:
    """Library root, totals and which capabilities are live."""
    settings = get_settings()
    try:
        totals = await asyncio.to_thread(library.counts)
    except sqlite3.Error as exc:
        raise HTTPException(
            status_code=503, detail=f"Index unavailable: {exc}"
        ) from exc
    writable = writes_available()
    return {
        "version": __version__,
        "library_root": str(settings.library_dir),
        "counts": totals,
        "features": {
            "browse": True,
            "search": True,
            "favorites": writable,
            "media_stream": True,
            "thumbnails": True,
            "uploads": writable,
            "manage": writable,
            "sorter": writable,
            "downloads": True,
            "dedupe": writable,
            "webp": writable,
            "zip": True,
            "analytics": True,
            "workers": get_settings().own_workers,
            "backups": True,
            "x265": True,
        },
    }


@router.get("/system/onboarding", response_model=OnboardingInfo)
async def onboarding() -> dict:
    """Everything the first-run tour needs, in one request."""
    settings = get_settings()
    facts = await asyncio.to_thread(_onboarding_index_facts)
    totals, root_row = facts["totals"], facts["root_row"]
    from media_library import backups, downloads

    snapshot = await asyncio.to_thread(backups.status)
    return {
        "version": __version__,
        "library_root": str(settings.library_dir),
        "source_dir": str(settings.source_dir),
        "source_available": os.path.isdir(str(settings.source_dir)),
        "counts": totals,
        "features": {
            "browse": True,
            "search": True,
            "favorites": writes_available(),
            "media_stream": True,
            "thumbnails": True,
            "uploads": writes_available(),
            "manage": writes_available(),
            "sorter": writes_available(),
            "downloads": True,
            "dedupe": writes_available(),
            "webp": writes_available(),
            "zip": True,
            "analytics": True,
            "backups": True,
            "x265": True,
            "workers": get_settings().own_workers,
        },
        "index": {
            "root_indexed": root_row is not None,
            "folders": totals["folders"],
            "files": totals["files"],
            "last_scanned": root_row["last_scanned"] if root_row else None,
        },
        "backup": {
            "exists": bool(snapshot.get("exists")),
            "created_at_str": snapshot.get("created_at_str"),
        },
        "tools": downloads.tools_available(),
    }


@router.get("/system/status", response_model=StatusResponse)
async def status() -> dict:
    """A small status body plus the crawler state."""
    settings = get_settings()
    # SQLite reads stay off the event loop (the watchdog polls /health).
    try:
        totals = await asyncio.to_thread(library.counts)
    except sqlite3.Error as exc:
        raise HTTPException(
            status_code=503, detail=f"Index unavailable: {exc}"
        ) from exc
    return {
        "version": __version__,
        "database": "ok" if await asyncio.to_thread(is_available) else "unavailable",
        "index": {
            "files": totals["files"],
            "folders": totals["folders"],
            "videos": totals["videos"],
            "images": totals["images"],
        },
        "workers": {
            "mode": "standalone",
            "thumbnail": "owned" if settings.own_workers else "off",
            "indexer": "owned" if settings.own_workers else "off",
        },
        "indexer": indexer.status(),
    }


@router.get("/system/index/status", response_model=IndexStatusResponse)
async def index_status() -> dict:
    return {
        "owned": get_settings().own_workers,
        "runtime": runtime.runtime_status(),
        **indexer.status(),
    }


@router.post("/system/index/sync", response_model=IndexSyncResponse)
async def index_sync(full: bool = True) -> dict:
    """Start a crawl. ``full=false`` keeps the incremental ``dir_mtime`` skip."""
    if not get_settings().own_workers:
        raise HTTPException(
            status_code=409,
            detail="The owned runtime is off. Set OWN_WORKERS=true to let it crawl.",
        )
    if not indexer.start_crawl(force_full=full):
        raise HTTPException(status_code=409, detail="A crawl is already running")
    return {"started": True, "status": indexer.status()}


@router.get("/health", response_model=HealthResponse)
async def health(response: Response) -> dict:
    """Cheap, credential-free liveness probe for systemd, watchdogs and containers."""
    healthy = await asyncio.to_thread(is_available)
    if not healthy:
        response.status_code = 503
    commit = build_info.get_commit()
    return {
        "status": "ok" if healthy else "degraded",
        "version": __version__,
        "database": "ok" if healthy else "unavailable",
        **({"commit": commit} if commit else {}),
    }
