import asyncio
import logging
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from media_library.core.config import (
    MAX_UPLOAD_FILE_SIZE_GB,
    UPLOAD_STAGING_DIR,
    UPLOAD_STAGING_TTL_HOURS,
)
from media_library.core.constants import IMAGE_EXTENSIONS, VIDEO_EXTENSIONS
from media_library.core.state import format_size
from media_library.db.indexer import index_folder
from media_library.services.library_manage_service import classify_library_path
from media_library.services.media_service import validate_backup_path

logger = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = VIDEO_EXTENSIONS | IMAGE_EXTENSIONS | {".zip"}


def _is_valid_upload_id(upload_id: str) -> bool:
    """Validates upload_id format to prevent directory traversal."""
    if not upload_id or len(upload_id) > 64:
        return False
    return bool(re.match(r"^[a-zA-Z0-9_\-]+$", upload_id))


def _staging_path(upload_id: str) -> Path:
    return UPLOAD_STAGING_DIR / f"{upload_id}.part"


def _staged_bytes(upload_id: str) -> int:
    staging_file = _staging_path(upload_id)
    try:
        return staging_file.stat().st_size if staging_file.exists() else 0
    except OSError:
        return 0


def get_upload_status(upload_id: str) -> dict[str, Any]:
    """Report how much of an upload the server already holds.

    The browser asks this before sending anything so an interrupted upload
    (dropped connection, closed tab, phone asleep) resumes at the last received
    byte instead of starting the multi-gigabyte transfer over.
    """
    if not _is_valid_upload_id(upload_id):
        raise HTTPException(status_code=400, detail="Invalid upload_id")
    received = _staged_bytes(upload_id)
    return {
        "status": "ok",
        "upload_id": upload_id,
        "received_bytes": received,
        "exists": received > 0,
    }


async def handle_upload_chunk(
    upload_id: str,
    chunk_index: int,
    total_chunks: int,
    chunk_data: bytes,
    offset: int | None = None,
) -> dict[str, Any]:
    """Append a chunk of data to the staging file, at a verifiable offset.

    Writes are offset-checked so a retried chunk can never be appended twice
    (which silently corrupted the assembled file) and a resume can never leave a
    gap: a chunk whose bytes the server already holds is acknowledged as a
    duplicate, and a chunk that starts past the staged size is rejected with the
    byte count the client should resume from.
    """
    if not _is_valid_upload_id(upload_id):
        raise HTTPException(status_code=400, detail="Invalid upload_id")

    staging_file = _staging_path(upload_id)
    current = _staged_bytes(upload_id)
    # Legacy callers without an explicit offset append where the file ends.
    start = current if offset is None else max(0, int(offset))

    if start > current:
        raise HTTPException(
            status_code=409,
            detail=f"Chunk starts at {start} but only {current} bytes are staged",
        )

    chunk_len = len(chunk_data)
    if start + chunk_len <= current:
        # Already received (retry after a timeout where the write succeeded).
        return {
            "status": "ok",
            "chunk_index": chunk_index,
            "received_bytes": current,
            "duplicate": True,
        }

    if start < current:
        # A partial tail from a crash mid-write is unusable; drop it and rewrite
        # this chunk from its own offset (the alternative — refusing — would
        # strand an upload that is otherwise complete up to here).
        def _truncate():
            with open(staging_file, "r+b") as f:
                f.truncate(start)

        await asyncio.to_thread(_truncate)
        current = start

    # Check size limit
    if current + chunk_len > MAX_UPLOAD_FILE_SIZE_GB * 1024 * 1024 * 1024:
        staging_file.unlink(missing_ok=True)
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds max size limit of {MAX_UPLOAD_FILE_SIZE_GB} GB",
        )

    def _write_chunk():
        with open(staging_file, "ab") as f:
            f.write(chunk_data)

    await asyncio.to_thread(_write_chunk)

    return {
        "status": "ok",
        "chunk_index": chunk_index,
        "received_bytes": start + chunk_len,
        "duplicate": False,
    }


def gc_stale_uploads(max_age_hours: float | None = None) -> dict[str, Any]:
    """Delete abandoned `.part` staging files and stray finalize artifacts.

    An upload that is never cancelled (browser closed mid-transfer) leaves its
    part file behind forever otherwise, filling the data disk with multi-gigabyte
    fragments. Runs at startup; age-based so an upload still in flight survives.
    """
    ttl = UPLOAD_STAGING_TTL_HOURS if max_age_hours is None else float(max_age_hours)
    cutoff = time.time() - max(0.0, ttl) * 3600.0
    removed = 0
    freed = 0

    candidates: list[Path] = []
    try:
        with os.scandir(UPLOAD_STAGING_DIR) as entries:
            for entry in entries:
                name = entry.name
                is_part = name.endswith(".part")
                is_tmp = name.startswith(".tmp_")
                if not (is_part or is_tmp):
                    continue  # `dl_*` job dirs belong to the URL download worker
                try:
                    if entry.is_file() and entry.stat().st_mtime < cutoff:
                        candidates.append(Path(entry.path))
                except OSError:
                    continue
    except OSError:
        return {"removed": 0, "freed_bytes": 0, "freed_formatted": "0 B"}

    for path in candidates:
        try:
            size = path.stat().st_size
            path.unlink()
            removed += 1
            freed += size
        except OSError:
            continue

    if removed:
        logger.info(
            f"[UploadGC] Removed {removed} abandoned upload staging file(s) "
            f"({format_size(freed)}) older than {ttl}h."
        )
    return {
        "removed": removed,
        "freed_bytes": freed,
        "freed_formatted": format_size(freed),
    }


