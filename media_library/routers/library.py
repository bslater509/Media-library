"""FastAPI router for Media Library browsing, search, index sync/prune, and thumbnail serving."""

import os
import mimetypes
import asyncio
import urllib.parse
from typing import Optional, List, Dict, Any
from pathlib import Path
from pydantic import BaseModel
from fastapi import APIRouter, Request, HTTPException, Query, Response
from fastapi.responses import StreamingResponse, FileResponse

from media_library.core.constants import VIDEO_EXTENSIONS, IMAGE_EXTENSIONS, NATIVE_EXTENSIONS
from media_library.core.config import DEFAULT_DESTINATION_DIR
from media_library.core.state import state, get_thumbnail_cache_path
from media_library.db.connection import get_db
from media_library.db.indexer import (
    index_folder,
    update_file_in_index,
    remove_file_from_index,
    prune_stale_index_entries,
    run_background_crawler,
    indexer_state,
)
from media_library.services.media_service import validate_backup_path, search_backup_index
from media_library.services.ffmpeg_service import get_thumbnail_path_fast, generate_thumbnail
from media_library.workers.thumbnail_worker import thumbnail_worker_instance
from media_library.workers.transcode_worker import transcode_worker_instance

router = APIRouter()

class BackupRenameRequest(BaseModel):
    path: str
    new_name: str

class BackupDeleteRequest(BaseModel):
    path: str

class ThumbnailGenerateRequest(BaseModel):
    folder_path: Optional[str] = None

