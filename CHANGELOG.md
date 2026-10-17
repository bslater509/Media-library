# Changelog

All notable changes to **Media Library & Video Sorter** are documented in this
file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
The version lives in exactly one place — `media_library/__init__.py`'s
`__version__` — and is reported by the API, the served pages and the backup
snapshot metadata.

## [Unreleased]

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
