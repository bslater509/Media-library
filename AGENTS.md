# 🤖 AGENTS.md — Media Library & Video Sorter Agent Guide

> **CRITICAL DIRECTIVE FOR ALL AI AGENTS**:
> **This document is the authoritative architectural and operational specification for this codebase. It MUST ALWAYS BE KEPT UP TO DATE.**
> Whenever you add, modify, or remove API endpoints, database schema fields, background workers, environment variables, or core workflows, you **MUST** update this file in the same task to reflect your changes.

---

## 1. System Overview & Technology Stack

**Media Library & Video Sorter** is a high-performance, web-based media asset management system and rapid keyboard-driven video sorter designed to handle tens of thousands of video and image files efficiently across local disks and remote FUSE mounts (e.g. pCloud, network shares).

### Core Capabilities
1. **Media Library & Gallery View**:
   - Sub-millisecond SQLite-indexed browsing, folder hierarchy navigation, and multi-word full library search.
   - Row-ordered natural-aspect-ratio layout for media files and folder covers: tiles are packed into **justified rows** — every tile in a row shares one row height chosen so the row fills the container width exactly — so reading order runs strictly left to right, then down. Tile widths follow probed `width`/`height` clamped to 2:3–3:2, unknown dimensions fall back to a 1:1 box filled with `object-cover`, and a trailing partial row keeps the target height until more items arrive. A thumbnail-size control in the library toolbar steps through five presets (XS–XL) that scale the row height; the choice is persisted in `localStorage` (`lib_tile_scale`).
   - Proactive background thumbnail/poster frame generation using FFmpeg.
   - Fullscreen lightbox viewer with HTTP 206 byte-range media streaming, scrubbing, loop toggle, inline rename, and deletion.
   - File management: right-click (desktop) or tap-and-hold (mobile) context menu with Move / Rename / Delete; multi-select (checkbox, Ctrl/Cmd-click, Shift-range, a Select All / Deselect All toolbar button, or Ctrl/Cmd+A) bulk Move / Delete / Rename / WebP; plus folder creation, rename, and recursive delete.
2. **Incoming Video Sorter Workflow**:
   - High-speed review (<50ms per item) with instant pre-buffering and autoplay.
   - 1-touch keyboard moves (`1`–`6` for recent folders, `/` for category search, `Enter` to commit) plus the "+ Create & Move" button for instant folder creation.
   - Asynchronous non-blocking file transfers with full smart Undo stack (`U` / `Ctrl+Z`).
3. **Automatic Transcoding**:
   - On-the-fly and background pre-transcoding of non-web formats (`.mkv`, `.avi`, `.wmv`, `.flv`, `.mov`) into web-native MP4 using stream copying or H.264 fallback.
4. **Automatic WebP Image Optimization**:
   - Background worker converting PNG, JPG, and other image formats to optimized WebP.
   - Atomic replacement with integrity checks and space-savings accounting.
5. **ZIP Archive Virtual Folders**:
   - Browsing ZIP archives as virtual folders without extraction via central directory inspection (<50ms).
   - On-demand extraction to a fast local cache with automatic LRU cache management.
   - Folder-to-ZIP compression for image folders, ZIP unpack back to disk, debounced sync-back for modified archives, batch ZIP compression across the library, and batch extraction ("Extract All ZIPs") of indexed archives back into folders with start/pause/resume/cancel and per-archive progress.
6. **Deduplication Engine**:
   - Exact file content matching via 128 KB head-hash + size fast hashing (full-content verification performed before any auto-delete).
   - Visual duplicate matching via perceptual hashing (`pHash`).
   - Group verification, automated resolution, and ignore/unignore false positive rules.
7. **Zero-Work Recovery & Snapshot Backups**:
   - Non-destructive SQLite live snapshotting (`sqlite3_backup`) and uncompressed thumbnail bundling directly into `<LIBRARY_DIR>/.media_library_backup/`.
   - Automatic restore on startup if local database or thumbnails are missing.
   - Clean shutdown snapshot creation.

### Tech Stack
- **Language & Runtime**: Python 3.9+ (tested on Python 3.13)
- **Web Framework**: FastAPI (v0.110+) with Uvicorn ASGI server and Starlette
- **Concurrency & Threads**: AnyIO (expanded threadpool tokens to 100), asyncio, Python `threading`
- **Database**: SQLite 3 with Write-Ahead Logging (`WAL`), `synchronous=NORMAL`, memory temp store, mmap, and optimized index structures
- **Media Processing**: FFmpeg & FFprobe (CLI subprocesses), Pillow (PIL), ImageHash, NumPy, SciPy
- **Frontend**: Vanilla JavaScript (ES6+), raw HTML templates (served via `HTMLResponse`, no Jinja2 dependency), CSS3, native HTML5 Video
- **Testing**: Pytest (v8.0+) with `conftest.py` sandboxing fixtures

---

## 2. Agent Rules of Engagement & Maintenance Contract

All AI assistants and autonomous coding agents working on this repository must strictly adhere to the following operational rules:

### Rule 1: The Documentation Sync Requirement
Whenever you touch code in this repository:
1. **New or Modified Routes**: Update the **API Routing & Endpoint Map** section below.
2. **Schema or DB Changes**: Update the **Database Architecture & Schema** section.
3. **Worker or Background Tasks**: Update the **Background Workers** section.
4. **Configuration / Environment Variables**: Update the **Configuration Reference** section and `.env.example`.
5. **Operational Workflows**: Update relevant architecture sections.

### Rule 2: FUSE & Remote Filesystem Discipline
The target media collection may reside on a remote FUSE filesystem (such as pCloud or NFS). Remote FUSE mounts have high round-trip latency:
- **Never perform unbounded synchronous `os.walk` or recursive scans in API request handlers.** Always query the SQLite index or use background async tasks (e.g. `/api/backup/browse` schedules missing-folder indexing on a background thread and returns immediately with `indexing: true`).
- **Never repeatedly `os.stat` remote files.** Use `media_library.core.state._file_stat_cache` or SQLite metadata whenever possible.
- **Never treat a transient `stat`/`os.path.exists` failure as a deletion.** Remote mounts fail intermittently; index pruning must confirm absence with retries (`media_library.db.indexer._path_confirmed_gone`) and abort when an implausibly large share of the library looks missing.
- **Always perform file writes atomically.** Write to a temporary file (`.tmp_*`) in the same filesystem directory first, verify completion, and call `os.replace()` to avoid partial writes or corrupted files over FUSE.
- **Guard against path traversal.** Always validate input paths using `media_library.services.media_service.validate_backup_path()` or `Path.resolve().relative_to()`.

### Rule 3: Database Concurrency & Connection Discipline
- SQLite is operated in WAL mode. Always obtain connections using `get_db(custom_path=None)` or, for scoped work, the `db_conn()` context manager (which always closes the connection in `finally`).
- Always wrap database writes in a transaction block: `with conn: conn.execute(...)`.
- Always close database connections in a `finally` block: `conn.close()`.
- Never hold a database connection open across `await` points or long-running network/disk operations.
- Schema migrations must be idempotent: modify `init_backup_db()` in `media_library/db/schema.py` using `ALTER TABLE ... ADD COLUMN ...` wrapped in `try...except sqlite3.OperationalError: pass`.

