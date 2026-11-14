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
- **Sorter**: reviews a source folder at a few keystrokes (1–6 destinations,
  `/` to search a folder, `Enter` to move), with duplicate warnings, undo,
  rename, delete and live transcoding for containers browsers cannot play. The
  folder defaults to the incoming folder and can be switched to any folder on
  the system from the page (persisted in `SORTER_STATE`).
- **Processing**: background poster and looping animated-WebP previews,
  metadata probing (dimensions, duration, codecs, fps, capture time), live
  transcoding for non-web containers, in-place x265 (HEVC) conversion, WebP
  optimisation, dedupe (content hash and perceptual pHash), ZIP unpacking and
  virtual archive browsing, URL+playlist downloads (yt-dlp, gallery-dl
  fallback), library analytics, and SQLite snapshots with auto-restore.
- **Auth**: optional single shared password (`APP_PASSWORD`), stateless signed
  cookie, no server-side session store.

### Stack

| Layer | Choice |
|---|---|
| Runtime | Python 3.10+ (PEP 604 unions evaluated at runtime; tested on 3.13) |
| Web | FastAPI + Uvicorn |
| Index | SQLite 3, WAL, `synchronous=NORMAL`, 64 MB page cache, mmap |
| Media | FFmpeg / FFprobe CLI, Pillow (+ pillow-heif for HEIC/HEIF), `imagehash` (+ SciPy for the DCT), NumPy |
| Downloads | yt-dlp / gallery-dl as isolated subprocesses (`python -m …`) |
| Client | React 19 + TypeScript 7, Vite 8, Tailwind 4, TanStack Query, Zustand, Radix, ECharts 6, `yet-another-react-lightbox`, Sonner, cmdk |
| Tests | Pytest 8 (+ cov), Vitest 5 |
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

`pytest.ini` puts `-n auto` in `addopts`, so a full run uses every CPU
(`pytest-xdist`); `.coveragerc` selects coverage's `sysmon` core on Python 3.12+.
Both only change wall-clock time, never results. Pass `-n0` to run serially
(useful under a debugger or when profiling), and remember that a subset run
with `--no-cov` skips the 100% gate that only makes sense for the full suite.

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
│   ├── activity.py            # memory-only telemetry + cooperative browsing priority
│   ├── transcode_stream.py    # live fragmented-MP4 pipe
│   ├── x265.py                # in-place HEVC queue
│   ├── web_mp4.py / web_mp4_log.py # independent checking/replacement + failure history
│   ├── webp.py                # WebP optimisation
│   ├── dedupe.py              # quick hash, pHash, resolve
│   ├── downloads.py           # yt-dlp / gallery-dl manager
│   ├── sorter.py              # incoming queue, destinations, moves, undo
│   ├── tools.py               # downloader tool versions, PyPI update checks,
│   │                          # in-place pip updates with streamed progress
│   ├── zipfs.py / zip_ops.py / zip_batch.py
│   ├── cleanup.py             # empty-folder cleanup
│   ├── analytics.py           # index-only rollups
│   ├── backups.py / restore.py
│   ├── schemas.py             # Pydantic response models (the OpenAPI contract)
│   ├── views.py               # SPA shell delivery
│   └── routers/               # auth, system, library, media, favorites, manage,
│                              # uploads, sorter, downloads, analytics, dedupe,
│                              # webp, cleanup, zip, zip_batch, backups, x265,
│                              # history, tools, activity
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
                               # download_archives/, download_queue.json,
                               # cache/zips/, backup_staging/
