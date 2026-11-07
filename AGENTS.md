# 🤖 AGENTS.md — Media Library & Video Sorter: agent guide

> **Critical directive:** this file is the authoritative specification of the
> repository. It MUST be updated in the same change that adds, modifies or
> removes an endpoint, schema column, worker, configuration variable or core
> workflow. The app became the only app in 3.0.0: there is no second
> implementation to fall back to and no `/v2` surface to keep in sync.

## 1. What the system is

A self-hosted media library and keyboard-driven video sorter for tens of
thousands of videos and images, often on a slow remote FUSE mount (pCloud,
NFS). One FastAPI process serves both a JSON API at `/api` and a React client
at `/`.

- **Library**: index-only browsing of a folder tree, multi-word search over
  filenames *and* library-relative folder paths, justified natural-ratio tiles,
  fullscreen viewer with byte-range streaming, favorites, multi-select with
  bulk move/delete/rename/star/WebP.
- **Sorter**: reviews an incoming folder at a few keystrokes (1–6 destinations,
  `/` to search a folder, `Enter` to move), with duplicate warnings, undo,
  rename, delete and live transcoding for containers browsers cannot play.
- **Processing**: background poster and looping animated-WebP previews,
  metadata probing (dimensions, duration, codecs, fps, capture time), live
  transcoding for non-web containers, in-place x265 (HEVC) conversion, WebP
  optimisation, dedupe (content hash and perceptual pHash), ZIP compression /
  unpacking / virtual browsing, URL+playlist downloads (yt-dlp, gallery-dl
  fallback), library analytics, and SQLite snapshots with auto-restore.
- **Auth**: optional single shared password (`APP_PASSWORD`), stateless signed
  cookie, no server-side session store.

### Stack

| Layer | Choice |
|---|---|
| Runtime | Python 3.10+ (PEP 604 unions evaluated at runtime; tested on 3.13) |
| Web | FastAPI + Uvicorn |
| Index | SQLite 3, WAL, `synchronous=NORMAL`, 64 MB page cache, mmap |
| Media | FFmpeg / FFprobe CLI, Pillow, `imagehash` (+ SciPy for the DCT), NumPy |
| Downloads | yt-dlp / gallery-dl as isolated subprocesses (`python -m …`) |
| Client | React 19 + TypeScript 7, Vite 8, Tailwind 4, TanStack Query, Zustand, Radix, ECharts 6, `yet-another-react-lightbox`, Sonner, cmdk |
| Tests | Pytest 8 (+ cov), Vitest 5 with Testing Library |
| Build | one npm project at the repo root; Vite root `frontend/`, output `static/dist/` |

There are no runtime CDN dependencies: fonts, icons and the lightbox are
bundled (`tests/test_no_cdn.py` enforces it).

---

## 2. Rules of engagement

### Rule 1 — Documentation and version sync
1. New or changed route → update the **API map** (§8).
2. Schema change → update **The index** (§5) and keep migrations idempotent.
3. New worker/queue → update **Background runtime** (§6).
4. New/renamed environment variable → update **Configuration** (§4) and
   `.env.example`.
5. Release bump → `media_library/__init__.py`'s `__version__` is the **only**
   place the version is written. FastAPI metadata, `/api/health`,
   `/api/system/*` and snapshot metadata read it; `package.json`, `README.md`
   and `CHANGELOG.md` must agree. `tests/test_version.py` fails otherwise.
6. Record user-visible changes in `CHANGELOG.md` (Keep a Changelog).

### Rule 2 — FUSE and remote-filesystem discipline
- **Never** run an unbounded `os.walk`/recursive scan in a request handler.
  Query SQLite; schedule indexing on a worker thread.
- **Never** `os.stat` the same remote file repeatedly. Cache or read the index.
- A failed `stat`/`exists` is **not** a deletion. Pruning confirms absence with
  retries and aborts when an implausible share of a folder looks missing.
- File writes are atomic: write `.tmp`/`.mllibtmp-*` in the destination
  directory, verify, then `os.replace()`.
- Validate every client-supplied path with `paths.resolve_library_path` /
  `validate_*` helpers (403 outside the library root).

### Rule 3 — Database discipline
- One SQLite database in WAL mode. Reads use `db.reader()` (read-only, always
  closed). Writes use `db.writer()` (refuses when read-only) inside
  `with conn:`.
