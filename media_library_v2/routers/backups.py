"""Library snapshot endpoints for v2."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from media_library_v2 import backups
from media_library_v2.routers.common import as_http_error
from media_library_v2.schemas import (
    BackupRestoreResponse,
    BackupSnapshotResponse,
    BackupStatus,
    BackupVerifyResponse,
)

router = APIRouter()


@router.get("/backups/status", response_model=BackupStatus)
async def status() -> dict:
    return await asyncio.to_thread(backups.status)


@router.post("/backups/snapshot", response_model=BackupSnapshotResponse)
async def snapshot() -> dict:
    try:
        return await asyncio.to_thread(backups.create_snapshot)
    except Exception as exc:
        raise as_http_error(exc) from exc


@router.get("/backups/verify", response_model=BackupVerifyResponse)
async def verify() -> dict:
    try:
        return await asyncio.to_thread(backups.verify_snapshot)
    except Exception as exc:
        raise as_http_error(exc) from exc


@router.post("/backups/restore", response_model=BackupRestoreResponse)
async def restore() -> dict:
    try:
        return await asyncio.to_thread(backups.restore_snapshot)
    except Exception as exc:
        raise as_http_error(exc) from exc


@router.get("/backups/download")
async def download(kind: str = Query("db")):
    db_path, tar_path, _meta = backups._paths()
    if kind == "db":
        path = db_path
        filename = "media-library-v2-snapshot.db"
        media_type = "application/vnd.sqlite3"
    elif kind in ("thumbs", "tar"):
        path = tar_path
        filename = "media-library-v2-thumbnails.tar"
        media_type = "application/x-tar"
    else:
        raise HTTPException(status_code=400, detail="Unknown snapshot file")
    if not path.is_file():
        raise HTTPException(status_code=404, detail="No snapshot found")
    return FileResponse(
        path,
        media_type=media_type,
        filename=filename,
        headers={"Cache-Control": "no-store"},
    )