@router.get("/api/backup/browse")
def browse_backup_library(path: Optional[str] = Query(None), refresh: Optional[bool] = Query(False)):
    base_dir = os.path.realpath(state.get("destination_dir") or DEFAULT_DESTINATION_DIR)
    try:
        target_path = validate_backup_path(path)
    except HTTPException:
        target_path = base_dir

    try:
        common = os.path.commonpath([base_dir, target_path])
        if common != base_dir:
            target_path = base_dir
    except (ValueError, OSError):
        target_path = base_dir

    if target_path != base_dir:
        try:
            if not os.path.isdir(target_path):
                parent = os.path.dirname(target_path)
                if parent and os.path.commonpath([base_dir, parent]) == base_dir:
                    target_path = parent
                else:
                    target_path = base_dir
        except (OSError, ValueError):
            pass

    rel_path = os.path.relpath(target_path, base_dir)
    is_root = (rel_path in (".", ""))

    breadcrumbs = [{"name": os.path.basename(base_dir) or "Library", "path": base_dir}]
    if not is_root:
        parts = Path(rel_path).parts
        accum = base_dir
        for p in parts:
            accum = os.path.join(accum, p)
            breadcrumbs.append({"name": p, "path": accum})

    root_folders = []
    conn = get_db()
    try:
        cur = conn.execute("SELECT name, path, file_count, subfolder_count FROM folders WHERE parent_path = ? ORDER BY LOWER(name)", (base_dir,))
        for r in cur.fetchall():
            root_folders.append({
                "name": r["name"],
                "path": r["path"],
                "file_count": r["file_count"],
                "subfolder_count": r["subfolder_count"]
            })
    except Exception as e:
        print(f"Error reading root folders from DB: {e}")
    finally:
        conn.close()

    if not root_folders:
        try:
            items = []
            with os.scandir(base_dir) as entries:
                for entry in entries:
                    if entry.is_dir(follow_symlinks=False) and not entry.name.startswith("."):
                        items.append({
                            "name": entry.name,
                            "path": entry.path,
                            "file_count": 0,
                            "subfolder_count": 0
                        })
            items.sort(key=lambda x: x["name"].lower())
            root_folders = items
        except Exception as e:
            print(f"Error reading root folders from {base_dir}: {e}")

    subfolders = []
    files = []

    if not is_root:
        is_indexed = False
        if not refresh:
            conn = get_db()
            try:
                cur = conn.execute("SELECT last_scanned FROM folders WHERE path = ?", (target_path,))
                row = cur.fetchone()
                if row and row["last_scanned"] > 0:
                    f_cur = conn.execute("SELECT * FROM files WHERE folder_path = ? ORDER BY LOWER(name) ASC", (target_path,))
                    for r in f_cur.fetchall():
                        ext = r["ext"]
                        is_vid = (r["type"] == "video")
                        is_native = ext in NATIVE_EXTENSIONS
                        fpath = r["path"]
                        media_url = (
                            f"/api/backup/media?path={urllib.parse.quote(fpath)}"
                            if (not is_vid or is_native)
                            else f"/api/backup/transcode_stream?path={urllib.parse.quote(fpath)}"
                        )
                        files.append({
                            "name": r["name"],
                            "path": fpath,
                            "type": r["type"],
                            "ext": ext,
                            "size": r["size"],
                            "size_formatted": r["size_formatted"],
                            "mtime": r["mtime"],
                            "needs_transcode": bool(r["needs_transcode"]),
                            "has_thumb": bool(r["has_thumb"]) if "has_thumb" in r.keys() else False,
                            "thumb_url": f"/api/backup/thumbnail?path={urllib.parse.quote(fpath)}",
                            "media_url": media_url,
                        })

                    sf_cur = conn.execute("SELECT path, name FROM folders WHERE parent_path = ? ORDER BY LOWER(name) ASC", (target_path,))
                    for sr in sf_cur.fetchall():
                        subfolders.append({
                            "name": sr["name"],
                            "path": sr["path"],
                        })
                    is_indexed = True
            except Exception as e:
                print(f"DB read error for {target_path}: {e}")
            finally:
                conn.close()

        if not is_indexed:
            index_folder(target_path, base_dir)
            conn = get_db()
            try:
                f_cur = conn.execute("SELECT * FROM files WHERE folder_path = ? ORDER BY LOWER(name) ASC", (target_path,))
                for r in f_cur.fetchall():
                    ext = r["ext"]
                    is_vid = (r["type"] == "video")
                    is_native = ext in NATIVE_EXTENSIONS
                    fpath = r["path"]
                    media_url = (
                        f"/api/backup/media?path={urllib.parse.quote(fpath)}"
                        if (not is_vid or is_native)
                        else f"/api/backup/transcode_stream?path={urllib.parse.quote(fpath)}"
                    )
                    files.append({
                        "name": r["name"],
                        "path": fpath,
                        "type": r["type"],
                        "ext": ext,
                        "size": r["size"],
                        "size_formatted": r["size_formatted"],
                        "mtime": r["mtime"],
                        "needs_transcode": bool(r["needs_transcode"]),
                        "has_thumb": bool(r["has_thumb"]) if "has_thumb" in r.keys() else False,
                        "thumb_url": f"/api/backup/thumbnail?path={urllib.parse.quote(fpath)}",
                        "media_url": media_url,
                    })

                sf_cur = conn.execute("SELECT path, name FROM folders WHERE parent_path = ? ORDER BY LOWER(name) ASC", (target_path,))
                for sr in sf_cur.fetchall():
                    subfolders.append({
                        "name": sr["name"],
                        "path": sr["path"],
                    })
            except Exception as e:
                print(f"DB read error after indexing {target_path}: {e}")
            finally:
                conn.close()

        try:
            transcode_worker_instance.enqueue_for_backup(folder_path=target_path)
            thumbnail_worker_instance.enqueue_folder(target_path)
        except Exception as e:
            print(f"Warning: could not enqueue background tasks for {target_path}: {e}")
    else:
        subfolders = [{"name": rf["name"], "path": rf["path"]} for rf in root_folders]

    return {
        "base_path": base_dir,
        "current_path": target_path,
        "relative_path": "" if is_root else rel_path,
        "is_root": is_root,
        "breadcrumbs": breadcrumbs,
        "root_folders": root_folders,
        "subfolders": subfolders,
        "files": files,
        "total_files": len(files),
        "total_subfolders": len(subfolders),
    }

@router.api_route("/api/backup/thumbnail", methods=["GET", "HEAD"])
async def get_backup_thumbnail(path: str = Query(...), request: Request = None):
    real_path = validate_backup_path(path)

    thumb_path = await asyncio.to_thread(get_thumbnail_path_fast, real_path)
    if thumb_path.exists() and thumb_path.stat().st_size > 0:
        if request and request.method == "HEAD":
            return Response(
                status_code=200,
                headers={"Content-Length": str(thumb_path.stat().st_size), "Content-Type": "image/jpeg"}
            )
        return FileResponse(
            str(thumb_path),
            media_type="image/jpeg",
            headers={"Cache-Control": "public, max-age=86400"}
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
                headers={"Content-Length": str(thumb.stat().st_size), "Content-Type": "image/jpeg"}
            )
        return FileResponse(
            str(thumb),
            media_type="image/jpeg",
            headers={"Cache-Control": "public, max-age=86400"}
        )

    if is_img:
        mime, _ = mimetypes.guess_type(real_path)
        mime_type = mime or "image/jpeg"
        f_size = await asyncio.to_thread(os.path.getsize, real_path)
        if request and request.method == "HEAD":
            return Response(
                status_code=200,
                headers={"Content-Length": str(f_size), "Content-Type": mime_type}
            )
        return FileResponse(
            real_path,
            media_type=mime_type,
            headers={"Cache-Control": "public, max-age=86400"}
        )

    raise HTTPException(status_code=500, detail="Could not generate thumbnail")

