"""Pydantic schemas for the v2 API.

Every endpoint declares an explicit response model, so the OpenAPI document is
a real contract that the TypeScript client is generated from.
"""

from __future__ import annotations

from typing import Any

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
    is_zip: bool = False


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


# --- mutations -------------------------------------------------------------


class FavoriteRequest(BaseModel):
    path: str


class FavoriteCounts(BaseModel):
    all: int
    videos: int
    images: int


class FavoriteResponse(BaseModel):
    path: str
    is_favorite: bool
    counts: FavoriteCounts


class RenameRequest(BaseModel):
    path: str
    name: str


class RenameResponse(BaseModel):
    path: str
    file: FileEntry | None = None


class DeleteRequest(BaseModel):
    paths: list[str] = Field(min_length=1, max_length=500)


class Failure(BaseModel):
    path: str
    error: str


class DeleteResponse(BaseModel):
    deleted: list[str]
    failed: list[Failure]


class CreateFolderRequest(BaseModel):
    parent: str
    name: str


class CreateFolderResponse(BaseModel):
    path: str
    name: str


class MoveRequest(BaseModel):
    paths: list[str] = Field(min_length=1, max_length=500)
    destination: str
    conflict: str = "auto_rename"


class MoveItem(BaseModel):
    source: str | None = None
    target: str | None = None
    path: str | None = None
    reason: str | None = None


class MoveResponse(BaseModel):
    moved: list[MoveItem]
    skipped: list[MoveItem]
    failed: list[Failure]


class UploadStatusResponse(BaseModel):
    upload_id: str
    exists: bool
    received_bytes: int


class UploadChunkResponse(BaseModel):
    received_bytes: int
    duplicate: bool


class UploadFinalizeRequest(BaseModel):
    upload_id: str
    name: str
    folder: str
    total_size: int | None = None
    conflict: str = "auto_rename"


class UploadFinalizeResponse(BaseModel):
    skipped: bool
    path: str
    file: FileEntry | None = None


class UploadCancelRequest(BaseModel):
    upload_id: str


class UploadCancelResponse(BaseModel):
    cancelled: bool


# --- sorter ----------------------------------------------------------------


class SorterItem(BaseModel):
    id: str
    name: str
    path: str
    size: int
    size_formatted: str
    mtime: float
    ext: str
    playable: bool
    needs_transcode: bool
    stream_url: str
    transcode_url: str | None = None


class SorterDestination(BaseModel):
    path: str
    name: str
    file_count: int | None = None
    subfolder_count: int | None = None


class SorterQueueResponse(BaseModel):
    source_dir: str
    available: bool
    items: list[SorterItem]
    undo_count: int
    moved: dict | None = None
    restored: str | None = None
    path: str | None = None


class SorterConfigResponse(BaseModel):
    source_dir: str
    source_available: bool
    library_root: str
    recent_destinations: list[SorterDestination]
    queue_count: int
    undo_count: int


class SorterMoveRequest(BaseModel):
    path: str
    destination: str


class SorterRenameRequest(BaseModel):
    path: str
    name: str


class SorterPathRequest(BaseModel):
    path: str


class SorterFolderRequest(BaseModel):
    parent: str
    name: str


# --- downloads -------------------------------------------------------------


class DownloadJob(BaseModel):
    id: str
    url: str
    destination: str
    status: str
    percent: float
    downloaded_bytes: int
    total_bytes: int
    speed: str
    eta: str
    title: str
    downloader: str
    error: str
    files: list[str]
    playlist_index: int | None = None
    playlist_count: int | None = None
    created_at: float
    started_at: float | None = None
    finished_at: float | None = None


class DownloadRejected(BaseModel):
    url: str
    error: str


class DownloadEnqueueRequest(BaseModel):
    urls: list[str] = Field(min_length=1, max_length=50)
    destination: str


class DownloadEnqueueResponse(BaseModel):
    jobs: list[DownloadJob]
    rejected: list[DownloadRejected]


class DownloadTools(BaseModel):
    ytdlp: bool
    gallery_dl: bool


class DownloadStatusResponse(BaseModel):
    status: str
    concurrency: int
    tools: DownloadTools
    active: list[DownloadJob]
    queued: list[DownloadJob]
    active_count: int
    queued_count: int
    history: list[DownloadJob]


class DownloadHistoryResponse(BaseModel):
    items: list[DownloadJob]
    total: int
    limit: int
    offset: int


class DownloadJobRequest(BaseModel):
    job_id: str


class DownloadRetryResponse(BaseModel):
    job: DownloadJob


class DownloadClearResponse(BaseModel):
    cleared: int


# --- analytics -------------------------------------------------------------


class DedupeGroup(BaseModel):
    id: str
    match_type: str
    size: int
    size_formatted: str
    reclaimable: int
    files: list[FileEntry]


