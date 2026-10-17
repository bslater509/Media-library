"""Face recognition / People API.

Serves the ``/people`` page and its curation actions on top of
:mod:`media_library.services.face_service`. Every read is index-only (SQLite
plus the local face-crop cache), so nothing here walks the mount or stats a
remote file.

The feature is opt-in (``FACE_RECOGNITION_ENABLED``): reads stay available so an
existing index can still be browsed and curated, but the endpoints that start
real work (``/api/people/scan``) refuse until it is enabled.
"""

import logging
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query
from fastapi.responses import FileResponse

from media_library.db.connection import db_conn
from media_library.services import face_service
from media_library.workers.face_worker import face_worker_instance

logger = logging.getLogger(__name__)

router = APIRouter(tags=["people"])


@router.get("/api/people/status")
async def people_status() -> dict[str, Any]:
    """Scanner + engine + cluster summary (also embedded in /api/system/status)."""
    return face_service.face_status_payload()


@router.get("/api/people")
async def list_people(
    limit: int = Query(200, ge=1, le=2000),
    offset: int = Query(0, ge=0),
    named: bool | None = Query(
        None,
        description="Only clusters with (true) or without (false) a name; omit for both",
    ),
    near: int | None = Query(
        None,
        ge=1,
        description="Rank clusters sharing folders with this person's faces first",
    ),
) -> dict[str, Any]:
    """Person clusters with derived counts and cover crops, biggest first.

    The merge picker asks for ``named=false`` (those are the clusters that can
    safely be folded into another one, since a named person would lose its name)
    plus ``near=<the cluster it was opened from>``: media is filed per folder, so
    the cluster it is really the same person as is usually filed right next to
    it, and ranking same-folder clusters first surfaces it instead of burying it
    under thousands of unrelated ones.
    """
    with db_conn() as conn:
        people = face_service.read_people(
            conn, limit=limit, offset=offset, named=named, near=near
        )
        counts = face_service.people_counts(conn)
        total = face_service.count_people(conn, named=named)
    return {
        "people": people,
        "counts": counts,
        "total": total,
        "limit": limit,
        "offset": offset,
        "status": face_service.face_status_payload(),
    }


# Declared before ``/api/people/{person_id}`` so the literal path wins over the
# parameterised one.
@router.get("/api/people/crop")
async def face_crop(face_id: int = Query(..., ge=1)) -> FileResponse:
    """Stream a stored face crop from the local cache (never touches the mount)."""
    try:
        with db_conn() as conn:
            row = conn.execute(
                "SELECT crop FROM faces WHERE id = ?", (face_id,)
            ).fetchone()
    except Exception as e:
        logger.error(f"[People] Crop lookup failed: {e}")
        raise HTTPException(status_code=404, detail="Face not found") from e
    if row is None:
        raise HTTPException(status_code=404, detail="Face not found")
    path = face_service.crop_path(row["crop"])
    if path is None:
        raise HTTPException(status_code=404, detail="Face crop not cached")
    return FileResponse(
        str(path),
        media_type="image/jpeg",
        headers={"Cache-Control": "public, max-age=604800, immutable"},
    )


