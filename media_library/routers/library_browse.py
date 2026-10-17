"""FastAPI router for Media Library browsing, folder tree, search, and index controls."""

import asyncio
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from media_library.core.config import DEFAULT_DESTINATION_DIR, SEARCH_LIMIT
from media_library.core.constants import (
    INDEX_INFLIGHT_CAP,
    INDEX_INFLIGHT_PRUNE_S,
    INDEX_SCHEDULE_DEBOUNCE_S,
)
from media_library.core.state import state
from media_library.db.connection import db_conn
from media_library.db.indexer import (
    index_folder,
    indexer_state,
    prune_stale_index_entries,
    run_background_crawler,
)
from media_library.services import face_service
from media_library.services.library_query import (
    _attach_folder_covers,
    _read_indexed_files,
    _read_indexed_subfolders,
)
from media_library.services.media_service import (
    search_backup_index,
    split_person_tokens,
    validate_backup_path,
)
from media_library.services.status import (
    index_status_payload,
    invalidate_index_counts_cache,
)
from media_library.services.zip import (
    extract_zip_to_cache,
    index_zip_archive,
    is_zip_extracted,
    is_zip_file,
    zip_lock,
    zip_tasks_state,
)
from media_library.workers.thumbnail_worker import thumbnail_worker_instance
from media_library.workers.transcode_worker import transcode_worker_instance
from media_library.workers.webp_worker import webp_worker_instance

router = APIRouter()

logger = logging.getLogger(__name__)

# Tracks folders for which a background indexing job was recently scheduled, so
# rapid/repeated browses (and client re-polls) don't spawn duplicate scans.
_browse_index_inflight: dict[str, float] = {}
_browse_index_lock = threading.Lock()


def _schedule_background_index(
    target_path: str, base_dir: str, is_target_zip: bool
) -> bool:
    """Indexes a folder archive in a background thread, never blocking the request.

    Browsing must not perform an unbounded ``os.scandir``/``stat`` walk over a
    remote FUSE mount inside a request handler (AGENTS.md Rule 2). When a folder
    is not yet in the index we return the current cached view immediately and
    re-index in the background; the client re-polls while ``indexing`` is true.
    Returns True if a new job was started.
    """
    now = time.time()
    with _browse_index_lock:
        last = _browse_index_inflight.get(target_path, 0.0)
        if now - last < INDEX_SCHEDULE_DEBOUNCE_S:
            return False
        _browse_index_inflight[target_path] = now
        if len(_browse_index_inflight) > INDEX_INFLIGHT_CAP:
            for k in [
                k
                for k, v in _browse_index_inflight.items()
                if now - v > INDEX_INFLIGHT_PRUNE_S
            ]:
                _browse_index_inflight.pop(k, None)

    def _run():
        try:
            if is_target_zip:
                index_zip_archive(target_path, parent_path=os.path.dirname(target_path))
            else:
                index_folder(target_path, base_dir)
            # Newly discovered files may need posters / transcodes.
            try:
                thumbnail_worker_instance.enqueue_folder(target_path)
            except Exception as e:
                logger.warning(
                    f"Warning: could not enqueue thumbnail tasks for {target_path}: {e}"
                )
            # ZIP archives are optimized on demand: enqueue the archive that was
            # just browsed so its images get converted to WebP.
            if is_target_zip:
                try:
                    webp_worker_instance.enqueue_folder(target_path)
                except Exception as e:
                    logger.warning(
                        f"Warning: could not enqueue webp tasks for {target_path}: {e}"
                    )
            try:
                transcode_worker_instance.enqueue_indexed_folder(target_path)
            except Exception as e:
                logger.warning(
                    f"Warning: could not enqueue transcode tasks for {target_path}: {e}"
                )
        except Exception as e:
            logger.error(f"Background index failed for {target_path}: {e}")

    threading.Thread(target=_run, daemon=True).start()
    return True


