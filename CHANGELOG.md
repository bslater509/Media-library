# Changelog

All notable changes to **Media Library & Video Sorter** are documented in this
file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
The version lives in exactly one place — `media_library/__init__.py`'s
`__version__` — and is reported by the API, the served pages and the backup
snapshot metadata.

## [1.1.5] - 2026-10-17

### Added

- **A `/people/review` page that works through unmatched face clusters.** With
  thousands of unnamed clusters and a handful of named people, matching them one
  by one on `/people` meant opening a cluster, opening the merge sheet and picking
  from a list — so the review queue does it in one tap per cluster. It shows the
  biggest unnamed cluster at a time (cover, eight of its crops, its face/file
  counts, and the folders its faces are filed in) with the six most likely people
  as **name pills** — the people you have already named, ranked by the folders they
  share with this cluster, which is the same `near=` ranking the merge sheet uses —
  so one tap folds the cluster into that person; a **More…** pill (showing how many
  others there are) opens the full list with crops, the folder-path search and the
  `1`–`9` keys, and the pills are numbered so the keyboard does the same thing.
  **Name** turns the cluster into a new person; **Not a person** dismisses it, with
  a confirm, since a dismissal is permanent. Every action removes the cluster from
  the unnamed set, so the queue needs no state of its own: the next request is
  simply the largest remaining cluster, and a reload lands where a fresh queue
  would. `M`/`N`/`X` and `1`–`9` drive it, `?` opens a key reference rendered from
  one table, the header counts what is left to review and how many faces have been
  dismissed, and `/people` gained a **Review unmatched** button badged with that
  count. `GET /api/people` now also reports `folder_names` per row and takes
  `samples` (1–12) for how many crops a row carries, so the same endpoint serves
  both the pills and the full list.

## [1.1.4] - 2026-10-17

### Added

- **The `/people` "Merge into…" picker ranks same-folder clusters first.** The
  media collection is filed per folder, so the cluster that is really the same
  person as the one you opened is nearly always filed right next to it — but the
  picker listed clusters by size, which buried the right choice under thousands
  of unrelated ones. The listing endpoint grew a `near=<person_id>` parameter
  (`GET /api/people?named=false&near=…`) that orders the candidates sharing
  folders with that person's faces first (then biggest first), and each row is
  annotated with `shared_folders`/`shared_faces`/`shared_folder_names` and badged
  with the shared folder's name so the ranking is legible. The weight
  (`affinity`) is the dot product of the two clusters' per-folder face counts, so
  a folder the person is all over outranks one they merely pass through (a folder
  shared with a whole cast would otherwise bury the duplicate).

## [1.1.3] - 2026-10-17

### Changed

