"""Video Sorter endpoints: queue, destinations, moves, undo, streaming."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse

from media_library import sorter
from media_library.config import get_settings
from media_library.paths import stable_id
from media_library.routers.common import as_http_error
from media_library.routers.media import (
    _TRANSCODE_SEMAPHORE,
    _ReleasingResponse,
    stream_path,
)
from media_library.schemas import (
    SorterConfigResponse,
    SorterDestination,
    SorterDuplicateResponse,
    SorterFolderRequest,
    SorterMoveRequest,
    SorterPathRequest,
    SorterQueueResponse,
    SorterRenameRequest,
)
from media_library.transcode_stream import build_command, relay

router = APIRouter()


@router.get("/sorter/config", response_model=SorterConfigResponse)
async def config() -> dict:
    return await asyncio.to_thread(sorter.sorter_config)


@router.get("/sorter/duplicate", response_model=SorterDuplicateResponse)
async def duplicate(path: str) -> dict:
    """Whether this incoming clip already exists in the library."""
    try:
        return await asyncio.to_thread(sorter.check_duplicate, path)
    except Exception as exc:
        raise as_http_error(exc) from exc


@router.get("/sorter/queue", response_model=SorterQueueResponse)
async def queue() -> dict:
    return await asyncio.to_thread(sorter._queue_payload)


@router.post("/sorter/refresh", response_model=SorterQueueResponse)
async def refresh() -> dict:
    await asyncio.to_thread(sorter.invalidate_queue)
    return await asyncio.to_thread(sorter._queue_payload)


@router.get("/sorter/destinations", response_model=list[SorterDestination])
async def destinations(q: str = Query(""), limit: int = Query(30, ge=1, le=50)):
    try:
        return await asyncio.to_thread(sorter.search_destinations, q, limit)
    except Exception as exc:
        raise as_http_error(exc) from exc


@router.post("/sorter/folders", response_model=SorterDestination)
async def create_folder(payload: SorterFolderRequest) -> dict:
    try:
        created = await asyncio.to_thread(
            sorter.create_destination, payload.parent, payload.name
        )
    except Exception as exc:
        raise as_http_error(exc) from exc
    return {"path": created["path"], "name": created["name"]}


@router.post("/sorter/move", response_model=SorterQueueResponse)
async def move(payload: SorterMoveRequest) -> dict:
    try:
        return await asyncio.to_thread(
            sorter.move_to_library, payload.path, payload.destination
        )
    except Exception as exc:
        raise as_http_error(exc) from exc


@router.post("/sorter/undo", response_model=SorterQueueResponse)
async def undo() -> dict:
    try:
        return await asyncio.to_thread(sorter.undo_last)
    except Exception as exc:
        raise as_http_error(exc) from exc


@router.post("/sorter/rename", response_model=SorterQueueResponse)
async def rename(payload: SorterRenameRequest) -> dict:
    try:
        return await asyncio.to_thread(
            sorter.rename_incoming, payload.path, payload.name
        )
    except Exception as exc:
        raise as_http_error(exc) from exc


@router.post("/sorter/delete", response_model=SorterQueueResponse)
async def delete(payload: SorterPathRequest) -> dict:
    try:
        return await asyncio.to_thread(sorter.delete_incoming, payload.path)
    except Exception as exc:
        raise as_http_error(exc) from exc


@router.api_route("/sorter/stream", methods=["GET", "HEAD"])
async def stream(request: Request, path: str):
    try:
        target = await asyncio.to_thread(sorter.resolve_source_file, path)
    except Exception as exc:
        raise as_http_error(exc) from exc
    return await stream_path(request, target)


@router.get("/sorter/transcode")
async def transcode(path: str, ss: float = Query(0.0)):
    try:
        target = await asyncio.to_thread(sorter.resolve_source_file, path)
    except Exception as exc:
        raise as_http_error(exc) from exc
    # Shared cap with /media/transcode: each viewer is a full encode.
    await _TRANSCODE_SEMAPHORE.acquire()
    try:
        process = await asyncio.create_subprocess_exec(
            *build_command(target, ss or 0.0),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except BaseException:
        _TRANSCODE_SEMAPHORE.release()
        raise
    return _ReleasingResponse(
        relay(process), semaphore=_TRANSCODE_SEMAPHORE, media_type="video/mp4"
    )


@router.get("/sorter/thumbnail")
async def thumbnail(path: str):
    """Frame 0 poster for the queue list; generated lazily into the thumb cache."""
    try:
        target = await asyncio.to_thread(sorter.resolve_source_file, path)
    except Exception as exc:
        raise as_http_error(exc) from exc

    cache = get_settings().thumbnail_dir / "sorter"
    cache.mkdir(parents=True, exist_ok=True)

    def _key_for(target: str) -> str:
        return stable_id(f"{target}:{Path(target).stat().st_mtime}")

    key = await asyncio.to_thread(_key_for, target)
    output = cache / f"s_{key}.jpg"
    if not output.is_file():
        # Atomic discipline: ffmpeg writes a temp sibling, a completed frame is
        # renamed in. Otherwise a concurrent request can serve a partial JPEG.
        temp = cache / f".mllibtmp-{key}-{os.getpid()}.jpg"
        process = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            "00:00:01",
            "-i",
            target,
            "-vf",
            "scale=320:-2:flags=fast_bilinear",
            "-frames:v",
            "1",
            "-q:v",
            "4",
            "-y",
            str(temp),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            await asyncio.wait_for(process.wait(), timeout=30)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            temp.unlink(missing_ok=True)
            raise HTTPException(
                status_code=504, detail="Preview frame extraction timed out"
            ) from None
        if temp.is_file():
            os.replace(temp, output)
        else:
            temp.unlink(missing_ok=True)
    if not output.is_file():
        raise HTTPException(status_code=404, detail="No preview frame")
    return FileResponse(
        output,
        media_type="image/jpeg",
        headers={"Cache-Control": "public, max-age=300"},
    )
