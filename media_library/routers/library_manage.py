"""FastAPI router for Library page file/folder management.

Move/delete/rename operations on the media library. Heavy operations (moves,
recursive folder deletes) are queued onto :data:`library_ops_worker_instance`
so they run off the request thread; the client polls ``/api/system/status`` for
progress. Lightweight single-path operations (folder create/rename) run inline.

All paths are validated with ``validate_backup_path`` to prevent traversal
outside ``LIBRARY_DIR``.
"""

import asyncio
import logging
import os
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from media_library.core.config import DEFAULT_DESTINATION_DIR
from media_library.core.constants import IMAGE_EXTENSIONS
from media_library.core.state import state
from media_library.services.library_manage_service import (
    classify_library_path,
    compute_bulk_rename,
    compute_move_targets,
    create_library_folder,
    delete_empty_folders,
    find_empty_folders,
    move_library_path,
    rename_library_path,
)
from media_library.services.media_service import validate_backup_path
from media_library.workers.library_ops_worker import library_ops_worker_instance
from media_library.workers.webp_worker import webp_worker_instance

router = APIRouter()

logger = logging.getLogger(__name__)


class LibraryMoveRequest(BaseModel):
    paths: list[str]
    destination_folder: str
    conflict_policy: str = "auto_rename"  # 'auto_rename' | 'skip' | 'error'


class FolderCreateRequest(BaseModel):
    parent_path: str
    name: str


class FolderRenameRequest(BaseModel):
    path: str
    new_name: str


class FolderDeleteRequest(BaseModel):
    path: str


class BulkRenameRequest(BaseModel):
    paths: list[str]
    mode: str  # 'prefix' | 'numbering' | 'find_replace' | 'case' | 'base_counter'
    params: dict | None = None


class BulkDeleteRequest(BaseModel):
    paths: list[str]


def _base_dir() -> str:
    return os.path.realpath(state.get("destination_dir") or DEFAULT_DESTINATION_DIR)


def _validate_destination(destination_folder: str) -> str:
    destination = validate_backup_path(destination_folder)
    if destination == _base_dir():
        return destination
    # Destination must be an existing directory (or a ZIP is not a valid target).
    if not os.path.isdir(destination):
        raise HTTPException(status_code=400, detail="Destination folder does not exist")
    return destination


@router.post("/api/backup/move/check")
def move_check(req: LibraryMoveRequest):
    destination = _validate_destination(req.destination_folder)
    if not req.paths:
        raise HTTPException(status_code=400, detail="No paths provided")

    plans, collisions = compute_move_targets(req.paths, destination, "auto_rename")
    return {
        "total": len(plans),
        "collision_count": len(collisions),
        "collisions": [
            {
                "source_path": c["source_path"],
                "target_path": c["target_path"],
                "name": os.path.basename(c["source_path"]),
                "kind": c["kind"],
            }
            for c in collisions
        ],
    }


@router.post("/api/backup/move")
def move_paths(req: LibraryMoveRequest):
    destination = _validate_destination(req.destination_folder)
    if not req.paths:
        raise HTTPException(status_code=400, detail="No paths provided")

    policy = (
        req.conflict_policy
        if req.conflict_policy in ("auto_rename", "skip", "error")
        else "auto_rename"
    )
    plans, collisions = compute_move_targets(req.paths, destination, policy)

    # Create the destination folder once for the whole batch. Each queued move
    # also guards its own parent (stat-only when it exists), so this avoids N
    # redundant slow FUSE mkdirs when all files land in the same new folder.
    try:
        os.makedirs(destination, exist_ok=True)
    except OSError:
        logger.exception("Failed to create destination folder %s", destination)
        raise HTTPException(
            status_code=500, detail="Failed to create destination folder"
        )

    if policy == "error" and collisions:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Destination already contains files with the same name",
                "collisions": [os.path.basename(c["source_path"]) for c in collisions],
            },
        )

    if not plans:
        return {"success": True, "queued": 0, "tasks": []}

    tasks = []
    for plan in plans:
        source = plan["source_path"]
        kind = plan["kind"]

        # Refuse to move a folder into itself or one of its own descendants.
        if kind == "folder":
            try:
                if os.path.commonpath(
                    [source, destination]
                ) == source and destination != os.path.dirname(source):
                    raise HTTPException(
                        status_code=400,
                        detail=f"Cannot move folder into itself: {os.path.basename(source)}",
                    )
            except ValueError:
                # commonpath raised: mixed/odd path arguments make the
                # self-move check meaningless, treat it as a normal move.
                pass

        task_id = uuid.uuid4().hex[:10]
        item = {
            "id": task_id,
            "op": "move",
            "source_path": source,
            "target_path": plan["target_path"],
            "kind": kind,
        }
        library_ops_worker_instance.enqueue(item)
        tasks.append(
            {
                "task_id": task_id,
                "source_path": source,
                "target_path": plan["target_path"],
                "kind": kind,
                "conflict": plan["conflict"],
            }
        )

    return {"success": True, "queued": len(tasks), "tasks": tasks}