- **The `/people` "Merge into…" picker now lists unnamed clusters only.** Merging
  folds the picked clusters' faces into the cluster the sheet was opened from, so
  a named person in that list was only an invitation to throw its name away; the
  candidates are now the unnamed clusters, which is what you actually want to fold
  in. It also loads its own paged list (`GET /api/people?named=false`, a new
  `named` query on the listing endpoint) instead of the grid page rendered behind
  the sheet, with a "Show more" row, so the choices are no longer limited to the
  ~120 clusters that happened to be loaded, and each row shows three of the
  candidate's face crops rather than a single cover so the right cluster is
  recognisable. The sheet header states the direction of the merge ("their faces
  move into …").

## [1.1.2] - 2026-10-17

### Fixed

- **The `/people` "Merge into…" picker was clipped top and bottom on a phone**
  and its cover thumbnails were too small to recognise. The dialog was a centred
  box sized in `vh`, which on a phone lays out against the *large* viewport, so
  its header sat under the mobile top bar and its Merge/Cancel footer under the
  browser's bottom bar. It is now a bottom-anchored full-height sheet
  (`.ml-sheet` / `.ml-sheet-card`, `100dvh`), matching the Library's other tall
  panels: the grabber and header are pinned at the top, the list is the single
  scroll region, and the footer stays clear of the bottom of the screen
  (safe-area aware). The sheet can also be swiped down to dismiss, and each row's
  cover crop grew from 44px to 64px.

## [1.1.1] - 2026-10-17

### Fixed

- **`/people` cards and covers were all showing broken images**: the crop and
  thumbnail URLs built by `frontend/src/people/index.js` were run through
  `safeAttrEscape` (which percent-encodes the whole string, `/` and `?`
  included) before landing in an `<img src>` attribute, so every request went to
  a mangled relative path and 404'd. URLs in `src` attributes are now
  HTML-escaped (`escapeHtml`), which leaves the URL intact; only the arguments
  interpolated into inline JS strings keep percent-encoding.
- **`/people` opened scrolled down, hiding the top of the view behind the
  mobile top bar**: the page scrolls inside `<main>` (the body is a
  viewport-locked column), but switching views called `window.scrollTo(0, 0)`,
  which does nothing there — so opening a person from a scrolled list, or
  returning with "All people", kept the previous scroll offset and clipped the
  new view's top. Both switches now reset the stage's `scrollTop`.
- **`/people` could be dragged sideways on a phone**: the status chip row was
  `shrink-0` and the progress line's filename did not truncate, so the content
  was ~46px wider than the viewport. The chips now shrink and wrap, the progress
  file name truncates with an ellipsis, and the stage clips horizontal
  overflow.

## [1.1.0] - 2026-10-17

### Added

- **Face recognition ("People")** — opt-in via `FACE_RECOGNITION_ENABLED=true`,
  off by default. A background worker detects faces in loose images (read from
  the already-cached thumbnails, so no source file crosses the FUSE mount) and in
  videos (480px sampled frames, one every `FACE_VIDEO_SAMPLE_INTERVAL` seconds,
  each hit keeping its timestamp as a "moment"), embeds each face with the
  dependency-light OpenCV pair YuNet + SFace (no torch, no onnxruntime; the two
  ONNX files are downloaded once into `DATA_DIR/models` and pinned by SHA-256),
  and groups them into person clusters. Faces join the nearest existing person
  within a cosine-distance threshold (`FACE_MATCH_THRESHOLD`, or the
  Strict/Balanced/Loose control on the new page, stored in the DB so it applies
  without a restart) and otherwise start a new cluster.
- **`/people` page** (nav tab in the Library, Sorter, Downloads and Analytics
  headers, plus a Library sidebar drawer link): cluster grid with cover crops and
  counts, per-person view with their media (a video appears once, with
  jump-to-moment chips that open the lightbox at that second), a face review
  strip for selecting faces, and curation — rename, merge clusters, split faces
  out, dismiss faces or a whole cluster as "not a person" (persisted so a re-scan
  never re-adds them), re-scan one person's files, and a "Re-group all"
  maintenance action that re-clusters everything (scipy, chunked so memory stays
  bounded) while keeping existing names by nearest centroid.
- **Search by person**: the Library search box understands `person:anna` (and
  `person:"anna smith"`), composing with the existing filename/folder words, and
  reports an unknown name instead of looking like an empty search. The person
  page deep-links into it (`/?q=person:Anna`).
- **Face Scanning card** in both Background Tasks modals (progress, current
  file, people/faces totals, crop-cache usage, "Scan all", "Open →"), plus the
  `faces` block in `/api/system/status` and the worker in `/api/health`'s
  liveness count. Face scans, cluster edits and crop-cache evictions are all
  covered by `GET /api/people/status`.
- **Liveness probe** `GET /api/health`: a cheap, credential-free check for
  systemd, uptime monitors and container healthchecks — reachability and worker
  liveness only, never a library path, count or filename — returning `503` when
  the database cannot be read. It also reports the running build's short git SHA
  (`commit`, from `media_library/core/build_info.py`, or a `GIT_COMMIT`/
  `BUILD_COMMIT` env value), so "is the live server on the latest commit?" is a
  one-line check instead of comparing file mtimes.
- **Continuous integration** (`.github/workflows/ci.yml`): `ruff check` +
  `ruff format --check`, `pytest`, and `npm test` + `npm run build` run on every
  push and pull request.
- **Keyboard shortcut reference** (`?`, or the Shortcuts button in the desktop
  header / the mobile Controls sheet's *System Status* tab): a full-height sheet
  listing every grid and viewer binding, rendered from one `SHORTCUT_GROUPS`
  table in `frontend/src/library/shortcuts.js` so the help cannot drift from the
  keydown handler.
- **Resumable browser uploads**: chunks are now staged at a verified byte offset
  and the new `GET /api/backup/upload/status` reports what the server already
  holds, so an interrupted upload (dropped connection, closed tab, phone asleep)
  resumes instead of re-sending gigabytes. A retried chunk is acknowledged as a
  duplicate rather than appended twice, and a short upload is refused at
  finalize instead of landing silently truncated.
- **Animated preview cache housekeeping**: orphaned `anim_*.webp` files are swept
  by the index prune/deep clean alongside thumbnails, the directory is trimmed by
  an LRU ceiling (`ANIMATED_CACHE_MAX_SIZE_GB`, default 5 GB — evicted previews
  are regenerated on the next view), and the Background Tasks modal's Animated
  Preview Generator card now reports the cache's size against its cap.
- **Favorites count endpoint** (`GET /api/backup/favorites/count`): the Library
  sidebar badge asked for the full favorites list — every favorite shaped into a
  browse entry with its thumbnail/media URLs — and read only `counts.all` from
  it, on every page load and every star toggle. The new endpoint answers with a
  single aggregate (~3 ms and a few bytes for 6,000 favorites, against ~130 ms
  and ~4.2 MB for the list), the `toggle` and `batch` responses now carry the
  fresh totals so a star updates the badge with no extra request at all, and the
  `type`-filtered list read only fetches the rows it returns.

### Fixed

- **Face rows follow their files**: moves, renames and deletes now rewrite the
  `faces`/`face_scans`/`dismissed_faces` rows (and a folder or ZIP move rewrites
  the whole subtree), so a person's media list survives sorting the library, and
  a restore that replaces the index drops the stale cluster centroids instead of
  assigning new faces to people that no longer exist.
- **Toast markup injection**: both toast helpers (`frontend/src/library/utils.js`,
  `frontend/src/app/state.js`) inserted their message with `innerHTML` while call
  sites interpolated filenames, folder names and server error text, so a file
  named `<img src=x onerror=...>.mp4` could execute script in the UI when it was
  renamed, moved or deleted. Messages are now inserted as text, and each toast is
  its own live region (`role="alert"` for errors, `role="status"` otherwise).
- **Import cycle in the DB layer**: `db/indexer_prune.py` no longer imports
  `services.anim_cache` at import time (it is imported inside the functions that
  use it), which removes the `db.indexer` ⇄ `services` ⇄ dedup cycle that turned
  `import media_library.db.indexer` into an `ImportError` as soon as the indexer
  facade's import order changed.

### Changed

- Removed the wildcard `CORSMiddleware` from `create_app` (the app is
  same-origin only; the wildcard-plus-credentials pair is rejected by browsers
  anyway).
- Applied `ruff check --fix` and `ruff format` across the tree, and the docs now
  state the real floor: Python 3.10+ (PEP 604 union syntax is evaluated at
  runtime), not 3.9.
- **Library search now matches folder names**: each query word may appear in the
  filename *or* in the file's folder path inside the library, so "iceland 2023"
  finds the clips in `…/Iceland 2023/`. The root and its ancestors are excluded
  from the match so a shared parent name cannot return the whole library.
- Abandoned upload staging files are garbage-collected at startup
  (`UPLOAD_STAGING_TTL_HOURS`, default 24 h), and an in-place x265 conversion
  drops the converted file's now-orphaned animated preview immediately.

## [1.0.0] - 2026-10-17

First public release. Self-hosted media asset management and rapid keyboard
video sorting for large collections (tens of thousands of files), designed to
stay fast on high-latency FUSE mounts such as pCloud or a network share.

### Added

- **SQLite-backed media library** with sub-millisecond folder browsing and
  multi-word, library-wide search (`SEARCH_LIMIT`-bounded, with a truncation
  indicator and query-refinement prompts).
- **Justified, natural-aspect-ratio gallery**: tiles packed into rows that fill
  the container exactly, aspect ratios taken from probed `width`/`height`
  (clamped 2:3–3:2), five persisted tile-size presets (XS–XL), duration and
  resolution badges, H.264/H.265 codec pills, high-framerate tags and
  card-footer dimensions.
- **Fullscreen lightbox** (Fancybox 5) with HTTP 206 byte-range streaming,
  playback speed 0.5×–2.0×, loop toggle, frame stepping, persistent
  volume/mute, custom poster-frame capture, a candidate-frame thumbnail chooser
  with timeline scrubbing, in-viewer move/rename/delete, download and metadata
  inspection. Video slides are sized to each clip's own aspect ratio rather
  than a fixed 16:9 box, with a bottom overlay control bar that keeps clear of
  the picture and the browser's native controls.
- **In-place H.265 (x265) conversion** from the lightbox (`libx265 -crf 18`,
  audio stream-copied where possible, atomic replace, live percent/fps/speed/
  ETA with per-job cancel, index and poster regeneration on completion).
- **Proactive poster and animated preview caching** via FFmpeg: background
  thumbnail generation plus looping animated WebP previews (2 fps, up to 24
  sampled frames) that play on hover on desktop and on-screen on touch, cached
  in browser `CacheStorage` through a service worker for instant recall, and
  paused whenever a tile is off-screen, in a background tab, or open in the
  lightbox.
- **Favourites/bookmarking** across the library and the sorter: per-folder
  filter pill, global sidebar collection, star actions on tiles, in the
  lightbox and in context menus, bulk star/unstar, and a dedicated SQLite table
  so rescans never lose bookmarks (starred sorter moves carry the star over).
- **Incoming video sorter**: sub-50 ms keyboard review with autoplay and
  pre-buffering, one-touch destination moves, "+ Create & Move", category
  search, a full undo stack, plus a mobile thumb-zone action bar.
- **URL and playlist downloads** (`/downloads`): multi-URL paste, recent-target
  chips and a folder-tree picker, yt-dlp with an automatic gallery-dl fallback
  for image boards and file hosts, parallel job slots, live progress with
  speed/ETA, paginated history, per-job cancel, retry of failed jobs and
  clear-history. Also reachable inline from the mobile Controls sheet.
- **Library Analytics dashboard** (`/analytics`): an index-only, read-only
  overview with a zoomable/roamable storage treemap, largest folders and files
  with in-page preview, media-mix and top-extension charts, a quality/runtime
  histogram and reclaimable-space and health panels that link into the tool
  that does the work. Charts are a tree-shaken ECharts build themed from the
  live theme tokens, with no runtime dependency and no CDN request.
- **ZIP archives as virtual folders**: central-directory browsing without
  extraction, an on-demand LRU extraction cache, folder-to-ZIP compression with
  on-the-fly WebP conversion, unpack back to disk, debounced sync-back for
  modified archives, and batch compress/extract with start/pause/resume/cancel.
- **Deduplication engine**: exact content matching via 128 KB head hashes plus
  size, with full-content verification before any auto-delete, and perceptual
  (pHash) visual duplicate detection — with group verification, conflict
  resolution, and ignore/unignore rules for false positives.
- **Automatic WebP optimisation** for loose images and on-demand archive
  rewrites, with cumulative space-savings accounting and a memory guard that
  backs off before the service cgroup cap so long sweeps cannot wedge the host.
- **Chunked browser uploads** (files and folders, drag-and-drop) with mobile
  iOS photo/video picking including HEIC/HEIF/DNG, staging-based atomic writes,
  auto-rename/skip/overwrite collision policies and instant re-indexing.
- **Library file management**: multi-select bulk move/delete/rename/WebP/
  animated-preview actions, folder create/rename/recursive delete, empty-folder
  cleanup, and a queued library-ops worker with per-item cancel and a move-only
  undo stack.
- **Zero-work backup and recovery**: live SQLite snapshots (`sqlite3_backup`)
  plus uncompressed thumbnail bundles inside the library folder, with automatic
  restore on startup when the local database or thumbnail cache is missing and
  a snapshot on clean shutdown. Snapshots wait until every background subsystem
  is idle.
- **Theme and appearance customiser**: six accent palettes, Midnight Stone /
  Pure OLED Black / Slate Navy surfaces, live CSS-custom-property updates with
  no flash on reload, and full lightbox/mobile parity.
- **Optional single shared-password gate** (`APP_PASSWORD`) over the whole UI
  and API using a stateless signed cookie, with a sliding 400-day expiry and no
  server-side session store.
- **Mobile experience**: auto-hiding single-line header, 5-tab icon-only
  controls sheet, full-height sheets with swipe-to-dismiss, safe-area-aware
  layouts, double-tap ±5 s seek, and touch swipe navigation.
- **Unified `/api/system/status`** aggregation so every worker, queue, crawl and
  conversion reports from one polled endpoint, with a truthful index/staleness
  report instead of a false 100%.
- **Incremental, mount-safe indexer**: mtime-keyed crawls that skip unchanged
  folders, prune confirmation with retries for flaky remote `stat` calls, and
  an abort threshold so a transient FUSE hiccup can never wipe the index.
- **544 backend tests across 49 modules** (pytest with sandboxed fixtures) and
  **133 frontend tests across 12 files** (vitest with happy-dom), plus
  version-consistency and repository-hygiene tests that fail if personal data,
  credentials or local state can reach a commit.