### Rule 4: Test Suite Verification
Before completing any task, execute the test suite:
```bash
./venv/bin/pytest
```
All existing tests must pass, and new features must include unit or integration tests under `tests/`.

---

## 3. Repository Architecture & Directory Structure

```
media-library/
├── app.py                           # CLI entrypoint, argument parser, Uvicorn server launcher
├── start.sh                         # Shell launcher: auto-creates venv, detects LAN IP, starts app
├── requirements.txt                 # Pinned dependencies
├── pytest.ini                       # Pytest configuration (pythonpath = ., basetemp = data/pytest_tmp)
├── config.json                      # Persistent runtime user preferences (recent destinations, source dir)
├── config.example.json              # Example configuration file
├── .env.example                     # Reference environment variables
├── .env                             # Local environment configuration (git-ignored)
├── media-library.service.example    # Systemd service unit template
├── LICENSE                          # MIT License
├── README.md                        # User-facing project documentation
├── AGENTS.md                        # THIS FILE: Comprehensive agent reference & architecture
│
├── media_library/                   # Core application Python package
│   ├── __init__.py
│   ├── app_factory.py               # FastAPI create_app factory & lifespan manager
│   │
│   ├── core/                        # Application configuration, constants, global state
│   │   ├── __init__.py
│   │   ├── config.py                # Path resolutions, env loading, defaults
│   │   ├── auth.py                  # Shared-password helpers, signed session cookie
│   │   ├── constants.py             # File extension sets, default folder candidates
│   │   ├── http_stream.py           # HTTP byte-range and media streaming response helpers
│   │   ├── state.py                 # In-memory state, AppState container, stat cache
│   │   └── templating.py            # Cached raw HTML template loader & injection helpers
│   │
│   ├── db/                          # Database connection, schema, and filesystem crawler
│   │   ├── __init__.py
│   │   ├── connection.py            # SQLite connection factory with WAL PRAGMAs
│   │   ├── schema.py                # Schema tables, indexes, idempotent migrations
│   │   └── indexer.py               # Filesystem crawler, folder indexing, index pruning
│   │
│   ├── routers/                     # FastAPI route modules
│   │   ├── __init__.py              # Export of all_routers list
│   │   ├── views.py                 # HTML page endpoints (/, /sorter, /library)
│   │   ├── auth.py                  # Shared-password login page/routes and auth middleware
│   │   ├── library_browse.py        # Library browsing, folder tree, search, index controls
│   │   ├── library_media.py         # Library file/thumbnail byte-range streaming
│   │   ├── library_admin.py         # Library rename/delete + thumbnail controls
│   │   ├── library_manage.py        # Library page file/folder management (move, folders, undo)
│   │   ├── sorter_queue.py          # Sorter config, scan, queue, browse, destinations
│   │   ├── sorter_move.py           # Sorter moves, undo, transfers, rename, delete
│   │   ├── sorter_media.py          # Sorter video stream + duplicate check
│   │   ├── transcode.py             # Live and queued video transcoding
│   │   ├── duplicates.py            # Content/visual duplicate detection and resolution
│   │   ├── webp.py                  # WebP image optimization controls
│   │   ├── zip_router.py            # ZIP compression, unpacking, batch compression
│   │   ├── backup.py                # Library snapshot backup save and restore
│   │   └── system.py                # /api/system/status unified status aggregation
│   │
│   ├── services/                    # Business logic and external service integrations
│   │   ├── __init__.py
│   │   ├── media_service.py         # Path validation, LAN IP detection, video queue scanner
│   │   ├── library_manage_service.py# Move/delete/rename/create helpers + index rewrite
│   │   ├── library_query.py         # Index-only row→dict shaping for library browsing
│   │   ├── status.py                # Shared worker/index/transfer/ZIP status payload builders
│   │   ├── ffmpeg_service.py        # FFmpeg/FFprobe poster extraction, WebP conversion
│   │   ├── deduplication_service.py # Quick-hash, pHash/dHash, duplicate resolution
│   │   ├── backup_service.py        # SQLite live backup, thumbnail tar bundling, auto-restore
│   │   └── zip_service.py           # ZIP metadata inspection, extraction cache, batch compressor
│   │
│   └── workers/                     # Background asynchronous worker subsystem
│       ├── __init__.py
│       ├── base.py                  # Abstract BaseWorker with lifecycle & pause/resume
│       ├── transfer_worker.py       # Sequential non-blocking file moves with undo
│       ├── transcode_worker.py      # Background video pre-transcoding
│       ├── thumbnail_worker.py      # Background video poster frame extraction
│       ├── metadata_worker.py       # MediaMetadataWorker: dimensions/duration/codec/capture-time probing
│       ├── duplicate_hash_worker.py # DuplicateHashWorker (quick) & ThumbHashWorker (visual)
│       ├── webp_worker.py           # WebPOptimizerWorker for background image conversion
│       ├── library_ops_worker.py    # LibraryOpsWorker for queued moves/deletes/renames + undo
│       └── backup_watcher.py        # Periodic & shutdown library backup snapshotting
│
├── static/                          # Frontend client assets
│   ├── app.js                       # Video Sorter UI logic (keyboard shortcuts, player, queue)
│   ├── library.js                   # Media Library UI logic (virtual scroll, search, lightbox, duplicates, ZIP)
│   └── styles.css                   # Shared styles and UI themes
│
├── templates/                       # Raw HTML templates (served via HTMLResponse)
│   ├── index.html                   # Video Sorter user interface
│   ├── login.html                   # Shared-password sign-in page
│   └── library.html                 # Media Library & Gallery user interface
│
├── tests/                           # Automated test suite
│   ├── conftest.py                  # Sandboxed fixtures (sandbox_env, sample_media)
│   ├── test_api_routes.py           # HTTP endpoints & status verification
│   ├── test_backup_service.py       # Backup snapshot & restore tests
│   ├── test_core_config.py          # Configuration loading & path resolution
│   ├── test_db_indexer.py           # SQLite indexer & pruning tests
│   ├── test_media_services.py       # FFmpeg service, poster extraction, media utils
│   ├── test_media_dimensions.py     # Dimension/metadata probing, natural-ratio tile data, folder totals
│   ├── test_performance_features.py# In-memory stat cache, subfolder caching, status API
│   ├── test_workers.py              # Worker lifecycle, pause/resume, queue execution
│   └── test_zip_service.py          # ZIP inspection, extraction, compression, LRU cache
│
└── data/                            # Runtime data directory (git-ignored)
    ├── library.db                   # Main SQLite database (WAL mode)
    ├── thumbnails/                  # Cached video/image poster frames (thumb_*.jpg)
    ├── transcodes/                  # Temporary transcoded video files
    ├── webp_staging/                # Staging folder for image conversions
    ├── backup_staging/              # Staging folder for snapshot database and tarballs
    ├── cache/zips/                  # Local extraction cache for virtual ZIP folders
    └── pytest_tmp/                  # Isolated temporary test directory
```

---

## 4. Environment Configuration Reference

All settings can be provided via `.env` file, environment variables, or CLI parameters:

