"""Explicit preview/confirmation API for library-wide web MP4 replacement."""

import asyncio
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from media_library.routers.common import as_http_error
from media_library.web_mp4 import WebMP4Manager

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


class Failure(BaseModel):
    path: str
    error: str


class Status(BaseModel):
    status: str
    total: int
    processed: int
    converted: int
    skipped: int
    failed: int
    current: str | None
    percent: float
    phase: str
    errors: list[Failure]
    cancel_requested: bool


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
    return manager.cancel_batch()