- **Never mutate the database on the event loop.** A write waits for SQLite's
  single writer lock for its whole timeout and, awaited from a coroutine,
  stalls every request while the process still looks alive. Routers call
  `await asyncio.to_thread(writes.fn, …)`; `tests/test_no_blocking_db_writes.py`
  is an AST guard against a direct `writes.*`/`writer()` call inside an
  `async def`.
- **Never hold a write transaction across remote I/O.** Resolve every
  `exists()`/`stat()` before opening the transaction.
- Filesystem first, index second. A failure between the two leaves a row the
  crawler reconciles; a rename/move rolls the filesystem change back if the
  index write fails.
- Migrations are idempotent (`CREATE … IF NOT EXISTS`, `ALTER TABLE … ADD
  COLUMN` in `try/except sqlite3.OperationalError`).

### Rule 4 — Tests and gates
```bash
./venv/bin/pytest                 # backend
npm test                          # client (Vitest)
npm run build                     # typecheck + production build
./venv/bin/ruff check . && ./venv/bin/ruff format --check .
```
All of it runs in CI (`.github/workflows/ci.yml`: `lint`, `backend`,
`frontend`). New behaviour needs a test in `tests/` or `frontend/src/**/*.test.ts`.
The suite is isolated: an autouse fixture points `DB_PATH`/`DATA_DIR` at a
temporary directory and creates the schema with `schema.init_schema`, so no test
can reach a real library or database.

### Rule 5 — Keep the live server on the current code
Python modules and the app are assembled at process start, so any backend
or asset change needs a restart:
```bash
systemctl restart media-library && systemctl is-active media-library
```
The unit runs `app.py`. Never start a second instance by hand — it races for
the port and makes systemd crash-loop. `Restart=always` only covers a process
that exits; a wedged process is caught by the health watchdog
(`scripts/media-library-watchdog.sh`, timer examples tracked).

### Rule 6 — Client rebuilds
The client is hashed and built, not served from source. After any change under
`frontend/`:
```bash
npm run build        # tsc --noEmit ×2 + vite build into static/dist/
```
`static/dist/index.html` is served `Cache-Control: no-cache`, and the bundles it
references are content-hashed, so a reload picks up a new build. Nothing outside
`static/dist/` may reference `/static/dist/v2/` — that path is gone.

### Rule 7 — Commit and push, with no personal data
- Never let a home path, real email, IP, token, password, key or database into
  a commit. Author identity stays `Media Library <dev@medialibrary.local>`.
  `.env`, `config.json`, `*.db`, `data/`, `plans/`,
  `REJECTED_IMPROVEMENTS.md` stay ignored; `tests/test_repo_hygiene.py` checks
  the tracked tree and the ignore rules.
- Remove scratch files before committing, and push after a green suite.

---

## 3. Repository layout