@router.get("/api/backup/browse")
def browse_backup_library(
    path: str | None = Query(None), refresh: bool | None = Query(False)
):
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

    is_target_zip = is_zip_file(target_path)
    if target_path != base_dir and not is_target_zip:
        try:
            if not os.path.isdir(target_path):
                parent = os.path.dirname(target_path)
                if parent and os.path.commonpath([base_dir, parent]) == base_dir:
                    target_path = parent
                else:
                    target_path = base_dir
        except (OSError, ValueError):
            # Unreadable/odd paths are clamped to base_dir so browse never
            # escapes the library root or fails on a transient stat error.
            pass
    is_target_zip = is_zip_file(target_path)

    rel_path = os.path.relpath(target_path, base_dir)
    is_root = rel_path in (".", "")

    breadcrumbs = [
        {
            "name": os.path.basename(base_dir) or "Library",
            "path": base_dir,
            "is_zip": False,
        }
    ]
    if not is_root:
        parts = Path(rel_path).parts
        accum = base_dir
        for p in parts:
            accum = os.path.join(accum, p)
            is_zip_part = is_zip_file(accum)
            display_name = Path(p).stem if is_zip_part else p
            breadcrumbs.append(
                {
                    "name": display_name,
                    "raw_name": p,
                    "path": accum,
                    "is_zip": is_zip_part,
                }
            )

    root_folders = []
    with db_conn() as conn:
        try:
            cur = conn.execute(
                "SELECT name, path, file_count, subfolder_count, is_zip, total_size FROM folders WHERE parent_path = ? ORDER BY LOWER(name)",
                (base_dir,),
            )
            for r in cur.fetchall():
                is_z = (
                    bool(r["is_zip"])
                    if "is_zip" in r.keys()
                    else r["path"].lower().endswith(".zip")
                )
                clean_n = Path(r["name"]).stem if is_z else r["name"]
                root_folders.append(
                    {
                        "name": clean_n,
                        "raw_name": r["name"],
                        "path": r["path"],
                        "file_count": r["file_count"],
                        "subfolder_count": r["subfolder_count"],
                        "is_zip": is_z,
                        "total_size": r["total_size"]
                        if "total_size" in r.keys()
                        else 0,
                    }
                )
        except Exception as e:
            logger.error(f"Error reading root folders from DB: {e}")

    if not root_folders:
        try:
            items = []
            with os.scandir(base_dir) as entries:
                for entry in entries:
                    if entry.name.startswith("."):
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        items.append(
                            {
                                "name": entry.name,
                                "raw_name": entry.name,
                                "path": entry.path,
                                "file_count": 0,
                                "subfolder_count": 0,
                                "is_zip": False,
                                "total_size": 0,
                            }
                        )
                    elif entry.is_file(
                        follow_symlinks=False
                    ) and entry.name.lower().endswith(".zip"):
                        items.append(
                            {
                                "name": Path(entry.name).stem,
                                "raw_name": entry.name,
                                "path": entry.path,
                                "file_count": 0,
                                "subfolder_count": 0,
                                "is_zip": True,
                                "total_size": 0,
                            }
                        )
            items.sort(key=lambda x: x["name"].lower())
            root_folders = items
        except Exception as e:
            logger.error(f"Error reading root folders from {base_dir}: {e}")

    subfolders = []
    files = []
    zip_extracting = False
    zip_extract_percent = 100.0
    indexing = False

    if not is_root:
        if is_target_zip:
            with zip_lock:
                task = zip_tasks_state["active_tasks"].get(target_path)
                is_running = bool(task and task.get("status") == "running")
            if not is_zip_extracted(target_path):
                zip_extracting = True
                zip_extract_percent = task.get("percent", 0.0) if task else 0.0
                if not is_running:

                    def _safe_extract(zp):
                        try:
                            extract_zip_to_cache(zp)
                        except Exception as ze:
                            logger.error(f"Background extraction failed for {zp}: {ze}")

                    threading.Thread(
                        target=_safe_extract, args=(target_path,), daemon=True
                    ).start()
            else:
                if is_running:
                    zip_extracting = True
                    zip_extract_percent = task.get("percent", 0.0)

        is_indexed = False
        if not refresh:
            with db_conn() as conn:
                try:
                    cur = conn.execute(
                        "SELECT last_scanned FROM folders WHERE path = ?",
                        (target_path,),
                    )
                    row = cur.fetchone()
                    if row and row["last_scanned"] > 0:
                        files = _read_indexed_files(conn, target_path)
                        subfolders = _read_indexed_subfolders(conn, target_path)
                        is_indexed = True
                except Exception as e:
                    logger.error(f"DB read error for {target_path}: {e}")

        if not is_indexed:
            # Never scan the (possibly remote) filesystem synchronously inside a
            # request: schedule indexing in the background and return the current
            # cached view. The client re-polls while ``indexing`` is true.
            indexing = _schedule_background_index(target_path, base_dir, is_target_zip)
            with db_conn() as conn:
                try:
                    files = _read_indexed_files(conn, target_path)
                    subfolders = _read_indexed_subfolders(conn, target_path)
                except Exception as e:
                    logger.error(
                        f"DB read error after scheduling index for {target_path}: {e}"
                    )

        if is_indexed:
            try:
                transcode_worker_instance.enqueue_indexed_folder(target_path)
            except Exception as e:
                logger.warning(
                    f"Warning: could not enqueue transcode tasks for {target_path}: {e}"
                )
            try:
                thumbnail_worker_instance.enqueue_folder(target_path)
            except Exception as e:
                logger.warning(
                    f"Warning: could not enqueue thumbnail tasks for {target_path}: {e}"
                )
            # ZIP archives are optimized on demand: while the user is browsing an
            # archive, enqueue it so its remaining non-WebP images are converted.
            if is_target_zip:
                try:
                    webp_worker_instance.enqueue_folder(target_path)
                except Exception as e:
                    logger.warning(
                        f"Warning: could not enqueue webp tasks for {target_path}: {e}"
                    )
    else:
        subfolders = [
            {
                "name": rf["name"],
                "raw_name": rf.get("raw_name", rf["name"]),
                "path": rf["path"],
                "is_zip": rf.get("is_zip", False),
                "file_count": rf.get("file_count", 0),
                "subfolder_count": rf.get("subfolder_count", 0),
                "total_size": rf.get("total_size", 0),
            }
            for rf in root_folders
        ]

        if refresh:
            indexing = _schedule_background_index(target_path, base_dir, False)

        with db_conn() as conn:
            try:
                files = _read_indexed_files(conn, target_path)
            except Exception as e:
                logger.error(f"Error reading root files: {e}")

        try:
            transcode_worker_instance.enqueue_indexed_folder(target_path)
        except Exception as e:
            logger.warning(f"Could not enqueue transcode for root: {e}")

        try:
            thumbnail_worker_instance.enqueue_folder(target_path)
        except Exception as e:
            logger.warning(f"Could not enqueue thumbnail for root: {e}")

    # Attach representative cover thumbnails for the card grid (index-only).
    with db_conn() as conn:
        try:
            _attach_folder_covers(conn, subfolders)
        except Exception as e:
            logger.error(f"Error attaching folder covers: {e}")

    return {
        "base_path": base_dir,
        "current_path": target_path,
        "relative_path": "" if is_root else rel_path,
        "is_root": is_root,
        "is_zip": is_target_zip,
        "zip_extracting": zip_extracting,
        "zip_extract_percent": zip_extract_percent,
        "indexing": indexing,
        "breadcrumbs": breadcrumbs,
        "root_folders": root_folders,
        "subfolders": subfolders,
        "files": files,
        "total_files": len(files),
        "total_subfolders": len(subfolders),
    }


