"""File and folder mutations for v2: rename, delete, create, move."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter

from media_library_v2 import library, writes
from media_library_v2.routers.common import as_http_error
from media_library_v2.schemas import (
    BulkRenameRequest,
    BulkRenameResponse,
    CreateFolderRequest,
    CreateFolderResponse,
    DeleteRequest,
    DeleteResponse,
    FolderRenameRequest,
    FolderRenameResponse,
    MoveRequest,
    MoveResponse,
    RenameRequest,
    RenameResponse,
)

router = APIRouter()


@router.post("/manage/rename", response_model=RenameResponse)
async def rename(payload: RenameRequest) -> dict:
    """Rename a file in place, keeping its parent folder and extension rules."""
    try:
        result = await asyncio.to_thread(writes.rename_file, payload.path, payload.name)
        entry = await asyncio.to_thread(library.get_file, result["path"])
    except Exception as exc:
        raise as_http_error(exc) from exc
    return {"path": result["path"], "file": entry}


@router.post("/manage/bulk-rename", response_model=BulkRenameResponse)
async def bulk_rename(payload: BulkRenameRequest) -> dict:
    """Rename many files; each failure is reported without stopping the rest."""
    try:
        return await asyncio.to_thread(
            writes.bulk_rename,
            [item.model_dump() for item in payload.renames],
        )
    except Exception as exc:
        raise as_http_error(exc) from exc


@router.post("/manage/delete", response_model=DeleteResponse)
async def delete(payload: DeleteRequest) -> dict:
    """Delete one or more files, updating the index and cached artwork."""
    try:
        return await asyncio.to_thread(writes.delete_files, payload.paths)
    except Exception as exc:
        raise as_http_error(exc) from exc


@router.post("/manage/folders/rename", response_model=FolderRenameResponse)
async def rename_folder(payload: FolderRenameRequest) -> dict:
    """Rename a folder or ZIP archive and rewrite its indexed subtree."""
    try:
        return await asyncio.to_thread(writes.rename_folder, payload.path, payload.name)
    except Exception as exc:
        raise as_http_error(exc) from exc


@router.post("/manage/folders", response_model=CreateFolderResponse)
async def create_folder(payload: CreateFolderRequest) -> dict:
    """Create a real subfolder inside the library."""
    try:
        return await asyncio.to_thread(
            writes.create_folder, payload.parent, payload.name
        )
    except Exception as exc:
        raise as_http_error(exc) from exc


@router.post("/manage/move", response_model=MoveResponse)
async def move(payload: MoveRequest) -> dict:
    """Move files into a destination folder with a conflict policy."""
    try:
        return await asyncio.to_thread(
            writes.move_files, payload.paths, payload.destination, payload.conflict
        )
    except Exception as exc:
        raise as_http_error(exc) from exc
