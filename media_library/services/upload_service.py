import asyncio
import logging
import os
import shutil
import time
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from media_library.core.config import MAX_UPLOAD_FILE_SIZE_GB, UPLOAD_STAGING_DIR
from media_library.core.constants import IMAGE_EXTENSIONS, VIDEO_EXTENSIONS
from media_library.db.indexer import index_folder
from media_library.services.library_manage_service import classify_library_path
from media_library.services.media_service import validate_backup_path

logger = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = VIDEO_EXTENSIONS | IMAGE_EXTENSIONS | {".zip"}

async def handle_upload_chunk(upload_id: str, chunk_index: int, total_chunks: int, chunk_data: bytes) -> dict[str, Any]:
    """Appends a chunk of data to the staging file."""
    if not upload_id or not upload_id.isalnum():
        raise HTTPException(status_code=400, detail="Invalid upload_id")
    
    staging_file = UPLOAD_STAGING_DIR / f"{upload_id}.part"
    
    # Check size limit
    if (
        staging_file.exists()
        and staging_file.stat().st_size + len(chunk_data) > MAX_UPLOAD_FILE_SIZE_GB * 1024 * 1024 * 1024
    ):
        staging_file.unlink(missing_ok=True)
        raise HTTPException(status_code=413, detail=f"File exceeds max size limit of {MAX_UPLOAD_FILE_SIZE_GB} GB")
    
    mode = "ab" if chunk_index > 0 else "wb"
    
    def _write_chunk():
        with open(staging_file, mode) as f:
            f.write(chunk_data)
    
    await asyncio.to_thread(_write_chunk)
    
    return {"status": "ok", "chunk_index": chunk_index}


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
    mtime: int | None = None
) -> dict[str, Any]:
    """Moves the staged file to its final destination and indexes it."""
    if not upload_id or not upload_id.isalnum():
        raise HTTPException(status_code=400, detail="Invalid upload_id")
        
    staging_file = UPLOAD_STAGING_DIR / f"{upload_id}.part"
    if not staging_file.exists():
        raise HTTPException(status_code=404, detail="Upload staging file not found")
        
    try:
        # Validate destination
        dest_path = validate_backup_path(destination_dir)
        kind = classify_library_path(dest_path)
        real_dest = dest_path
        
        if kind == "zip":
            raise HTTPException(status_code=400, detail="Cannot upload into a virtual ZIP archive")
            
        real_dest_path = Path(real_dest)
        if not real_dest_path.is_dir():
            raise HTTPException(status_code=400, detail="Destination is not a directory")
            
        # Extension validation
        ext = Path(original_filename).suffix.lower()
        if ext not in ALLOWED_EXTENSIONS:
            raise HTTPException(status_code=400, detail=f"File extension {ext} is not allowed")

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
            else: # "auto-rename"
                target_file_path = get_unique_filename(target_parent, target_file_path.name)
                
        # Atomic move (FUSE safety)
        tmp_final_path = target_parent / f".tmp_{upload_id}_{target_file_path.name}"
        
        def _finalize():
            shutil.move(str(staging_file), str(tmp_final_path))
            os.replace(str(tmp_final_path), str(target_file_path))
            
            # Apply mtime if provided
            if mtime is not None:
                try:
                    os.utime(target_file_path, (time.time(), mtime / 1000.0))
                except Exception as e:
                    logger.warning(f"Failed to set mtime on {target_file_path}: {e}")
                    
            # Index the parent folder
            index_folder(str(target_parent))
            
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
    if not upload_id or not upload_id.isalnum():
        raise HTTPException(status_code=400, detail="Invalid upload_id")
        
    staging_file = UPLOAD_STAGING_DIR / f"{upload_id}.part"
    if staging_file.exists():
        staging_file.unlink(missing_ok=True)
        return {"status": "cancelled"}
    return {"status": "not_found"}