| Variable | CLI Argument | Default Value | Description |
|---|---|---|---|
| `LIBRARY_DIR` | `--dest` | `./data/media` | Target organized media collection directory (FUSE / local) |
| `INCOMING_DIR` | `--source` | `./data/incoming` | Incoming unsorted downloads directory |
| `DATA_DIR` | N/A | `./data` | Root runtime directory for database, thumbnails, transcodes |
| `DB_PATH` | N/A | `${DATA_DIR}/library.db` (a root-level `backup_index.db` wins if present) | Explicit SQLite database file location |
| `THUMBNAIL_DIR` | N/A | `${DATA_DIR}/thumbnails` | Path where cached thumbnail images (`thumb_*.jpg`) reside |
| `TRANSCODE_DIR` | N/A | `${DATA_DIR}/transcodes` | Temporary directory for transcoded video stream files |
| `HOST` | `--host` | `0.0.0.0` | Network binding interface |
| `PORT` | `--port` | `8888` | Port to listen on |
| `LIBRARY_BACKUP_DIR` | N/A | `${LIBRARY_DIR}/.media_library_backup` | Absolute path for backup snapshots |
| `LIBRARY_BACKUP_SUBDIR` | N/A | `.media_library_backup` | Subdirectory name inside `LIBRARY_DIR` for backups |
| `LIBRARY_BACKUP_AUTO_ENABLED` | N/A | `true` | Enable background automatic snapshot backups when system settles idle |
| `LIBRARY_BACKUP_AUTO_INTERVAL_HOURS` | N/A | `6.0` | Minimum interval (in hours) between automatic background snapshots |
| `BACKUP_STAGING_DIR` | N/A | `${DATA_DIR}/backup_staging` | Staging area for constructing snapshot tarballs |
| `WEBP_AUTO_OPTIMIZE` | N/A | `true` | Enable background conversion of loose (non-ZIP) images to WebP. Images inside ZIP archives are **never** swept automatically — archives are optimized on demand (when browsed or via the Optimize WebP button) |
| `WEBP_WORKER_CONCURRENCY`| N/A | `1` | Concurrency for WebP conversions (recommended `1` on FUSE) |
| `WEBP_QUALITY` | N/A | `85` | WebP conversion quality factor (1–100) |
| `WEBP_STAGING_DIR` | N/A | `${DATA_DIR}/webp_staging` | Staging area for WebP conversion temporary files |
| `WEBP_SKIP_LARGER` | N/A | `false` | If `true`, abort conversion if WebP exceeds original file size |
| `ZIP_STAGING_DIR` | N/A | `${DATA_DIR}/zip_staging` | Local fast-disk staging area for assembling ZIP archives before FUSE transfer |
| `ZIP_CACHE_DIR` | N/A | `${DATA_DIR}/cache/zips`| Local fast-disk cache for extracted ZIP contents |
| `ZIP_CACHE_MAX_SIZE_GB`| N/A | `5.0` | Maximum disk size in GB for extracted ZIP cache before LRU eviction |
| `QUICK_NAV_DIRS` | N/A | Auto-detected | Comma-separated quick navigation directory paths |
| `APP_PASSWORD` | N/A | *(empty — auth disabled)* | Single shared password gating the entire web UI and API; empty/unset disables authentication |

---

## 5. Database Architecture & Schema

The application uses a single SQLite database (`library.db`) configured with high-concurrency PRAGMAs:
- `PRAGMA journal_mode = WAL;` (Write-Ahead Logging for non-blocking concurrent reads and writes)
- `PRAGMA synchronous = NORMAL;` (Safe and optimal write performance with WAL)
- `PRAGMA cache_size = -64000;` (~64MB in-memory page cache)
- `PRAGMA mmap_size = 268435456;` (256MB memory-mapped I/O)
- `PRAGMA temp_store = MEMORY;`

### Tables & Columns

#### 1. `folders`
Represents indexed directories and virtual ZIP archives in the media collection.
```sql
CREATE TABLE folders (
    path TEXT PRIMARY KEY,
    name TEXT,
    parent_path TEXT,
    file_count INTEGER DEFAULT 0,
    subfolder_count INTEGER DEFAULT 0,
    last_scanned REAL DEFAULT 0,
    is_zip INTEGER DEFAULT 0,
    total_size INTEGER DEFAULT 0,
    dir_mtime REAL
);
```
- `is_zip = 1` indicates that the path is a `.zip` archive acting as a virtual directory.
- `total_size` is the summed byte size of the folder's direct-child files (or a ZIP's member sizes).
- `dir_mtime` is the directory's (or ZIP file's) own mtime as recorded at index time. The startup crawler compares the current mtime to it to skip re-indexing an unchanged folder; NULL (never scanned, or a folder mutated by an in-app move/add/rename) forces a re-scan.

#### 2. `files`
Represents indexed video and image files.
```sql
CREATE TABLE files (
    path TEXT PRIMARY KEY,
    name TEXT,
    folder_path TEXT,
    type TEXT,                 -- 'video' | 'image' | 'other'
    ext TEXT,                  -- Lowercase extension (e.g. '.mp4', '.webp')
    size INTEGER,              -- File size in bytes
    size_formatted TEXT,       -- Human-readable size (e.g. '12.4 MB')
    mtime REAL,                -- Last modified epoch timestamp
    needs_transcode INTEGER DEFAULT 0, -- 1 if video requires transcode for web playback
    has_thumb INTEGER DEFAULT 0,       -- 1 if cached thumbnail exists on disk
    quick_hash TEXT,           -- SHA-256 of the first 128 KB head chunk + exact byte size (see compute_quick_hash)
    thumb_hash TEXT,           -- Bare 16-hex pHash string (64-bit DCT perceptual hash), or 'no_thumb'/'blank_thumb'
    webp_optimized INTEGER DEFAULT 0, -- 1 if converted to WebP or native WebP
    width INTEGER,                     -- Display width (rotation applied), probed by the metadata worker
    height INTEGER,                    -- Display height (rotation applied), probed by the metadata worker
    duration REAL,                     -- Media duration in seconds (videos)
    video_codec TEXT,                  -- First video stream codec (e.g. 'h264')
    audio_codec TEXT,                  -- First audio stream codec (e.g. 'aac')
    fps REAL,                          -- Average video frame rate
    capture_time REAL                  -- Capture/creation epoch (EXIF or container creation_time), or NULL
);
```
- `width`/`height` drive natural-aspect-ratio Library tiles (clamped client-side to 2:3–3:2).
- `width IS NULL` marks a file as not-yet-probed; the worker fills all probe columns in a single pass. Files that fail to probe are stored as `0 x 0` so they are not retried until the file changes. ZIP members (which have no cheap source access) instead take `width`/`height` from their cached local thumbnail, swept periodically.

#### 3. `webp_stats`
Singleton row (`id = 1`) tracking cumulative savings from WebP optimization.
```sql
CREATE TABLE webp_stats (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    saved_bytes INTEGER DEFAULT 0,
    converted_count INTEGER DEFAULT 0,
    skipped_count INTEGER DEFAULT 0
);
```