@router.get("/api/people/{person_id}")
async def person_detail(
    person_id: int,
    limit: int = Query(200, ge=1, le=2000),
    offset: int = Query(0, ge=0),
    sort: str = Query("count"),
) -> dict[str, Any]:
    """One person: their media (grouped by file, videos carry their moments)."""
    with db_conn() as conn:
        row = conn.execute(
            "SELECT id, name, created_at FROM people WHERE id = ?", (person_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Unknown person")
        faces = face_service.read_person_faces(conn, person_id, limit=24, offset=0)
        face_count = face_service.count_person_faces(conn, person_id)
        media = face_service.read_person_media(
            conn, person_id, limit=limit, offset=offset, sort=sort
        )
    return {
        "id": int(row["id"]),
        "name": row["name"],
        "created_at": row["created_at"],
        "face_count": face_count,
        "media": media["files"],
        "media_total": media["total"],
        "truncated": media["truncated"],
        "offset": offset,
        "limit": limit,
        "sort": sort,
        "faces": faces,
    }


@router.get("/api/people/{person_id}/faces")
async def person_faces(
    person_id: int,
    limit: int = Query(120, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    """The review grid: this person's faces, best score first."""
    with db_conn() as conn:
        faces = face_service.read_person_faces(
            conn, person_id, limit=limit, offset=offset
        )
        total = face_service.count_person_faces(conn, person_id)
    return {"faces": faces, "total": total, "limit": limit, "offset": offset}


@router.post("/api/people/rename")
async def rename_person(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Name a cluster (an empty name turns it back into an unnamed cluster)."""
    person_id = int(payload.get("person_id") or 0)
    if not person_id:
        raise HTTPException(status_code=422, detail="person_id is required")
    ok = face_service.rename_person(person_id, payload.get("name"))
    if not ok:
        raise HTTPException(status_code=404, detail="Unknown person")
    return {
        "success": True,
        "person_id": person_id,
        "name": (payload.get("name") or "").strip(),
    }


@router.post("/api/people/merge")
async def merge_people(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Fold one or more clusters into a target cluster."""
    target = int(payload.get("target_id") or 0)
    sources = [int(s) for s in (payload.get("source_ids") or []) if int(s) != target]
    if not target:
        raise HTTPException(status_code=422, detail="target_id is required")
    if not sources:
        raise HTTPException(
            status_code=422, detail="source_ids must name at least one other cluster"
        )
    result = face_service.merge_people(target, sources)
    if not result.get("success"):
        raise HTTPException(
            status_code=404, detail=result.get("error") or "Merge failed"
        )
    return result


@router.post("/api/people/split")
async def split_faces(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Pull faces out of their clusters into a new (optionally named) cluster."""
    face_ids = [int(f) for f in (payload.get("face_ids") or [])]
    if not face_ids:
        raise HTTPException(status_code=422, detail="face_ids is required")
    result = face_service.split_faces(face_ids, payload.get("name"))
    if not result.get("success"):
        raise HTTPException(
            status_code=404, detail=result.get("error") or "Split failed"
        )
    return result


@router.post("/api/people/dismiss")
async def dismiss_faces(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Mark faces (or a whole cluster) as "not a person"."""
    person_id = payload.get("person_id")
    if person_id:
        result = face_service.dismiss_person(int(person_id))
        if not result.get("success"):
            raise HTTPException(
                status_code=400, detail=result.get("error") or "Dismiss failed"
            )
        return result
    face_ids = [int(f) for f in (payload.get("face_ids") or [])]
    if not face_ids:
        raise HTTPException(status_code=422, detail="face_ids or person_id is required")
    return {
        "success": True,
        "dismissed": face_service.dismiss_faces(face_ids),
        "removed": 0,
    }


@router.post("/api/people/threshold")
async def set_threshold(payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    """Set the match strictness (a preset name or an explicit threshold)."""
    preset = (payload.get("preset") or "").strip().lower()
    if preset:
        if preset not in face_service.THRESHOLD_PRESETS:
            raise HTTPException(
                status_code=422,
                detail="preset must be one of "
                + ", ".join(sorted(face_service.THRESHOLD_PRESETS)),
            )
        value = face_service.set_threshold(face_service.THRESHOLD_PRESETS[preset])
    elif payload.get("threshold") is not None:
        value = face_service.set_threshold(float(payload["threshold"]))
    else:
        raise HTTPException(status_code=422, detail="Pass preset or threshold")
    return {
        "success": True,
        "threshold": value,
        "preset": face_service.threshold_preset_name(value),
        "presets": dict(face_service.THRESHOLD_PRESETS),
    }


@router.post("/api/people/recluster")
async def recluster(payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    """Re-group every face, re-attaching existing names by nearest centroid."""
    threshold = payload.get("threshold")
    result = face_service.recluster_people(
        float(threshold) if threshold is not None else None
    )
    if not result.get("success"):
        raise HTTPException(
            status_code=409, detail=result.get("error") or "Re-cluster failed"
        )
    return result


@router.post("/api/people/scan")
async def scan(payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    """Queue (or re-queue) a folder, a path list, or the whole library."""
    if not face_service.is_enabled():
        raise HTTPException(
            status_code=409,
            detail="Face recognition is disabled (set FACE_RECOGNITION_ENABLED=true)",
        )

    folder = (payload.get("folder") or "").strip()
    paths = [str(p) for p in (payload.get("paths") or []) if str(p).strip()]
    scan_all = bool(payload.get("all"))
    queued = 0
    try:
        with db_conn() as conn, conn:
            if paths:
                for start in range(0, len(paths), 200):
                    chunk = paths[start : start + 200]
                    placeholders = ",".join("?" for _ in chunk)
                    cur = conn.execute(
                        f"DELETE FROM face_scans WHERE file_path IN ({placeholders})",
                        chunk,
                    )
                    queued += max(0, cur.rowcount or 0)
            elif folder:
                prefix = folder.rstrip("/") + "/"
                cur = conn.execute(
                    "DELETE FROM face_scans WHERE file_path = ? OR file_path LIKE ?",
                    (folder, prefix + "%"),
                )
                queued += max(0, cur.rowcount or 0)
            elif scan_all:
                cur = conn.execute("DELETE FROM face_scans")
                queued += max(0, cur.rowcount or 0)
            else:
                raise HTTPException(
                    status_code=422, detail="Pass folder, paths or all=true"
                )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[People] Could not queue a face scan: {e}")
        raise HTTPException(status_code=500, detail="Could not queue the scan") from e

    if face_worker_instance.is_paused():
        face_worker_instance.resume()
    return {
        "success": True,
        "queued": queued,
        "scope": "paths" if paths else ("folder" if folder else "all"),
    }


@router.post("/api/people/pause")
async def pause_scan(payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    """Pause or resume the face-scanning sweep."""
    paused = bool(payload.get("paused", True))
    if paused:
        face_worker_instance.pause()
    else:
        face_worker_instance.resume()
    return {"success": True, "is_paused": face_worker_instance.is_paused()}
