"""Standalone v2 application: the post-cutover entrypoint.

While the rewrite runs beside the current app, v1's ``create_app`` mounts v2 at
``/v2`` and owns the process. This module is the other half: a complete FastAPI
app that serves the v2 client at ``/`` with its own shared-password gate and its
own worker runtime (``V2_OWN_WORKERS=true``).

Run it with ``./venv/bin/python -m media_library_v2.standalone --port 8888`` or
``./venv/bin/python v2_app.py``.

``load_environment`` populates the process environment from ``.env`` in
``main`` (before the app is built): importing this module therefore has no side
effects, which keeps the test suite off the developer's real configuration.
"""

from __future__ import annotations

import argparse
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from media_library_v2 import __version__, auth, backups
from media_library_v2.config import get_settings, reload_settings
from media_library_v2.db import is_available
from media_library_v2.mount import api_router
from media_library_v2.runtime import start_runtime, stop_runtime
from media_library_v2.views import build_router

logger = logging.getLogger(__name__)


def load_environment() -> None:
    """Load ``.env`` into the process environment and re-read the settings."""
    load_dotenv(BASE_DIR / ".env")
    reload_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    if not auth.get_app_password():
        logger.warning("[v2] APP_PASSWORD is empty: the app is open to every client")
    if not settings.own_workers:
        logger.warning(
            "[v2] V2_OWN_WORKERS is false: no crawler, metadata probes, posters "
            "or automatic snapshots will run"
        )
    try:
        restored = backups.auto_restore_if_missing()
        if restored.get("restored"):
            logger.warning(
                "[v2] Restored the index from the %s snapshot (%s files, %s folders)",
                restored.get("source"),
                restored.get("files"),
                restored.get("folders"),
            )
    except Exception:
        logger.exception("[v2] Could not check the startup snapshot")

    start_runtime()
    try:
        yield
    finally:
        try:
            backups.create_snapshot()
            logger.info("[v2] Wrote a shutdown snapshot")
        except Exception:
            logger.exception("[v2] Shutdown snapshot failed")
        stop_runtime()


def build_standalone_app() -> FastAPI:
    """Assemble the full v2 app (API + client + auth gate) at ``/``."""
    settings = get_settings()
    app = FastAPI(
        title="Media Library",
        version=__version__,
        docs_url="/api/v2/docs",
        openapi_url="/api/v2/openapi.json",
        lifespan=lifespan,
    )
    app.middleware("http")(auth.auth_middleware)

    app.include_router(api_router())

    @app.get("/api/health", include_in_schema=False)
    async def health_alias():
        """Liveness probe under the path the existing watchdog already polls."""
        healthy = is_available()
        return JSONResponse(
            {
                "status": "ok" if healthy else "degraded",
                "version": __version__,
                "database": "ok" if healthy else "unavailable",
            },
            status_code=200 if healthy else 503,
        )

    # Static assets must be mounted before the SPA catch-all. Vite's build uses
    # the absolute base /static/dist/v2/, which works at both mount points.
    @app.api_route("/v2", methods=["GET", "HEAD"], include_in_schema=False)
    async def v2_root_redirect():
        """Old bookmarks from the side-by-side preview keep working."""
        return RedirectResponse(url="/", status_code=307)

    @app.api_route(
        "/v2/{spa_path:path}", methods=["GET", "HEAD"], include_in_schema=False
    )
    async def v2_deep_redirect(spa_path: str, request: Request):
        target = f"/{spa_path}"
        if request.url.query:
            target = f"{target}?{request.url.query}"
        return RedirectResponse(url=target, status_code=307)

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

    parser = argparse.ArgumentParser(description="Run the standalone v2 app")
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
        build_standalone_app(),
        host=args.host,
        port=args.port,
        log_level=args.log_level,
    )


if __name__ == "__main__":
    main()