@router.get("/api/backup/folders")
def browse_folder_children(path: str | None = Query(None)):
    """List a folder's immediate child folders (index-only, FUSE-safe).

    Powers the sidebar folder tree's lazy expansion. Never scans the (possibly
    remote) filesystem synchronously inside a request: it reads the SQLite index
    and, when a folder is not yet indexed, schedules background indexing and
    returns the current cached view with ``indexing`` true (mirroring
    ``/api/backup/browse``). Unlike ``/api/backup/browse`` it does not read file
    entries nor trigger ZIP extraction.
    """
    base_dir = os.path.realpath(state.get("destination_dir") or DEFAULT_DESTINATION_DIR)
    try:
        target_path = validate_backup_path(path) if path else base_dir
    except HTTPException:
        target_path = base_dir

    try:
        common = os.path.commonpath([base_dir, target_path])
        if common != base_dir:
            target_path = base_dir
    except (ValueError, OSError):
        target_path = base_dir

    children = []
    indexing = False
    with db_conn() as conn:
        try:
            children = _read_indexed_subfolders(conn, target_path)
            row = conn.execute(
                "SELECT last_scanned FROM folders WHERE path = ?", (target_path,)
            ).fetchone()
            is_indexed = bool(row and row["last_scanned"] > 0)
        except Exception as e:
            logger.error(
                f"Error reading folder children from DB for {target_path}: {e}"
            )
            is_indexed = False

    if not is_indexed:
        is_target_zip = is_zip_file(target_path)
        if _schedule_background_index(target_path, base_dir, is_target_zip):
            indexing = True
        with db_conn() as conn:
            try:
                children = _read_indexed_subfolders(conn, target_path)
            except Exception as e:
                logger.error(
                    f"DB read error after scheduling index for {target_path}: {e}"
                )

    return {
        "current_path": target_path,
        "is_root": target_path == base_dir,
        "subfolders": children,
        "indexing": indexing,
    }