#### 4. `ignored_duplicates`
Stores user-confirmed false positive duplicate pairs.
```sql
CREATE TABLE ignored_duplicates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path1 TEXT NOT NULL,
    path2 TEXT NOT NULL,
    match_type TEXT NOT NULL,  -- 'content' | 'visual'
    created_at REAL NOT NULL,
    UNIQUE(path1, path2)
);
```
- Pairs are normalized lexicographically (`min(p1, p2)`, `max(p1, p2)`) before insertion.

#### 5. `db_meta`
Key-value store for application metadata (e.g. index version, backup timestamps).
```sql
CREATE TABLE db_meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
```

### Essential Indexes
- `idx_files_folder ON files(folder_path)`
- `idx_files_folder_lower_name ON files(folder_path, LOWER(name))`
- `idx_files_name ON files(name)`
- `idx_files_type ON files(type)`
- `idx_files_has_thumb ON files(has_thumb)`
- `idx_files_size ON files(size)`
- `idx_files_quick_hash ON files(quick_hash)`
- `idx_files_thumb_hash ON files(thumb_hash)`
- `idx_files_webp_optimized ON files(webp_optimized)`
- `idx_files_width ON files(width)`
- `idx_folders_parent ON folders(parent_path)`
- `idx_folders_parent_lower_name ON folders(parent_path, LOWER(name))`
- `idx_ignored_p1_p2 ON ignored_duplicates(path1, path2)`

---

## 6. Background Workers & Lifecycles

All background workers inherit from `BaseWorker` (`media_library.workers.base`) providing:
- `start()`: Initializes and starts the background `asyncio.Task` loop.
- `stop()`: Gracefully signals cancellation and awaits completion.
- `pause()` / `resume()`: Non-destructively suspends and resumes processing.
- `is_paused()`: Returns Boolean pause state.
- `get_status()`: Returns a status dictionary for API polling.

### Worker Roster

| Worker Instance | Class | Module | Responsibility |
|---|---|---|---|
| `transfer_worker_instance` | `TransferWorker` | `workers.transfer_worker` | Executes queued file moves sequentially; tracks undo operations. |
| `transcode_worker_instance` | `TranscodeWorker` | `workers.transcode_worker` | Background pre-transcoding of non-web videos to MP4. |
| `thumbnail_worker_instance` | `ThumbnailWorker` | `workers.thumbnail_worker` | Background extraction of poster frames for videos/images lacking thumbnails; skips non-WebP images inside ZIPs (deferred until WebP conversion). |
| `metadata_worker_instance` | `MediaMetadataWorker` | `workers.metadata_worker` | Probes source dimensions, duration, codecs, fps, and capture time once per loose file (FFprobe + EXIF); backfills ZIP-member dimensions from cached local thumbnails; powers natural-aspect-ratio tiles and avoids re-probing in the transcode path. |
| `duplicate_hash_worker_instance` | `DuplicateHashWorker` | `workers.duplicate_hash_worker` | Computes 128 KB head SHA-256 (+ size) for files with identical sizes. |
| `thumb_hash_worker_instance` | `ThumbHashWorker` | `workers.duplicate_hash_worker` | Computes perceptual `pHash` from video posters & images. |
| `webp_worker_instance` | `WebPOptimizerWorker` | `workers.webp_worker` | Background conversion of loose (non-ZIP) images to WebP. ZIP archives are excluded from the automatic sweep and are optimized on demand only — as a single archive-level unit when browsed or via the Optimize WebP button. Backs off when memory is low (measured against the tighter of host RAM and the service cgroup cap) and returns freed heap between conversions. |
| `library_ops_worker_instance` | `LibraryOpsWorker` | `workers.library_ops_worker` | Sequentially executes queued Library page operations — moves (file/folder/ZIP), recursive deletes, and renames — off the request thread, with per-item cancel and a move-only undo stack. |
| `backup_watcher_instance` | `BackupWatcherWorker` | `workers.backup_watcher` | Manages scheduled and shutdown snapshot backups to library folder. |
| *Batch ZIP Runner* | *Thread-based* | `services.zip_service` | Sequential batch compression of leaf image folders into ZIPs. |
| *Batch Unpack Runner* | *Thread-based* | `services.zip_service` | Sequential batch extraction of indexed ZIP archives back into folders (start/pause/resume/cancel), reusing `unpack_zip_to_folder`. |
| *Background Crawler* | *Async task* | `db.indexer` | Crawls library folder hierarchy, updates SQLite, prunes stale items. **Incremental on startup**: a folder whose recorded `dir_mtime` still matches is skipped without re-stat-ing/rewriting its files, though its known child folders are still traversed (so deep changes and pruning stay correct, and a transient `stat` failure protects rather than prunes the subtree). A manual sync forces a full re-scan. Prune is skipped for incomplete crawls and aborts when an implausibly large share of folders appears missing (transient mount protection). |

### Application Lifespan (`media_library.app_factory.create_app`)
1. **Startup Sequence**:
   - AnyIO thread limiter set to `100` tokens for concurrent FUSE I/O.
   - `check_and_auto_restore_library_backup()`: Detects if database or thumbnails are missing, restoring snapshot if found.
   - `init_backup_db()`: Initializes database schema and runs idempotent migrations.
   - Workers started: `transfer`, `transcode`, `crawler`, `thumbnail`, `metadata`, `duplicate_hash`, `thumb_hash`, `webp`, `library_ops`, `backup_watcher`.
   - If `source_dir` configured, enqueues pre-transcoding in background thread.
2. **Shutdown Sequence**:
   - All workers gracefully stopped (`await worker.stop()`).
   - Active crawler task cancelled and awaited.
   - `_create_library_backup_sync(is_shutdown=True)`: Creates a clean live snapshot of the SQLite database, config, and thumbnail archive in `.media_library_backup/`.

---

## 7. Media Processing & Streaming Pipelines

### Supported File Extensions
Defined in `media_library.core.constants`:
- **Video Extensions**: `.mp4`, `.m4v`, `.mkv`, `.webm`, `.avi`, `.mov`, `.wmv`, `.flv`, `.ts`, `.m2ts`, `.mpg`, `.mpeg`, `.vob`, `.3gp`, `.asf`
- **Native Browser Video**: `.mp4`, `.m4v`, `.webm`, `.mov`
- **Image Extensions**: `.jpg`, `.jpeg`, `.png`, `.webp`, `.gif`, `.bmp`, `.avif`, `.svg`

### HTTP 206 Byte-Range Streaming
Implemented in `media_library.routers.library_media.stream_backup_media` and `media_library.routers.sorter_media.stream_video`:
- Handles standard browser `Range: bytes=start-end` request headers.
- Emits `206 Partial Content` with `Content-Range: bytes start-end/total`, `Accept-Ranges: bytes`, and accurate `Content-Length`.
- Streams in 1MB chunks (`65536 * 16`) to support seamless instant seeking without buffering the whole file.

### Video Posters & Thumbnail Generation
1. Deterministic hashing: Thumbnail path is `THUMBNAIL_CACHE_DIR / "thumb_{h}.jpg"` where `h` is the first 24 characters of the SHA-256 of `{file_path}_{mtime}_{size}`.
2. Fast generation (`media_library.services.ffmpeg_service.generate_thumbnail`):
   - Runs `ffmpeg -ss 00:00:01 -i {file_path} -vf "scale=360:-2:flags=fast_bilinear" -frames:v 1 -update 1 -q:v 3 {temp_file}` (wrapped in `nice`, 8 s timeout, retried at `-ss 00:00:00`).
   - Atomically replaces into final path.
