"""Library browse, folder tree, search and favorites endpoints."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, HTTPException, Query

from media_library_v2 import library
from media_library_v2.schemas import (
    BrowseResponse,
    FavoritesResponse,
    FolderSearchResponse,
    FoldersResponse,
    SearchResponse,
)

router = APIRouter()


def _guard(exc: sqlite3.Error) -> HTTPException:
    return HTTPException(status_code=503, detail=f"Index unavailable: {exc}")


@router.get("/library/browse", response_model=BrowseResponse)
async def browse(
    path: str | None = Query(None),
    offset: int = Query(0, ge=0),
    limit: int | None = Query(None, ge=1, le=500),
) -> dict:
    """List subfolders and media in one folder, index-only."""
    try:
        return library.browse(path, offset=offset, limit=limit)
    except sqlite3.Error as exc:
        raise _guard(exc) from exc


@router.get("/library/folders", response_model=FoldersResponse)
async def folders(path: str | None = Query(None)) -> dict:
    """List a folder's immediate children for the navigation tree."""
    try:
        return library.list_folders(path)
    except sqlite3.Error as exc:
        raise _guard(exc) from exc


@router.get("/library/folders/search", response_model=FolderSearchResponse)
async def folder_search(
    q: str = Query(..., min_length=1),
    limit: int = Query(20, ge=1, le=50),
) -> dict:
    """Find folders by name for the command palette."""
    try:
        return library.search_folders(q, limit=limit)
    except sqlite3.Error as exc:
        raise _guard(exc) from exc


@router.get("/library/search", response_model=SearchResponse)
async def search(
    q: str = Query(..., min_length=1),
    type: str | None = Query(None),
    sort: str = Query("name_asc"),
    limit: int | None = Query(None, ge=1, le=2000),
    offset: int = Query(0, ge=0),
) -> dict:
    """Search filenames and library-relative folder paths."""
    try:
        return library.search(
            q, type_filter=type, sort=sort, limit=limit, offset=offset
        )
    except sqlite3.Error as exc:
        raise _guard(exc) from exc


@router.get("/library/favorites", response_model=FavoritesResponse)
async def favorites(type: str | None = Query(None)) -> dict:
    """List favorited media, newest star first."""
    try:
        return library.favorites(type_filter=type)
    except sqlite3.Error as exc:
        raise _guard(exc) from exc