@router.api_route("/api/backup/media", methods=["GET", "HEAD"])
async def stream_backup_media(path: str = Query(...), request: Request = None):
    real_path = validate_backup_path(path)
    file_exists = await asyncio.to_thread(os.path.exists, real_path)
    if not file_exists:
        raise HTTPException(status_code=404, detail="File not found")

    ext = os.path.splitext(real_path)[1].lower()
    mime_type, _ = mimetypes.guess_type(real_path)
    file_size = await asyncio.to_thread(os.path.getsize, real_path)

    if ext in IMAGE_EXTENSIONS:
        if not mime_type:
            mime_type = "image/jpeg"
        if request and request.method == "HEAD":
            return Response(
                status_code=200,
                headers={"Content-Length": str(file_size), "Content-Type": mime_type}
            )
        return FileResponse(
            real_path,
            media_type=mime_type,
            headers={"Cache-Control": "public, max-age=3600"}
        )

    if not mime_type:
        mime_type = "video/mp4"

    if request and request.method == "HEAD":
        headers = {
            "Content-Length": str(file_size),
            "Content-Type": mime_type,
            "Accept-Ranges": "bytes",
        }
        return Response(status_code=200, headers=headers)

    range_header = request.headers.get("Range") if request else None
    if not range_header:
        async def full_file_sender():
            def _open():
                return open(real_path, "rb")
            f = await asyncio.to_thread(_open)
            try:
                while True:
                    chunk = await asyncio.to_thread(f.read, 1024 * 1024)
                    if not chunk:
                        break
                    yield chunk
            finally:
                await asyncio.to_thread(f.close)

        headers = {
            "Content-Length": str(file_size),
            "Content-Type": mime_type,
            "Accept-Ranges": "bytes",
        }
        return StreamingResponse(full_file_sender(), status_code=200, headers=headers)

    try:
        range_val = range_header.strip().replace("bytes=", "")
        parts = range_val.split("-")
        start = int(parts[0]) if parts[0] else 0
        end = int(parts[1]) if len(parts) > 1 and parts[1] else file_size - 1

        if start >= file_size or end >= file_size or start > end:
            return Response(
                status_code=416,
                headers={"Content-Range": f"bytes */{file_size}"}
            )

        chunk_max = 2 * 1024 * 1024
        if (end - start + 1) > chunk_max:
            end = start + chunk_max - 1

        content_length = end - start + 1

        async def chunk_generator(filepath, start_pos, length):
            def _open_and_seek():
                f = open(filepath, "rb")
                f.seek(start_pos)
                return f

            f = await asyncio.to_thread(_open_and_seek)
            try:
                bytes_left = length
                while bytes_left > 0:
                    read_size = min(bytes_left, 128 * 1024)
                    data = await asyncio.to_thread(f.read, read_size)
                    if not data:
                        break
                    bytes_left -= len(data)
                    yield data
            finally:
                await asyncio.to_thread(f.close)

        headers = {
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Accept-Ranges": "bytes",
            "Content-Length": str(content_length),
            "Content-Type": mime_type,
        }
        return StreamingResponse(
            chunk_generator(real_path, start, content_length),
            status_code=206,
            headers=headers
        )
    except Exception as e:
        print(f"Error streaming range for {real_path}: {e}")
        raise HTTPException(status_code=500, detail="Error streaming video range")

@router.post("/api/backup/rename")
def backup_rename(req: BackupRenameRequest):
    real_path = validate_backup_path(req.path)
    if not os.path.exists(real_path) or not os.path.isfile(real_path):
        raise HTTPException(status_code=404, detail="File not found")

    new_name = req.new_name.strip()
    if not new_name or "/" in new_name or "\\" in new_name or new_name.startswith("."):
        raise HTTPException(status_code=400, detail="Invalid filename")

    dir_path = os.path.dirname(real_path)
    target_path = os.path.join(dir_path, new_name)

    if target_path == real_path:
        return {"success": True, "path": target_path, "name": new_name}

    if os.path.exists(target_path):
        raise HTTPException(status_code=400, detail="A file with this name already exists")

    try:
        os.rename(real_path, target_path)
        update_file_in_index(real_path, target_path, new_name)
        old_thumb = get_thumbnail_cache_path(real_path)
        if old_thumb.exists():
            try:
                old_thumb.unlink()
            except OSError:
                pass
        return {
            "success": True,
            "old_path": real_path,
            "new_path": target_path,
            "new_name": new_name,
            "thumb_url": f"/api/backup/thumbnail?path={urllib.parse.quote(target_path)}",
            "media_url": f"/api/backup/media?path={urllib.parse.quote(target_path)}"
        }
    except Exception as e:
        print(f"Error renaming {real_path} to {target_path}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to rename: {str(e)}")