@router.get("/api/backup/search")
def search_backup(
    q: str = Query(..., min_length=1),
    type: str | None = Query(None),
    sort: str | None = Query("name_asc"),
    limit: int | None = Query(None),
):
    effective_limit = limit if (limit is not None and limit > 0) else SEARCH_LIMIT
    # `person:anna` filters the results to the files that person appears in; the
    # remaining words keep matching filenames and relative folder paths.
    text_query, person_terms = split_person_tokens(q)
    person_info: dict[str, Any] | None = None
    person_ids: list[int] | None = None
    if person_terms:
        person_ids = []
        resolved: list[dict[str, Any]] = []
        try:
            with db_conn() as conn:
                for term in person_terms:
                    ids = face_service.resolve_person_ids(conn, term)
                    if not ids:
                        continue
                    person_ids.extend(ids)
                    rows = conn.execute(
                        "SELECT id, name FROM people WHERE id IN ("
                        + ",".join("?" for _ in ids)
                        + ") ORDER BY LOWER(name) ASC",
                        ids,
                    ).fetchall()
                    resolved.extend(
                        {"id": int(r["id"]), "name": r["name"]} for r in rows
                    )
        except Exception as e:
            logger.error(f"Error resolving person search token: {e}")
        person_ids = sorted(set(person_ids))
        person_info = {
            "terms": person_terms,
            "matched": bool(person_ids),
            "people": resolved,
        }
        if not person_ids:
            return {
                "query": q,
                "total": 0,
                "limit": effective_limit,
                "truncated": False,
                "results": [],
                "person": person_info,
            }

    results, truncated = search_backup_index(
        query=text_query,
        type_filter=type,
        sort_by=sort,
        limit=effective_limit,
        return_meta=True,
        person_ids=person_ids,
    )
    return {
        "query": q,
        "total": len(results),
        "limit": effective_limit,
        "truncated": truncated,
        "results": results,
        "person": person_info,
    }


@router.get("/api/backup/index/status")
async def get_backup_index_status() -> dict:
    payload = index_status_payload()
    payload["current_folder"] = indexer_state["current_folder"]
    payload["last_synced"] = indexer_state["last_synced"]
    return payload


@router.post("/api/backup/index/sync")
async def trigger_backup_sync():
    if indexer_state["status"] == "crawling":
        return {
            "success": False,
            "message": "Crawler is already running",
            "status": indexer_state["status"],
        }
    # Manual sync is a deliberate, full re-scan (startup crawls are incremental).
    asyncio.create_task(run_background_crawler(force_full=True))
    return {"success": True, "message": "Index crawl started", "status": "crawling"}


@router.post("/api/backup/index/prune")
async def trigger_backup_prune():
    # Explicit, user-confirmed prune: bypass the crawler's mount-safety limit
    # (which refuses to delete >10% of the index at once). Without ``force``
    # this endpoint could never clear a genuinely large remote deletion — it
    # just re-ran the same aborted prune and reported success.
    base_dir = os.path.realpath(state.get("destination_dir") or DEFAULT_DESTINATION_DIR)
    stats = await asyncio.to_thread(
        prune_stale_index_entries, base_dir, None, True, True
    )
    invalidate_index_counts_cache()
    return {"success": True, "stats": stats}
