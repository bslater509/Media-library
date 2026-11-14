"""Byte-range media streaming and thumbnail delivery"""

from __future__ import annotations

import asyncio
import mimetypes
import os
import re
import urllib.parse
from collections.abc import Iterator
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse

from media_library import animated, thumbnails, zipfs
from media_library.cache import cache_key
from media_library.config import get_settings
from media_library.db import reader, writes_available
from media_library.paths import (
    is_zip_member,
    resolve_existing_file,
    resolve_library_path,
)
from media_library.routers.common import as_http_error
from media_library.schemas import (
    AnimatedBatchRequest,
    AnimatedRequest,
    AnimatedStatus,
    PosterRequest,
    PosterResponse,
    ThumbnailCandidates,
    ThumbnailExistsResponse,
)
from media_library.thumbnails import generate_thumbnail
from media_library.transcode_stream import build_command, relay

router = APIRouter()

_CHUNK = 65536 * 16
_RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")


def _attachment_disposition(name: str) -> str:
    """RFC 5987 header: quoted ASCII fallback plus a UTF-8 filename*.

    Filenames from the filesystem are not trust-boundary-clean inside header
    values: a quote breaks the parameter, control characters (including CR/LF)
    are refused outright by the WSGI/ASGI layer and tear down the connection,
    and non-ASCII needs the ext-value form.
    """
    safe_name = name.replace("\\", "_").replace('"', "_")
    safe_name = "".join("_" if (c < " " or c == "\x7f") else c for c in safe_name)
    encoded = urllib.parse.quote(name.encode("utf-8"), safe="")
    return f"attachment; filename=\"{safe_name}\"; filename*=UTF-8''{encoded}"


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
    """The fixed cache key, so every poster already on disk is reused."""
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
        end = file_size - 1
        if start >= file_size or end < start:
            # A suffix range on an empty file is unsatisfiable.
            return None
        return start, end
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


_TRANSCODE_SEMAPHORE = asyncio.Semaphore(2)


