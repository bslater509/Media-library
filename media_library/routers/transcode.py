"""FastAPI router for video transcoding management and streaming."""

import asyncio
import logging
import os

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from media_library.core.constants import VIDEO_EXTENSIONS
from media_library.core.state import format_size, state
from media_library.db.connection import db_conn
from media_library.services.ffmpeg_service import (
    build_live_transcode_command,
    relay_process_stdout,
)
from media_library.services.media_service import (
    resolve_video_path,
    validate_backup_path,
)
from media_library.workers.h265_worker import h265_worker_instance
from media_library.workers.transcode_worker import transcode_worker_instance

router = APIRouter()

logger = logging.getLogger(__name__)


class CancelTranscodeRequest(BaseModel):
    task_id: str


class BackupTranscodeRequest(BaseModel):
    folder_path: str | None = None
    file_path: str | None = None


class X265ConvertRequest(BaseModel):
    path: str | None = None
    job_id: str | None = None


@router.post("/api/transcode/pause")
async def toggle_transcode_pause() -> dict:
    if not transcode_worker_instance.transcode_paused:
        transcode_worker_instance.pause()
        return {"success": True, "is_paused": True}
    else:
        transcode_worker_instance.resume()
        return {"success": True, "is_paused": False}


@router.post("/api/transcode/cancel")
async def cancel_transcode_task(req: CancelTranscodeRequest) -> dict:
    cancelled = transcode_worker_instance.cancel(req.task_id)
    if cancelled:
        return {"success": True, "action": "cancelled"}
    raise HTTPException(status_code=404, detail="Transcode task not found")


@router.post("/api/transcode/cancel_all")
async def cancel_all_transcode_tasks() -> dict:
    transcode_worker_instance.cancel_all()
    return {"success": True}


@router.get("/api/transcode/status")
async def get_transcode_status() -> dict:
    return transcode_worker_instance.get_status()


@router.get("/api/transcode/{file_id}")
async def transcode_video(
    file_id: str, request: Request, ss: float | None = Query(0.0)
) -> StreamingResponse:
    if not state.get("source_dir"):
        raise HTTPException(status_code=400, detail="No source directory configured")

    file_path = resolve_video_path(
        file_id, transcode_worker_instance.transcode_items_map
    )
    if not file_path or not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Video file not found")

    process = await asyncio.create_subprocess_exec(
        *build_live_transcode_command(file_path, ss or 0.0),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )

    return StreamingResponse(relay_process_stdout(process), media_type="video/mp4")


@router.post("/api/backup/transcode/enqueue")
async def backup_enqueue_transcode(req: BackupTranscodeRequest) -> dict:
    enqueued = await asyncio.to_thread(
        transcode_worker_instance.enqueue_for_backup,
        folder_path=req.folder_path,
        file_path=req.file_path,
    )
    return {
        "success": True,
        "enqueued_count": len(enqueued),
        "enqueued": enqueued,
    }


@router.get("/api/backup/transcode/summary")
def get_backup_transcode_summary() -> dict:
    with db_conn() as conn:
        try:
            cur = conn.execute(
                "SELECT path, name, size FROM files WHERE type = 'video' AND LOWER(path) NOT LIKE '%.mp4'"
            )
            rows = cur.fetchall()
        except Exception as e:
            logger.error(f"Error querying transcode summary: {e}")
            rows = []

    active_or_queued_paths = set()
    for t in transcode_worker_instance.transcode_items_map.values():
        if t.get("status") in ("queued", "transcoding", "completed"):
            active_or_queued_paths.add(t.get("source_path"))

    pending_files = []
    breakdown = {}
    total_bytes = 0

    for r in rows:
        p = r["path"]
        if p in active_or_queued_paths:
            continue
        ext = os.path.splitext(p)[1].lower()
        breakdown[ext] = breakdown.get(ext, 0) + 1
        total_bytes += r["size"] or 0
        pending_files.append(r)

    sorted_breakdown = dict(
        sorted(breakdown.items(), key=lambda item: item[1], reverse=True)
    )

    return {
        "pending_count": len(pending_files),
        "total_bytes": total_bytes,
        "total_size_formatted": format_size(total_bytes),
        "breakdown": sorted_breakdown,
        "already_queued_count": len(active_or_queued_paths),
    }


@router.post("/api/backup/transcode/enqueue_all")
async def backup_enqueue_all_transcode() -> dict:
    enqueued = await asyncio.to_thread(transcode_worker_instance.enqueue_all_for_backup)
    return {
        "success": True,
        "enqueued_count": len(enqueued),
    }


@router.post("/api/backup/transcode/x265")
async def enqueue_x265_conversion(req: X265ConvertRequest) -> dict:
    """Queue an in-place H.265 (HEVC) re-encode of a library video.

    Drives the lightbox's "Convert to x265" button. The worker keeps the
    original filename and just replaces the bytes (a ``.mp4`` sibling is
    produced when the source used a container the browser cannot play).
    """
    if not req.path:
        raise HTTPException(status_code=400, detail="A file path is required")
    real_path = validate_backup_path(req.path)
    if not os.path.exists(real_path) or not os.path.isfile(real_path):
        raise HTTPException(status_code=404, detail="Video file not found")
    if os.path.splitext(real_path)[1].lower() not in VIDEO_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Path is not a video file")

    try:
        job = h265_worker_instance.enqueue(real_path)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))

    return {
        "success": True,
        "job_id": job["id"],
        "source_path": real_path,
        "status": job["status"],
    }


@router.get("/api/backup/transcode/x265/status")
def get_x265_status(path: str | None = Query(None)) -> dict:
    """Active/queued x265 jobs and, when ``path`` is given, that file's job."""
    return h265_worker_instance.get_status(path)


@router.post("/api/backup/transcode/x265/cancel")
async def cancel_x265_conversion(req: X265ConvertRequest) -> dict:
    job_id = req.job_id
    if not job_id and req.path:
        job = h265_worker_instance.job_for_path(validate_backup_path(req.path))
        job_id = job["id"] if job else None
    if not job_id or not h265_worker_instance.cancel(job_id):
        raise HTTPException(status_code=404, detail="Conversion job not found")
    return {"success": True, "action": "cancelled"}


@router.get("/api/backup/transcode_stream")
async def stream_backup_transcode(
    path: str = Query(...), ss: float | None = Query(0.0)
) -> StreamingResponse:
    real_path = validate_backup_path(path)
    if not os.path.exists(real_path) or not os.path.isfile(real_path):
        raise HTTPException(status_code=404, detail="Video file not found")

    process = await asyncio.create_subprocess_exec(
        *build_live_transcode_command(real_path, ss or 0.0),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )

    return StreamingResponse(relay_process_stdout(process), media_type="video/mp4")
