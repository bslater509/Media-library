"""WebP optimization API routes."""

from fastapi import APIRouter
from pydantic import BaseModel

from media_library.services.media_service import validate_backup_path
from media_library.workers.webp_worker import webp_worker_instance

router = APIRouter(tags=["webp"])


class WebPOptimizeFolderRequest(BaseModel):
    folder_path: str


@router.get("/api/backup/webp/status")
async def get_webp_status():
    return {
        "status": webp_worker_instance.state["status"],
        "total_images": webp_worker_instance.state["total_images"],
        "converted_count": webp_worker_instance.state["converted_count"],
        "skipped_count": webp_worker_instance.state["skipped_count"],
        "pending_count": webp_worker_instance.state["pending_count"],
        "percent": webp_worker_instance.state["percent"],
        "saved_bytes": webp_worker_instance.state["saved_bytes"],
        "saved_formatted": webp_worker_instance.state["saved_formatted"],
        "current_file": webp_worker_instance.state["current_file"],
        "current_folder": webp_worker_instance.state["current_folder"],
        "is_paused": webp_worker_instance.state["is_paused"],
        "concurrency": webp_worker_instance.state["concurrency"],
        "last_synced": webp_worker_instance.state["last_synced"],
    }


@router.post("/api/backup/webp/pause")
async def toggle_webp_pause():
    if not webp_worker_instance.state["is_paused"]:
        webp_worker_instance.pause()
        return {"success": True, "is_paused": True, "message": "WebP optimizer paused"}
    else:
        webp_worker_instance.resume()
        return {"success": True, "is_paused": False, "message": "WebP optimizer resumed"}


@router.post("/api/backup/webp/resume")
async def resume_webp():
    webp_worker_instance.resume()
    return {"success": True, "is_paused": False, "message": "WebP optimizer resumed"}


@router.post("/api/backup/webp/optimize-folder")
async def optimize_folder_webp(req: WebPOptimizeFolderRequest):
    real_path = validate_backup_path(req.folder_path)
    count = webp_worker_instance.enqueue_folder(real_path)
    webp_worker_instance.resume()
    return {"success": True, "enqueued": count, "message": f"Enqueued {count} images for WebP optimization"}
