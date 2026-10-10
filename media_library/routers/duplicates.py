"""FastAPI router for duplicate detection, hashing workers, verification, and resolution."""

import logging
import time
import urllib.parse
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from media_library.db.connection import db_conn
from media_library.services.deduplication_service import (
    autodelete_verified_duplicates as autodelete_service,
)
from media_library.services.deduplication_service import (
    compute_quick_hash,
    get_backup_duplicates,
    normalize_pair,
    visual_duplicates_cache,
)
from media_library.services.deduplication_service import (
    resolve_duplicate_group as resolve_duplicates_service,
)
from media_library.services.media_service import validate_backup_path
from media_library.services.status import duplicate_hash_payload
from media_library.workers.duplicate_hash_worker import (
    duplicate_hash_worker_instance,
    thumb_hash_worker_instance,
)

router = APIRouter()

logger = logging.getLogger(__name__)


class DuplicateResolveRequest(BaseModel):
    keep_path: str
    delete_paths: list[str]


class DuplicateVerifyRequest(BaseModel):
    paths: list[str]


class DuplicateIgnoreRequest(BaseModel):
    paths: list[str]
    match_type: str = "visual"


class DuplicateUnignoreRequest(BaseModel):
    id: int | None = None
    paths: list[str] | None = None


@router.get("/api/backup/duplicates")
def get_duplicates(
    mode: str = Query("content"), limit: int = Query(50), offset: int = Query(0)
):
    return get_backup_duplicates(mode=mode, limit=limit, offset=offset)


@router.get("/api/backup/duplicates/hash-status")
async def get_duplicate_hash_status() -> dict:
    return duplicate_hash_payload()


@router.post("/api/backup/duplicates/hash-pause")
async def toggle_duplicate_hash_pause():
    if not duplicate_hash_worker_instance.is_paused():
        duplicate_hash_worker_instance.pause()
        return {
            "success": True,
            "is_paused": True,
            "message": "Duplicate hash verification paused",
        }
    else:
        duplicate_hash_worker_instance.resume()
        return {
            "success": True,
            "is_paused": False,
            "message": "Duplicate hash verification resumed",
        }


@router.post("/api/backup/duplicates/hash-start")
async def start_duplicate_hash():
    duplicate_hash_worker_instance.resume()
    return {
        "success": True,
        "is_paused": False,
        "message": "Duplicate hash verification active",
    }


@router.get("/api/backup/duplicates/thumb-hash-status")
async def get_thumb_hash_status():
    return thumb_hash_worker_instance.get_status()


@router.post("/api/backup/duplicates/ignore")
def ignore_duplicate_match(req: DuplicateIgnoreRequest):
    if not req.paths or len(req.paths) < 2:
        raise HTTPException(
            status_code=400,
            detail="At least 2 file paths are required to ignore a duplicate match.",
        )

    with db_conn() as conn:
        pairs = []
        now = time.time()
        for i in range(len(req.paths)):
            for j in range(i + 1, len(req.paths)):
                p1, p2 = normalize_pair(req.paths[i], req.paths[j])
                pairs.append((p1, p2, req.match_type, now))

        with conn:
            conn.executemany(
                "INSERT OR IGNORE INTO ignored_duplicates (path1, path2, match_type, created_at) VALUES (?, ?, ?, ?)",
                pairs,
            )

    ignored_paths_set = set(req.paths)
    if visual_duplicates_cache.get("groups"):
        remaining_groups = []
        reclaimable_sub = 0
        for g in visual_duplicates_cache["groups"]:
            g_paths = {f["path"] for f in g.get("files", [])}
            if g_paths.intersection(ignored_paths_set):
                reclaimable_sub += g.get("reclaimable_bytes", 0)
            else:
                remaining_groups.append(g)
        visual_duplicates_cache["groups"] = remaining_groups
        visual_duplicates_cache["total_groups"] = len(remaining_groups)
        visual_duplicates_cache["total_reclaimable"] = max(
            0, visual_duplicates_cache.get("total_reclaimable", 0) - reclaimable_sub
        )
        visual_duplicates_cache["ignored_count"] = visual_duplicates_cache.get(
            "ignored_count", 0
        ) + len(pairs)
    else:
        visual_duplicates_cache["timestamp"] = 0

    return {
        "success": True,
        "message": f"Ignored {len(pairs)} pair(s) successfully.",
        "pairs_count": len(pairs),
    }