@router.post("/api/backup/delete")
def backup_delete(req: BackupDeleteRequest):
    real_path = validate_backup_path(req.path)
    if not os.path.exists(real_path):
        raise HTTPException(status_code=404, detail="File not found")

    if not os.path.isfile(real_path):
        raise HTTPException(status_code=400, detail="Only files can be deleted")

    try:
        thumb = get_thumbnail_cache_path(real_path)
        if thumb.exists():
            try:
                thumb.unlink()
            except OSError:
                pass

        os.remove(real_path)
        remove_file_from_index(real_path)
        return {"success": True, "deleted_path": real_path}
    except Exception as e:
        print(f"Error deleting {real_path}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to delete: {str(e)}")

@router.get("/api/backup/search")
def search_backup(
    q: str = Query(..., min_length=1),
    type: Optional[str] = Query(None),
    sort: Optional[str] = Query("name_asc"),
    limit: Optional[int] = Query(150),
):
    results = search_backup_index(query=q, type_filter=type, sort_by=sort, limit=limit)
    return {
        "query": q,
        "total": len(results),
        "results": results,
    }

@router.get("/api/backup/index/status")
async def get_backup_index_status():
    conn = get_db()
    total_files = 0
    total_folders = 0
    try:
        cur_files = conn.execute("SELECT COUNT(*) FROM files")
        total_files = cur_files.fetchone()[0]
        cur_folders = conn.execute("SELECT COUNT(*) FROM folders")
        total_folders = cur_folders.fetchone()[0]
    except Exception as e:
        print(f"Error reading index counts: {e}")
        total_files = indexer_state.get("total_files", 0)
        total_folders = indexer_state.get("total_folders", 0)
    finally:
        conn.close()

    is_crawling = (indexer_state.get("status") == "crawling")
    scanned_f = indexer_state.get("scanned_folders", 0)
    total_f = indexer_state.get("total_folders", 0) if is_crawling else (total_folders or indexer_state.get("total_folders", 0))
    if is_crawling and total_f > 0:
        pct = min(99.0, max(1.0, round(scanned_f / total_f * 100.0, 1)))
    else:
        pct = 100.0

    return {
        "status": indexer_state["status"],
        "scanned_folders": scanned_f,
        "total_folders": total_f,
        "scanned_files": indexer_state.get("scanned_files", 0),
        "total_files": total_files,
        "percent": pct,
        "current_folder": indexer_state["current_folder"],
        "last_synced": indexer_state["last_synced"],
    }

@router.post("/api/backup/index/sync")
async def trigger_backup_sync():
    if indexer_state["status"] == "crawling":
        return {"success": False, "message": "Crawler is already running", "status": indexer_state["status"]}
    asyncio.create_task(run_background_crawler())
    return {"success": True, "message": "Index crawl started", "status": "crawling"}

@router.post("/api/backup/index/prune")
async def trigger_backup_prune():
    base_dir = os.path.realpath(state.get("destination_dir") or DEFAULT_DESTINATION_DIR)
    stats = await asyncio.to_thread(prune_stale_index_entries, base_dir, None, True)
    return {"success": True, "stats": stats}

@router.get("/api/backup/thumbnails/status")
async def get_thumbnails_status():
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
async def toggle_thumbnail_pause():
    if not thumbnail_worker_instance.is_paused():
        thumbnail_worker_instance.pause()
        return {"success": True, "is_paused": True, "message": "Thumbnail generator paused"}
    else:
        thumbnail_worker_instance.resume()
        return {"success": True, "is_paused": False, "message": "Thumbnail generator resumed"}

@router.post("/api/backup/thumbnails/generate")
async def trigger_thumbnail_generate(req: Optional[ThumbnailGenerateRequest] = None):
    folder_path = req.folder_path if req else None
    if folder_path:
        real_path = validate_backup_path(folder_path)
        thumbnail_worker_instance.enqueue_folder(real_path)
        thumbnail_worker_instance.resume()
        return {"success": True, "message": f"Enqueued thumbnails for {os.path.basename(real_path)}"}
    else:
        conn = get_db()
        try:
            with conn:
                conn.execute("UPDATE files SET has_thumb = 0 WHERE has_thumb = 2")
        except Exception:
            pass
        finally:
            conn.close()
        thumbnail_worker_instance.resume()
        return {"success": True, "message": "Thumbnail generator active for all files"}
