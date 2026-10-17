"""FastAPI router for Media Library favorites / bookmarking."""

import logging
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from media_library.db.connection import db_conn
from media_library.services.library_query import read_indexed_favorites

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

    return {"status": "success", "path": path, "is_favorite": is_fav}


@router.post("/api/backup/favorites/batch")
def batch_favorites(req: FavoriteBatchRequest) -> dict[str, Any]:
    """Batch star, unstar, or toggle favorite status on multiple files."""
    if not req.paths:
        return {"status": "success", "updated": 0, "action": req.action}

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

    return {"status": "success", "updated": updated, "action": req.action}


@router.get("/api/backup/favorites")
def get_favorites(type: str | None = Query(None)) -> dict[str, Any]:
    """Returns all favorited files in the library, sorted by favorited timestamp descending."""
    media_type = type if type in ("video", "image") else None
    with db_conn() as conn:
        # Get all favorites to calculate type counts
        all_favs = read_indexed_favorites(conn, None)
        total_count = len(all_favs)
        video_count = sum(1 for f in all_favs if f.get("type") == "video")
        image_count = sum(1 for f in all_favs if f.get("type") == "image")

        files = (
            all_favs
            if media_type is None
            else [f for f in all_favs if f.get("type") == media_type]
        )

    return {
        "files": files,
        "counts": {
            "all": total_count,
            "videos": video_count,
            "images": image_count,
        },
    }


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