```

---

## 4. Configuration

Read from the process environment first, then `.env`. Some `V2_…` names
(Paths, staging dirs, `OWN_WORKERS`, `DB_READ_ONLY`, `WRITES`, limits, backups)
are still accepted as a fallback so a pre-upgrade `.env` keeps working; the
full set has plain names only. `HOST`, `PORT`, `APP_PASSWORD`,
`GIT_COMMIT`/`BUILD_COMMIT` and `LOG_LEVEL` are read straight from the
environment by their own modules, not through `config.py`'s `Settings`.

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
| `DOWNLOAD_STATE` | `$DATA_DIR/download_queue.json` | Persisted download queue (restored at startup) |
| `DOWNLOAD_ARCHIVE_DIR` | `$DATA_DIR/download_archives` | Per-(URL, folder) resume ledgers; unused ones pruned after 30 days |
| `DOWNLOAD_WORKER_CONCURRENCY` | `2` | Parallel downloads |
| `DOWNLOAD_COOKIES_FILE` | *(empty)* | Netscape cookies for gated sites |
| `SORTER_STATE` | `$DATA_DIR/sorter_state.json` | Recent sorter destinations and the folder under review |
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
| `LOG_LEVEL` | `info` | Uvicorn log level |
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
watch_history(path PK, first_viewed_at, last_viewed_at,
              seconds_watched, play_count)
visual_frames(path PK, mtime, size, duration, hashes, captured_at)
growth_history(day PK, files_count, video_count, image_count,
               bytes_total, recorded_at)
file_issues(path, issue, detected_at, dismissed_at, PK(path, issue))
```

Hard contracts:

- `folders.is_zip = 1` marks a `.zip` archive browsed as a virtual folder;
  member rows live in `files` with the archive-prefixed path.
- `dir_mtime` is what makes the crawler incremental: a folder whose mtime still
  matches is skipped without re-stat-ing its children (they are still
  traversed). NULL forces a re-scan.
- `width IS NULL` means "not probed yet". Definitive failures (ffprobe rejects
  the file, the format is unidentified) are stored as `0×0` so the file is not
  retried until it changes; transient failures (probe timeouts, mount I/O
  errors) stay NULL and simply retry on a later sweep.
- `has_thumb`: `1` poster cached, `0` pending, `-1` generation failed.
- `has_animated`: `1` preview cached, `0` pending, `-1` failed.
- `quick_hash = sha256(first 128 KB + size)`; `thumb_hash` is a bare 16-hex
  pHash (or `no_thumb`/`blank_thumb`). Both formats are load-bearing: dedupe,
  the sorter's "already in the library" warning and cached work depend on them.
- `webp_optimized`: `1` done/native, `0` pending, `-1` skipped (larger).
- `visual_frames.hashes` is a JSON list of five 16-hex pHashes (or nulls) taken
  at 10–90% of a video's duration. It is keyed by path and kept for as long as
  the file exists: an `mtime`/`size` change does not invalidate it, a rename or
  move rewrites the key, and a delete/prune or `POST /api/dedupe/reset` drops
  it. A visual group can therefore be re-checked without re-running FFmpeg.

- `watch_history` is one rollup row per viewed file: `play_count` counts
  recordings, `seconds_watched` keeps a high-water mark (a shorter rewatch or
  backward scrub never shrinks progress). Rename/move rewrites the path and
  delete drops the row, exactly like favorites.