class DedupeGroups(BaseModel):
    mode: str
    total_groups: int
    total_files: int
    unhashed: int
    groups: list[DedupeGroup]


class DedupeStatus(BaseModel):
    content: dict[str, int]
    visual: dict[str, int]
    ignored: int


class DedupeHashRequest(BaseModel):
    mode: str = "content"
    limit: int = Field(default=50, ge=1, le=200)


class DedupeHashResponse(BaseModel):
    hashed: int
    failed: int = 0
    remaining: int


class DedupeVerifyRequest(BaseModel):
    keep: str
    path: str


class DedupeVerifyResponse(BaseModel):
    keep: str
    path: str
    identical: bool


class DedupeResolveRequest(BaseModel):
    keep: str
    delete: list[str] = Field(min_length=1, max_length=500)
    mode: str = "content"


class DedupeResolveResponse(BaseModel):
    kept: str
    deleted: list[str]
    failed: list[Failure]
    mismatched: list[str]


class DedupeIgnoreRequest(BaseModel):
    path1: str
    path2: str
    match_type: str = "content"


class DedupeIgnoreResponse(BaseModel):
    ignored: bool
    path1: str
    path2: str
    match_type: str


class DedupeUnignoreRequest(BaseModel):
    path1: str
    path2: str


class DedupeUnignoreResponse(BaseModel):
    unignored: bool
    path1: str
    path2: str


class IgnoredPair(BaseModel):
    id: int
    path1: str
    path2: str
    match_type: str
    created_at: float


class IgnoredPairsPage(BaseModel):
    items: list[IgnoredPair]
    total: int
    limit: int
    offset: int


class WebpStatus(BaseModel):
    pending: int
    saved_bytes: int
    saved_formatted: str
    converted: int
    skipped: int
    quality: int
    skip_larger: bool
    scope: str
    updated_at: float


class WebpOptimizeRequest(BaseModel):
    limit: int = Field(default=20, ge=1, le=100)
    folder: str | None = None


class WebpOptimizeResponse(BaseModel):
    converted: int
    skipped: int
    failed: int
    saved_bytes: int
    saved_formatted: str
    remaining: int
    errors: list[dict[str, str]]
    quality: int
    skip_larger: bool


class EmptyFolder(BaseModel):
    path: str
    name: str
    parent_path: str | None = None


class EmptyFoldersResponse(BaseModel):
    count: int
    folders: list[EmptyFolder]


class EmptyDeleteRequest(BaseModel):
    paths: list[str] = Field(min_length=1, max_length=1000)


class EmptyDeleteResponse(BaseModel):
    deleted: list[str]
    failed: list[Failure]
    remaining: int


class ZipCheck(BaseModel):
    path: str
    target: str
    eligible: bool
    images: int
    videos: int
    reason: str


class ZipCompressRequest(BaseModel):
    path: str


class ZipCompressResponse(BaseModel):
    archive: str
    members: int
    bytes: int
    size_formatted: str
    removed: str


class ZipUnpackRequest(BaseModel):
    path: str


class ZipUnpackResponse(BaseModel):
    folder: str
    files: int
    removed: str


class ZipCandidate(BaseModel):
    path: str
    name: str
    file_count: int
    size_formatted: str


class ZipArchive(BaseModel):
    path: str
    name: str
    file_count: int
    size_formatted: str


class ZipOverview(BaseModel):
    folders: list[ZipCandidate]
    archives: list[ZipArchive]
    cache: dict[str, Any]


class BackupStatus(BaseModel):
    exists: bool
    directory: str
    created_at: float | None = None
    created_at_str: str | None = None
    version: str | None = None
    files: int | None = None
    folders: int | None = None
    thumbnails: int | None = None
    db_bytes: int
    db_size_formatted: str
    tar_bytes: int
    tar_size_formatted: str
    allow_live_restore: bool


class BackupSnapshotResponse(BaseModel):
    success: bool
    created_at: float
    files: int
    folders: int
    thumbnails: int
    db_bytes: int
    tar_bytes: int
    directory: str


class BackupVerifyResponse(BaseModel):
    snapshot_integrity: str
    live_integrity: str
    files: int
    folders: int
    thumbnails: int
    meta_present: bool


class BackupRestoreResponse(BaseModel):
    restored: bool
    database: str
    thumbnails: int
    files: int
    folders: int
    restart_required: bool


class AnalyticsOverview(BaseModel):
    generated_at: float
    library_root: str
    limits: dict[str, int]
    totals: dict[str, Any]
    by_type: list[dict[str, Any]]
    by_extension: dict[str, Any]
    folders: dict[str, Any]
    largest_files: list[FileEntry]
    quality: dict[str, Any]
    reclaimable: dict[str, Any]
    health: dict[str, Any]