def get_unique_filename(target_dir: Path, filename: str) -> Path:
    """Generates a unique filename (e.g., file (1).ext) if collision exists."""
    target_path = target_dir / filename
    if not target_path.exists():
        return target_path

    base = target_path.stem
    ext = target_path.suffix
    counter = 1
    while True:
        new_path = target_dir / f"{base} ({counter}){ext}"
        if not new_path.exists():
            return new_path
        counter += 1


async def finalize_upload(
    upload_id: str,
    original_filename: str,
    relative_path: str,
    destination_dir: str,
    collision_policy: str,
    total_size: int | None = None,
    mtime: int | None = None,
) -> dict[str, Any]:
    """Moves the staged file to its final destination and indexes it."""
    if not _is_valid_upload_id(upload_id):
        raise HTTPException(status_code=400, detail="Invalid upload_id")

    staging_file = UPLOAD_STAGING_DIR / f"{upload_id}.part"
    if not staging_file.exists():
        raise HTTPException(status_code=404, detail="Upload staging file not found")

    # Refuse to commit an upload whose chunks did not all arrive: finalizing a
    # short file produced a silently truncated image/video in the library.
    if total_size and total_size > 0:
        staged = staging_file.stat().st_size
        if staged != total_size:
            logger.warning(
                f"[Upload] Refusing to finalize {upload_id}: {staged} of {total_size} bytes staged."
            )
            raise HTTPException(
                status_code=400,
                detail=f"Upload incomplete: {staged} of {total_size} bytes received",
            )

    try:
        # Validate destination
        dest_path = validate_backup_path(destination_dir)
        kind = classify_library_path(dest_path)
        real_dest = dest_path

        if kind == "zip":
            raise HTTPException(
                status_code=400, detail="Cannot upload into a virtual ZIP archive"
            )

        real_dest_path = Path(real_dest)
        if not real_dest_path.is_dir():
            raise HTTPException(
                status_code=400, detail="Destination is not a directory"
            )

        # Extension validation
        ext = Path(original_filename).suffix.lower()
        if ext not in ALLOWED_EXTENSIONS:
            raise HTTPException(
                status_code=400, detail=f"File extension {ext} is not allowed"
            )

        # Determine target file path incorporating relative_path (for folder structure preservation)
        clean_rel_path = Path(relative_path).as_posix()
        if ".." in clean_rel_path or clean_rel_path.startswith("/"):
            raise HTTPException(status_code=400, detail="Invalid relative path")

        target_file_path = real_dest_path / clean_rel_path
        target_parent = target_file_path.parent

        # Create nested subdirectories if preserving folder structure
        if not target_parent.exists():
            target_parent.mkdir(parents=True, exist_ok=True)

        # Collision handling
        if target_file_path.exists():
            if collision_policy == "skip":
                staging_file.unlink(missing_ok=True)
                return {"status": "skipped", "path": str(target_file_path)}
            elif collision_policy == "overwrite":
                pass
            else:  # "auto-rename"
                target_file_path = get_unique_filename(
                    target_parent, target_file_path.name
                )

        # Atomic move (FUSE safety)
        tmp_final_path = target_parent / f".tmp_{upload_id}_{target_file_path.name}"

        def _finalize():
            try:
                shutil.move(str(staging_file), str(tmp_final_path))
                os.replace(str(tmp_final_path), str(target_file_path))

                # Apply mtime if provided
                if mtime is not None:
                    try:
                        os.utime(target_file_path, (time.time(), mtime / 1000.0))
                    except Exception as e:
                        logger.warning(
                            f"Failed to set mtime on {target_file_path}: {e}"
                        )

                # Index the parent folder
                index_folder(str(target_parent))
            finally:
                if tmp_final_path.exists():
                    try:
                        tmp_final_path.unlink()
                    except Exception:
                        pass

        await asyncio.to_thread(_finalize)

        return {"status": "ok", "path": str(target_file_path)}

    except Exception as e:
        staging_file.unlink(missing_ok=True)
        if isinstance(e, HTTPException):
            raise
        logger.error(f"Error finalizing upload {upload_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


async def cancel_upload(upload_id: str) -> dict[str, Any]:
    """Cleans up a cancelled upload."""
    if not _is_valid_upload_id(upload_id):
        raise HTTPException(status_code=400, detail="Invalid upload_id")

    staging_file = UPLOAD_STAGING_DIR / f"{upload_id}.part"
    if staging_file.exists():
        staging_file.unlink(missing_ok=True)
        return {"status": "cancelled"}
    return {"status": "not_found"}
