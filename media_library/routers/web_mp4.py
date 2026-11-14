"""Explicit preview/confirmation API for library-wide web MP4 replacement."""

import asyncio
from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from media_library.routers.common import as_http_error
from media_library.schemas import WebMP4Status as Status
from media_library.web_mp4 import WebMP4Manager
from media_library.web_mp4_log import log_path

router = APIRouter()
manager = WebMP4Manager()


class PreviewItem(BaseModel):
    path: str
    ext: str | None = None
    video_codec: str | None = None
    audio_codec: str | None = None


class Preview(BaseModel):
    token: str
    total: int
    archives: int
    sample: list[PreviewItem]


class Start(BaseModel):
    token: str
    confirm_replace: Literal[True]


@router.get("/transcode/web-mp4/preview", response_model=Preview)
async def preview() -> dict:
    try:
        return await asyncio.to_thread(manager.preview)
    except Exception as exc:
        raise as_http_error(exc) from exc


@router.post("/transcode/web-mp4/start", response_model=Status)
async def start(payload: Start) -> dict:
    try:
        return await manager.start(payload.token)
    except Exception as exc:
        raise as_http_error(exc) from exc


@router.get("/transcode/web-mp4/status", response_model=Status)
async def status() -> dict:
    return manager.snapshot()


@router.post("/transcode/web-mp4/cancel", response_model=Status)
async def cancel() -> dict:
    try:
        return await manager.request_cancel()
    except Exception as exc:
        raise as_http_error(exc) from exc


@router.post("/transcode/web-mp4/resume", response_model=Status)
async def resume() -> dict:
    try:
        return await manager.resume()
    except Exception as exc:
        raise as_http_error(exc) from exc


@router.get("/transcode/web-mp4/failures/log", response_model=None)
async def failure_log() -> FileResponse:
    path = log_path()
    if not await asyncio.to_thread(path.is_file):
        raise HTTPException(status_code=404, detail="No MP4 failure log yet")
    return FileResponse(
        path,
        media_type="application/x-ndjson",
        filename="web-mp4-failures.jsonl",
        headers={"Cache-Control": "no-store"},
    )
