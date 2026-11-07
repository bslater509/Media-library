"""Library browse, folder tree, search and favorites endpoints."""

from __future__ import annotations

import asyncio
import sqlite3

from fastapi import APIRouter, HTTPException, Query

from media_library import library
from media_library.schemas import (
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
    type: str | None = Query(None),
    favorite: bool = Query(False),
    sort: str = Query("name_asc"),
) -> dict:
    """List subfolders and media in one folder, index-only."""
    try:
        # Off the event loop: browse confirms the target is a real directory,
        # which is one FUSE round trip the loop must not wait on (Rule 2).
        return await asyncio.to_thread(
            library.browse,
            path,
            offset,
            limit,
            type,
            favorite,
            sort,
        )
    except sqlite3.Error as exc:
        raise _guard(exc) from exc


@router.get("/library/folders", response_model=FoldersResponse)
async def folders(path: str | None = Query(None)) -> dict:
    """List a folder's immediate children for the navigation tree."""
    try:
        return await asyncio.to_thread(library.list_folders, path)
    except sqlite3.Error as exc:
        raise _guard(exc) from exc


@router.get("/library/folders/search", response_model=FolderSearchResponse)
async def folder_search(
    q: str = Query(..., min_length=1),
    limit: int = Query(20, ge=1, le=50),
) -> dict:
    """Find folders by name for the command palette."""
    try:
        # Off the event loop: LIKE scans block the loop on a large index.
        return await asyncio.to_thread(library.search_folders, q, limit=limit)
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
        # Off the event loop: a multi-word substring search is a full table
        # scan on tens of thousands of rows.
        return await asyncio.to_thread(
            library.search,
            q,
            type_filter=type,
            sort=sort,
            limit=limit,
            offset=offset,
        )
    except sqlite3.Error as exc:
        raise _guard(exc) from exc


@router.get("/library/favorites", response_model=FavoritesResponse)
async def favorites(
    type: str | None = Query(None),
    limit: int | None = Query(None, ge=1, le=5000),
    offset: int = Query(0, ge=0),
) -> dict:
    """List favorited media, newest star first."""
    try:
        return await asyncio.to_thread(
            library.favorites, type, limit=limit, offset=offset
        )
    except sqlite3.Error as exc:
        raise _guard(exc) from exc
