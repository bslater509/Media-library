"""FastAPI router for Media Library thumbnail and byte-range media streaming."""

import asyncio
import logging
import mimetypes
import os
import urllib.parse

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse

from media_library.core.constants import (
    IMAGE_EXTENSIONS,
    MEDIA_CACHE_MAX_AGE,
    THUMB_CACHE_MAX_AGE_IMMUTABLE,
    VIDEO_EXTENSIONS,
)
from media_library.core.http_stream import stream_file_with_range
from media_library.db.connection import db_conn
from media_library.services.ffmpeg_service import (
    extract_candidate_frame,
    generate_animated_preview,
    generate_thumbnail,
    get_animated_preview_path,
    get_candidate_cache_path,
    get_thumbnail_path_fast,
    probe_video_meta,
)
from media_library.services.media_service import validate_backup_path
from media_library.services.zip import (
    extract_zip_to_cache,
    resolve_zip_file_path,
    split_zip_path,
)

router = APIRouter()

logger = logging.getLogger(__name__)


@router.api_route("/api/backup/thumbnail", methods=["GET", "HEAD"])
async def get_backup_thumbnail(
    path: str = Query(...),
    mtime: float | None = Query(None),
    size: int | None = Query(None),
    v: str | None = Query(None),
    request: Request = None,
):
    real_path = validate_backup_path(path)

    # Check if path references an item inside a zip archive
    zip_p, inner_p = split_zip_path(real_path)
    if zip_p and inner_p:
        resolved = resolve_zip_file_path(real_path)
        if not resolved:
            try:
                cache_dir = await asyncio.to_thread(extract_zip_to_cache, zip_p)
                resolved = resolve_zip_file_path(real_path)
                if not resolved and cache_dir:
                    resolved = str(cache_dir / inner_p)
            except Exception as ex:
                logger.error(f"Extraction error for thumbnail {real_path}: {ex}")
        if resolved and os.path.exists(resolved):
            real_path = resolved

    ext = os.path.splitext(real_path)[1].lower()
    is_video = ext in VIDEO_EXTENSIONS
    is_img = ext in IMAGE_EXTENSIONS
    if not is_video and not is_img:
        raise HTTPException(status_code=400, detail="Unsupported media file type")

    thumb_path = await asyncio.to_thread(
        get_thumbnail_path_fast, real_path, mtime, size
    )
    if thumb_path.exists() and thumb_path.stat().st_size > 0:
        etag = f'"{thumb_path.name}_{int(thumb_path.stat().st_mtime)}"'
        cache_hdr = (
            f"public, max-age={THUMB_CACHE_MAX_AGE_IMMUTABLE}, immutable"
            if (mtime is not None and size is not None and v is None)
            else f"public, max-age={MEDIA_CACHE_MAX_AGE}"
        )
        if request and request.headers.get("if-none-match") == etag:
            return Response(
                status_code=304, headers={"ETag": etag, "Cache-Control": cache_hdr}
            )
        if request and request.method == "HEAD":
            return Response(
                status_code=200,
                headers={
                    "Content-Length": str(thumb_path.stat().st_size),
                    "Content-Type": "image/jpeg",
                    "ETag": etag,
                    "Cache-Control": cache_hdr,
                },
            )
        return FileResponse(
            str(thumb_path),
            media_type="image/jpeg",
            headers={"Cache-Control": cache_hdr, "ETag": etag},
        )

    file_exists = await asyncio.to_thread(os.path.exists, real_path)
    if not file_exists:
        raise HTTPException(status_code=404, detail="File not found")

    ext = os.path.splitext(real_path)[1].lower()
    is_video = ext in VIDEO_EXTENSIONS
    is_img = ext in IMAGE_EXTENSIONS
    if not is_video and not is_img:
        raise HTTPException(status_code=400, detail="Unsupported media file type")

    thumb = await generate_thumbnail(real_path, is_video=is_video, update_db=True)
    if thumb and thumb.exists():
        if request and request.method == "HEAD":
            return Response(
                status_code=200,
                headers={
                    "Content-Length": str(thumb.stat().st_size),
                    "Content-Type": "image/jpeg",
                },
            )
        return FileResponse(
            str(thumb),
            media_type="image/jpeg",
            headers={"Cache-Control": f"public, max-age={MEDIA_CACHE_MAX_AGE}"},
        )

    if is_img:
        mime, _ = mimetypes.guess_type(real_path)
        mime_type = mime or "image/jpeg"
        f_size = await asyncio.to_thread(os.path.getsize, real_path)
        if request and request.method == "HEAD":
            return Response(
                status_code=200,
                headers={"Content-Length": str(f_size), "Content-Type": mime_type},
            )
        return FileResponse(
            real_path,
            media_type=mime_type,
            headers={"Cache-Control": f"public, max-age={MEDIA_CACHE_MAX_AGE}"},
        )

    raise HTTPException(status_code=500, detail="Could not generate thumbnail")


