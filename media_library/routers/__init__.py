"""Routers package exports."""

from media_library.routers.auth import router as auth_router
from media_library.routers.backup import router as backup_router
from media_library.routers.duplicates import router as duplicates_router
from media_library.routers.library_admin import router as library_admin_router
from media_library.routers.library_browse import router as library_browse_router
from media_library.routers.library_manage import router as library_manage_router
from media_library.routers.library_media import router as library_media_router
from media_library.routers.sorter_media import router as sorter_media_router
from media_library.routers.sorter_move import router as sorter_move_router
from media_library.routers.sorter_queue import router as sorter_queue_router
from media_library.routers.system import router as system_router
from media_library.routers.transcode import router as transcode_router
from media_library.routers.views import router as views_router
from media_library.routers.webp import router as webp_router
from media_library.routers.zip_router import router as zip_router

all_routers = [
    views_router,
    auth_router,
    sorter_queue_router,
    sorter_move_router,
    sorter_media_router,
    library_browse_router,
    library_media_router,
    library_admin_router,
    library_manage_router,
    duplicates_router,
    transcode_router,
    webp_router,
    backup_router,
    zip_router,
    system_router,
]

__all__ = [
    "all_routers",
    "auth_router",
    "backup_router",
    "duplicates_router",
    "library_admin_router",
    "library_browse_router",
    "library_manage_router",
    "library_media_router",
    "sorter_media_router",
    "sorter_move_router",
    "sorter_queue_router",
    "system_router",
    "transcode_router",
    "views_router",
    "webp_router",
    "zip_router",
]