@router.get("/api/backup/library-ops/status")
def library_ops_status():
    return library_ops_worker_instance.get_status()


@router.post("/api/backup/library-ops/cancel")
def library_ops_cancel(req: dict):
    task_id = (req or {}).get("task_id")
    if not task_id:
        raise HTTPException(status_code=400, detail="task_id required")
    if not library_ops_worker_instance.cancel(task_id):
        raise HTTPException(status_code=404, detail="Task not found")
    return {"success": True, "cancelled": task_id}


@router.post("/api/backup/undo")
async def undo_last_library_move():
    record = library_ops_worker_instance.pop_undo()
    if not record:
        raise HTTPException(status_code=400, detail="Nothing to undo")

    source = record["source_path"]
    target = record["target_path"]
    kind = record["kind"]

    if not os.path.exists(target):
        raise HTTPException(
            status_code=404, detail="Moved item no longer exists at destination"
        )

    restore_target = source
    if os.path.exists(restore_target):
        stem, ext = os.path.splitext(source)
        counter = 1
        while os.path.exists(f"{stem}_restored_{counter}{ext}"):
            counter += 1
        restore_target = f"{stem}_restored_{counter}{ext}"

    try:
        await asyncio.to_thread(move_library_path, target, restore_target, kind)
    except Exception:
        logger.exception("Failed to undo move of %s", target)
        library_ops_worker_instance.push_undo(record)
        raise HTTPException(status_code=500, detail="Failed to undo move")

    return {
        "success": True,
        "restored_path": restore_target,
        "remaining_undo_count": len(library_ops_worker_instance.undo_stack),
    }


@router.post("/api/backup/folders")
def create_folder(req: FolderCreateRequest):
    try:
        result = create_library_folder(req.parent_path, req.name)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid folder name")
    except FileExistsError:
        raise HTTPException(
            status_code=400, detail="A folder with this name already exists"
        )
    except Exception:
        logger.exception("Failed to create folder %r in %s", req.name, req.parent_path)
        raise HTTPException(status_code=500, detail="Failed to create folder")
    return result


@router.post("/api/backup/folders/rename")
def rename_folder(req: FolderRenameRequest):
    try:
        result = rename_library_path(req.path, req.new_name)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid name")
    except FileExistsError:
        raise HTTPException(
            status_code=400, detail="An item with this name already exists"
        )
    except Exception:
        logger.exception("Failed to rename %s", req.path)
        raise HTTPException(status_code=500, detail="Failed to rename")
    return result


@router.post("/api/backup/folders/delete")
def delete_folder(req: FolderDeleteRequest):
    path = validate_backup_path(req.path)
    kind = classify_library_path(path)
    if kind not in ("folder", "zip"):
        raise HTTPException(
            status_code=400, detail="Only folders or ZIP archives can be deleted here"
        )

    task_id = uuid.uuid4().hex[:10]
    library_ops_worker_instance.enqueue(
        {
            "id": task_id,
            "op": "delete",
            "path": path,
            "kind": kind,
        }
    )
    return {"success": True, "queued": True, "task_id": task_id, "kind": kind}