@router.api_route("/api/backup/media", methods=["GET", "HEAD"])
async def stream_backup_media(path: str = Query(...), request: Request = None):
    real_path = validate_backup_path(path)

    # Check if path references an item inside a zip archive
    zip_p, inner_p = split_zip_path(real_path)
    if zip_p and inner_p:
        resolved = resolve_zip_file_path(real_path)
        if not resolved:
            try:
                cache_dir = await asyncio.to_thread(extract_zip_to_cache, zip_p)
                resolved = resolve_zip_file_path(real_path)
                if not resolved and cache_dir:
                    resolved = str(cache_dir / inner_p)
            except Exception as ex:
                logger.error(f"Extraction error for media stream {real_path}: {ex}")
        if resolved and os.path.exists(resolved):
            real_path = resolved

    file_exists = await asyncio.to_thread(os.path.exists, real_path)
    if not file_exists:
        raise HTTPException(status_code=404, detail="File not found")

    ext = os.path.splitext(real_path)[1].lower()
    mime_type, _ = mimetypes.guess_type(real_path)

    if ext in IMAGE_EXTENSIONS:
        if not mime_type:
            mime_type = "image/jpeg"
        if request and request.method == "HEAD":
            f_size = await asyncio.to_thread(os.path.getsize, real_path)
            return Response(
                status_code=200,
                headers={"Content-Length": str(f_size), "Content-Type": mime_type},
            )
        return FileResponse(
            real_path,
            media_type=mime_type,
            headers={"Cache-Control": "public, max-age=3600"},
        )

    return await stream_file_with_range(real_path, request, mime_type)


