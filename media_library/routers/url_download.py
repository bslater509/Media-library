"""URL / playlist video download endpoints (yt-dlp backed).

Extends the Library upload flow: ``Download from URL`` queues a background job
that downloads into ``UPLOAD_STAGING_DIR`` and finally moves the media into the
chosen library folder (auto-rename on collisions) before re-indexing it.
"""

import logging
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from media_library.services.media_service import validate_backup_path
from media_library.services.library_manage_service import classify_library_path
from media_library.workers import (
    is_gallery_dl_available,
    is_ytdlp_available,
    url_download_worker_instance,
)
from media_library.workers.url_download_worker import _URL_RE

logger = logging.getLogger(__name__)

router = APIRouter(tags=["URL Downloads"])

MAX_URL_LENGTH = 2048


class UrlDownloadRequest(BaseModel):
    url: str
    destination_dir: str


class UrlCancelRequest(BaseModel):
    job_id: str


class UrlRetryRequest(BaseModel):
    job_id: str


def _validate_destination(destination_dir: str) -> str:
    if not destination_dir or not destination_dir.strip():
        raise HTTPException(status_code=400, detail="destination_dir is required")
    try:
        dest_path = validate_backup_path(destination_dir)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid destination folder: {e}")
    dest_path_obj = Path(dest_path)
    if classify_library_path(dest_path) == "zip":
        raise HTTPException(
            status_code=400, detail="Cannot download into a virtual ZIP archive"
        )
    if not dest_path_obj.is_dir():
        raise HTTPException(status_code=400, detail="Destination is not a directory")
    return str(dest_path)


@router.post("/api/backup/upload/url")
def enqueue_url_download(req: UrlDownloadRequest) -> dict[str, Any]:
    """Queue a URL/playlist download into the given library folder."""
    url = (req.url or "").strip()
    if not url or len(url) > MAX_URL_LENGTH or not _URL_RE.match(url):
        raise HTTPException(
            status_code=400,
            detail="Invalid URL: must be an http(s) link to a video or playlist",
        )
    destination = _validate_destination(req.destination_dir)

    if not is_ytdlp_available() and not is_gallery_dl_available():
        raise HTTPException(
            status_code=503,
            detail="Neither yt-dlp nor gallery-dl is available on this server. Install them via requirements.txt.",
        )

    job_id = url_download_worker_instance.enqueue(url, destination)
    logger.info(f"Queued URL download {job_id}: {url} -> {destination}")
    return {"status": "queued", "job_id": job_id}


@router.get("/api/backup/upload/url/status")
def url_download_status() -> dict[str, Any]:
    """Active, queued, and recent URL download jobs."""
    return url_download_worker_instance.get_status()


@router.post("/api/backup/upload/url/cancel")
def cancel_url_download(req: UrlCancelRequest) -> dict[str, Any]:
    """Cancel a queued or running URL download job."""
    if not req.job_id or not req.job_id.startswith("dl_"):
        raise HTTPException(status_code=400, detail="Invalid job_id")
    cancelled = url_download_worker_instance.cancel(req.job_id)
    if not cancelled:
        raise HTTPException(status_code=404, detail="Download job not found")
    return {"status": "cancelled", "job_id": req.job_id}


@router.post("/api/backup/upload/url/retry")
def retry_url_download(req: UrlRetryRequest) -> dict[str, Any]:
    """Clone a failed or cancelled download as a new queued job."""
    if not req.job_id or not req.job_id.startswith("dl_"):
        raise HTTPException(status_code=400, detail="Invalid job_id")
    entry = url_download_worker_instance.find_finished(req.job_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Download job not found")
    if entry.get("status") not in ("failed", "cancelled"):
        raise HTTPException(
            status_code=409,
            detail="Only failed or cancelled downloads can be retried",
        )
    # Re-validate the original destination before cloning: it may have been
    # deleted or turned into a ZIP archive since the job first ran.
    _validate_destination(entry.get("destination_dir") or "")
    new_job_id = url_download_worker_instance.retry(req.job_id)
    if not new_job_id:
        raise HTTPException(status_code=409, detail="Could not retry this download")
    return {
        "status": "queued",
        "job_id": new_job_id,
        "retried_from": req.job_id,
    }


@router.post("/api/backup/upload/url/clear-history")
def clear_url_download_history() -> dict[str, Any]:
    """Remove all finished URL download jobs from the worker's history."""
    cleared = url_download_worker_instance.clear_history()
    return {"status": "ok", "cleared": cleared}


@router.get("/api/backup/upload/url/history")
def url_download_history(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    """Paginated finished-history listing for the /downloads page, newest first."""
    return url_download_worker_instance.get_history(limit=limit, offset=offset)
