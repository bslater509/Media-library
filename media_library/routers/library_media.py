"""FastAPI router for Media Library thumbnail and byte-range media streaming."""

import asyncio
import logging
import mimetypes
import os

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse

from media_library.core.constants import (
    IMAGE_EXTENSIONS,
    MEDIA_CACHE_MAX_AGE,
    THUMB_CACHE_MAX_AGE_IMMUTABLE,
    VIDEO_EXTENSIONS,
)
from media_library.core.http_stream import stream_file_with_range
from media_library.services.ffmpeg_service import (
    generate_thumbnail,
    get_thumbnail_path_fast,
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
