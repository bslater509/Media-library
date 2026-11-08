"""The application: one FastAPI app serving the client at ``/`` and the API at ``/api``.

``create_app`` assembles the API router, the shared-password gate, the liveness
probe and the SPA catch-all. ``main`` is the entrypoint the systemd unit and
``python app.py`` use: it loads ``.env`` into the process environment first, so
the settings (including ``APP_PASSWORD``) are real before the app is built.

Keeping the dotenv load in ``main`` means importing this module has no side
effects, which is what keeps the test suite off the developer's configuration.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import shutil
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import APIRouter, FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from media_library import __version__, auth, backups
from media_library.config import BASE_DIR, get_settings, reload_settings
from media_library.db import is_available, writes_available
from media_library.routers import analytics as analytics_router
from media_library.routers import auth as auth_router
from media_library.routers import backups as backups_router
from media_library.routers import cleanup as cleanup_router
from media_library.routers import dedupe as dedupe_router
from media_library.routers import downloads as downloads_router
from media_library.routers import favorites as favorites_router
from media_library.routers import history as history_router
from media_library.routers import library as library_router
from media_library.routers import manage as manage_router
from media_library.routers import media as media_router
from media_library.routers import sorter as sorter_router
from media_library.routers import system as system_router
from media_library.routers import uploads as uploads_router
from media_library.routers import verify as verify_router
from media_library.routers import webp as webp_router
from media_library.routers import x265 as x265_router
from media_library.routers import zip as zip_router
from media_library.routers import zip_batch as zip_batch_router
from media_library.runtime import start_runtime, stop_runtime
from media_library.schema import init_schema
from media_library.views import build_router

logger = logging.getLogger(__name__)


def load_environment() -> None:
    """Load ``.env`` into the process environment and re-read the settings."""
    load_dotenv(BASE_DIR / ".env")
    reload_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    if not auth.get_app_password():
        logger.warning("[api] APP_PASSWORD is empty: the app is open to every client")
    if not settings.own_workers:
        logger.warning(
            "[api] OWN_WORKERS is false: no crawler, metadata probes, posters "
            "or automatic snapshots will run"
        )
    try:
        restored = backups.auto_restore_if_missing()
        if restored.get("restored"):
            logger.warning(
                "[api] Restored the index from the %s snapshot (%s files, %s folders)",
                restored.get("source"),
                restored.get("files"),
                restored.get("folders"),
            )
    except Exception:
        logger.exception("[api] Could not check the startup snapshot")

    # Create the index schema before any worker touches it. A write, so it runs
    # in a thread (AGENTS.md Rule 3) and is skipped in read-only mode.
    if writes_available():
        try:
            report = await asyncio.to_thread(init_schema)
            if report["created"]:
                logger.info("[api] Created the index schema at %s", report["path"])
            if report["dropped_indexes"]:
                logger.info(
                    "[api] Dropped obsolete indexes: %s",
                    ", ".join(report["dropped_indexes"]),
                )
        except Exception:
            logger.exception("[api] Could not initialise the index schema")
    elif not is_available():
        logger.error(
            "[api] The index is read-only and has no usable schema; "
            "start once with DB_READ_ONLY=false or restore a snapshot"
        )

    # One-time migration from the removed ZIP-compression feature: sweep the
    # staging directory its builders wrote to (idempotent; ZIP_CACHE_DIR, the
    # member cache browsable archives still need, is untouched).
    legacy_staging = os.environ.get("ZIP_STAGING_DIR") or os.environ.get(
        "V2_ZIP_STAGING_DIR"
    )
    staging_path = (
        settings.data_dir / "zip_staging"
        if not legacy_staging
        else Path(legacy_staging)
    )
    if staging_path.is_dir():
        await asyncio.to_thread(shutil.rmtree, staging_path, ignore_errors=True)
        logger.info("[api] Removed the obsolete zip staging directory")

    start_runtime()
    try:
        yield
    finally:
        # To a thread: a snapshot is a SQLite backup + VACUUM + poster tar,
        # minutes on a big library, and must not block the loop on shutdown.
        try:
            await asyncio.to_thread(backups.create_snapshot)
            logger.info("[api] Wrote a shutdown snapshot")
        except Exception:
            logger.exception("[api] Shutdown snapshot failed")
        stop_runtime()


def api_router() -> APIRouter:
    """The JSON API, mounted at ``/api``."""
    router = APIRouter(prefix="/api")
    router.include_router(auth_router.router, tags=["auth"])
    router.include_router(system_router.router, tags=["system"])
    router.include_router(library_router.router, tags=["library"])
    router.include_router(media_router.router, tags=["media"])
    router.include_router(favorites_router.router, tags=["favorites"])
    router.include_router(history_router.router, tags=["history"])
    router.include_router(manage_router.router, tags=["manage"])
    router.include_router(uploads_router.router, tags=["uploads"])
    router.include_router(sorter_router.router, tags=["sorter"])
    router.include_router(downloads_router.router, tags=["downloads"])
    router.include_router(analytics_router.router, tags=["analytics"])
    router.include_router(dedupe_router.router, tags=["dedupe"])
    router.include_router(webp_router.router, tags=["webp"])
    router.include_router(verify_router.router, tags=["verify"])
    router.include_router(cleanup_router.router, tags=["cleanup"])
    router.include_router(zip_router.router, tags=["zip"])
    router.include_router(zip_batch_router.router, tags=["zip"])
    router.include_router(backups_router.router, tags=["backups"])
    router.include_router(x265_router.router, tags=["x265"])
    return router


def create_app() -> FastAPI:
    """Assemble the API, the auth gate, the health probe and the client."""
    settings = get_settings()
    app = FastAPI(
        title="Media Library",
        version=__version__,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )
    app.middleware("http")(auth.auth_middleware)

    app.include_router(api_router())

    # Static assets must be mounted before the SPA catch-all.
    static_root = settings.static_dir
    if (static_root / "dist").is_dir():
        app.mount("/static", StaticFiles(directory=str(static_root)), name="static")

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon():
        icon = static_root / "favicon.ico"
        if icon.is_file():
            return FileResponse(icon)
        return JSONResponse({"detail": "Not found"}, status_code=404)

    app.include_router(build_router(""))
    return app


def main() -> None:
    import uvicorn

    load_environment()

    parser = argparse.ArgumentParser(description="Run the media library")
    parser.add_argument("--host", default=os.environ.get("HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8888")))
    parser.add_argument(
        "--log-level", default=os.environ.get("LOG_LEVEL", "info").lower()
    )
    args = parser.parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(levelname)s %(name)s %(message)s",
    )
    uvicorn.run(
        create_app(),
        host=args.host,
        port=args.port,
        log_level=args.log_level,
    )


if __name__ == "__main__":
    main()