```
media-library/
├── app.py                     # entrypoint: python app.py --host --port
├── start.sh                   # venv bootstrap + LAN IP + python app.py
├── package.json               # the client's npm project (single one)
├── package-lock.json
├── vite.config.ts             # root: frontend/, outDir: ../static/dist, base /static/dist/
├── tsconfig.json / tsconfig.node.json
├── pytest.ini                 # pythonpath=., basetemp=data/pytest_tmp, --cov=media_library
├── ruff.toml                  # lint config, target py310, documented exclusions
├── requirements.txt
├── .env.example               # every supported variable, documented
├── media-library.service.example
├── media-library-watchdog.service.example / .timer.example
├── scripts/media-library-watchdog.sh
├── README.md / CHANGELOG.md / LICENSE
│
├── media_library/             # the application package
│   ├── app.py                 # create_app(), api_router(), lifespan, main()
│   ├── config.py              # env + .env settings (new names, V2_ fallbacks)
│   ├── schema.py              # DDL creation + obsolete-index convergence
│   ├── db.py                  # reader() / writer() / DatabaseReadOnlyError
│   ├── auth.py                # signed ml_auth cookie + middleware
│   ├── build_info.py          # short git SHA for /api/health
│   ├── paths.py               # library-root validation, breadcrumbs, formatting
│   ├── library.py             # index-only browse/folders/search/favorites
│   ├── writes.py              # rename/move/delete/folders/uploads (sync, off-loop)
│   ├── indexer.py             # incremental crawler + prune
│   ├── probe.py               # metadata prober
│   ├── thumbnails.py          # poster + animated preview generation
│   ├── thumbnail_worker.py    # poster sweep
│   ├── cache.py               # cache keys + orphaned-artwork sweep
│   ├── animated.py            # animated preview queue
│   ├── runtime.py             # owned background threads
│   ├── transcode_stream.py    # live fragmented-MP4 pipe
│   ├── x265.py                # in-place HEVC queue
│   ├── webp.py                # WebP optimisation
│   ├── dedupe.py              # quick hash, pHash, resolve
│   ├── downloads.py           # yt-dlp / gallery-dl manager
│   ├── sorter.py              # incoming queue, destinations, moves, undo
│   ├── zipfs.py / zip_ops.py / zip_webp.py / zip_batch.py
│   ├── cleanup.py             # empty-folder cleanup
│   ├── analytics.py           # index-only rollups
│   ├── backups.py / restore.py
│   ├── schemas.py             # Pydantic response models (the OpenAPI contract)
│   ├── views.py               # SPA shell delivery
│   └── routers/               # auth, system, library, media, favorites, manage,
│                              # uploads, sorter, downloads, analytics, dedupe,
│                              # webp, cleanup, zip, zip_batch, backups, x265
│
├── frontend/                  # React client source
│   ├── index.html             # Vite entry (build only; not served directly)
│   └── src/
│       ├── main.tsx / app/router.tsx
│       ├── pages/             # Library, Search, Favorites, Images, Sorter,
│       │                      # Downloads, Analytics, Dedupe, WebP/Tools, Zip,
│       │                      # Empty folders, Backups, Settings, Roadmap
│       ├── components/        # shell, media, manage, onboarding, ui primitives
│       ├── lib/               # api client, queries, stores, theme, format,
│       │                      # justified layout, bulk rename, upload, onboarding
│       └── styles/
│
├── static/
│   └── dist/                  # built client (index.html + hashed assets)
│
├── tests/                     # pytest suite (30 modules)
└── data/                      # runtime, git-ignored: library.db, thumbnails/,
                               # transcodes/, upload_staging/, download_staging/,
                               # zip_staging/, cache/zips/, backup_staging/
```

---

## 4. Configuration

Read from the process environment first, then `.env`. The `V2_…` names are
still accepted as a fallback so a pre-upgrade `.env` keeps working.

| Variable | Default | Meaning |
|---|---|---|
| `LIBRARY_DIR` | `./data/media` | The library root everything is served from |
| `INCOMING_DIR` | `./data/incoming` | Folder the video sorter reviews |
| `SOURCE_DIR` | `INCOMING_DIR` | Explicit sorter folder |
| `DATA_DIR` | `./data` | Runtime root (index, caches, staging) |
| `DB_PATH` | `$DATA_DIR/library.db` (a root `backup_index.db` wins) | SQLite index |
| `DB_READ_ONLY` | `false` | Open the index read-only; every mutation answers 503 |
| `WRITES` | `true` | Master switch for write endpoints |
| `THUMBNAIL_DIR` | `$DATA_DIR/thumbnails` | Poster + animated preview cache |
| `TRANSCODE_DIR` | `$DATA_DIR/transcodes` | x265 output while encoding |
| `UPLOAD_STAGING_DIR` | `$DATA_DIR/upload_staging` | Resumable upload chunks |
| `UPLOAD_STAGING_TTL_HOURS` | `24` | Abandoned upload retention |
| `DOWNLOAD_STAGING_DIR` | `$DATA_DIR/download_staging` | yt-dlp / gallery-dl working dirs |
| `DOWNLOAD_WORKER_CONCURRENCY` | `2` | Parallel downloads |
| `DOWNLOAD_COOKIES_FILE` | *(empty)* | Netscape cookies for gated sites |
| `SORTER_STATE` | `$DATA_DIR/sorter_state.json` | Recent sorter destinations |
| `ZIP_STAGING_DIR` | `$DATA_DIR/zip_staging` | Local archive assembly |
| `ZIP_CACHE_DIR` | `$DATA_DIR/cache/zips` | Extracted archive members |
| `ZIP_CACHE_MAX_SIZE_GB` | `5.0` | LRU ceiling for that cache |
| `LIBRARY_BACKUP_DIR` | `$LIBRARY_DIR/.media_library_backup` | Snapshot location |
| `BACKUP_STAGING_DIR` | `$DATA_DIR/backup_staging` | Snapshot assembly |
| `ALLOW_LIVE_RESTORE` | `false` | Permit `/api/backups/restore` over the live index |
| `OWN_WORKERS` | `true` | Run the crawler, prober, sweeps and snapshots |
| `CRAWL_INTERVAL_MINUTES` | `30` | Owned crawler cadence |
| `BACKUP_INTERVAL_HOURS` | `6.0` | Automatic snapshot cadence |
| `WEBP_QUALITY` / `WEBP_SKIP_LARGER` | `85` / `false` | WebP conversion |
| `X265_PRESET` / `X265_CRF` | `medium` / `18` | HEVC encode |
| `BROWSE_LIMIT` / `SEARCH_LIMIT` | `120` / `1000` | Default page sizes |
| `HOST` / `PORT` | `0.0.0.0` / `8888` | Bind address |
| `APP_PASSWORD` | *(empty — auth off)* | Shared password for the whole app |
| `GIT_COMMIT` / `BUILD_COMMIT` | *(from `.git`)* | Build revision in `/api/health` |

