"""FastAPI Application Factory for Media Library and Sorter."""

import asyncio
import logging
import os
from contextlib import asynccontextmanager

import anyio
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware

from media_library import __version__
from media_library.core.config import STATIC_DIR
from media_library.core.state import state
from media_library.db.indexer import run_background_crawler
from media_library.db.schema import init_backup_db
from media_library.routers import all_routers
from media_library.routers.auth import auth_middleware
from media_library.services.backup_service import (
    _create_library_backup_sync,
    check_and_auto_restore_library_backup,
)
from media_library.services.upload_service import gc_stale_uploads
from media_library.workers import (
    animated_thumb_worker_instance,
    backup_watcher_instance,
    duplicate_hash_worker_instance,
    enqueue_pretranscode_for_source,
    h265_worker_instance,
    library_ops_worker_instance,
    metadata_worker_instance,
    thumb_hash_worker_instance,
    thumbnail_worker_instance,
    transcode_worker_instance,
    transfer_worker_instance,
    url_download_worker_instance,
    webp_worker_instance,
)

logger = logging.getLogger(__name__)


def create_app(
    source_dir: str | None = None,
    destination_dir: str | None = None,
    run_startup_workers: bool = True,
) -> FastAPI:
    """Creates and configures the FastAPI application instance."""

    if source_dir:
        state.set_source_dir(os.path.realpath(source_dir))
    if destination_dir:
        state.set_destination_dir(os.path.realpath(destination_dir))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Expand AnyIO worker threadpool capacity for high-concurrency FUSE I/O and streaming
        try:
            limiter = anyio.to_thread.current_default_thread_limiter()
            limiter.total_tokens = 100
        except Exception as e:
            logger.warning(f"Warning: could not set AnyIO thread limiter: {e}")

        # Auto-restore database and thumbnails from library backup if local data is missing or empty
        try:
            auto_restored = await asyncio.to_thread(
                check_and_auto_restore_library_backup
            )
            if auto_restored:
                logger.info(
                    "[Auto-Restore] Successfully restored database and thumbnails from library backup."
                )
        except Exception as e:
            logger.warning(
                f"[Auto-Restore] Warning during startup auto-restore check: {e}"
            )

        # Garbage-collect abandoned browser-upload staging files (closed tabs,
        # dropped connections) so they cannot accumulate on the data disk.
        try:
            gc_result = await asyncio.to_thread(gc_stale_uploads)
            if gc_result.get("removed"):
                logger.info(
                    f"[Startup] Removed {gc_result['removed']} stale upload staging file(s) "
                    f"({gc_result.get('freed_formatted', '0 B')})."
                )
        except Exception as e:
            logger.warning(f"[Startup] Warning during upload staging GC: {e}")

        # Initialize SQLite backup index
        init_backup_db()

        crawler_task: asyncio.Task | None = None

        if run_startup_workers:
            # Start background transfer, pre-transcode, index crawler, thumbnail, duplicate hash, thumb hash, webp, and backup watcher workers
            await transfer_worker_instance.start()
            await transcode_worker_instance.start()
            crawler_task = asyncio.create_task(run_background_crawler())
            await thumbnail_worker_instance.start()
            await metadata_worker_instance.start()
            await duplicate_hash_worker_instance.start()
            await thumb_hash_worker_instance.start()
            await webp_worker_instance.start()
            await backup_watcher_instance.start()
            await library_ops_worker_instance.start()
            await animated_thumb_worker_instance.start()
            await url_download_worker_instance.start()
            await h265_worker_instance.start()

            # Auto-enqueue pre-transcoding for configured source directory
            if state.get("source_dir") and os.path.isdir(state["source_dir"]):
                asyncio.create_task(
                    asyncio.to_thread(
                        enqueue_pretranscode_for_source, state["source_dir"]
                    )
                )

        yield

        if run_startup_workers:
            await transfer_worker_instance.stop()
            await transcode_worker_instance.stop()
            if crawler_task and not crawler_task.done():
                crawler_task.cancel()
                try:
                    await crawler_task
                except asyncio.CancelledError:
                    pass
            await thumbnail_worker_instance.stop()
            await metadata_worker_instance.stop()
            await duplicate_hash_worker_instance.stop()
            await thumb_hash_worker_instance.stop()
            await webp_worker_instance.stop()
            await backup_watcher_instance.stop()
            await library_ops_worker_instance.stop()
            await animated_thumb_worker_instance.stop()
            await url_download_worker_instance.stop()
            await h265_worker_instance.stop()

        # Perform clean snapshot backup to library folder on clean shutdown
        try:
            logger.info(
                "[Shutdown] Creating clean snapshot backup in library folder..."
            )
            sh_res = await asyncio.to_thread(
                _create_library_backup_sync, is_shutdown=True
            )
            if sh_res.get("success"):
                logger.info("[Shutdown] Clean snapshot backup created successfully.")
            else:
                logger.warning(
                    f"[Shutdown] Warning creating library backup: {sh_res.get('error')}"
                )
        except Exception as e:
            logger.warning(
                f"[Shutdown] Warning: Exception creating clean library backup on shutdown: {e}"
            )

    class CachedStaticFiles(StaticFiles):
        async def get_response(self, path: str, scope):
            resp = await super().get_response(path, scope)
            if resp.status_code == 200:
                # Revalidate on every load (304 when unchanged) so frontend edits
                # under static/ are picked up by a normal browser refresh rather
                # than being served from a stale day-long cache.
                resp.headers["Cache-Control"] = "no-cache"
            return resp

    app = FastAPI(
        title="Media Library & Video Sorter",
        version=__version__,
        lifespan=lifespan,
    )

    # No CORS middleware: the app is same-origin only (pages, media streaming,
    # thumbnails and the API are all served from this app). A wildcard
    # ``allow_origins`` together with credentialed requests is rejected by
    # browsers anyway and only widens the surface for third-party pages.
    app.add_middleware(GZipMiddleware, minimum_size=1000)

    # Password gate for the whole app (no-op when APP_PASSWORD is unset).
    app.middleware("http")(auth_middleware)

    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    app.mount("/static", CachedStaticFiles(directory=str(STATIC_DIR)), name="static")

    for router in all_routers:
        app.include_router(router)

    return app
