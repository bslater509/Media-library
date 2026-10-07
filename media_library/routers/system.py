"""Unified System Status API consolidating polling across background subsystems."""

from fastapi import APIRouter
from typing import Dict, Any

from media_library.workers import (
    transfer_worker_instance,
    transcode_worker_instance,
    thumbnail_worker_instance,
    duplicate_hash_worker_instance,
    thumb_hash_worker_instance,
    webp_worker_instance,
)
from media_library.db.indexer import indexer_state
from media_library.services.backup_service import library_backup_state
from media_library.services.zip_service import get_batch_zip_status
from media_library.routers.zip_router import get_zip_status
from media_library.routers.library import get_cached_index_counts
from media_library.routers.sorter import get_transfers_status

router = APIRouter(tags=["system"])

@router.get("/api/system/status")
async def get_all_system_status() -> Dict[str, Any]:
    """Returns aggregated status of all background workers, indexing, and transfers in a single call."""

    # 1. Transfers
    transfers_data = await get_transfers_status()

    # 2. Transcode
    transcode_data = transcode_worker_instance.get_status()

    # 3. Index status (cached count)
    total_files, total_folders = get_cached_index_counts()
    is_crawling = (indexer_state.get("status") == "crawling")
    scanned_f = indexer_state.get("scanned_folders", 0)
    total_f = indexer_state.get("total_folders", 0) if is_crawling else (total_folders or indexer_state.get("total_folders", 0))
    if is_crawling and total_f > 0:
        pct = min(99.0, max(1.0, round(scanned_f / total_f * 100.0, 1)))
    else:
        pct = 100.0

    index_data = {
        "status": indexer_state["status"],
        "scanned_folders": scanned_f,
        "total_folders": total_f,
        "scanned_files": indexer_state.get("scanned_files", 0),
        "total_files": total_files,
        "percent": pct,
        "is_paused": indexer_state.get("is_paused", False),
    }

    # 4. Thumbnails
    thumb_data = thumbnail_worker_instance.get_status()

    # 5. Duplicate Hash
    dup_st = duplicate_hash_worker_instance.get_status()
    duplicate_hash_data = {
        "status": dup_st["status"],
        "total_candidates": dup_st["total_candidates"],
        "hashed_count": dup_st["hashed_count"],
        "pending_count": dup_st["pending_count"],
        "percent": dup_st["percent"],
        "current_file": dup_st["current_file"],
        "processed_count": dup_st["processed_count"],
        "is_paused": duplicate_hash_worker_instance.is_paused(),
        "last_synced": dup_st["last_synced"],
    }

    # 6. Thumb Hash
    thumb_hash_data = thumb_hash_worker_instance.get_status()

    # 7. WebP
    webp_data = webp_worker_instance.get_status()

    # 8. Library Backup
    lib_bck_data = {
        "status": library_backup_state["status"],
        "progress_percent": library_backup_state["progress_percent"],
        "current_step": library_backup_state["current_step"],
        "last_backup_time": library_backup_state["last_backup_time"],
        "last_backup_str": library_backup_state["last_backup_str"],
        "backup_exists": library_backup_state["backup_exists"],
        "file_count": library_backup_state["file_count"],
        "folder_count": library_backup_state["folder_count"],
        "thumb_count": library_backup_state["thumb_count"],
        "total_size_bytes": library_backup_state["total_size_bytes"],
        "total_size_formatted": library_backup_state["total_size_formatted"],
        "backup_path": library_backup_state["backup_path"],
        "error_message": library_backup_state["error_message"],
    }

    # 9. ZIP status
    zip_data = await get_zip_status()

    # 10. Batch ZIP status
    batch_zip_data = get_batch_zip_status()

    # Calculate overall activity flag so client can adapt poll interval
    is_active = (
        bool(transfers_data.get("active")) or
        len(transfers_data.get("queued", [])) > 0 or
        bool(transcode_data.get("active")) or
        is_crawling or
        thumb_data.get("status") == "generating" or
        dup_st.get("status") == "hashing" or
        webp_data.get("status") == "optimizing" or
        library_backup_state.get("status") in ("backing_up", "restoring") or
        zip_data.get("is_active", False) or
        batch_zip_data.get("status") in ("running", "processing")
    )

    return {
        "is_active": is_active,
        "transfers": transfers_data,
        "transcode": transcode_data,
        "index": index_data,
        "thumbnails": thumb_data,
        "duplicate_hash": duplicate_hash_data,
        "thumb_hash": thumb_hash_data,
        "webp": webp_data,
        "library_backup": lib_bck_data,
        "zip": zip_data,
        "batch_zip": batch_zip_data,
    }