---

## 5. The index

One SQLite file, WAL mode. `schema.init_schema()` creates it at startup (in a
thread, writes mode only) and drops three indexes no query can use
(`idx_files_name`, `idx_files_webp_optimized`, `idx_ignored_match_type`), so an
index written by the 2.x app and a fresh one have the same shape. It never
rewrites or deletes a row.

```sql
folders(path PK, name, parent_path, file_count, subfolder_count,
        last_scanned, is_zip, total_size, dir_mtime)
files  (path PK, name, folder_path, type, ext, size, size_formatted, mtime,
        needs_transcode, has_thumb, width, height, duration, video_codec,
        audio_codec, fps, capture_time, thumb_mtime, has_animated,
        quick_hash, thumb_hash, webp_optimized)
webp_stats(id=1, saved_bytes, converted_count, skipped_count)
ignored_duplicates(id PK, path1, path2, match_type, created_at, UNIQUE(path1,path2))
favorites(path PK, created_at)
```

Hard contracts:

- `folders.is_zip = 1` marks a `.zip` archive browsed as a virtual folder;
  member rows live in `files` with the archive-prefixed path.
- `dir_mtime` is what makes the crawler incremental: a folder whose mtime still
  matches is skipped without re-stat-ing its children (they are still
  traversed). NULL forces a re-scan.
- `width IS NULL` means "not probed yet". Failures are stored as `0×0` so the
  file is not retried until it changes.
- `has_thumb`: `1` poster cached, `0` pending, `-1` generation failed.
- `has_animated`: `1` preview cached, `0` pending, `-1` failed.
- `quick_hash = sha256(first 128 KB + size)`; `thumb_hash` is a bare 16-hex
  pHash (or `no_thumb`/`blank_thumb`). Both formats are load-bearing: dedupe,
  the sorter's "already in the library" warning and cached work depend on them.
- `webp_optimized`: `1` done/native, `0` pending, `-1` skipped (larger).

Indexes: `files(folder_path)`, `files(folder_path, LOWER(name))`, `files(type)`,
`files(has_thumb)`, `files(has_animated)`, `files(size)`, `files(quick_hash)`,
`files(thumb_hash)`, `files(width)`, `folders(parent_path)`,
`folders(parent_path, LOWER(name))`, `ignored_duplicates(path1, path2)`,
`ignored_duplicates(created_at DESC)`, `favorites(created_at DESC)`.

### Artwork cache keys (never change)

```
key         = sha256(f"{path}_{mtime}_{size}")[:24]
poster      = THUMBNAIL_DIR / f"thumb_{key}.jpg"
animated    = THUMBNAIL_DIR / "animated" / f"anim_{key}.webp"
candidates  = THUMBNAIL_DIR / "candidates" / f"v{sha256(path)[:24]}" / f"cand_{ss}.jpg"
```