3. **Deferred ZIP images**: `ThumbnailWorker` skips non-WebP image members inside ZIP archives (predicate `COALESCE(fo.is_zip,0)=1 AND f.type='image' AND f.ext != '.webp'`) because the WebP optimizer will convert them — a pre-conversion thumbnail would be orphaned when the member is renamed to `.webp`. Videos and non-ZIP files are always thumbnailed. Thumbnail totals/pending/percent are recomputed from the DB each loop iteration so they stay accurate as conversion shrinks the deferred set.

### Media Metadata Probing (natural-ratio tiles)
`MediaMetadataWorker` (`workers.metadata_worker`) probes each loose indexed file once via `probe_media_metadata` (`services.ffmpeg_service`):
- Single FFprobe pass captures display `width`/`height` (rotation applied), `duration`, `video_codec`, `audio_codec`, `fps`, and container `creation_time`. Images without a container timestamp fall back to an EXIF `DateTimeOriginal` read via Pillow.
- `width IS NULL` marks a file as not-yet-probed; the worker fills all probe columns together and writes `0 x 0` on failure so the file is not retried until the indexer sees it change (carry-over in `index_folder`/`update_file_in_index` keeps results across crawls).
- ZIP members are probed separately: rather than force archive extraction over FUSE, the worker reads their dimensions from the cached local thumbnail (`_backfill_zip_batch`, one batch per loop alongside loose probing, with a 60s sweep cooldown). Members without a thumbnail yet fall back to a 1:1 box filled with `object-cover` (the justified grid no longer re-flows on image load).
- Progress is exposed under `metadata` in `/api/system/status`.

### WebP Image Optimization Pipeline
1. `convert_image_file_to_webp(input_path, quality=85)`:
   - Uses Pillow (`PIL.Image.open`).
   - Automatically handles EXIF orientation and RGBA alpha preservation.
   - Saves to temporary file (`.tmp_*.webp`).
   - If `WEBP_SKIP_LARGER` is true and WebP is larger than original, deletes temp file and marks skipped.
   - Otherwise atomically replaces original file on disk: `os.replace(temp_path, dest_path)`.
   - If original extension was not `.webp`, removes original file and updates SQLite index.
2. `convert_image_to_webp_bytes(source, quality=85)`:
   - Converts an image path or raw bytes to WebP bytes in memory (used for ZIP members).
   - Inferring PNG input (`img.format == "PNG"`) uses lossless WebP; all other formats use lossy WebP at `WEBP_QUALITY`.
3. **ZIP archive optimization (`optimize_zip_images_to_webp`)**:
   - Rewrites a `.zip` archive once, converting every non-WebP image member to `.webp`.
   - Serialized per archive via the shared per-archive operation lock (`_get_archive_lock`) so a WebP rewrite can never race an extract, unpack, or sync-back of the same remote archive.
   - **Local-first I/O**: if the archive is already extracted to the local cache (`is_zip_extracted`), image member bytes are read from the cache via `resolve_zip_file_path` (zero remote reads); otherwise the archive is downloaded to `ZIP_STAGING_DIR` with a single sequential `shutil.copyfile` before processing. The optimized archive is uploaded once (`copyfile` + `os.replace`).
   - Streams non-image members (`zf_in.open` + `copyfileobj`) to bound peak memory; image members are converted from bytes.
   - If `WEBP_SKIP_LARGER` is true and the WebP bytes are larger than/equal to the original, the original member is kept and marked `webp_optimized = -1` (skipped) so it is not retried.
   - Verifies the staged archive with `testzip()`, atomically replaces the remote archive, purges the extraction cache, re-indexes in SQLite, and updates `webp_stats`.
   - `WebPOptimizerWorker` never sweeps ZIP archives in the background. Archives are dispatched **on demand** as a single unit per archive (deduplicated + throttled, one archive rewrite per archive) — automatically when the archive is browsed, or explicitly via the Optimize WebP button. Loose (non-ZIP) images are converted by the background sweep when `WEBP_AUTO_OPTIMIZE` is enabled. **Memory safety**: the worker's back-off measures the tighter of host `MemAvailable` and the service's own cgroup headroom (`memory.high`/`memory.max` via `/proc/self/cgroup`, `MEMORY_GUARD_MB = 1536`), so a long image sweep pauses itself before hitting the cap instead of letting the kernel throttle sockets and freeze the server. It also runs `gc.collect()` + `malloc_trim(0)` (`_release_memory`) after every conversion and archive rewrite to return freed Pillow decode/encode arenas to the OS; the systemd unit additionally sets `MALLOC_ARENA_MAX=2` to bound glibc per-thread arenas.

---

## 8. ZIP Virtual Folders & Archive Management

The ZIP system enables transparent browsing, compression, and extraction of archives:

### Key Components (`media_library.services.zip_service`)
1. **Virtual Folder Splitting**:
   - `split_zip_path("/media/album.zip/sub/photo.jpg")` -> `("/media/album.zip", "sub/photo.jpg")`.
   - Allows endpoints like `/api/backup/browse` and `/api/backup/media` to operate on files inside ZIPs as if they were regular folders.
2. **Fast Metadata Inspection (`get_zip_metadata`)**:
   - Reads the ZIP central directory header without decompressing file contents (<50ms for thousands of images).
   - Indexed in SQLite as a virtual folder (`is_zip = 1`).
3. **On-Demand Extraction Cache**:
   - Cache location: `ZIP_CACHE_DIR / "{stem}_{sha256(real_path)[:16]}"`.
   - Marked with `.extracted_done`.
   - Thread-safe synchronization: A single per-archive operation lock (`_get_archive_lock`) serializes all archive operations (cache extract, unpack, sync-back, WebP rewrite) so they cannot interleave on the same remote file; an extract that finds the archive already consumed by a concurrent unpack exits quietly instead of recording a spurious failure. Unique staging directory names (`_staging_{uuid}`) prevent staging collisions.
   - Root prefix stripping: Detects common root folder prefixes in third-party archives and strips them to prevent double-nested directory paths during extraction and unpacking.
   - Multi-tier resolution (`resolve_zip_file_path`): Resolves inner files via direct lookup, immediate subdirectory lookup (for preserved subfolders), and recursive fallback, and touches `.extracted_done` on access for accurate LRU tracking.
   - Zip Slip protection: resolves member paths to ensure they stay within the target cache directory.
4. **LRU Cache Management (`manage_lru_cache`)**:
   - When total cache exceeds `ZIP_CACHE_MAX_SIZE_GB` (default 5.0 GB), evicts oldest accessed archives based on `.extracted_done` marker and member mtimes.
   - Filter semantics: Only archives with actively `running` or `queued` tasks are protected from eviction (completed and failed tasks are evicted when capacity is reached).
   - Pre-emptive & Post-operation pruning: Invoked before extraction starts and upon completion to guarantee quota headroom.
   - Staging garbage collection: Automatically cleans up stale `_staging_*` directories and `.tmp_*` files older than 10 minutes to prevent storage leaks.
