"""Status payload builders shared by the unified status API and individual routers.

Centralizes the shaping of background-worker, index, transfer, and ZIP status
so routers never import other routers and the payloads cannot diverge.
"""

import logging
import os
import sqlite3
import threading
import time
from typing import Any

from media_library.core.constants import INDEX_COUNT_CACHE_TTL_S
from media_library.core.state import format_size
from media_library.db.connection import db_conn
from media_library.db.indexer import indexer_state
from media_library.services.zip import zip_lock, zip_tasks_state
from media_library.workers import (
    duplicate_hash_worker_instance,
    transcode_worker_instance,
    transfer_worker_instance,
)

logger = logging.getLogger(__name__)

_index_counts_cache = {"total_files": 0, "total_folders": 0, "expires_at": 0.0}
_index_counts_lock = threading.Lock()


def get_cached_index_counts() -> tuple[int, int]:
    """Return indexed file/folder counts, cached for 30 seconds (FUSE-safe)."""
    now = time.time()
    with _index_counts_lock:
        if (
            now < _index_counts_cache["expires_at"]
            and _index_counts_cache["total_files"] > 0
        ):
            return _index_counts_cache["total_files"], _index_counts_cache[
                "total_folders"
            ]
        try:
            with db_conn() as conn:
                total_files = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
                total_folders = conn.execute("SELECT COUNT(*) FROM folders").fetchone()[
                    0
                ]
                _index_counts_cache["total_files"] = total_files
                _index_counts_cache["total_folders"] = total_folders
                _index_counts_cache["expires_at"] = now + INDEX_COUNT_CACHE_TTL_S
                return total_files, total_folders
        except sqlite3.DatabaseError as e:
            logger.error(f"Error reading index counts: {e}")
            # Fall back to the crawler's counters; a count failure must not
            # break the index status panel.
            return indexer_state.get("total_files", 0), indexer_state.get(
                "total_folders", 0
            )


def index_status_payload() -> dict[str, Any]:
    """Common index-status body: percent math plus crawler counters."""
    total_files, total_folders = get_cached_index_counts()

    is_crawling = indexer_state.get("status") == "crawling"
    scanned_f = indexer_state.get("scanned_folders", 0)
    total_f = (
        indexer_state.get("total_folders", 0)
        if is_crawling
        else (total_folders or indexer_state.get("total_folders", 0))
    )
    if is_crawling and total_f > 0:
        pct = min(99.0, max(1.0, round(scanned_f / total_f * 100.0, 1)))
    else:
        pct = 100.0

    return {
        "status": indexer_state["status"],
        "scanned_folders": scanned_f,
        "skipped_folders": indexer_state.get("skipped_folders", 0),
        "total_folders": total_f,
        "scanned_files": indexer_state.get("scanned_files", 0),
        "total_files": total_files,
        "percent": pct,
        "current_folder": indexer_state.get("current_folder", ""),
    }


def duplicate_hash_payload() -> dict[str, Any]:
    """Duplicate (quick) hash worker status body."""
    st = duplicate_hash_worker_instance.get_status()
    return {
        "status": st["status"],
        "total_candidates": st["total_candidates"],
        "hashed_count": st["hashed_count"],
        "pending_count": st["pending_count"],
        "percent": st["percent"],
        "current_file": st["current_file"],
        "processed_count": st["processed_count"],
        "is_paused": duplicate_hash_worker_instance.is_paused(),
        "last_synced": st["last_synced"],
    }


