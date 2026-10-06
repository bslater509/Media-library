"""Library backup snapshot and restore API routes."""

import time
from fastapi import APIRouter, BackgroundTasks
from starlette.responses import JSONResponse

from media_library.services.backup_service import (
    get_library_backup_dir,
    get_existing_backup_meta,
    library_backup_state,
)
from media_library.workers.backup_watcher import (
    run_library_backup_async,
    run_library_restore_async,
)

router = APIRouter(tags=["backup"])


@router.get("/api/backup/library-backup/status")
async def get_library_backup_status():
    bck_dir = get_library_backup_dir()
    meta = get_existing_backup_meta()
    if meta:
        library_backup_state["last_backup_time"] = meta.get("timestamp")
        library_backup_state["last_backup_str"] = (
            time.strftime("%b %d, %H:%M:%S", time.localtime(meta["timestamp"]))
            if meta.get("timestamp")
            else ""
        )
        library_backup_state["backup_exists"] = True
        library_backup_state["file_count"] = meta.get("file_count", 0)
        library_backup_state["folder_count"] = meta.get("folder_count", 0)
        library_backup_state["thumb_count"] = meta.get("thumb_count", 0)
        library_backup_state["total_size_bytes"] = meta.get("total_size_bytes", 0)
        library_backup_state["total_size_formatted"] = meta.get("total_size_formatted", "0 B")
        library_backup_state["backup_path"] = str(bck_dir)

    return {
        "status": library_backup_state["status"],
        "progress_percent": library_backup_state["progress_percent"],
        "current_step": library_backup_state["current_step"],
        "last_backup_time": library_backup_state["last_backup_time"],
        "last_backup_str": library_backup_state["last_backup_str"],
        "backup_exists": library_backup_state["backup_exists"],
        "file_count": library_backup_state["file_count"],
        "folder_count": library_backup_state["folder_count"],
        "thumb_count": library_backup_state["thumb_count"],
        "total_size_bytes": library_backup_state["total_size_bytes"],
        "total_size_formatted": library_backup_state["total_size_formatted"],
        "backup_path": str(bck_dir),
        "error_message": library_backup_state["error_message"],
    }


@router.post("/api/backup/library-backup/save")
async def trigger_save_library_backup(background_tasks: BackgroundTasks):
    if library_backup_state["status"] in ("backing_up", "restoring"):
        return JSONResponse(
            status_code=409,
            content={
                "success": False,
                "message": f"Operation already in progress: {library_backup_state['status']}",
            },
        )
    background_tasks.add_task(run_library_backup_async)
    return {"success": True, "message": "Library backup started in background"}


@router.post("/api/backup/library-backup/restore")
async def trigger_restore_library_backup(background_tasks: BackgroundTasks):
    if library_backup_state["status"] in ("backing_up", "restoring"):
        return JSONResponse(
            status_code=409,
            content={
                "success": False,
                "message": f"Operation already in progress: {library_backup_state['status']}",
            },
        )
    background_tasks.add_task(run_library_restore_async)
    return {"success": True, "message": "Library restore started in background"}
