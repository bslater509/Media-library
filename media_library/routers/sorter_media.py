"""FastAPI router for the Video Sorter: incoming video streaming and duplicate checks."""

import asyncio
import logging
import os
import urllib.parse

from fastapi import APIRouter, HTTPException, Query, Request

from media_library.core.config import DEFAULT_DESTINATION_DIR
from media_library.core.http_stream import stream_file_with_range
from media_library.core.state import format_size, state
from media_library.db.connection import db_conn
from media_library.services.deduplication_service import compute_quick_hash
from media_library.services.media_service import resolve_video_path
from media_library.workers.transcode_worker import transcode_worker_instance

router = APIRouter()

logger = logging.getLogger(__name__)


@router.api_route("/api/stream/{file_id}", methods=["GET", "HEAD"])
async def stream_video(file_id: str, request: Request):
    if not state["source_dir"]:
        raise HTTPException(status_code=400, detail="No source directory configured")

    file_path = resolve_video_path(
        file_id, transcode_worker_instance.transcode_items_map
    )
    if not file_path:
        raise HTTPException(status_code=404, detail="Video file not found")

    file_exists = await asyncio.to_thread(os.path.exists, file_path)
    if not file_exists:
        raise HTTPException(status_code=404, detail="Video file not found")

    return await stream_file_with_range(file_path, request)


@router.get("/api/sorter/check-duplicate")
def check_sorter_duplicate(
    file_id: str | None = Query(None), path: str | None = Query(None)
) -> dict:
    target_path = None
    if path:
        target_path = os.path.abspath(path.strip())
    elif file_id:
        target_path = resolve_video_path(
            file_id, transcode_worker_instance.transcode_items_map
        )

    if (
        not target_path
        or not os.path.exists(target_path)
        or not os.path.isfile(target_path)
    ):
        return {"is_duplicate": False, "matches": []}

    try:
        incoming_size = os.path.getsize(target_path)
        incoming_name = os.path.basename(target_path)
        if incoming_size == 0:
            return {"is_duplicate": False, "matches": []}

        with db_conn() as conn:
            base_dir = os.path.realpath(
                state.get("destination_dir") or DEFAULT_DESTINATION_DIR
            )

            size_matches = conn.execute(
                """
                SELECT path, name, folder_path, type, ext, size, size_formatted, mtime, has_thumb, quick_hash
                FROM files
                WHERE size = ?
            """,
                (incoming_size,),
            ).fetchall()

            matched_content = []
            if size_matches:
                incoming_hash = compute_quick_hash(target_path)
                if incoming_hash:
                    for sm in size_matches:
                        cand_path = sm["path"]
                        cand_hash = sm["quick_hash"]
                        if not cand_hash:
                            cand_hash = compute_quick_hash(cand_path)
                            if cand_hash:
                                with conn:
                                    conn.execute(
                                        "UPDATE files SET quick_hash = ? WHERE path = ?",
                                        (cand_hash, cand_path),
                                    )
                        if cand_hash and cand_hash == incoming_hash:
                            try:
                                rel_folder = os.path.relpath(
                                    sm["folder_path"], base_dir
                                )
                            except ValueError:
                                # Cross-drive/mixed paths can't be made relative;
                                # fall back to the absolute path for display.
                                rel_folder = sm["folder_path"]
                            if rel_folder == ".":
                                rel_folder = "Root"
                            matched_content.append(
                                {
                                    "path": cand_path,
                                    "name": sm["name"],
                                    "folder_path": sm["folder_path"],
                                    "folder_display": rel_folder,
                                    "size": sm["size"],
                                    "size_formatted": sm["size_formatted"],
                                    "match_reason": f"Exact content match ({sm['size_formatted']})",
                                    "media_url": f"/api/backup/media?path={urllib.parse.quote(cand_path)}",
                                    "thumb_url": f"/api/backup/thumbnail?path={urllib.parse.quote(cand_path)}",
                                }
                            )

            name_matches = conn.execute(
                """
                SELECT path, name, folder_path, type, ext, size, size_formatted, mtime, has_thumb
                FROM files
                WHERE name = ?
            """,
                (incoming_name,),
            ).fetchall()

            matched_names = []
            existing_paths = {m["path"] for m in matched_content}
            for nm in name_matches:
                if nm["path"] not in existing_paths:
                    try:
                        rel_folder = os.path.relpath(nm["folder_path"], base_dir)
                    except ValueError:
                        # Cross-drive/mixed paths can't be made relative;
                        # fall back to the absolute path for display.
                        rel_folder = nm["folder_path"]
                    if rel_folder == ".":
                        rel_folder = "Root"
                    matched_names.append(
                        {
                            "path": nm["path"],
                            "name": nm["name"],
                            "folder_path": nm["folder_path"],
                            "folder_display": rel_folder,
                            "size": nm["size"],
                            "size_formatted": nm["size_formatted"],
                            "match_reason": f"Exact filename match (in {rel_folder})",
                            "media_url": f"/api/backup/media?path={urllib.parse.quote(nm['path'])}",
                            "thumb_url": f"/api/backup/thumbnail?path={urllib.parse.quote(nm['path'])}",
                        }
                    )

        if matched_content:
            return {
                "is_duplicate": True,
                "match_type": "content",
                "incoming_file": {
                    "filename": incoming_name,
                    "size": incoming_size,
                    "size_formatted": format_size(incoming_size),
                },
                "matches": matched_content,
            }
        elif matched_names:
            return {
                "is_duplicate": True,
                "match_type": "name",
                "incoming_file": {
                    "filename": incoming_name,
                    "size": incoming_size,
                    "size_formatted": format_size(incoming_size),
                },
                "matches": matched_names,
            }
        else:
            return {"is_duplicate": False, "matches": []}
    except Exception:
        logger.exception("Error checking duplicate for %s", target_path)
        raise HTTPException(status_code=500, detail="Failed to check duplicate")