def transfers_payload() -> dict[str, Any]:
    """Transfer + (sorter) transcode status body for /api/transfers."""
    t_status = transfer_worker_instance.get_status()
    tc_status = transcode_worker_instance.get_status()

    active_data = None
    if t_status["active"]:
        act = t_status["active"]
        active_data = {
            "id": act["id"],
            "filename": act["filename"],
            "destination_folder": act["destination_folder"],
            "bytes_copied": act.get("bytes_copied", 0),
            "total_bytes": act.get("total_bytes", 0),
            "percent": act.get("percent", 0.0),
            "status": "transferring",
        }

    queued_list = []
    for t in transfer_worker_instance.transfer_items_map.values():
        if t.get("status") == "queued":
            queued_list.append(
                {
                    "id": t["id"],
                    "filename": t["filename"],
                    "destination_folder": t["destination_folder"],
                    "size_formatted": t.get("size_formatted", ""),
                    "status": "queued",
                    "created_at": t.get("created_at"),
                }
            )
    queued_list.sort(key=lambda x: x.get("created_at", 0))

    recent_history = []
    for t in reversed(transfer_worker_instance.transfers_history[-20:]):
        if t.get("status") in ("completed", "failed", "cancelled"):
            recent_history.append(
                {
                    "id": t["id"],
                    "filename": t["filename"],
                    "destination_folder": t["destination_folder"],
                    "status": t["status"],
                    "error": t.get("error"),
                    "completed_at": t.get("completed_at"),
                }
            )

    total_pending = (1 if active_data else 0) + len(queued_list)

    transcode_active_data = None
    if tc_status["active"]:
        act_tc = tc_status["active"]
        transcode_active_data = {
            "id": act_tc["id"],
            "filename": act_tc["filename"],
            "source_path": act_tc.get("source_path"),
            "target_path": act_tc.get("target_path"),
            "percent": act_tc.get("percent", 0.0),
            "fps": act_tc.get("fps", 0.0),
            "speed": act_tc.get("speed", ""),
            "eta": act_tc.get("eta", ""),
            "method": act_tc.get("method", "reencode"),
            "status": "transcoding",
        }

    transcode_queued_list = []
    for t in transcode_worker_instance.transcode_items_map.values():
        if t.get("status") == "queued":
            transcode_queued_list.append(
                {
                    "id": t["id"],
                    "filename": t["filename"],
                    "source_path": t.get("source_path"),
                    "status": "queued",
                    "created_at": t.get("created_at"),
                }
            )
    transcode_queued_list.sort(key=lambda x: x.get("created_at", 0))

    transcode_history_list = []
    for t in reversed(transcode_worker_instance.transcode_history[-15:]):
        if t.get("status") in ("completed", "failed", "cancelled"):
            target_size = t.get("target_size")
            transcode_history_list.append(
                {
                    "id": t["id"],
                    "filename": t["filename"],
                    "source_path": t.get("source_path"),
                    "target_path": t.get("target_path"),
                    "target_filename": os.path.basename(t["target_path"])
                    if t.get("target_path")
                    else None,
                    "target_size": target_size,
                    "target_size_formatted": format_size(target_size)
                    if target_size
                    else None,
                    "status": t["status"],
                    "error": t.get("error"),
                    "method": t.get("method"),
                    "completed_at": t.get("completed_at"),
                }
            )

    return {
        "active": active_data,
        "queued": queued_list,
        "history": recent_history,
        "active_count": 1 if active_data else 0,
        "queued_count": len(queued_list),
        "total_pending": total_pending,
        "transcode": {
            "active": transcode_active_data,
            "queued": transcode_queued_list,
            "queued_count": len(transcode_queued_list),
            "history": transcode_history_list,
            "is_paused": transcode_worker_instance.transcode_paused,
            "total_pending": (1 if transcode_active_data else 0)
            + len(transcode_queued_list),
        },
    }


def zip_status_payload() -> dict[str, Any]:
    """Active and recent ZIP operations for the Background Tasks panel."""
    with zip_lock:
        active = dict(zip_tasks_state["active_tasks"])

    tasks_list = []
    for path_key, info in active.items():
        tasks_list.append(
            {
                "path": path_key,
                "filename": os.path.basename(path_key),
                "type": info.get("type", "zip"),
                "status": info.get("status", "running"),
                "percent": info.get("percent", 0.0),
                "current": info.get("current", 0),
                "total": info.get("total", 0),
                "message": info.get("message", ""),
                "started_at": info.get("started_at", 0),
            }
        )

    tasks_list.sort(key=lambda x: x["started_at"], reverse=True)
    return {
        "tasks": tasks_list,
        "is_active": any(t["status"] == "running" for t in tasks_list),
    }
