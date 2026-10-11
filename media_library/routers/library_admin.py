"""FastAPI router for Media Library file/folder admin: rename, delete, thumbnail controls."""

import logging
import os
import shutil
import sqlite3
import urllib.parse
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from media_library.core.state import get_thumbnail_cache_path
from media_library.db.connection import db_conn
from media_library.db.indexer import (
    remove_file_from_index,
    update_file_in_index,
)
from media_library.services.media_service import validate_backup_path
from media_library.services.zip import (
    get_zip_cache_dir,
    is_zip_file,
    resolve_zip_file_path,
    split_zip_path,
    trigger_debounced_sync_back,
)
from media_library.workers.thumbnail_worker import thumbnail_worker_instance

router = APIRouter()

logger = logging.getLogger(__name__)


class BackupRenameRequest(BaseModel):
    path: str
    new_name: str


class BackupDeleteRequest(BaseModel):
    path: str


class ThumbnailGenerateRequest(BaseModel):
    folder_path: str | None = None


@router.post("/api/backup/rename")
def backup_rename(req: BackupRenameRequest):
    real_path = validate_backup_path(req.path)

    new_name = req.new_name.strip()
    if not new_name or "/" in new_name or "\\" in new_name or new_name.startswith("."):
        raise HTTPException(status_code=400, detail="Invalid filename")

    # Check if target is inside a zip file
    zip_p, inner_p = split_zip_path(real_path)
    if zip_p and inner_p:
        resolved = resolve_zip_file_path(real_path)
        if not resolved or not os.path.exists(resolved):
            raise HTTPException(status_code=404, detail="File not found in archive")
        old_local = Path(resolved)
        new_local = old_local.parent / new_name
        if new_local.exists() and new_local != old_local:
            raise HTTPException(
                status_code=400,
                detail="A file with this name already exists in archive",
            )
        os.rename(old_local, new_local)
        inner_dir = os.path.dirname(inner_p)
        new_inner = os.path.join(inner_dir, new_name) if inner_dir else new_name
        new_vpath = os.path.join(zip_p, new_inner)
        update_file_in_index(real_path, new_vpath, new_name)
        trigger_debounced_sync_back(zip_p)
        return {
            "success": True,
            "old_path": real_path,
            "new_path": new_vpath,
            "new_name": new_name,
            "thumb_url": f"/api/backup/thumbnail?path={urllib.parse.quote(new_vpath)}",
            "media_url": f"/api/backup/media?path={urllib.parse.quote(new_vpath)}",
        }

    if not os.path.exists(real_path) or not os.path.isfile(real_path):
        raise HTTPException(status_code=404, detail="File not found")

    dir_path = os.path.dirname(real_path)
    target_path = os.path.join(dir_path, new_name)

    if target_path == real_path:
        return {"success": True, "path": target_path, "name": new_name}

    if os.path.exists(target_path):
        raise HTTPException(
            status_code=400, detail="A file with this name already exists"
        )

    try:
        os.rename(real_path, target_path)
        update_file_in_index(real_path, target_path, new_name)
        old_thumb = get_thumbnail_cache_path(real_path)
        if old_thumb.exists():
            try:
                old_thumb.unlink()
            except OSError:
                # Stale cached poster removal is best-effort; orphans are
                # pruned by the index/prune sweep regardless.
                pass
        return {
            "success": True,
            "old_path": real_path,
            "new_path": target_path,
            "new_name": new_name,
            "thumb_url": f"/api/backup/thumbnail?path={urllib.parse.quote(target_path)}",
            "media_url": f"/api/backup/media?path={urllib.parse.quote(target_path)}",
        }
    except Exception:
        logger.exception("Error renaming %s to %s", real_path, target_path)
        raise HTTPException(status_code=500, detail="Failed to rename")


@router.post("/api/backup/delete")
def backup_delete(req: BackupDeleteRequest):
    real_path = validate_backup_path(req.path)

    # Check if target is inside a zip file
    zip_p, inner_p = split_zip_path(real_path)
    if zip_p and inner_p:
        local_file = resolve_zip_file_path(real_path)
        if local_file and os.path.exists(local_file):
            try:
                os.remove(local_file)
            except OSError:
                logger.exception("Failed to delete %s from archive %s", inner_p, zip_p)
                raise HTTPException(
                    status_code=500, detail="Failed to delete file from archive"
                )
        remove_file_from_index(real_path)
        trigger_debounced_sync_back(zip_p)
        return {"success": True, "deleted_path": real_path}

    if not os.path.exists(real_path):
        raise HTTPException(status_code=404, detail="File not found")

    # If deleting the entire zip archive
    if is_zip_file(real_path):
        cache_dir = get_zip_cache_dir(real_path)
        if cache_dir.exists():
            shutil.rmtree(cache_dir, ignore_errors=True)
        os.remove(real_path)
        with db_conn() as conn, conn:
            conn.execute("DELETE FROM folders WHERE path = ?", (real_path,))
            conn.execute("DELETE FROM files WHERE folder_path = ?", (real_path,))
        return {"success": True, "deleted_path": real_path}

    if not os.path.isfile(real_path):
        raise HTTPException(status_code=400, detail="Only files can be deleted")

    try:
        thumb = get_thumbnail_cache_path(real_path)
        if thumb.exists():
            try:
                thumb.unlink()
            except OSError:
                # Stale cached poster removal is best-effort; orphans are
                # pruned by the index/prune sweep regardless.
                pass

        os.remove(real_path)
        remove_file_from_index(real_path)
        return {"success": True, "deleted_path": real_path}
    except Exception:
        logger.exception("Error deleting %s", real_path)
        raise HTTPException(status_code=500, detail="Failed to delete")


@router.get("/api/backup/thumbnails/status")
async def get_thumbnails_status() -> dict:
    st = thumbnail_worker_instance.get_status()
    return {
        "status": st["status"],
        "total_files": st["total_files"],
        "cached_count": st["cached_count"],
        "pending_count": st["pending_count"],
        "percent": st["percent"],
        "current_file": st["current_file"],
        "current_folder": st["current_folder"],
        "processed_count": st["processed_count"],
        "is_paused": thumbnail_worker_instance.is_paused(),
    }


@router.post("/api/backup/thumbnails/pause")
async def toggle_thumbnail_pause() -> dict:
    if not thumbnail_worker_instance.is_paused():
        thumbnail_worker_instance.pause()
        return {
            "success": True,
            "is_paused": True,
            "message": "Thumbnail generator paused",
        }
    else:
        thumbnail_worker_instance.resume()
        return {
            "success": True,
            "is_paused": False,
            "message": "Thumbnail generator resumed",
        }


@router.post("/api/backup/thumbnails/generate")
async def trigger_thumbnail_generate(req: ThumbnailGenerateRequest) -> dict:
    folder_path = req.folder_path
    if folder_path:
        real_path = validate_backup_path(folder_path)
        thumbnail_worker_instance.enqueue_folder(real_path)
        thumbnail_worker_instance.resume()
        return {
            "success": True,
            "message": f"Enqueued thumbnails for {os.path.basename(real_path)}",
        }
    else:
        with db_conn() as conn:
            try:
                with conn:
                    conn.execute("UPDATE files SET has_thumb = 0 WHERE has_thumb = 2")
            except sqlite3.DatabaseError:
                logger.warning("Could not reset stale has_thumb markers", exc_info=True)
        thumbnail_worker_instance.resume()
        return {"success": True, "message": "Thumbnail generator active for all files"}
