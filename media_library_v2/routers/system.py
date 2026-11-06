"""System, status and health endpoints for v2."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, HTTPException

from media_library_v2 import __version__, library
from media_library_v2.config import get_settings
from media_library_v2.db import is_available, writes_available
from media_library_v2.schemas import HealthResponse, InfoResponse, StatusResponse

router = APIRouter()


@router.get("/system/info", response_model=InfoResponse)
async def info() -> dict:
    """Library root, totals and which v2 capabilities are live."""
    settings = get_settings()
    try:
        totals = library.counts()
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
            "dedupe": writable and True,
            "zip": False,
            "analytics": True,
            "workers": False,
        },
    }


@router.get("/system/status", response_model=StatusResponse)
async def status() -> dict:
    """A small status body; the v2 worker runtime lands in phase 2."""
    try:
        totals = library.counts()
    except sqlite3.Error as exc:
        raise HTTPException(
            status_code=503, detail=f"Index unavailable: {exc}"
        ) from exc
    return {
        "version": __version__,
        "database": "ok" if is_available() else "unavailable",
        "index": {
            "files": totals["files"],
            "folders": totals["folders"],
            "videos": totals["videos"],
            "images": totals["images"],
        },
        "workers": {
            "mode": "shared-v1",
            "thumbnail": "v1",
            "indexer": "v1",
        },
    }


@router.get("/health", response_model=HealthResponse)
async def health() -> dict:
    healthy = is_available()
    return {
        "status": "ok" if healthy else "degraded",
        "version": __version__,
        "database": "ok" if healthy else "unavailable",
    }