A rename/move **moves** the cached files to the new key and a delete removes
them. The owned runtime sweeps files whose index row is gone (strict 24-hex
match protects an in-flight temp file). Changing the key function would orphan
every poster on disk.

---

## 6. Background runtime

`runtime.start_runtime()` starts these only when `OWN_WORKERS=true`; each is a
daemon thread that pauses between items.

| Unit | What it does |
|---|---|
| `CrawlerThread` | Incremental crawl of the library: folders, ZIP archives, file rows; prune with mount-safety guards (confirm gone with retries, abort a folder whose children mostly look missing). Inspects the full mount after `POST /api/system/index/sync`. |
| `MetadataWorker` | One FFprobe pass per video/image: display dimensions (rotation applied), duration, codecs, fps, capture time; ZIP members take dimensions from the cached poster. |
| `ThumbnailSweep` | Posters for `has_thumb = 0`. ZIP members are skipped (`-1`) because they are generated on demand. |
| `_BatchSweep("webp-sweep")` | Converts loose images to WebP, releasing heap between batches. |
| `_BatchSweep("content-hashes")` | Quick hashes files that share a size. |
| `_BatchSweep("visual-hashes")` | pHashes posters for visual duplicate clustering. |
| Backup thread | Snapshot every `BACKUP_INTERVAL_HOURS` when idle, and once at shutdown. |
| `animated` queue | On-demand generation of looping WebP previews (browse asks for one, one job at a time). |
| `x265` queue | Serial in-place HEVC re-encodes with live progress and cancel. |
| `DownloadManager` | Up to `DOWNLOAD_WORKER_CONCURRENCY` yt-dlp/gallery-dl jobs, 500-entry history. |
| ZIP batch runner | One compress/unpack archive at a time; pause takes effect between items. |

Progress is exposed by `/api/system/status` (index, workers, runtime ownership)
and the per-feature status routes. x265, downloads and ZIP batches are also
in-memory: a restart stops running jobs and clears their history.

---

## 7. Media pipelines

### Posters and animated previews
- Poster: FFmpeg `-ss 1 -frames:v 1`, 360 px, atomic replace; Pillow fallback
  for images. Generated on demand for a tile/viewer, and by the sweep in bulk.
- Animated preview: ~24 candidate frames sampled across the duration at 480 px,
  compiled to a looping WebP (2 fps). Only played where the client asks
  (hover on desktop, visible tiles on touch).
- The candidate-frame cache is binned per video so it can be swept.

### Playback and transcoding
- `/api/media` streams with HTTP 206 ranges (1 MB chunks), ETag/304 and HEAD.
- Containers the browser cannot play are streamed through
  `/api/media/transcode` as fragmented MP4 (`libx264 -preset ultrafast
  -tune zerolatency`, audio AAC) — playback starts at the first fragment and
  `ss` restarts the pipe for a seek. The sorter has the same pair under
  `/api/sorter/stream` and `/api/sorter/transcode`.
- x265 conversion (`POST /api/transcode/x265`) re-encodes in place to HEVC
  (`libx265 -preset medium -crf 18 -tag:v hvc1`), stream-copies MP4-compatible
  audio else AAC 192k, commits atomically (`.mp4` sibling for non-MP4 sources),
  rewrites the row (`video_codec='hevc'`, hashes cleared, `has_thumb=0`) and
  deletes the stale poster.

### WebP
Loose images convert one file at a time (atomic `.tmp` → `os.replace`), poster
and hashes are updated, and a native `.webp` is marked optimised without
conversion. Archives are rewritten as a unit on demand (browse or
`POST /api/zip/webp/optimize`), never by the background sweep.

### ZIP
- `zipfs` inspects the central directory (fast, no extraction) and extracts a
  member into `ZIP_CACHE_DIR/<hash>` on first access, LRU-trimmed to
  `ZIP_CACHE_MAX_SIZE_GB`.
- Compress: build on local staging, verify with `testzip()`, copy as `.tmp`,
  `os.replace`, then replace the folder's index subtree with archive members.
- Unpack: extract to a temporary sibling (skipping `..`), rename into place,
  delete the archive, re-index.
- A per-archive lock serialises compress, unpack, WebP rewrite and sync-back.