5. **Folder Compression to ZIP (`compress_folder_to_zip`)**:
   - Validates eligibility: requires at least 1 image and 0 video files (`check_folder_compressibility`).
   - Stages archive creation on fast local disk (`ZIP_STAGING_DIR`) to avoid remote FUSE round-trip latency.
   - Converts non-WebP images to WebP on the fly during archive creation.
   - Verifies archive integrity using `testzip()` on local disk in milliseconds.
   - Copies completed archive to remote target sequentially, atomically replaces via `os.replace()`, cleans up local staging, safely deletes original folder, and updates SQLite index.
6. **Debounced Sync-Back (`trigger_debounced_sync_back`)**:
   - When files inside an extracted archive are deleted or renamed, schedules a 4-second debounced background task to re-compress the updated cache on local staging before streaming back into the storage ZIP archive.
7. **Batch ZIP Compression**:
   - Queries candidate folders (`find_batch_zip_candidate_folders`): leaf folders with 0 videos, 0 subfolders, and >=2 images.
   - Queues folders and processes them sequentially with pause, resume, cancel, and progress tracking.
8. **Batch ZIP Extraction (Extract All ZIPs)**:
   - Queries candidate archives (`find_batch_unzip_candidate_zips`): index-only list of every `folders.is_zip = 1` archive with member counts and sizes (FUSE-safe, no disk walk).
   - Sequential `unpack_zip_to_folder` per archive (conflict auto-rename, ZIP deletion, re-index) with start/pause/resume/cancel, overall percent, per-archive progress, and skip-and-continue on failure. State exposed via `get_batch_unzip_status` and in `/api/system/status` under `batch_unzip`.

---

## 9. Deduplication Engine

The deduplication subsystem detects both identical files and visual duplicates:

### 1. Content (Binary) Deduplication
- Group candidate selection: files sharing exact identical sizes (`files.size`).
- `compute_quick_hash(file_path)`: Hashes the first 128 KB head chunk and appends the exact byte size using SHA-256. Avoids reading entire multi-gigabyte files over network FUSE mounts.
- Files matching quick hash are **full-content verified before any auto-delete** (`autodelete_verified_duplicates`), and the user's `ignored_duplicates` list is always honoured.

### 2. Visual Duplicate Detection
- Computes a 64-bit DCT perceptual hash (`pHash`) on video poster thumbnails and images using `imagehash`.
- Stored in `files.thumb_hash` as a bare 16-hex pHash string (or `'no_thumb'` / `'blank_thumb'`).
- Hamming distance threshold: matches if `length(thumb_hash) = 16` and `distance(pHash) <= 2`.

### 3. Resolution & False Positives
- **Resolution**: User selects a `keep_path` and `delete_paths`. Deleted files are removed from disk, removed from SQLite, and marked as resolved.
- **Ignore / Unignore**: User can mark matching pairs as false positives. Pairs are stored in `ignored_duplicates` and filtered out of all future scans.

---

## 10. Library Backup & Auto-Restore System

### Problem Solved
On remote FUSE mounts (like pCloud) or ephemeral environments, rescanning tens of thousands of media files and extracting video posters can take hours.

### Solution Architecture (`media_library.services.backup_service`)
- Backup folder: `<LIBRARY_DIR>/.media_library_backup/` (or configured `LIBRARY_BACKUP_DIR`).
- Contents:
  - `library.db`: Clean SQLite database snapshot created using `conn.backup(backup_conn)` while the application is running.
  - `thumbnails.tar`: Uncompressed tar archive containing all thumbnail files from `THUMBNAIL_CACHE_DIR`.
  - `config.json`: Copy of active configuration.
  - `backup_meta.json`: Metadata with timestamp, total files, folders, thumbnails, and sizes.

### Auto-Restore on Startup
In `app_factory.py`, before launching background crawlers:
- Checks if local database is missing or empty.
- If a valid backup snapshot exists in the library directory, automatically restores `library.db` and extracts `thumbnails.tar` into `THUMBNAIL_CACHE_DIR`.
- Indexing and thumbnail generation work is completely avoided on fresh deployments.

### Clean Shutdown Snapshot
On clean application shutdown, a live SQLite backup and thumbnail archive are written to the library backup directory.

---

## 11. Authentication & Access Control

The entire web UI and API can optionally be gated behind a **single shared password** (`APP_PASSWORD`). When `APP_PASSWORD` is empty or unset, authentication is fully disabled and every route behaves exactly as before (this keeps local development and the test suite auth-free by default).

- **Login flow**: `GET /login` renders `templates/login.html` (a raw `HTMLResponse`, no Jinja2). `POST /login` accepts an `application/x-www-form-urlencoded` `password` (plus an optional same-site `next` target) and, on success, sets a signed session cookie and redirects to `next` (validated to an absolute same-site path to prevent open redirects; falls back to `/`). A wrong password re-renders the page with an inline error and issues no cookie.
- **Signed cookie (stateless)**: the cookie (`ml_auth`) is `base64url(issued_epoch "." HMAC-SHA256)`, keyed by a SHA-256 derivation of the password (`media_library/core/auth.py`). There is **no server-side session store and no DB writes**, so sessions survive restarts, and changing `APP_PASSWORD` automatically invalidates every previously issued cookie. The password itself is compared with `hmac.compare_digest` (constant time).
- **Remember-forever (sliding expiry)**: the cookie is `HttpOnly`, `SameSite=Lax`, `Path=/`, with a ~400-day `Max-Age` (browsers cap cookie lifetime there). It is re-issued (slid forward) only when the embedded issue time is older than 30 days, so an actively used browser effectively never has to sign in again. There is no logout button — logging out means clearing cookies.
- **Coverage & enforcement**: `auth_middleware` (registered in `app_factory.create_app`, active only when a password is set) gates **every** route — all pages, all `/api/...` endpoints, and all media/thumbnail/stream endpoints. Only `GET`/`HEAD` `POST /login` and `/static/*` stay public so the login page can render. Unauthenticated `/api/*` requests receive `401 JSON`, while page requests are `302`-redirected to `/login?next=...`. Because the cookie is sent automatically on same-origin `fetch()` and `<video>`/`<img>` src requests, no frontend JavaScript changes are required.
- **Not covered**: no rate limiting/lockout on failed logins, and the cookie is not marked `Secure` (the app is typically served over plain LAN HTTP).

---

## 12. Consolidated System API (`/api/system/status`)

To eliminate excessive polling across multiple widgets in the frontend, all background subsystem states are aggregated into a single endpoint:

