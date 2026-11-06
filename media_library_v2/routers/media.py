"""Byte-range media streaming and thumbnail delivery for v2."""

from __future__ import annotations

import mimetypes
import os
import re
from collections.abc import Iterator
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse

from media_library_v2.cache import cache_key
from media_library_v2.config import get_settings
from media_library_v2.db import reader
from media_library_v2.paths import is_zip_member, resolve_existing_file

router = APIRouter()

_CHUNK = 65536 * 16
_RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")

_MEDIA_TYPES = {
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
    ".mkv": "video/x-matroska",
    ".avi": "video/x-msvideo",
    ".ts": "video/mp2t",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".avif": "image/avif",
    ".bmp": "image/bmp",
    ".svg": "image/svg+xml",
}


def _media_type(path: str) -> str:
    ext = Path(path).suffix.lower()
    return (
        _MEDIA_TYPES.get(ext)
        or mimetypes.guess_type(path)[0]
        or "application/octet-stream"
    )


def _thumb_key(path: str, mtime: float, size: int) -> str:
    """Same deterministic key v1 uses, so its cache is reused unchanged."""
    return cache_key(path, mtime, size)


def _row_stat(path: str) -> tuple[float, int, str, bool]:
    with reader() as conn:
        row = conn.execute(
            "SELECT mtime, size, type, has_thumb FROM files WHERE path = ?", (path,)
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Not indexed")
    return (
        float(row["mtime"] or 0),
        int(row["size"] or 0),
        row["type"],
        bool(row["has_thumb"]),
    )


def _parse_range(header: str, file_size: int) -> tuple[int, int] | None:
    match = _RANGE_RE.match(header.strip())
    if not match:
        return None
    raw_start, raw_end = match.groups()
    if not raw_start and not raw_end:
        return None
    if not raw_start:
        length = int(raw_end)
        if length <= 0:
            return None
        start = max(0, file_size - length)
        return start, file_size - 1
    start = int(raw_start)
    end = int(raw_end) if raw_end else file_size - 1
    if start >= file_size or end < start:
        return None
    return start, min(end, file_size - 1)


def _iter_file(path: str, start: int, end: int) -> Iterator[bytes]:
    with open(path, "rb") as handle:
        handle.seek(start)
        remaining = end - start + 1
        while remaining > 0:
            chunk = handle.read(min(_CHUNK, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


@router.api_route("/media", methods=["GET", "HEAD"])
async def stream_media(request: Request, path: str, download: bool = False):
    """Stream a library file with HTTP 206 partial-content support."""
    if is_zip_member(path):
        raise HTTPException(
            status_code=501,
            detail="ZIP member streaming is not available in this build",
        )
    return await stream_path(request, resolve_existing_file(path), download=download)


async def stream_path(request: Request, target: str, download: bool = False):
    """Range-stream any validated file path (shared with the sorter)."""
    stat = await _stat(target)
    file_size = stat.st_size
    etag = f'"{file_size:x}-{int(stat.st_mtime):x}"'
    headers = {
        "Accept-Ranges": "bytes",
        "ETag": etag,
        "Last-Modified": _http_date(stat.st_mtime),
        "Cache-Control": "private, max-age=0, must-revalidate",
        "Content-Disposition": (
            f'attachment; filename="{os.path.basename(target)}"'
            if download
            else "inline"
        ),
    }
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)

    media_type = _media_type(target)
    range_header = request.headers.get("range")
    if range_header:
        parsed = _parse_range(range_header, file_size)
        if parsed is None:
            return Response(
                status_code=416,
                headers={**headers, "Content-Range": f"bytes */{file_size}"},
            )
        start, end = parsed
        headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"
        headers["Content-Length"] = str(end - start + 1)
        if request.method == "HEAD":
            return Response(status_code=206, headers=headers, media_type=media_type)
        return StreamingResponse(
            _iter_file(target, start, end),
            status_code=206,
            headers=headers,
            media_type=media_type,
        )

    headers["Content-Length"] = str(file_size)
    if request.method == "HEAD":
        return Response(status_code=200, headers=headers, media_type=media_type)
    return StreamingResponse(
        _iter_file(target, 0, file_size),
        headers=headers,
        media_type=media_type,
    )


async def _stat(path: str) -> os.stat_result:
    try:
        return os.stat(path)
    except OSError as exc:
        raise HTTPException(status_code=404, detail="File not found") from exc


def _http_date(timestamp: float) -> str:
    from email.utils import formatdate

    return formatdate(timestamp, usegmt=True)


@router.get("/thumbnails")
async def get_thumbnail(path: str, kind: str = "static"):
    """Serve a cached poster frame (``static``) or animated preview."""
    if not path:
        raise HTTPException(status_code=400, detail="A path is required")
    target = Path(path)
    if not target.is_absolute():
        raise HTTPException(status_code=400, detail="A path is required")
    mtime, size, _file_type, _has_thumb = _row_stat(path)
    key = _thumb_key(path, mtime, size)
    settings = get_settings()
    if kind == "animated":
        candidate = settings.thumbnail_dir / "animated" / f"anim_{key}.webp"
        media_type = "image/webp"
    else:
        candidate = settings.thumbnail_dir / f"thumb_{key}.jpg"
        media_type = "image/jpeg"
    if not candidate.is_file():
        raise HTTPException(status_code=404, detail="Thumbnail not generated")
    return FileResponse(
        candidate,
        media_type=media_type,
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@router.get("/thumbnails/exists")
async def thumbnail_exists(path: str, kind: str = "static") -> dict:
    """Cheap check the client uses to decide between poster and full image."""
    try:
        mtime, size, _t, _h = _row_stat(path)
    except HTTPException:
        return {"static": False, "animated": False}
    key = _thumb_key(path, mtime, size)
    settings = get_settings()
    return {
        "static": (settings.thumbnail_dir / f"thumb_{key}.jpg").is_file(),
        "animated": (
            settings.thumbnail_dir / "animated" / f"anim_{key}.webp"
        ).is_file(),
    }