### Dedupe
Content groups come from identical sizes and `quick_hash`; every deletion is
preceded by a full byte comparison. Visual clusters use complete linkage on the
64-bit pHash (distance ≤ 2) capped at 12 files. Ignored pairs are stored
normalised and filtered from every scan.

### Downloads
Jobs run yt-dlp (`--ignore-errors`, skip live/premiere, best video+audio merged
to MP4), with gallery-dl as the fallback for sites yt-dlp lacks — including its
`[Piracy]` blocklist refusals — and for `gofile.io`, which skips yt-dlp
entirely. Media extensions land in the target folder with `auto_rename`
collisions; sidecar files are dropped. Retry clones a failed/cancelled job,
clear-history drops finished rows only.

### Snapshots and recovery
`POST /api/backups/snapshot` writes a `sqlite3.backup` copy (VACUUM-compacted),
a poster tar and `meta.json` into `LIBRARY_BACKUP_DIR` (meta swapped in last).
Startup restores when the index or posters are missing/empty; a non-empty index
is never touched. `POST /api/backups/restore` requires `ALLOW_LIVE_RESTORE=true`;
the supported path is `python -m media_library.restore --yes` with the service
stopped.

---

## 8. API map

Base `/api`. OpenAPI: `/api/docs`, `/api/openapi.json`. All routes have an
explicit `response_model`; errors are `{"detail": …}` with a meaningful status
(403 outside the root, 404 missing, 409 conflict, 422 validation, 503 index
unavailable / read-only).

### Auth and system (`routers/auth.py`, `routers/system.py`)
| Route | Purpose |
|---|---|
| `GET /auth/session` | `{enabled, authenticated}`; public |
| `POST /auth/login` / `POST /auth/logout` | Set / clear the signed cookie |
| `GET /health` | Public liveness probe: status, version, database, commit; 503 when the index is unreadable |
| `GET /system/info` | Version, library root, counts, capability flags |
| `GET /system/status` | Counts, runtime mode, crawler state |
| `GET /system/index/status` | Owned runtime + crawler counters |
| `POST /system/index/sync` | Forced crawl; 409 unless `OWN_WORKERS` |
| `GET /system/onboarding` | First-run tour payload (paths, counts, tools, backup) |

### Library (`routers/library.py`, `media.py`)
`GET /library/browse?path=&offset=&limit=&type=&favorite=&sort=` ·
`GET /library/folders?path=` · `GET /library/folders/search?q=&limit=` ·
`GET /library/search?q=&type=&sort=&limit=&offset=` ·
`GET /library/favorites?type=` · `GET /media?path=&download=` (206 ranges) ·
`GET /media/transcode?path=&ss=`

### Favorites and mutations (`routers/favorites.py`, `manage.py`)
`GET /favorites/counts` · `POST /favorites/toggle` · `POST /favorites/batch` ·
`POST /manage/rename` · `POST /manage/bulk-rename` · `POST /manage/delete` ·
`POST /manage/folders` · `POST /manage/folders/rename` · `POST /manage/move`

### Uploads (`routers/uploads.py`)
`GET /uploads/status?upload_id=` · `POST /uploads/chunk` (explicit byte offset;
duplicates acked, gaps 409) · `POST /uploads/finalize` (refuses a size mismatch) ·
`POST /uploads/cancel`

### Sorter (`routers/sorter.py`)
`GET /sorter/config` · `GET /sorter/queue` · `POST /sorter/refresh` ·
`GET /sorter/destinations?q=&limit=` · `POST /sorter/folders` ·
`POST /sorter/move` · `POST /sorter/undo` · `POST /sorter/rename` ·
`POST /sorter/delete` · `GET /sorter/duplicate?path=` ·
`GET /sorter/stream` · `GET /sorter/transcode` · `GET /sorter/thumbnail`

### Downloads (`routers/downloads.py`)
`POST /downloads/queue` · `GET /downloads/status` ·
`GET /downloads/history?limit=&offset=` · `POST /downloads/cancel` ·
`POST /downloads/retry` · `POST /downloads/clear-history`

### Thumbnails (`routers/media.py`)
`GET /thumbnails?path=&kind=static|animated` · `GET /thumbnails/exists?path=` ·
`GET /thumbnails/candidates?path=&interval=&count=` ·
`GET /thumbnails/frame?path=&timestamp=` · `POST /thumbnails/poster` ·
`POST /thumbnails/animated` · `POST /thumbnails/animated/batch` ·
`GET /thumbnails/animated/status` · `POST /thumbnails/animated/cancel`