@router.get("/media/transcode")
async def transcode_media(request: Request, path: str, ss: float = 0):
    """Live fragmented-MP4 stream for containers the browser cannot play."""
    # Cap concurrent encoders: every active viewer of a non-native container
    # is a full x264 pipe, and an unbounded queue of them OOMs the host.
    await _TRANSCODE_SEMAPHORE.acquire()
    try:
        target = path
        # is_zip_member consults the index: keep it off the event loop.
        if await asyncio.to_thread(is_zip_member, path):
            split = zipfs.split_zip_path(path)
            assert split is not None
            archive = resolve_library_path(split[0])
            try:
                target = await asyncio.to_thread(
                    zipfs.extract_member, archive, split[1]
                )
            except Exception as exc:
                raise as_http_error(exc) from exc
        else:
            target = await asyncio.to_thread(resolve_existing_file, path)
        process = await asyncio.create_subprocess_exec(
            *build_command(target, float(ss or 0)),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except BaseException:
        _TRANSCODE_SEMAPHORE.release()
        raise
    return _ReleasingResponse(
        relay(process), semaphore=_TRANSCODE_SEMAPHORE, media_type="video/mp4"
    )


class _ReleasingResponse(StreamingResponse):
    """StreamingResponse that frees the transcode slot when the stream ends."""

    def __init__(self, content, semaphore: asyncio.Semaphore, **kwargs) -> None:
        super().__init__(content, **kwargs)
        self._semaphore = semaphore

    async def stream_response(self, send) -> None:  # type: ignore[override]
        try:
            await super().stream_response(send)
        finally:
            self._semaphore.release()


@router.api_route("/media", methods=["GET", "HEAD"])
async def stream_media(request: Request, path: str, download: bool = False):
    """Stream a library file with HTTP 206 partial-content support."""
    if await asyncio.to_thread(is_zip_member, path):
        split = zipfs.split_zip_path(path)
        assert split is not None
        archive = resolve_library_path(split[0])
        try:
            target = await asyncio.to_thread(zipfs.extract_member, archive, split[1])
        except Exception as exc:
            raise as_http_error(exc) from exc
        return await stream_path(request, target, download=download)
    return await stream_path(
        request, await asyncio.to_thread(resolve_existing_file, path), download=download
    )


async def stream_path(request: Request, target: str, download: bool = False):
    """Range-stream any validated file path (shared with the sorter)."""
    stat = await _stat(target)
    file_size = stat.st_size
    etag = f'"{file_size:x}-{int(stat.st_mtime):x}"'
    media_type = _media_type(target)
    # SVG can carry scripts, so it is never offered for navigation in-app;
    # <img> embedding in the viewer keeps working (disposition is
    # navigation-only). nosniff stops the browser sniffing anything else.
    force_attachment = download or media_type == "image/svg+xml"
    headers = {
        "Accept-Ranges": "bytes",
        "ETag": etag,
        "Last-Modified": _http_date(stat.st_mtime),
        "Cache-Control": "private, max-age=0, must-revalidate",
        "X-Content-Type-Options": "nosniff",
        "Content-Disposition": (
            _attachment_disposition(os.path.basename(target))
            if force_attachment
            else "inline"
        ),
    }
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
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
        return await asyncio.to_thread(os.stat, path)
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
    # Clamp first, like every media read: an index row from another library
    # root must never turn this endpoint into an arbitrary-file reader.
    path = await asyncio.to_thread(resolve_library_path, path)
    # _row_stat opens SQLite: keep it off the event loop.
    mtime, size, _file_type, _has_thumb = await asyncio.to_thread(_row_stat, path)
    key = _thumb_key(path, mtime, size)
    settings = get_settings()
    if kind == "animated":
        candidate = settings.thumbnail_dir / "animated" / f"anim_{key}.webp"
        media_type = "image/webp"
    else:
        candidate = settings.thumbnail_dir / f"thumb_{key}.jpg"
        media_type = "image/jpeg"
    if not await asyncio.to_thread(candidate.is_file):
        if kind == "static" and _file_type in ("image", "video"):
            try:
                candidate = await asyncio.to_thread(
                    generate_thumbnail, path, _file_type, mtime, size
                )
            except Exception:
                # An unreadable/corrupt image is a missing preview, not a 5xx.
                raise HTTPException(
                    status_code=404, detail="Thumbnail not generated"
                ) from None
        else:
            raise HTTPException(status_code=404, detail="Thumbnail not generated")
    return FileResponse(
        candidate,
        media_type=media_type,
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@router.get("/thumbnails/cache/{key}", response_class=FileResponse)
async def get_cached_thumbnail(key: str, path: str, kind: str = "static"):
    """Serve only fixed-name local artwork; validate the source on cache miss.

    The opaque key cannot address the library or arbitrary local files. A hit
    needs neither SQLite nor realpath/lstat of a remote original. Keep the
    path-based endpoint as the fully validated fallback for evicted artwork.
    """
    if re.fullmatch(r"[0-9a-f]{24}", key) is None:
        raise HTTPException(status_code=404, detail="Invalid thumbnail key")
    root = get_settings().thumbnail_dir
    animated_kind = kind == "animated"
    candidate = (
        root / "animated" / f"anim_{key}.webp"
        if animated_kind
        else root / f"thumb_{key}.jpg"
    )
    if not await asyncio.to_thread(candidate.is_file):
        # The regenerated key may have changed since this URL was issued.
        # Do not cache that response under the obsolete immutable URL.
        response = await get_thumbnail(path, kind)
        response.headers["Cache-Control"] = "private, no-cache"
        return response
    return FileResponse(
        candidate,
        media_type="image/webp" if animated_kind else "image/jpeg",
        headers={"Cache-Control": "private, max-age=31536000, immutable"},
    )


@router.get("/thumbnails/candidates", response_model=ThumbnailCandidates)
async def thumbnail_candidates(
    path: str, interval: float = 30.0, count: int | None = None
) -> dict:
    """Evenly spaced frames the poster chooser can offer."""
    try:
        return await asyncio.to_thread(thumbnails.candidates, path, interval, count)
    except Exception as exc:
        raise as_http_error(exc) from exc


@router.get("/thumbnails/frame")
async def thumbnail_frame(path: str, timestamp: float):
    """One 480px candidate frame, cached per video and timestamp."""
    try:
        candidate = await asyncio.to_thread(thumbnails.frame, path, timestamp)
    except Exception as exc:
        raise as_http_error(exc) from exc
    return FileResponse(
        candidate,
        media_type="image/jpeg",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.post("/thumbnails/poster", response_model=PosterResponse)
async def thumbnail_set_poster(payload: PosterRequest) -> dict:
    """Replace a video's poster with the chosen frame."""
    if not writes_available():
        raise HTTPException(status_code=503, detail="Writes are disabled")
    try:
        return await asyncio.to_thread(
            thumbnails.set_poster, payload.path, payload.timestamp
        )
    except Exception as exc:
        raise as_http_error(exc) from exc


@router.post("/thumbnails/animated", response_model=AnimatedStatus)
async def thumbnail_animated(payload: AnimatedRequest) -> dict:
    """Queue one video for an animated preview."""
    if not writes_available():
        raise HTTPException(status_code=503, detail="Writes are disabled")
    try:
        added = await asyncio.to_thread(animated.enqueue, [payload.path])
    except Exception as exc:
        raise as_http_error(exc) from exc
    return {**animated.status(), "added": added}


@router.post("/thumbnails/animated/batch", response_model=AnimatedStatus)
async def thumbnail_animated_batch(payload: AnimatedBatchRequest) -> dict:
    """Queue every video in a folder (or the whole library) without a preview."""
    if not writes_available():
        raise HTTPException(status_code=503, detail="Writes are disabled")
    try:
        added = await asyncio.to_thread(
            animated.enqueue_batch, payload.folder, payload.all_videos, payload.limit
        )
    except Exception as exc:
        raise as_http_error(exc) from exc
    return {**animated.status(), "added": added}


@router.get("/thumbnails/animated/status", response_model=AnimatedStatus)
async def thumbnail_animated_status() -> dict:
    return await asyncio.to_thread(animated.status)


@router.post("/thumbnails/animated/cancel", response_model=AnimatedStatus)
async def thumbnail_animated_cancel() -> dict:
    await asyncio.to_thread(animated.cancel)
    return animated.status()


@router.get("/thumbnails/exists", response_model=ThumbnailExistsResponse)
async def thumbnail_exists(path: str, kind: str = "static") -> dict:
    """Cheap check the client uses to decide between poster and full image."""
    if not path:
        raise HTTPException(status_code=400, detail="A path is required")
    # Clamp like /thumbnails does: keep the endpoint library-root-only even
    # though the lookup is index + hash-named cache file today.
    path = resolve_library_path(path)
    try:
        mtime, size, _t, _h = await asyncio.to_thread(_row_stat, path)
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