Indexes: `files(folder_path)`, `files(folder_path, LOWER(name))`, `files(type)`,
`files(has_thumb)`, `files(has_animated)`, `files(size)`, `files(quick_hash)`,
`files(thumb_hash)`, `files(width)`, `folders(parent_path)`,
`folders(parent_path, LOWER(name))`, `ignored_duplicates(path1, path2)`,
`ignored_duplicates(created_at DESC)`, `favorites(created_at DESC)`,
`watch_history(last_viewed_at DESC)`.

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
| `CrawlerThread` | Incremental crawl of the library: folders, ZIP archives, file rows; prune with mount-safety guards (confirm gone with retries, abort a folder whose children mostly look missing). `POST /api/system/index/sync?full=false` runs it incrementally; the default (`full=true`) ignores `dir_mtime`. Every run reports folders scanned/skipped, loose files seen/added/updated/removed, archives rebuilt, elapsed time and the folder in flight through `/api/system/index/status`. |
| `MetadataWorker` | One FFprobe pass per video/image: display dimensions (rotation applied), duration, codecs, fps, capture time; ZIP members take dimensions from the cached poster. |
| `ThumbnailSweep` | Posters for `has_thumb = 0`. ZIP members are skipped (`-1`) because they are generated on demand. |
| `_BatchSweep("webp-sweep")` | Converts loose images to WebP, releasing heap between batches. |
| `_BatchSweep("content-hashes")` | Quick hashes files that share a size. |
| `_BatchSweep("visual-hashes")` | pHashes posters for visual duplicate clustering. |
| Backup thread | Snapshot every `BACKUP_INTERVAL_HOURS` when idle, and once at shutdown. |
| `VerifyWorker` | Index-only sweep that flags `zero_size`, `probe_failed` (0×0) and `thumb_failed` (`has_thumb = -1`) rows into `file_issues`, prunes rows whose `files` row is gone; keyset-paginated batches of 200 paths with a pause between batches, the next full pass ~30 min after the last or immediately on `POST /api/verify/rescan`. ZIP members are never flagged. |
| `GrowthWorker` | Upserts the daily `growth_history` row at process start and on UTC day rollover (wake-up check ~25 min); read-only mode is tolerated by logging into its worker state. |
| `animated` queue | On-demand generation of looping WebP previews (browse asks for one, one job at a time). |
| `poster_batch` queue | Explicit folder/selection poster regeneration at 20% of duration, serial daemon independent of `OWN_WORKERS`; memory-only progress at `/api/thumbnails/poster/status`, respects browsing priority between videos. Restart clears pending work. |
| `x265` queue | Serial in-place HEVC re-encodes with live progress and cancel. |
| `WebMP4Manager` (`web_mp4.py`) | Explicit library-wide H.264/AAC MP4 replacement, independent of `OWN_WORKERS`. Four independent FFprobe checkers can finish the entire confirmed library during one slow encode; compatible files/errors finish immediately, incompatible paths enter a durable replacement backlog. One conversion/verification/commit at a time. SQLite-only preview (first 50 paths/codec hints), one-use 15-minute confirmation token. Compatible streams copy, otherwise libx264 medium CRF 18 / AAC 192k stereo. Sibling staging and full output decode before replacement; conflicts/changed sources/failures preserve originals. Hidden `.mllibtmp-*.original` backups survive the index transaction and restore on failure; crash leftovers are never GC'd. `$DATA_DIR/web_mp4_state.json` v2 atomically stores unfinished paths split into `unchecked` and `ready`, cumulative counters/errors and size/mtime-nanosecond-keyed probe cache at most once per two seconds, plus forced saves before launch/cancel and at completion/shutdown. V1 checkpoints migrate without resetting progress. Shutdown pauses and startup resumes previously running queues; manually cancelled queues need explicit resume. Disk-save failure stops both lanes and surfaces `persistence_error`; cancelling a save waits for its thread before writing a final checkpoint. Browsing priority defers new checks and conversions. Status/check progress/replacement backlog/current phase/cache hits at `/api/transcode/web-mp4/status`, summary in Activity, cancel/resume on Tools. |
| `DownloadManager` | Up to `DOWNLOAD_WORKER_CONCURRENCY` yt-dlp/gallery-dl jobs, 500-entry history. The queue itself (queued and running jobs, with their `file_progress` rows) is written atomically to `DOWNLOAD_STATE` — at most once per 10 s while it changes — and restored as queued on the next start; shutdown flushes it and leaves a running job's staging in place (the job keeps its id, so completed members are imported by the next run). Each job carries a per-(URL, destination, downloader) `--download-archive`, so a retried album skips members it already fetched; finished files that a failed/cancelled job left in staging are imported before cleanup, and a signal-killed downloader puts the job back to queued instead of finishing it. When a gallery-dl job dies because a member's own title exceeds the filesystem's 255-byte filename cap (ENAMETOOLONG), the album is retried once with a byte-bounded filename template (`{filename[b:180]}.{extension}`), so the rejected member lands under a truncated name. gallery-dl jobs also run a `--simulate` listing alongside the download, which fills `file_progress` with every album member (existing destination/`archives` names marked done) so the client shows the whole queue; yt-dlp jobs stay incremental and report `playlist_index`/`playlist_count` (their output template byte-trims the title already). Archive containers are kept in `<destination>/archives` and RAR/7z sets are unpacked there with the system `7z`. |
| ZIP batch runner | Unpacks one archive at a time; pause takes effect between items. |
| Tool updates | `tools.py`: reads the installed yt-dlp / gallery-dl versions from distribution metadata (never importing them in-process), checks PyPI for newer releases at most once a day on a daemon thread, and runs one `pip install --upgrade` at a time with parsed phases (resolving → downloading → installing → verifying), a 30-min watchdog and cancel. In-memory like the x265 queue: a restart stops it and forgets the last result. |

