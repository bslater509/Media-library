"""FastAPI router for Media Library favorites / bookmarking."""

import logging
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from media_library.db.connection import db_conn
from media_library.services.library_query import (
    read_favorites_counts,
    read_indexed_favorites,
)

router = APIRouter()

logger = logging.getLogger(__name__)


class FavoriteToggleRequest(BaseModel):
    path: str


class FavoriteBatchRequest(BaseModel):
    paths: list[str]
    action: str = "toggle"  # "add" | "remove" | "toggle"


@router.post("/api/backup/favorites/toggle")
def toggle_favorite(req: FavoriteToggleRequest) -> dict[str, Any]:
    """Toggles favorite status for a single media file."""
    path = req.path.strip()
    if not path:
        raise HTTPException(status_code=400, detail="Path cannot be empty")

    with db_conn() as conn, conn:
        cur = conn.execute("SELECT 1 FROM favorites WHERE path = ?", (path,))
        exists = cur.fetchone() is not None
        if exists:
            conn.execute("DELETE FROM favorites WHERE path = ?", (path,))
            is_fav = False
        else:
            conn.execute(
                "INSERT OR REPLACE INTO favorites (path, created_at) VALUES (?, ?)",
                (path, time.time()),
            )
            is_fav = True
        # The new totals ride along so the client can update its badge without a
        # second request (the whole reason the count stays cheap).
        counts = read_favorites_counts(conn)

    return {
        "status": "success",
        "path": path,
        "is_favorite": is_fav,
        "counts": counts,
    }


@router.post("/api/backup/favorites/batch")
def batch_favorites(req: FavoriteBatchRequest) -> dict[str, Any]:
    """Batch star, unstar, or toggle favorite status on multiple files."""
    if not req.paths:
        with db_conn() as conn:
            counts = read_favorites_counts(conn)
        return {
            "status": "success",
            "updated": 0,
            "action": req.action,
            "counts": counts,
        }

    now = time.time()
    updated = 0
    with db_conn() as conn, conn:
        for p in req.paths:
            path = p.strip()
            if not path:
                continue
            cur = conn.execute("SELECT 1 FROM favorites WHERE path = ?", (path,))
            exists = cur.fetchone() is not None

            if req.action == "add":
                if not exists:
                    conn.execute(
                        "INSERT OR REPLACE INTO favorites (path, created_at) VALUES (?, ?)",
                        (path, now),
                    )
                    updated += 1
            elif req.action == "remove":
                if exists:
                    conn.execute("DELETE FROM favorites WHERE path = ?", (path,))
                    updated += 1
            elif req.action == "toggle":
                if exists:
                    conn.execute("DELETE FROM favorites WHERE path = ?", (path,))
                else:
                    conn.execute(
                        "INSERT OR REPLACE INTO favorites (path, created_at) VALUES (?, ?)",
                        (path, now),
                    )
                updated += 1

        counts = read_favorites_counts(conn)

    return {
        "status": "success",
        "updated": updated,
        "action": req.action,
        "counts": counts,
    }


@router.get("/api/backup/favorites/count")
def get_favorites_count() -> dict[str, Any]:
    """Favorites totals only (`all`/`videos`/`images`), index-only.

    The sidebar badge refreshes on page load and after every star toggle; a
    single aggregate over the ``favorites`` join answers it in milliseconds,
    where the full ``GET /api/backup/favorites`` list would shape and ship every
    favorite just to read a count. The Library falls back to the list endpoint
    when it actually needs the rows (the Favorites view).
    """
    with db_conn() as conn:
        return {"counts": read_favorites_counts(conn)}


@router.get("/api/backup/favorites")
def get_favorites(type: str | None = Query(None)) -> dict[str, Any]:
    """Returns all favorited files in the library, sorted by favorited timestamp descending."""
    media_type = type if type in ("video", "image") else None
    with db_conn() as conn:
        # Only the requested media type is read; the totals still cover all
        # favorites so the badge counts do not change with the filter.
        files = read_indexed_favorites(conn, media_type)
        counts = read_favorites_counts(conn)

    return {"files": files, "counts": counts}


@router.get("/api/backup/favorites/status")
def get_favorite_status(path: str = Query(...)) -> dict[str, Any]:
    """Checks whether a single file is marked as a favorite."""
    path = path.strip()
    if not path:
        return {"path": path, "is_favorite": False}

    with db_conn() as conn:
        cur = conn.execute("SELECT 1 FROM favorites WHERE path = ?", (path,))
        is_fav = cur.fetchone() is not None

    return {"path": path, "is_favorite": is_fav}
