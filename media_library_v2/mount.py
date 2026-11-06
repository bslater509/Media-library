"""Composition root for v2.

``mount_v2`` adds the v2 API and SPA to an existing FastAPI app (v1's, while
both run side by side). ``build_v2_app`` assembles the same routes into a
standalone app for tests and, later, for the post-cutover entrypoint.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, FastAPI

from media_library_v2 import __version__
from media_library_v2.config import get_settings
from media_library_v2.routers import analytics as analytics_router
from media_library_v2.routers import backups as backups_router
from media_library_v2.routers import cleanup as cleanup_router
from media_library_v2.routers import dedupe as dedupe_router
from media_library_v2.routers import downloads as downloads_router
from media_library_v2.routers import favorites as favorites_router
from media_library_v2.routers import library as library_router
from media_library_v2.routers import manage as manage_router
from media_library_v2.routers import media as media_router
from media_library_v2.routers import sorter as sorter_router
from media_library_v2.routers import system as system_router
from media_library_v2.routers import uploads as uploads_router
from media_library_v2.routers import webp as webp_router
from media_library_v2.routers import x265 as x265_router
from media_library_v2.routers import zip as zip_router
from media_library_v2.views import router as views_router

logger = logging.getLogger(__name__)


def api_router() -> APIRouter:
    router = APIRouter(prefix="/api/v2")
    router.include_router(system_router.router, tags=["system"])
    router.include_router(library_router.router, tags=["library"])
    router.include_router(media_router.router, tags=["media"])
    router.include_router(favorites_router.router, tags=["favorites"])
    router.include_router(manage_router.router, tags=["manage"])
    router.include_router(uploads_router.router, tags=["uploads"])
    router.include_router(sorter_router.router, tags=["sorter"])
    router.include_router(downloads_router.router, tags=["downloads"])
    router.include_router(analytics_router.router, tags=["analytics"])
    router.include_router(dedupe_router.router, tags=["dedupe"])
    router.include_router(webp_router.router, tags=["webp"])
    router.include_router(cleanup_router.router, tags=["cleanup"])
    router.include_router(zip_router.router, tags=["zip"])
    router.include_router(backups_router.router, tags=["backups"])
    router.include_router(x265_router.router, tags=["x265"])
    return router


def mount_v2(app: FastAPI) -> bool:
    """Attach v2 to ``app``; returns False when disabled or on failure."""
    settings = get_settings()
    if not settings.enabled:
        logger.info("[v2] Disabled by V2_ENABLED")
        return False
    app.include_router(api_router())
    app.include_router(views_router)
    logger.info(
        "[v2] Mounted at /v2 (db=%s read_only=%s)",
        settings.db_path,
        settings.db_read_only,
    )
    return True


def build_v2_app() -> FastAPI:
    """A standalone v2 app for tests and integration checks."""
    app = FastAPI(
        title="Media Library v2",
        version=__version__,
        docs_url="/api/v2/docs",
        openapi_url="/api/v2/openapi.json",
    )
    app.include_router(api_router())
    app.include_router(views_router)
    return app