**Endpoint**: `GET /api/system/status`
**Returns**:
```json
{
  "is_active": true,
  "transfers": { "active": null, "queued": [], "completed": [] },
  "transcode": { "active": null, "queued": [], "percent": 0 },
  "index": { "status": "idle", "scanned_folders": 120, "skipped_folders": 0, "total_folders": 120, "percent": 100 },
  "thumbnails": { "status": "idle", "pending_count": 0, "percent": 100 },
  "metadata": { "status": "idle", "pending_count": 0, "zip_pending_count": 0, "processed_count": 0, "percent": 100 },
  "duplicate_hash": { "status": "idle", "pending_count": 0, "percent": 100 },
  "thumb_hash": { "status": "idle", "pending_count": 0, "percent": 100 },
  "webp": { "status": "idle", "saved_formatted": "42.1 MB", "converted_count": 140 },
  "library_backup": { "status": "idle", "backup_exists": true, "last_backup_str": "Oct 06, 12:00:00" },
  "zip": { "is_active": false, "tasks": [] },
  "batch_zip": { "status": "idle", "succeeded": 12, "overall_percent": 100.0 },
  "batch_unzip": { "status": "idle", "succeeded": 0, "failed": 0, "errors": [], "overall_percent": 0.0 },
  "library_ops": { "status": "idle", "active": null, "queued": [], "undo_count": 0 }
}
```
- The frontend uses `is_active` to adjust polling frequency: **2000 ms** when active background work is in flight, **6000 ms** when idle, **8000 ms** on error.

---

## 13. Complete API Routing & Endpoint Map

### HTML Views (`media_library.routers.views`)
- `GET /` — Serves Media Library UI (`templates/library.html`)
- `GET /sorter` / `GET /sort` — Serves Incoming Video Sorter UI (`templates/index.html`)
- `GET /library` — 302 Redirect to `/`

### Authentication (`media_library.routers.auth`)
- `GET /login` — Serves the shared-password sign-in page (`templates/login.html`); redirects to `/` when auth is disabled or already signed in
- `POST /login` — Validates the shared password and sets the signed `ml_auth` cookie (303/302 redirect to the validated `next` target)