Progress is exposed by `/api/system/status` (index, workers, runtime ownership)
and the per-feature status routes. x265 and ZIP batches are also in-memory: a
restart stops their jobs and clears their history. Downloads persist their
queue (finished history is still memory-only) and resume after a restart.
Web MP4 replacement also persists its queue and aggregate progress; completed
checks are not repeated when resuming. A hard crash can replay work since the
last two-second checkpoint, and an interrupted encode restarts that file.

`activity.py` combines **in-memory-only** worker telemetry at
`GET /api/system/activity`; polling never inspects the remote filesystem,
discovers downloader executables, or runs database counts. Owned metadata,
posters, WebP, content/visual hashes, snapshots, verification and growth rollups
record their active item/last result/error there; crawler, animated previews,
five-frame checks, downloads, x265, ZIP batches and tool updates reuse their
existing memory snapshots. Feature pages remain the detailed source of truth.
The Activity header button is present on every app page and polls every 2 s
while open, 5 s with active work and 30 s while idle, stopping interval polling
when the browser is hidden. Recent download/encode history is bounded to 20
entries per queue.

`POST /api/system/activity/priority` toggles process-wide, **in-memory** browsing
priority (off after a restart; shared across devices). Automatic crawler folders,
metadata/poster/WebP/hash items, and automatic snapshots defer at cooperative
boundaries; queued download/encode jobs, animated previews, five-frame groups
and ZIP archives wait before starting their next item. Current operations finish
without killing FFmpeg or interrupting atomic writes; an album already running
finishes as one download job. Explicit synchronous actions (manual crawl,
snapshot, poster selection, WebP requests) and lightweight verification/growth
SQL work remain available. Waits hold no database transaction, worker queue lock
or WebP conversion lock and owned worker stops/cancelled queues can wake out of
them. Tool installation already requested by the user is not deferred.
`POST /api/system/activity/action` exposes only existing supported controls:
download cancel/retry, x265 cancel, ZIP pause/resume/cancel, queued-preview clear,
and tool-update cancel. Clearing previews lets the current preview finish.

---

## 7. Media pipelines

