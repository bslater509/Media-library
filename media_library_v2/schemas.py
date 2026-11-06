"""Pydantic schemas for the v2 API.

Every endpoint declares an explicit response model, so the OpenAPI document is
a real contract that the TypeScript client is generated from.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Breadcrumb(BaseModel):
    name: str
    path: str
    is_zip: bool = False
    raw_name: str | None = None


class FolderEntry(BaseModel):
    name: str
    raw_name: str
    path: str
    file_count: int
    subfolder_count: int
    is_zip: bool
    total_size: int
    size_formatted: str
    cover_thumb_url: str | None = None
    cover_width: int | None = None
    cover_height: int | None = None


class FileEntry(BaseModel):
    id: str
    name: str
    path: str
    folder_path: str
    type: str
    ext: str
    size: int
    size_formatted: str
    mtime: float | None = None
    width: int | None = None
    height: int | None = None
    duration: float | None = None
    video_codec: str | None = None
    audio_codec: str | None = None
    fps: float | None = None
    capture_time: float | None = None
    has_thumb: bool = False
    has_animated: bool = False
    is_favorite: bool = False
    needs_transcode: bool = False
    playable: bool = True
    thumb_url: str
    animated_url: str | None = None
    media_url: str


class SearchFileEntry(FileEntry):
    folder_name: str
    rel_folder: str


class BrowseResponse(BaseModel):
    path: str
    root: str
    name: str
    breadcrumbs: list[Breadcrumb]
    folders: list[FolderEntry]
    files: list[FileEntry]
    total: int
    offset: int
    limit: int
    next_offset: int | None = None
    indexed: bool = True


class FoldersResponse(BaseModel):
    path: str
    root: str
    folders: list[FolderEntry]


class FolderSearchResponse(BaseModel):
    query: str
    results: list[FolderEntry]


class SearchResponse(BaseModel):
    query: str
    total: int
    limit: int
    offset: int
    truncated: bool
    results: list[SearchFileEntry]


class Counts(BaseModel):
    files: int
    folders: int
    videos: int
    images: int
    favorites: int


class InfoResponse(BaseModel):
    version: str
    library_root: str
    counts: Counts
    features: dict[str, bool] = Field(default_factory=dict)


class IndexStatus(BaseModel):
    files: int
    folders: int
    videos: int
    images: int


class StatusResponse(BaseModel):
    version: str
    database: str
    index: IndexStatus
    workers: dict[str, str]


class HealthResponse(BaseModel):
    status: str
    version: str
    database: str


class FavoritesResponse(BaseModel):
    counts: Counts
    files: list[FileEntry]


class ThumbnailCandidates(BaseModel):
    path: str
    duration: float | None = None
    interval: float
    frames: list[dict[str, float | str]]