@router.get("/api/backup/thumbnails/candidates")
async def get_thumbnail_candidates(
    path: str = Query(...),
    count: int | None = Query(None, ge=2, le=500),
    interval: float | None = Query(None, ge=1.0, le=3600.0),
):
    """Calculates candidate thumbnail timestamps spaced across video duration.

    If `count` is specified (and `interval` is omitted), evenly divides the duration into `count` frames.
    Otherwise (by default, or when `interval` is provided), candidates are sampled every `interval` seconds
    (default 30.0s) across the video duration.
    """
    real_path = validate_backup_path(path)
    ext = os.path.splitext(real_path)[1].lower()
    if ext not in VIDEO_EXTENSIONS:
        raise HTTPException(
            status_code=400, detail="Only video files support thumbnail candidates"
        )

    zip_p, inner_p = split_zip_path(real_path)
    if not (zip_p and inner_p):
        file_exists = await asyncio.to_thread(os.path.exists, real_path)
        if not file_exists:
            raise HTTPException(status_code=404, detail="Video file not found")

    duration = 0.0
    width = None
    height = None
    try:
        with db_conn() as conn:
            row = conn.execute(
                "SELECT duration, width, height FROM files WHERE path = ? LIMIT 1",
                (real_path,),
            ).fetchone()
            if row:
                duration = float(row["duration"] or 0.0)
                width = row["width"]
                height = row["height"]
    except Exception:
        pass

    if duration <= 0.0:
        meta = await probe_video_meta(real_path)
        duration = float(meta.get("duration") or 0.0)

    if duration <= 0.0:
        duration = 1.0

    chosen_interval: float | None = None
    if count is not None and interval is None:
        # Fixed count mode (evenly spaced across duration)
        margin = min(1.0, duration * 0.05) if duration > 3.0 else 0.0
        start_t = margin
        end_t = max(margin, duration - margin)
        if end_t <= start_t:
            timestamps = [start_t] * count
        else:
            step = (end_t - start_t) / max(1, count - 1)
            timestamps = [round(start_t + i * step, 3) for i in range(count)]
    else:
        # Interval mode (default 30.0s)
        chosen_interval = float(interval if interval is not None else 30.0)
        max_safe = max(0.0, duration - min(0.5, duration * 0.02))

        if duration <= chosen_interval:
            # For short videos (<= interval), offer a few evenly spaced candidates across the video
            sub_count = max(2, min(6, int(duration // 2) + 1))
            step = duration / max(1, sub_count - 1)
            timestamps = [
                round(min(i * step, max_safe), 3)
                for i in range(sub_count)
            ]
        else:
            timestamps = []
            curr = 0.0
            while curr < duration and len(timestamps) < 500:
                ts = min(curr, max_safe)
                timestamps.append(round(ts, 3))
                curr += chosen_interval

            # If the tail after the last candidate is significant (>= interval * 0.33 or >= 10s),
            # include a near-end candidate frame as well
            if (duration - timestamps[-1]) >= max(5.0, chosen_interval * 0.33):
                end_ts = round(max_safe, 3)
                if end_ts > timestamps[-1] and (end_ts - timestamps[-1]) >= 3.0 and len(timestamps) < 500:
                    timestamps.append(end_ts)

    def _format_time(sec: float) -> str:
        s = int(round(sec))
        m, s = divmod(s, 60)
        h, m = divmod(m, 60)
        if h > 0:
            return f"{h:02d}:{m:02d}:{s:02d}"
        return f"{m:02d}:{s:02d}"

    candidates = [
        {
            "timestamp": ts,
            "time_str": _format_time(ts),
            "url": f"/api/backup/thumbnails/frame?path={urllib.parse.quote(real_path)}&timestamp={ts}",
        }
        for ts in timestamps
    ]

    return {
        "path": real_path,
        "duration": round(duration, 3),
        "duration_formatted": _format_time(duration),
        "width": width,
        "height": height,
        "interval": chosen_interval,
        "count": len(candidates),
        "candidates": candidates,
    }


@router.api_route("/api/backup/thumbnails/frame", methods=["GET", "HEAD"])
async def get_thumbnail_frame(
    path: str = Query(...),
    timestamp: float = Query(0.0, ge=0.0),
    request: Request = None,
):
    """Returns a specific candidate video frame image at the given timestamp."""
    real_path = validate_backup_path(path)
    ext = os.path.splitext(real_path)[1].lower()
    if ext not in VIDEO_EXTENSIONS:
        raise HTTPException(
            status_code=400, detail="Only video files support frame extraction"
        )

    zip_p, inner_p = split_zip_path(real_path)
    if not (zip_p and inner_p):
        file_exists = await asyncio.to_thread(os.path.exists, real_path)
        if not file_exists:
            raise HTTPException(status_code=404, detail="Video file not found")

    cand_path = await asyncio.to_thread(get_candidate_cache_path, real_path, timestamp)
    if not (cand_path.exists() and cand_path.stat().st_size > 0):
        frame_path = await extract_candidate_frame(real_path, timestamp)
        if not frame_path or not frame_path.exists():
            raise HTTPException(status_code=500, detail="Could not extract candidate frame")
        cand_path = frame_path

    if request and request.method == "HEAD":
        return Response(
            status_code=200,
            headers={
                "Content-Length": str(cand_path.stat().st_size),
                "Content-Type": "image/jpeg",
            },
        )
    return FileResponse(
        str(cand_path),
        media_type="image/jpeg",
        headers={"Cache-Control": f"public, max-age={MEDIA_CACHE_MAX_AGE}"},
    )


@router.api_route("/api/backup/thumbnails/animated", methods=["GET", "HEAD"])
async def get_animated_thumbnail(
    path: str = Query(...),
    mtime: float | None = Query(None),
    size: int | None = Query(None),
    v: str | None = Query(None),
    request: Request = None,
):
    """Returns an animated looping WebP preview for a video file, generating on-demand if needed."""
    real_path = validate_backup_path(path)
    ext = os.path.splitext(real_path)[1].lower()
    if ext not in VIDEO_EXTENSIONS:
        raise HTTPException(
            status_code=400, detail="Only video files support animated previews"
        )

    zip_p, inner_p = split_zip_path(real_path)
    if not (zip_p and inner_p):
        file_exists = await asyncio.to_thread(os.path.exists, real_path)
        if not file_exists:
            raise HTTPException(status_code=404, detail="Video file not found")

    anim_path = await asyncio.to_thread(get_animated_preview_path, real_path, mtime, size)
    if not (anim_path.exists() and anim_path.stat().st_size > 0):
        gen_path = await generate_animated_preview(real_path)
        if gen_path and gen_path.exists() and gen_path.stat().st_size > 0:
            anim_path = gen_path
        else:
            # Fallback to static thumbnail if animated generation failed
            fallback_thumb = await generate_thumbnail(real_path, is_video=True, mtime=mtime, size=size)
            if fallback_thumb and fallback_thumb.exists():
                if request and request.method == "HEAD":
                    return Response(
                        status_code=200,
                        headers={
                            "Content-Length": str(fallback_thumb.stat().st_size),
                            "Content-Type": "image/jpeg",
                        },
                    )
                return FileResponse(
                    str(fallback_thumb),
                    media_type="image/jpeg",
                    headers={"Cache-Control": f"public, max-age={MEDIA_CACHE_MAX_AGE}"},
                )
            raise HTTPException(status_code=500, detail="Could not generate animated preview")

    if request and request.method == "HEAD":
        return Response(
            status_code=200,
            headers={
                "Content-Length": str(anim_path.stat().st_size),
                "Content-Type": "image/webp",
            },
        )

    cache_header = (
        f"public, max-age={THUMB_CACHE_MAX_AGE_IMMUTABLE}, immutable"
        if (mtime is not None or v is not None)
        else f"public, max-age={MEDIA_CACHE_MAX_AGE}"
    )

    return FileResponse(
        str(anim_path),
        media_type="image/webp",
        headers={"Cache-Control": cache_header},
    )
