"""FastAPI router for video transcoding management and streaming."""

import os
import asyncio
from typing import Optional, List, Dict, Any
from pydantic import BaseModel
from fastapi import APIRouter, Request, HTTPException, Query
from fastapi.responses import StreamingResponse

from media_library.core.state import state, format_size
from media_library.db.connection import get_db
from media_library.services.media_service import resolve_video_path, validate_backup_path
from media_library.workers.transcode_worker import transcode_worker_instance

router = APIRouter()

class CancelTranscodeRequest(BaseModel):
    task_id: str

class BackupTranscodeRequest(BaseModel):
    folder_path: Optional[str] = None
    file_path: Optional[str] = None

@router.post("/api/transcode/pause")
async def toggle_transcode_pause():
    if not transcode_worker_instance.transcode_paused:
        transcode_worker_instance.pause()
        return {"success": True, "is_paused": True}
    else:
        transcode_worker_instance.resume()
        return {"success": True, "is_paused": False}

@router.post("/api/transcode/cancel")
async def cancel_transcode_task(req: CancelTranscodeRequest):
    cancelled = transcode_worker_instance.cancel(req.task_id)
    if cancelled:
        return {"success": True, "action": "cancelled"}
    raise HTTPException(status_code=404, detail="Transcode task not found")

@router.post("/api/transcode/cancel_all")
async def cancel_all_transcode_tasks():
    transcode_worker_instance.cancel_all()
    return {"success": True}

@router.get("/api/transcode/status")
async def get_transcode_status():
    return transcode_worker_instance.get_status()

@router.get("/api/transcode/{file_id}")
async def transcode_video(file_id: str, request: Request, ss: Optional[float] = Query(0.0)):
    if not state.get("source_dir"):
        raise HTTPException(status_code=400, detail="No source directory configured")

    file_path = resolve_video_path(file_id, transcode_worker_instance.transcode_items_map)
    if not file_path or not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Video file not found")

    ffmpeg_cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
    ]

    if ss and ss > 0:
        ffmpeg_cmd.extend(["-ss", str(ss)])

    ffmpeg_cmd.extend([
        "-i", file_path,
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-tune", "zerolatency",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "128k",
        "-ac", "2",
        "-movflags", "frag_keyframe+empty_moov+default_base_moof",
        "-f", "mp4",
        "pipe:1"
    ])

    process = await asyncio.create_subprocess_exec(
        *ffmpeg_cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL
    )

    async def transcode_stream():
        try:
            while True:
                chunk = await process.stdout.read(64 * 1024)
                if not chunk:
                    break
                yield chunk
        finally:
            try:
                process.terminate()
                await process.wait()
            except Exception:
                pass

    return StreamingResponse(transcode_stream(), media_type="video/mp4")

@router.post("/api/backup/transcode/enqueue")
async def backup_enqueue_transcode(req: BackupTranscodeRequest):
    enqueued = transcode_worker_instance.enqueue_for_backup(folder_path=req.folder_path, file_path=req.file_path)
    return {
        "success": True,
        "enqueued_count": len(enqueued),
        "enqueued": enqueued,
    }

@router.get("/api/backup/transcode/summary")
def get_backup_transcode_summary():
    conn = get_db()
    try:
        cur = conn.execute(
            "SELECT path, name, size FROM files WHERE type = 'video' AND LOWER(path) NOT LIKE '%.mp4'"
        )
        rows = cur.fetchall()
    except Exception as e:
        print(f"Error querying transcode summary: {e}")
        rows = []
    finally:
        conn.close()

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
        total_bytes += (r["size"] or 0)
        pending_files.append(r)

    sorted_breakdown = dict(sorted(breakdown.items(), key=lambda item: item[1], reverse=True))

    return {
        "pending_count": len(pending_files),
        "total_bytes": total_bytes,
        "total_size_formatted": format_size(total_bytes),
        "breakdown": sorted_breakdown,
        "already_queued_count": len(active_or_queued_paths),
    }

@router.post("/api/backup/transcode/enqueue_all")
async def backup_enqueue_all_transcode():
    enqueued = transcode_worker_instance.enqueue_all_for_backup()
    return {
        "success": True,
        "enqueued_count": len(enqueued),
    }

@router.get("/api/backup/transcode_stream")
async def stream_backup_transcode(path: str = Query(...), ss: Optional[float] = Query(0.0)):
    real_path = validate_backup_path(path)
    if not os.path.exists(real_path) or not os.path.isfile(real_path):
        raise HTTPException(status_code=404, detail="Video file not found")

    ffmpeg_cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
    ]

    if ss and ss > 0:
        ffmpeg_cmd.extend(["-ss", str(ss)])

    ffmpeg_cmd.extend([
        "-i", real_path,
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-tune", "zerolatency",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "128k",
        "-ac", "2",
        "-movflags", "frag_keyframe+empty_moov+default_base_moof",
        "-f", "mp4",
        "pipe:1"
    ])

    process = await asyncio.create_subprocess_exec(
        *ffmpeg_cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL
    )

    async def transcode_stream():
        try:
            while True:
                chunk = await process.stdout.read(64 * 1024)
                if not chunk:
                    break
                yield chunk
        finally:
            try:
                process.terminate()
                await process.wait()
            except Exception:
                pass

    return StreamingResponse(transcode_stream(), media_type="video/mp4")
