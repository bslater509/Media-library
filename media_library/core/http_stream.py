"""Shared HTTP 206 byte-range streaming helper for media endpoints.

Extracted from `stream_backup_media` (`routers/library.py`) and
`stream_video` (`routers/sorter.py`) so the range semantics (HEAD handling,
full-file sender, range parsing, 416/206 responses, chunk clamps) live in one
place and cannot drift between callers.

Web-layer helper only: no database or services dependencies.
"""

import asyncio
import logging
import mimetypes
import os

from fastapi import HTTPException, Request, Response
from fastapi.responses import StreamingResponse

from media_library.core.constants import (
    STREAM_FULL_CHUNK,
    STREAM_MAX_RANGE_BYTES,
    STREAM_RANGE_CHUNK,
)

logger = logging.getLogger(__name__)


async def full_file_sender(real_path: str):
    """Yield the whole file in 1 MB chunks without blocking the event loop."""

    def _open():
        return open(real_path, "rb")

    f = await asyncio.to_thread(_open)
    try:
        while True:
            chunk = await asyncio.to_thread(f.read, STREAM_FULL_CHUNK)
            if not chunk:
                break
            yield chunk
    finally:
        await asyncio.to_thread(f.close)


async def chunk_generator(filepath: str, start_pos: int, length: int):
    """Yield up to `length` bytes from `start_pos` in 128 KB async chunk reads."""

    def _open_and_seek():
        # Opened here so the generator can close it in `finally` after the
        # async chunk loop; not a context-managed open by design.
        f = open(filepath, "rb")  # noqa: SIM115
        f.seek(start_pos)
        return f

    f = await asyncio.to_thread(_open_and_seek)
    try:
        bytes_left = length
        while bytes_left > 0:
            read_size = min(bytes_left, STREAM_RANGE_CHUNK)
            data = await asyncio.to_thread(f.read, read_size)
            if not data:
                break
            bytes_left -= len(data)
            yield data
    finally:
        await asyncio.to_thread(f.close)


async def stream_file_with_range(
    real_path: str,
    request: Request | None,
    mime_type: str | None = None,
    default_mime: str = "video/mp4",
) -> Response:
    """Stream a file honoring `Range: bytes=start-end` requests.

    Handles HEAD (metadata-only), full-file (no Range header), and 206/416
    ranged responses with the shared chunk sizes and 2 MB range clamp. Raising
    `HTTPException(500)` on malformed ranges matches the previous callers.
    """
    file_size = await asyncio.to_thread(os.path.getsize, real_path)
    if not mime_type:
        mime_type, _ = mimetypes.guess_type(real_path)
    if not mime_type:
        mime_type = default_mime

    if request is not None and request.method == "HEAD":
        return Response(
            status_code=200,
            headers={
                "Content-Length": str(file_size),
                "Content-Type": mime_type,
                "Accept-Ranges": "bytes",
            },
        )

    range_header = request.headers.get("Range") if request is not None else None
    if not range_header:
        headers = {
            "Content-Length": str(file_size),
            "Content-Type": mime_type,
            "Accept-Ranges": "bytes",
        }
        return StreamingResponse(
            full_file_sender(real_path), status_code=200, headers=headers
        )

    try:
        range_val = range_header.strip().replace("bytes=", "")
        parts = range_val.split("-")
        start = int(parts[0]) if parts[0] else 0
        end = int(parts[1]) if len(parts) > 1 and parts[1] else file_size - 1

        if start >= file_size:
            return Response(
                status_code=416,
                headers={"Content-Range": f"bytes */{file_size}"},
            )
        if end >= file_size:
            end = file_size - 1
        if start > end:
            return Response(
                status_code=416,
                headers={"Content-Range": f"bytes */{file_size}"},
            )

        if (end - start + 1) > STREAM_MAX_RANGE_BYTES:
            end = start + STREAM_MAX_RANGE_BYTES - 1

        content_length = end - start + 1

        headers = {
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Accept-Ranges": "bytes",
            "Content-Length": str(content_length),
            "Content-Type": mime_type,
        }
        return StreamingResponse(
            chunk_generator(real_path, start, content_length),
            status_code=206,
            headers=headers,
        )
    except Exception as e:
        logger.error(f"Error streaming range for {real_path}: {e}")
        raise HTTPException(status_code=500, detail="Error streaming video range")