### Posters and animated previews
- Library file and folder-cover URLs use `GET /api/thumbnails/cache/{key}?path=&kind=`.
  The strict 24-hex key addresses local artwork only: hits do not query SQLite
  or resolve/stat the remote original. A miss falls back to the path-based
  endpoint's full validation/generation, off the event loop, with `private,
  no-cache` so a changed source is not cached under an obsolete key. Hits use
  private immutable caching; `thumb_mtime` versions poster selections.
- Poster: FFmpeg seeks to 20% of the indexed duration (FFprobe fallback when
  unknown; 1 second if unavailable, then frame zero on extraction failure),
  `-frames:v 1`, 360 px, atomic replace; Pillow fallback
  for images. Generated on demand for a tile/viewer, and by the sweep in bulk.
  The Pillow fallback registers the `pillow-heif` opener once per process, so
  iOS-captured HEIC/HEIF files get posters, probed dimensions and WebP
  conversion like any other image (`.heic`/`.heif` archive members convert
  too).
- Animated preview: ~24 candidate frames sampled across the duration at 480 px,
  compiled to a looping WebP (2 fps). Only played where the client asks
  (hover on desktop, visible tiles on touch).
- The candidate-frame cache is binned per video so it can be swept.

Library toolbar and selection actions explicitly regenerate posters at 20% for
all videos directly in the current folder (not subfolders or just loaded tiles)
or selected videos. Confirmation warns that manual posters are replaced.
`poster_batch.py` owns a serial, in-memory daemon queue, available even without
owned workers; restart clears it. It cooperatively respects browsing priority
between videos. `GET /api/thumbnails/poster/status` exposes current path,
queued/completed/failed counts and last error; the library polls progress and
refreshes tiles. Failure preserves the old poster. Poster replacements version
`thumb_mtime`, clear `thumb_hash` and invalidate the merged dedupe cache without
clearing independent five-frame evidence or changing videos.

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

### Web-compatible MP4 replacement
Tools → **Convert to web MP4** previews every indexed loose video (archive
members excluded), then requires explicit destructive confirmation. Actual
codecs are probed even for `.mp4`: H.264/yuv420p video with AAC audio of at
most two channels (or no audio) is the broad-browser target. Single-video files
only; unknown duration/probe failures are rejected. Non-MP4 compatible streams
are remuxed; incompatible streams are re-encoded. Audio tracks are retained,
subtitles/attachments are dropped; re-encoding is lossy, so keep a separate
original-media backup (snapshots contain index/artwork only).
Each new MP4 passes codec/audio-count/duration checks and a full error-fatal
FFmpeg decode before replacement. The source's size/mtime must still match.
Existing sibling MP4s are refused. Installation uses native atomic no-clobber
rename or hard links where supported. rclone/FUSE mounts that reject those
operations use an exclusively created, random-token destination reservation,
verify that claim, then atomically rename the verified output over that claim.
This is not an unguarded exists-then-overwrite fallback: pre-existing files and
changed reservations fail safely; other external writers must not modify the
destination during the brief reserved commit boundary. Rollback also refuses
an unexpected source-path occupant and retains the original hidden backup on
such a conflict. Filesystem changes
precede a guarded index transaction; SQL failure restores the original.
Favorites, watch history and lifetime five-frame evidence follow renamed paths;
posters/previews and content/poster hashes are regenerated, stale issue/ignored
pair rows are removed. Disk space is required for sibling output. Cancelling
stops FFmpeg and removes temporary output; an ongoing commit finishes safely.
Four independent parallel checkers validate paths/stat originals and reuse cached FFprobe
results only when actual size/mtime_ns still match. Conversion stays serial;
queued conversion inputs are rechecked if their fingerprint changed.
If FFprobe's initial analysis cannot determine the video pixel format, one
bounded retry increases analysis to 20 seconds / 20 MB, handling long audio-only
lead-ins without falsely rejecting otherwise compatible H.264/AAC MP4s.
Cached probes with unknown pixel format are refreshed rather than trusted.
A resumed
batch retains its confirmed remaining path list and counters (not a new scan).
New scans reuse the persisted probe cache but still stat each source once.
Read-only startup loads progress paused without launching writes; corrupt or
wrong-library state is reported instead of overwritten/automatically resumed.
Checking never waits behind an encode: compatible files and failed checks are
recorded immediately, and incompatible paths accumulate in the confirmed batch's
durable replacement backlog. The active replacement remains in `ready` until its
result is committed. Status `checked` counts completed checks, `unchecked` counts
pending/active checks, and `conversion_pending` includes the active replacement.
Legacy `processed` still counts terminal file outcomes (converted/skipped/failed),
not checks. Tools shows separate checking and replacement sections; each
replacement percentage belongs only to its current encoding/verification phase.
Activity uses the actual `checked` count and backlog, not `processed`.
`web_mp4_log.py` retains an append-only, private
`$DATA_DIR/web_mp4_failures.jsonl` independently of queue history and the latest
100 GUI errors. Each failure records its original path, checking/conversion
stage, UTC occurrence/recording timestamps, diagnostic text and source
size/mtime_ns when available. FFprobe errors include captured stderr rather
than only a command/exit-code exception; missing `moov` metadata is explained in
the GUI. On first startup with this feature, retained recent queue errors are
backfilled with unknown occurrence time and `origin=restored_recent` (older
rolled-off errors cannot be recovered). Appends are serialized off-loop and
fsynced; the log survives new batches, cancellation and restarts. Log-write
failure is visible as `failure_log_error`; recent errors still enter queue state.
Tools offers an authenticated, no-cache NDJSON download for inspection/retesting.

### WebP
Loose images convert one file at a time (atomic `.tmp` → `os.replace`), poster
and hashes are updated, and a native `.webp` is marked optimised without
conversion. Archive members are excluded from the loose-image sweep and are
never rewritten in place; unpack first.

### ZIP
- `zipfs` inspects the central directory (fast, no extraction) and extracts a
  member into `ZIP_CACHE_DIR/<hash>` on first access, LRU-trimmed to
  `ZIP_CACHE_MAX_SIZE_GB`.
- Unpack: extract to a temporary sibling (skipping `..`), rename into place,
  delete the archive, re-index.
- A per-archive lock serialises unpack and the member-cache purge.

### Dedupe
Content groups come from identical sizes and `quick_hash`; a resolve deletes on
that evidence alone (no byte re-comparison, so deletion is instant on a slow
mount). `POST /dedupe/verify` still offers the explicit full byte comparison.
Visual clusters use complete linkage on the
64-bit poster pHash (distance ≤ 2) capped at 12 files. `include_images=false`
removes image rows from content groups, visual groups, status counts and hash
batches (the client's Show images switch, off by default), while the API
default keeps them. Any visual group can be double-checked
against five sampled frames per video (10–90% of duration), and the result is
cached in `visual_frames` for the file's lifetime. The client shows content and
visual groups in one list and preselects the largest file as the copy to keep;
a visual cluster already covered by a byte-identical group is folded into it.
Byte-identical groups and visual groups whose five-frame check
confirmed (`confirmed === true`) delete directly, skipping the confirmation
dialog; visual groups without a confirming check still get it. A bulk
"Delete all" on the page posts every resolvable loaded group to
`POST /dedupe/resolve/batch` (content and 5-frame-confirmed visual; unverified
visual groups only with an explicit switch in the confirm dialog) and reports
one summary with per-group failures.
Fetching groups auto-queues a five-frame check for every
visual group on the page; a batch of checks runs on one daemon thread and
exposes progress (group, file and sampled frame) at
`GET /api/dedupe/verify-visual/status`, so leaving the page does not stop it.
Groups whose frames disagree are hidden by default (`include_rejected=true`
shows them). `POST /api/dedupe/reset` clears every ignored pair and cached
frame check. The combined "all" listing caches its merged group universe for
a few seconds, keyed on the index shape and invalidated by ignore / unignore /
reset / resolve / hash operations.

### Downloads
Jobs run yt-dlp (`--ignore-errors`, skip live/premiere, best video+audio merged
to MP4), with gallery-dl as the fallback for sites yt-dlp lacks — including its
`[Piracy]` blocklist refusals — and for `gofile.io`, which skips yt-dlp
entirely. Media extensions land in the target folder, skipping names the folder
already contains; sidecar files are dropped. Containers (`.rar`, `.7z`, `.zip`)
are kept too: they move to `<destination>/archives` as they complete, and a
RAR/7z set is extracted in place with the system `7z` once its first volume and
siblings are present (extraction happens in a hidden work directory and only
successful output is moved out, so a missing volume leaves no half film; the
first volume's name in `extracted-<hash>.json` marks a set done). The
crawler indexes the unpacked media on its next pass. Every job passes a
`--download-archive` scoped to its URL, destination and downloader, so retrying
a failed album fetches only the members the archive does not list, and whole
files a stopped job left in staging are imported before cleanup (a `.part` is
not media and stays out). gallery-dl albums get a `file_progress` row per
member from a `--simulate` listing that runs alongside the download; names the
destination (or its `archives/`) already holds are marked done, so the client
shows the full queue and where a resume starts. Retry clones a
failed/cancelled job, clear-history drops finished rows only.

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
| `GET /system/activity` | Memory-only global activity rows, active/error counts and browsing priority |
| `POST /system/activity/priority` | `{prioritize_browsing: bool}`; process-wide cooperative scheduling, reset on restart |
| `POST /system/activity/action` | `{id, action: pause\|resume\|cancel\|retry}`; existing controls only; 404 unknown row, 409 unavailable action |
| `GET /system/index/status` | Owned runtime + crawler counters |
| `POST /system/index/sync?full=` | Start a crawl (default `full=true` re-reads every folder; `full=false` keeps the incremental `dir_mtime` skip); 409 unless `OWN_WORKERS` |
| `GET /system/onboarding` | First-run tour payload (paths, counts, tools, backup) |

### Library (`routers/library.py`, `media.py`)
`GET /library/browse?path=&offset=&limit=&type=&favorite=&sort=` ·
`GET /library/folders?path=` · `GET /library/folders/search?q=&limit=` ·
`GET /library/search?q=&type=&sort=&limit=&offset=` ·
`GET /library/favorites?type=&limit=&offset=` · `GET /media?path=&download=` (206 ranges) ·
`GET /media/transcode?path=&ss=`

### Favorites and mutations (`routers/favorites.py`, `manage.py`)
`GET /favorites/counts` · `POST /favorites/toggle` · `POST /favorites/batch` ·
`POST /manage/rename` · `POST /manage/bulk-rename` · `POST /manage/delete` ·
`POST /manage/folders` · `POST /manage/folders/rename` · `POST /manage/move`

### Watch history (`routers/history.py`)
`POST /history/record` (rollup upsert; 503 when the index is read-only) ·
`GET /history/recent?type=&limit=&offset=` · `GET /history/resume?limit=&offset=`
(videos in progress) · `GET /history/unwatched?type=&limit=&offset=`

### Uploads (`routers/uploads.py`)
`GET /uploads/status?upload_id=` · `POST /uploads/chunk` (explicit byte offset;
duplicates acked, gaps 409) · `POST /uploads/finalize` (refuses a size mismatch) ·
`POST /uploads/cancel`

### Sorter (`routers/sorter.py`)
`GET /sorter/config` · `GET /sorter/queue` · `POST /sorter/refresh` ·
`POST /sorter/source` (choose the reviewed folder; null restores the incoming one) ·
`GET /sorter/source/children?path=` (system folder browser for the chooser) ·
`GET /sorter/destinations?q=&limit=` · `POST /sorter/folders` ·
`POST /sorter/move` · `POST /sorter/undo` · `POST /sorter/rename` ·
`POST /sorter/delete` · `GET /sorter/duplicate?path=` ·
`GET /sorter/stream` · `GET /sorter/transcode` · `GET /sorter/thumbnail`

### Downloads (`routers/downloads.py`)
`POST /downloads/queue` · `GET /downloads/status` ·
`GET /downloads/history?limit=&offset=` · `POST /downloads/cancel` ·
`POST /downloads/retry` · `POST /downloads/clear-history`

### Downloader tools (`routers/tools.py`)
`GET /tools/status?refresh=` (installed + latest versions, update job;
the PyPI check runs at most daily on a thread) ·
`POST /tools/update` (pip upgrade for `ytdlp`/`gallery_dl`, one at a time;
503 read-only, 409 while one runs) ·
`POST /tools/update/cancel`

### Thumbnails (`routers/media.py`)
`GET /thumbnails/cache/{key}?path=&kind=static|animated` (local cache-only hit;
fully validated path-based fallback on miss) ·
`GET /thumbnails?path=&kind=static|animated` · `GET /thumbnails/exists?path=` ·
`GET /thumbnails/candidates?path=&interval=&count=` ·
`GET /thumbnails/frame?path=&timestamp=` · `POST /thumbnails/poster` ·
`POST /thumbnails/poster/batch` (`paths` up to 5000 or `folder`, exactly one;
queues indexed videos only, 503 when writes disabled) ·
`GET /thumbnails/poster/status` (memory-only regeneration queue) ·
`POST /thumbnails/animated` · `POST /thumbnails/animated/batch` ·
`GET /thumbnails/animated/status` · `POST /thumbnails/animated/cancel`

### Processing (`routers/webp.py`, `zip.py`, `zip_batch.py`, `x265.py`)
`GET /webp/status` · `POST /webp/optimize` ·
`GET /zip/overview` · `POST /zip/unpack` ·
`POST /zip/batch/start|pause|resume|cancel` · `GET /zip/batch/status` ·
`POST /transcode/x265` · `GET /transcode/x265/status?path=` ·
`POST /transcode/x265/cancel`

Web MP4 (`routers/web_mp4.py`): `GET /transcode/web-mp4/preview` (SQLite-only
snapshot, token/total/archive count/first 50 entries) ·
`POST /transcode/web-mp4/start` (`token`, `confirm_replace: true`; 409 expired
preview or active batch, 503 writes disabled) · `GET /transcode/web-mp4/status`
(memory snapshot of persisted totals, phase/current-file percent, latest 100
failures, `checking`, `checker_concurrency`, `cached`, `remaining`,
`persistence_error`, `checked`, `unchecked`, `conversion_pending`) · `POST /transcode/web-mp4/cancel` (save cancellation and
unfinished paths; finish any active commit) · `POST /transcode/web-mp4/resume`
(resume a previously confirmed unfinished batch with its counters; 409 active
or nothing unfinished, 503 writes disabled or cannot save state).
`GET /transcode/web-mp4/failures/log` downloads the persistent NDJSON failure
history (404 before one exists, `Cache-Control: no-store`); status also includes
`failure_log_error` when initializing/appending that log fails.

### Dedupe, analytics, cleanup, backups
`GET /dedupe/status?include_images=` · `GET /dedupe/groups?mode=all|content|visual&limit=&offset=&include_rejected=&include_images=&auto_verify=` ·
`POST /dedupe/hash` · `POST /dedupe/verify` · `POST /dedupe/verify-visual` ·
`POST /dedupe/verify-visual/batch` · `GET /dedupe/verify-visual/status` ·
`POST /dedupe/resolve` · `POST /dedupe/resolve/batch` (up to 200 keep/delete
sets in one off-thread pass; per-group failures recorded, the rest resolve) ·
`POST /dedupe/ignore` · `GET /dedupe/ignored` ·
`POST /dedupe/unignore` · `POST /dedupe/reset` ·
`GET /analytics/overview?folder_limit=&file_limit=&refresh=` ·
`GET /analytics/growth?days=30` (history, oldest first, plus live today counts) ·
`POST /analytics/growth/refresh` (recompute today's row; 503 read-only) ·
`GET /verify/status` · `GET /verify/issues?issue=&include_dismissed=&limit=&offset=` ·
`POST /verify/rescan` (409 unless `OWN_WORKERS`) · `POST /verify/recheck` (real probe for 0×0 rows) ·
`POST /verify/dismiss` ·
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
  toasts, `@fontsource` variable fonts. Global header Activity panel with
  progress/errors, supported queue controls, links to feature details, and a
  manual process-wide "Prioritize browsing" toggle.
  On desktop (`md` and up), the sidebar sticks to the viewport at `100dvh`;
  its folder list scrolls independently with scroll chaining contained, keeping
  navigation and Settings visible. Main content retains document scrolling
  and the sticky header; mobile navigation is unchanged.
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