@router.get("/api/backup/duplicates/ignored")
def get_ignored_duplicates(limit: int = 100, offset: int = 0) -> dict:
    with db_conn() as conn:
        tot_row = conn.execute("SELECT COUNT(*) FROM ignored_duplicates").fetchone()
        total = tot_row[0] if tot_row else 0
        if limit <= 0:
            return {
                "success": True,
                "total": total,
                "items": [],
                "limit": limit,
                "offset": offset,
            }

        rows = conn.execute(
            """
            SELECT id, path1, path2, match_type, created_at
            FROM ignored_duplicates
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
        """,
            (limit, offset),
        ).fetchall()

        items = []
        for r in rows:
            f1 = conn.execute(
                "SELECT name, size_formatted FROM files WHERE path = ?", (r["path1"],)
            ).fetchone()
            f2 = conn.execute(
                "SELECT name, size_formatted FROM files WHERE path = ?", (r["path2"],)
            ).fetchone()
            items.append(
                {
                    "id": r["id"],
                    "path1": r["path1"],
                    "name1": f1["name"] if f1 else Path(r["path1"]).name,
                    "size1": f1["size_formatted"] if f1 else "",
                    "thumb1": f"/api/backup/thumbnail?path={urllib.parse.quote(r['path1'])}",
                    "path2": r["path2"],
                    "name2": f2["name"] if f2 else Path(r["path2"]).name,
                    "size2": f2["size_formatted"] if f2 else "",
                    "thumb2": f"/api/backup/thumbnail?path={urllib.parse.quote(r['path2'])}",
                    "match_type": r["match_type"],
                    "created_at": r["created_at"],
                }
            )
        return {
            "success": True,
            "total": total,
            "items": items,
            "limit": limit,
            "offset": offset,
        }


@router.post("/api/backup/duplicates/unignore")
def unignore_duplicate_match(req: DuplicateUnignoreRequest):
    with db_conn() as conn, conn:
        if req.id is not None:
            conn.execute("DELETE FROM ignored_duplicates WHERE id = ?", (req.id,))
        elif req.paths and len(req.paths) >= 2:
            p1, p2 = normalize_pair(req.paths[0], req.paths[1])
            conn.execute(
                "DELETE FROM ignored_duplicates WHERE path1 = ? AND path2 = ?", (p1, p2)
            )
        else:
            raise HTTPException(
                status_code=400,
                detail="Must provide either 'id' or 'paths' with 2 paths.",
            )

    visual_duplicates_cache["timestamp"] = 0
    return {"success": True, "message": "Duplicate match un-ignored."}


@router.post("/api/backup/duplicates/verify-group")
def verify_duplicate_group(req: DuplicateVerifyRequest):
    hashes = {}
    missing_paths = []

    with db_conn() as conn:
        for p in req.paths:
            try:
                real_p = validate_backup_path(p)
                row = conn.execute(
                    "SELECT quick_hash FROM files WHERE path = ?", (real_p,)
                ).fetchone()
                if row and row["quick_hash"]:
                    hashes[p] = row["quick_hash"]
                else:
                    missing_paths.append((p, real_p))
            except Exception as e:
                logger.error(f"Error checking hash for {p}: {e}")

    if missing_paths:
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=min(4, len(missing_paths))) as executor:
            future_to_path = {
                executor.submit(compute_quick_hash, real_p): (p, real_p)
                for p, real_p in missing_paths
            }
            to_update = []
            for fut, (p, real_p) in future_to_path.items():
                try:
                    q_hash = fut.result()
                    if q_hash:
                        hashes[p] = q_hash
                        to_update.append((q_hash, real_p))
                except Exception as e:
                    logger.error(f"Error hashing {real_p}: {e}")

            if to_update:
                with db_conn() as conn, conn:
                    conn.executemany(
                        "UPDATE files SET quick_hash = ? WHERE path = ?", to_update
                    )

    unique_hashes = set(hashes.values())
    all_match = len(unique_hashes) == 1 and len(hashes) == len(req.paths)
    return {"success": True, "all_match": all_match, "hashes": hashes}


@router.post("/api/backup/duplicates/resolve")
def resolve_duplicate_group(req: DuplicateResolveRequest):
    return resolve_duplicates_service(req.keep_path, req.delete_paths)


@router.post("/api/backup/duplicates/autodelete-verified")
def autodelete_verified_duplicates():
    return autodelete_service()