### Media Library & Browser (`media_library.routers.library_browse` + `library_media` + `library_admin`)
- `GET /api/backup/browse?path={folder_path}&refresh={bool}` — List subfolders, files, breadcrumbs, ZIP indicators. Never indexes inline: for a folder not yet in the index it schedules indexing on a background thread and returns the cached view with `indexing: true` (the client re-polls). Returns per-file `thumb_url`/`media_url` plus probed `width`/`height`/`duration`/`video_codec`/`audio_codec`/`fps`/`capture_time` (dimensions drive natural-aspect-ratio tiles). Each subfolder entry carries `file_count`/`subfolder_count`/`total_size`, a representative cover `thumb_url` (alphabetically-first direct-child image by `LOWER(name)`, falling back to the first direct-child video's poster, index-only), the cover's `width`/`height`, and `has_thumb`; folders with no media get `thumb_url: null`, and the client card falls back to an icon until the on-demand thumbnail is generated. (`library_browse`)
- `GET /api/backup/folders?path={folder_path}` — List only a folder's immediate child folders (index-only, FUSE-safe) with `file_count`/`subfolder_count`/`is_zip`. Powers the sidebar folder tree's lazy expansion; never reads file entries nor triggers ZIP extraction, and schedules background indexing with `indexing: true` for unindexed folders. (`library_browse`)
- `GET /api/backup/search?q={query}` — Search media files by multi-word query across index; results include probed `width`/`height`/`duration`/`video_codec`/`audio_codec`/`fps`/`capture_time` (`library_browse`)
- `GET /api/backup/media?path={file_path}` — Stream video/image with byte-range headers and caching (`library_media`)
- `GET /api/backup/thumbnail?path={file_path}` — Stream thumbnail image (`library_media`)
- `POST /api/backup/thumbnails/generate` — Enqueue folder for poster frame extraction (`library_admin`)
- `GET /api/backup/thumbnails/status` — Get poster generation status (`library_admin`)
- `POST /api/backup/thumbnails/pause` — Pause/resume poster generation (`library_admin`)
- `POST /api/backup/rename` — Rename media file, update on disk and in database (`library_admin`)
- `POST /api/backup/delete` — Delete media file from disk and database (`library_admin`)
- `POST /api/backup/index/sync` — Trigger a manual **full** library crawler re-index (startup crawls are incremental, keyed on per-folder `dir_mtime`) (`library_browse`)
- `GET /api/backup/index/status` — Crawler progress status (`library_browse`)
- `POST /api/backup/index/prune` — Remove stale deleted folders and orphaned thumbnails (`library_browse`)

### Library File & Folder Management (`media_library.routers.library_manage`)
- `POST /api/backup/move/check` — Pre-check name conflicts before a move; returns collision list
- `POST /api/backup/move` — Queue file/folder/ZIP moves (conflict policy: `auto_rename` | `skip` | `error`)
- `POST /api/backup/undo` — Undo the last queued library move (move-only undo)
- `GET /api/backup/library-ops/status` — Active/queued/history state for the LibraryOpsWorker (surfaced in the UI's Background Tasks panel and header badge as "Library Operations": active move/delete/rename, queue count, completed/undo counts)
- `POST /api/backup/library-ops/cancel` — Cancel a queued library operation by `task_id`
- `POST /api/backup/folders` — Create a real folder inside a given parent
- `POST /api/backup/folders/rename` — Rename a folder or ZIP archive (rewrites index paths)
- `POST /api/backup/folders/delete` — Queue recursive deletion of a folder or ZIP archive
- `POST /api/backup/bulk/rename/preview` — Preview a bulk rename (prefix/numbering/base+counter/find&replace/case) without touching disk
- `POST /api/backup/bulk/rename` — Queue bulk rename operations (base name only, extension preserved)
- `POST /api/backup/bulk/delete` — Queue deletion of many files/folders/ZIPs
- `POST /api/backup/bulk/webp-optimize` — Convert selected images, enqueue selected folders for WebP, skip others

### Sorter Workflow (`media_library.routers.sorter_queue` + `sorter_move` + `sorter_media`)
- `GET /api/config` — App config, queue count, undo availability, LAN IP (`sorter_queue`)
- `POST /api/source/scan` — Scan incoming folder for videos (`sorter_queue`)
- `GET /api/browse?path={path}` — Browse filesystem directories (source folder picker). Tolerates trailing whitespace in folder names via `_resolve_directory_tolerant()`; returns `404` (not a silent redirect to `/`) when the path does not exist (`sorter_queue`)
- `GET /api/queue` — Incoming video items (`sorter_queue`)
- `GET /api/destinations` — Destination category folder hierarchy (`sorter_queue`)
- `POST /api/destinations/create` — Create category folder (`sorter_queue`)
- `POST /api/move` — Enqueue video move to destination (`sorter_move`)
- `POST /api/rename` — Rename video in incoming queue (`sorter_move`)
- `POST /api/delete` — Delete video in incoming queue (`sorter_move`)
- `POST /api/undo` — Undo last move operation (`sorter_move`)
- `GET /api/quick-nav` — Quick navigation shortcuts (`sorter_queue`)
- `GET /api/transfers` — Active and queued file moves (`sorter_move`)
- `POST /api/transfers/cancel` — Cancel pending file move (`sorter_move`)
- `GET /api/stream/{file_id}` — Stream video from incoming directory (`sorter_media`)
- `GET /api/sorter/check-duplicate?path={path}` — Check incoming video against library (`sorter_media`)

### Video Transcoding (`media_library.routers.transcode`)
- `GET /api/transcode/{file_id}` — Live transcoding stream
- `GET /api/transcode/status` — Pre-transcoding queue status
- `POST /api/transcode/pause` — Pause/resume transcoding worker
- `POST /api/transcode/cancel` — Cancel specific transcode task
- `POST /api/transcode/cancel_all` — Cancel all transcode tasks
- `GET /api/backup/transcode_stream?path={file_path}` — Real-time FFmpeg transcoding stream
- `GET /api/backup/transcode/summary?folder_path={path}` — Files needing transcoding
- `POST /api/backup/transcode/enqueue` — Enqueue single file for transcoding
- `POST /api/backup/transcode/enqueue_all` — Enqueue all non-web videos in folder

### Deduplication (`media_library.routers.duplicates`)
- `GET /api/backup/duplicates?mode={content|visual}&limit={n}&offset={n}` — List duplicate groups
- `GET /api/backup/duplicates/hash-status` — Quick-hash worker progress
- `POST /api/backup/duplicates/hash-pause` — Pause/resume quick-hash worker
- `POST /api/backup/duplicates/hash-start` — Start/resume quick-hash worker
- `GET /api/backup/duplicates/thumb-hash-status` — Visual hash worker progress
- `POST /api/backup/duplicates/verify-group` — Verify group with on-demand hashing
- `POST /api/backup/duplicates/resolve` — Delete duplicate copies, keep specified file
- `POST /api/backup/duplicates/autodelete-verified` — Batch auto-delete verified duplicates
- `POST /api/backup/duplicates/ignore` — Mark duplicate pair as false positive
- `GET /api/backup/duplicates/ignored` — Paginated list of ignored pairs
- `POST /api/backup/duplicates/unignore` — Remove pair from ignored duplicates

### WebP Optimization (`media_library.routers.webp`)
- `GET /api/backup/webp/status` — Optimizer status, saved bytes, conversion count
- `POST /api/backup/webp/pause` — Pause optimizer worker
- `POST /api/backup/webp/resume` — Resume optimizer worker
- `POST /api/backup/webp/optimize-folder` — Enqueue folder for WebP conversion

### ZIP Archives & Compression (`media_library.routers.zip_router`)
- `GET /api/backup/zip/check?folder_path={path}` — Check folder compression eligibility
- `GET /api/backup/zip/status` — Active and recent ZIP tasks
- `POST /api/backup/zip/compress` — Compress eligible folder to ZIP archive
- `POST /api/backup/zip/unpack` — Unpack ZIP back into standard folder
- `GET /api/backup/zip/batch/candidates` — List all leaf image folders eligible for ZIP
- `POST /api/backup/zip/batch/start` — Start batch ZIP compression
- `POST /api/backup/zip/batch/pause` — Pause active batch compression
- `POST /api/backup/zip/batch/resume` — Resume paused batch compression
- `POST /api/backup/zip/batch/cancel` — Cancel batch compression
- `GET /api/backup/zip/batch/status` — Batch compression status and progress
- `GET /api/backup/zip/batch-unpack/candidates` — List all indexed ZIP archives eligible for batch extraction
- `POST /api/backup/zip/batch-unpack/start` — Start batch extraction of selected ZIP archives
- `POST /api/backup/zip/batch-unpack/pause` — Pause active batch extraction (toggles resume)
- `POST /api/backup/zip/batch-unpack/resume` — Resume paused batch extraction
- `POST /api/backup/zip/batch-unpack/cancel` — Cancel batch extraction
- `GET /api/backup/zip/batch-unpack/status` — Batch extraction status and progress

### Library Backup Snapshots (`media_library.routers.backup`)
- `GET /api/backup/library-backup/status` — Backup status, size, file/thumb count, timestamp
- `POST /api/backup/library-backup/save` — Trigger background snapshot creation
- `POST /api/backup/library-backup/restore` — Trigger background snapshot restoration

### Unified System Status (`media_library.routers.system`)
- `GET /api/system/status` — Consolidated status for all background subsystems

---

## 14. Development, Testing, and Maintenance Workflows

### Setting Up the Environment
```bash
# Clone the repository and enter directory
cd /root/media-library

# Ensure Python 3.9+ is available
python3 --version

# Use existing venv or create a new one
python3 -m venv venv
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt
```

### Running the Application
```bash
# Using the start script:
./start.sh

# Or directly with Uvicorn / Python:
./venv/bin/python app.py --port 8888 --dest /path/to/library --source /path/to/incoming
```

**Restarting to apply changes**: The server runs *without* `--reload`, and Python modules are loaded once at startup. `static/*` assets are served fresh from disk on each request, but HTML templates are read once and cached in memory (`core/templating.load_template`) — so any backend change (routers, services, workers, schema) **and any `templates/*.html` edit** require a server **restart** to take effect; only `static/` edits need a browser refresh.

In production the app is managed by the `media-library.service` systemd unit (`ExecStart=/root/media-library/venv/bin/python /root/media-library/app.py --host 0.0.0.0 --port 8888`, `Restart=always`). **Never start a second instance by hand** — doing so races for port `8888` and can make systemd crash-loop. Restart the service instead; SIGTERM triggers a clean worker shutdown and shutdown snapshot (can take ~15s):

```bash
systemctl restart media-library     # graceful restart; the unit auto-restarts on exit
systemctl status media-library      # confirm a single active MainPID owns port 8888
```

Only for an ad-hoc foreground/dev run (not the deployed service) use `./start.sh` or `./venv/bin/python app.py ...`.

### Running Tests
The test suite is powered by Pytest with automatic PYTHONPATH configuration in `pytest.ini`:
```bash
# Run all tests:
./venv/bin/pytest

# Run tests with verbose output:
./venv/bin/pytest -v

# Run a specific test module:
./venv/bin/pytest tests/test_zip_service.py
```

### Writing New Tests
All test modules must use the sandboxed environment fixtures defined in `tests/conftest.py`:
- `sandbox_env`: Creates isolated temporary directories for `media`, `incoming`, `thumbnails`, and a dedicated SQLite database. Never touches production directories.
- `sample_media`: Pre-populates the sandbox with sample category directories, video clips, and poster images.

Example test pattern:
```python
def test_new_feature(sandbox_env):
    db_path = sandbox_env["db_path"]
    media_dir = sandbox_env["media_dir"]
    # Test implementation using sandboxed paths
```

---

## 15. Checklist for Agents Making Changes

Before concluding your turn or submitting your work, complete this verification checklist:

1. [ ] **Code Quality & Typing**: Python code follows modular structure, includes type hints, and handles exceptions cleanly.
2. [ ] **FUSE Safety**: No synchronous directory tree walking in request handlers; writes use atomic temporary files (`.tmp_*` -> `os.replace`).
3. [ ] **Database Integrity**: SQLite connections are opened with `get_db()`, use WAL mode, execute writes inside `with conn:`, and are always closed in `finally: conn.close()`.
4. [ ] **Schema Migrations**: Schema alterations in `init_backup_db()` are idempotent (`ALTER TABLE ... ADD COLUMN ...` with try/except).
5. [ ] **System Status Sync**: Any new background task or worker state is exposed in `/api/system/status`.
6. [ ] **Automated Tests**: Ran `./venv/bin/pytest` and confirmed all tests pass without errors.
7. [ ] **AGENTS.md Updated**: Verified that `AGENTS.md` accurately reflects any changes made to endpoints, schema, workers, or configuration.