@router.post("/api/backup/bulk/rename/preview")
def bulk_rename_preview(req: BulkRenameRequest):
    if not req.paths:
        raise HTTPException(status_code=400, detail="No paths provided")
    try:
        items = compute_bulk_rename(req.paths, req.mode, req.params)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid rename parameters")
    return {"items": items, "conflict_count": sum(1 for i in items if i["conflict"])}


@router.post("/api/backup/bulk/rename")
def bulk_rename_apply(req: BulkRenameRequest):
    if not req.paths:
        raise HTTPException(status_code=400, detail="No paths provided")
    try:
        items = compute_bulk_rename(req.paths, req.mode, req.params)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid rename parameters")

    if any(i["conflict"] for i in items):
        raise HTTPException(
            status_code=409, detail="Bulk rename would produce duplicate filenames"
        )

    tasks = []
    for item in items:
        if item["new_name"] == item["name"]:
            continue
        task_id = uuid.uuid4().hex[:10]
        library_ops_worker_instance.enqueue(
            {
                "id": task_id,
                "op": "rename",
                "path": item["path"],
                "new_name": item["new_name"],
            }
        )
        tasks.append(
            {"task_id": task_id, "path": item["path"], "new_name": item["new_name"]}
        )

    return {"success": True, "queued": len(tasks), "tasks": tasks}


@router.post("/api/backup/bulk/delete")
def bulk_delete(req: BulkDeleteRequest):
    if not req.paths:
        raise HTTPException(status_code=400, detail="No paths provided")

    tasks = []
    for p in req.paths:
        path = validate_backup_path(p)
        kind = classify_library_path(path)
        task_id = uuid.uuid4().hex[:10]
        library_ops_worker_instance.enqueue(
            {
                "id": task_id,
                "op": "delete",
                "path": path,
                "kind": kind,
            }
        )
        tasks.append({"task_id": task_id, "path": path, "kind": kind})

    return {"success": True, "queued": len(tasks), "tasks": tasks}


@router.post("/api/backup/bulk/webp-optimize")
def bulk_webp_optimize(req: BulkDeleteRequest):
    """Convert selected images, enqueue selected folders, skip the rest."""
    if not req.paths:
        raise HTTPException(status_code=400, detail="No paths provided")

    image_paths = []
    folders = []
    skipped = 0
    for p in req.paths:
        path = validate_backup_path(p)
        kind = classify_library_path(path)
        if kind == "file":
            ext = os.path.splitext(path)[1].lower()
            if ext in IMAGE_EXTENSIONS and ext != ".webp":
                image_paths.append(path)
            else:
                skipped += 1
        elif kind in ("folder", "zip"):
            folders.append(path)
        else:
            skipped += 1

    converted = webp_worker_instance.enqueue_files(image_paths)
    for folder in folders:
        try:
            webp_worker_instance.enqueue_folder(folder)
        except Exception as e:
            logger.warning(
                f"Warning: could not enqueue WebP optimization for {folder}: {e}"
            )

    return {
        "success": True,
        "images_queued": converted,
        "folders_queued": len(folders),
        "skipped": skipped,
    }


class DeleteEmptyFoldersRequest(BaseModel):
    folder_paths: list[str] | None = None


@router.get("/api/backup/folders/empty/candidates")
async def get_empty_folder_candidates():
    """Finds all empty folders across the library that have zero files and zero non-empty subfolders."""
    base_dir = state.get("destination_dir") or DEFAULT_DESTINATION_DIR
    res = await asyncio.to_thread(find_empty_folders, str(base_dir))
    return res


@router.post("/api/backup/folders/empty/delete")
async def delete_empty_folders_endpoint(
    req: DeleteEmptyFoldersRequest = DeleteEmptyFoldersRequest(),
):
    """Deletes empty folders bottom-up and updates the index."""
    base_dir = state.get("destination_dir") or DEFAULT_DESTINATION_DIR
    paths = None
    if req and req.folder_paths:
        paths = []
        for p in req.folder_paths:
            try:
                paths.append(validate_backup_path(p, str(base_dir)))
            except HTTPException:
                logger.warning("Skipping path outside backup directory: %s", p)
    res = await asyncio.to_thread(delete_empty_folders, paths, str(base_dir))
    return res