### Processing (`routers/webp.py`, `zip.py`, `zip_batch.py`, `x265.py`)
`GET /webp/status` · `POST /webp/optimize` ·
`GET /zip/check?path=` · `GET /zip/overview` · `POST /zip/compress` ·
`POST /zip/unpack` · `POST /zip/webp/optimize` ·
`POST /zip/batch/start|pause|resume|cancel` · `GET /zip/batch/status` ·
`POST /transcode/x265` · `GET /transcode/x265/status?path=` ·
`POST /transcode/x265/cancel`

### Dedupe, analytics, cleanup, backups
`GET /dedupe/status` · `GET /dedupe/groups?mode=content|visual&limit=&offset=` ·
`POST /dedupe/hash` · `POST /dedupe/verify` · `POST /dedupe/resolve` ·
`POST /dedupe/ignore` · `GET /dedupe/ignored` · `POST /dedupe/unignore` ·
`GET /analytics/overview?folder_limit=&file_limit=&refresh=` ·
`GET /folders/empty` · `POST /folders/empty/delete` ·
`GET /backups/status` · `POST /backups/snapshot` · `GET /backups/verify` ·
`POST /backups/restore` · `GET /backups/download?kind=db|thumbs`

Any non-API page path returns the SPA shell; unknown `/api/…` and `/static/…`
paths keep their JSON/static answer.

---

## 9. Client

`frontend/src`, built by the root npm project. Routing is `react-router`;
server state lives in TanStack Query (`lib/queries.ts`), small UI state in
Zustand stores (`lib/stores.ts`). `lib/api.ts` is the only place the API base
(`/api`) is written.

- **Library**: justified rows (`lib/justified.ts`), virtualised gallery, poster
  hover previews, viewer with poster chooser, x265 action, move/rename/delete,
  multi-select and bulk actions, uploads with resume (`lib/upload.ts`).
- **Polish**: theme (light/dark/system, accent), command palette (cmdk),
  onboarding tour (`lib/onboarding.ts`, dismissal in `localStorage`), Sonner
  toasts, `@fontsource` variable fonts.
- **Sorter**: queue with preloading, 1–6 destination keys, search, create-and-
  move, undo, rename, delete, duplicate warning.
- **Numbers**: every dashboard figure comes from `/api/analytics/overview`.
- **Escaping**: user-controlled strings render through JSX text (escaped) or
  `textContent`; never `dangerouslySetInnerHTML` with a filename, download
  title or server message.

## 10. Development and deployment

```bash
python3 -m venv venv && ./venv/bin/pip install -r requirements.txt
npm ci
npm run dev            # Vite dev server on :5173, proxying /api and /static
./venv/bin/python app.py --host 0.0.0.0 --port 8888
./start.sh             # venv bootstrap + LAN IP, runs python app.py
```

Production: the `media-library.service` unit runs `app.py` with
`Restart=always` and `MALLOC_ARENA_MAX=2`; `systemctl restart media-library`
applies any change. The optional watchdog timer restarts a process that holds
its port but stops answering `/api/health`.

Tests, in order: `./venv/bin/pytest`, `npm test`, `npm run build`,
`./venv/bin/ruff check .`, `./venv/bin/ruff format --check .`.

## 11. Agent checklist

1. [ ] Code follows the module layout above; new logic has type hints and a
       clear failure mode.
2. [ ] No directory walk or repeated `stat` in a request handler; no write
       transaction on the event loop or across remote I/O.
3. [ ] Connections come from `db.reader()`/`db.writer()` and are always closed.
4. [ ] New state is visible in `/api/system/status` (or its feature route).
5. [ ] `./venv/bin/pytest` and `npm test` pass; `npm run build` succeeds.
6. [ ] `ruff check` and `ruff format --check` pass.
7. [ ] AGENTS.md, `.env.example`, README and CHANGELOG match the change; the
       version lives only in `media_library/__init__.py`.
8. [ ] `/static/dist/` rebuilt if `frontend/` changed; service restarted.
9. [ ] Nothing personal or secret is staged; the commit is pushed.
