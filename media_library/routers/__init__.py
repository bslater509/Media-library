"""Routers package exports."""

from media_library.routers.views import router as views_router
from media_library.routers.sorter import router as sorter_router
from media_library.routers.library import router as library_router
from media_library.routers.duplicates import router as duplicates_router
from media_library.routers.transcode import router as transcode_router
from media_library.routers.webp import router as webp_router
from media_library.routers.backup import router as backup_router
from media_library.routers.zip_router import router as zip_router
from media_library.routers.system import router as system_router

all_routers = [
    views_router,
    sorter_router,
    library_router,
    duplicates_router,
    transcode_router,
    webp_router,
    backup_router,
    zip_router,
    system_router,
]

__all__ = [
    "views_router",
    "sorter_router",
    "library_router",
    "duplicates_router",
    "transcode_router",
    "webp_router",
    "backup_router",
    "zip_router",
    "system_router",
    "all_routers",
]
