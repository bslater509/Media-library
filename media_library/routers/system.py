"""Unified System Status API consolidating polling across background subsystems."""

from typing import Any

from fastapi import APIRouter, BackgroundTasks

from media_library.db.indexer import deep_clean_database, indexer_state
from media_library.services.backup_service import library_backup_state
from media_library.services.status import (
    duplicate_hash_payload,
    index_status_payload,
    transfers_payload,
    zip_status_payload,
)
from media_library.services.zip import (
    get_batch_unzip_status,
    get_batch_zip_status,
)
from media_library.workers import (
    animated_thumb_worker_instance,
    library_ops_worker_instance,
    metadata_worker_instance,
    thumb_hash_worker_instance,
    thumbnail_worker_instance,
    transcode_worker_instance,
    webp_worker_instance,
)

router = APIRouter(tags=["system"])


@router.get("/api/system/status")
async def get_all_system_status() -> dict[str, Any]:
    """Returns aggregated status of all background workers, indexing, and transfers in a single call."""

    # 1. Transfers
    transfers_data = transfers_payload()

    # 2. Transcode
    transcode_data = transcode_worker_instance.get_status()

    # 3. Index status (cached count)
    index_data = index_status_payload()
    index_data["is_paused"] = indexer_state.get("is_paused", False)

    # 4. Thumbnails
    thumb_data = thumbnail_worker_instance.get_status()

    # 5. Media metadata (natural-ratio tile dimensions)
    metadata_data = metadata_worker_instance.get_status()

    # 5. Duplicate Hash
    duplicate_hash_data = duplicate_hash_payload()

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
    zip_data = zip_status_payload()

    # 10. Batch ZIP status
    batch_zip_data = get_batch_zip_status()

    # 10b. Batch ZIP extract-all status
    batch_unzip_data = get_batch_unzip_status()

    # 11. Library ops (moves / deletes / renames queued from the Library page)
    library_ops_data = library_ops_worker_instance.get_status()

    # 12. Animated WebP thumbnails worker & queue
    anim_thumb_data = animated_thumb_worker_instance.get_status()

    # Calculate overall activity flag so client can adapt poll interval
    is_active = (
        bool(transfers_data.get("active"))
        or len(transfers_data.get("queued", [])) > 0
        or bool(transcode_data.get("active"))
        or index_data.get("status") == "crawling"
        or thumb_data.get("status") == "generating"
        or metadata_data.get("status") == "probing"
        or duplicate_hash_data.get("status") == "hashing"
        or thumb_hash_data.get("status") in ("hashing", "migrating")
        or webp_data.get("status") == "converting"
        or library_backup_state.get("status") in ("backing_up", "restoring")
        or zip_data.get("is_active", False)
        or batch_zip_data.get("status") in ("running", "processing")
        or batch_unzip_data.get("status") in ("running", "processing")
        or bool(library_ops_data.get("active"))
        or library_ops_data.get("queue_length", 0) > 0
        or anim_thumb_data.get("status") == "generating"
        or anim_thumb_data.get("queue_length", 0) > 0
        or bool(anim_thumb_data.get("active"))
    )

    return {
        "is_active": is_active,
        "transfers": transfers_data,
        "transcode": transcode_data,
        "index": index_data,
        "thumbnails": thumb_data,
        "animated_thumbnails": anim_thumb_data,
        "metadata": metadata_data,
        "duplicate_hash": duplicate_hash_data,
        "thumb_hash": thumb_hash_data,
        "webp": webp_data,
        "library_backup": lib_bck_data,
        "zip": zip_data,
        "batch_zip": batch_zip_data,
        "batch_unzip": batch_unzip_data,
        "library_ops": library_ops_data,
    }


@router.post("/api/system/clean-db")
async def clean_database_and_thumbnails(background_tasks: BackgroundTasks):
    background_tasks.add_task(deep_clean_database)
    return {"success": True, "message": "Deep database scan and thumbnail cleanup started in the background."}
