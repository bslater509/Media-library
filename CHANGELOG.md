# Changelog

All notable changes to **Media Library & Video Sorter** are documented in this
file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
The version lives in exactly one place — `media_library/__init__.py`'s
`__version__` — and is reported by the API, the served pages and the backup
snapshot metadata.

## [Unreleased]

### Added

- Library folder and selection actions to regenerate video thumbnails at 20%
  into each clip, with confirmation, background progress and per-file failures.
  New posters also use 20% instead of the first second to skip shared intros.
  Replacing a poster refreshes its visual hash and browser cache version.

- **Global background Activity panel.** A header button on every app page
  shows active/queued work, recent results and failures, current files,
  measurable progress, links to detailed feature pages and supported
  pause/resume/cancel/retry controls. Its consolidated status endpoint reads
  memory only and slows polling when closed/idle.
- **Prioritize browsing.** A process-wide toggle cooperatively defers automatic
  heavy sweeps and queued downloads, encodes, previews, visual-check groups and
  ZIP batches at safe boundaries. In-flight work finishes without interrupting
  writes; explicit actions stay available. Priority resets on restart.
  New routes: `GET /api/system/activity`,
  `POST /api/system/activity/priority` and
  `POST /api/system/activity/action`.

- **The Sorter can review any folder on the system.** A "Reviewing" picker on
  the Sorter page (also offered when the incoming folder is missing or the
  queue is empty) browses the system's folders read-only — breadcrumbs,
  subfolders, or a pasted path — and switches the queue to it. The choice is
  persisted in `SORTER_STATE`, survives a restart, and resets back to the
  incoming folder with one button. New endpoints: `POST /api/sorter/source`
  and `GET /api/sorter/source/children?path=`.

### Fixed

- **Independent desktop sidebar scrolling.** The sidebar stays pinned to the
  viewport while its folder list scrolls separately from the main page.
  Navigation and Settings remain visible, and reaching the folder list's edge
  no longer scrolls the page. Mobile navigation is unchanged.

- **Cached artwork no longer waits for the remote drive.** Grid posters, folder
  covers and animated previews use a cache-only URL, avoiding remote source
  path checks on cache hits. Missing artwork falls back to validated generation;
  remote validation runs off the event loop so it cannot freeze other requests.

- **Downloads no longer die on enormous file names.** A gallery-dl album
  whose member title is longer than the filesystem's 255-byte filename cap
  (ENAMETOOLONG) used to fail the whole job every time. The manager now
  retries the album once with a byte-truncated filename template, so the
  rejected member lands under a shortened name while everything already
  fetched is kept (the per-URL download archive skips it).

## [3.3.0] - 2026-11-10

### Added

- **Downloader updates from the Tools page.** The Tools page shows the
  installed and latest yt-dlp / gallery-dl versions and flags a newer release
  (looked up on PyPI at most once a day, never in a request path). An Update
  button runs `pip install --upgrade` as one server-side subprocess with
  streamed phases and progress, the pip log, a cancel button and the pip
  error on failure; running downloads keep the old version until each
  subprocess starts. New endpoints: `GET /api/tools/status?refresh=`,
  `POST /api/tools/update` and `POST /api/tools/update/cancel` — the update
  state is in-memory, one at a time, and 503 while the index is read-only.

### Changed

- **The backend test suite is parallel.** `pytest.ini` now starts one
  `pytest-xdist` worker per CPU, and coverage uses Python 3.12+'s
  `sys.monitoring` core instead of the C tracer. A full 844-test run drops
  from about 77 s to roughly 16 s on an 8-core machine with the same 100%
  coverage gate; `-n0` still forces a serial run.

- **Duplicate deletion is instant.** Resolving a content group deletes on the
  `quick_hash` evidence (same size, same first 128 KB) alone instead of
  re-reading every file in full first, which on a remote mount made deleting a
  large video's duplicate take minutes. The explicit `POST /dedupe/verify`
  still performs a full byte comparison when you want one before deleting.
- **Frame-verified visual duplicates skip the confirmation dialog.** A visual
  group whose five-frame check confirmed now deletes straight from the group's
  Delete button; byte-identical groups do the same, trusted from `quick_hash`
  evidence alone. Visual groups without a confirming check still ask in a
  confirmation dialog first.

### Fixed

- **Large gallery-dl downloads are no longer killed as "wedged".** The
  downloader prints nothing while a file streams in pipe mode, so a file that
  took longer than five minutes — routine on a throttled CDN, and every file
  of a large album — tripped the inactivity timeout. The watchdog now also
  counts growth in the job's staging directory as activity, so it only stops a
  process that has produced no output *and* written no bytes for five minutes.

### Added

- **Bulk delete for duplicates.** A "Delete all" action on the Duplicates page
  resolves every resolvable group loaded on the page with one request:
  `POST /dedupe/resolve/batch` takes up to 200 keep/delete sets, resolves each
  group independently off the request thread, records per-group failures
  (a vanished keep never blocks the rest) and returns one summary. The
  confirm dialog shows what is included — byte-identical groups and
  5-frame-confirmed visual groups — and an explicit switch adds visual groups
  that still have no confirming frame check.

- **Downloaded archives are kept and unpacked.** `.rar`, `.7z` and `.zip`
  containers no longer get dropped by the importer: they move to an `archives`
  folder inside the download destination as each one completes, and a RAR/7z
  set is extracted there with the system `7z` once its first volume and the
  rest of the set are present. Extraction runs in a hidden work directory and
  only successful output is moved into place, so a set with a missing volume
  never leaves a truncated film in the library; the first volume's name is
  recorded in `DOWNLOAD_ARCHIVE_DIR` so a resumed job does not unpack a set
  twice. The queue listing and download filter now include the archive
  extensions, and volumes already in `archives` show as done.

- **Downloads resume, and the queue survives a restart.** Every job now passes
  a `--download-archive` scoped to its URL, destination and downloader, so
  retrying a failed or cancelled album fetches only the members it does not
  already list instead of starting from file 1; whole files a stopped job left
  in staging are imported into the library before cleanup (a partial `.part`
  stays out), and shutdown leaves staging in place for the next run. The
  queued/running jobs and their per-file rows are written atomically to
  `${DATA_DIR}/download_queue.json` at most every 10 seconds and restored as
  queued at startup.

- **The Downloads page shows the whole album queue.** gallery-dl jobs run a
  `--simulate` listing next to the download, which adds a `file_progress` row
  for every album member as it is discovered, marks names the destination
  already holds as done, and drives the job percent from the known queue.
  Active rows open with the full file list (done, current, pending) instead of
  waiting for each file to finish, and finished rows show "stopped after N of
  M files". The listing is best-effort: a slow or blocked album only delays the
  pending rows, never the download.

- **Index scan from the Tools page.** A Library index card starts an
  incremental scan ("Scan for changes") or a full rescan, and shows live
  progress: folders scanned/skipped/discovered, the folder in flight, elapsed
  time, loose files seen/added/changed/removed, ZIP archives rebuilt, the last
  run's duration and the mount-safety warnings. `POST /api/system/index/sync`
  accepts `full=false` for the incremental variant; the default stays a full
  rescan.

## [3.2.0] - 2026-11-08

### Removed

- **ZIP compression.** Folders are no longer archived into ZIPs:
  `POST /api/zip/compress` and `GET /api/zip/check` are gone, as are the
  "Folders ready to archive" card, the compress action on the Library page
  and the ZIP-eligible metric on the Analytics page. Archives remain
  first-class citizens — they still open as virtual folders with member
  streaming, posters and previews — and unpacking keeps its single and batch
  paths (`POST /api/zip/unpack`, `/api/zip/batch/*`, both unpack-only now).
  Per-archive WebP rewrites (`POST /api/zip/webp/optimize`) are also gone;
  image members stay excluded from the loose-image optimizer. On startup the
  obsolete archive staging directory is swept (honouring a leftover
  `ZIP_STAGING_DIR`/`V2_ZIP_STAGING_DIR` env value), removing that setting
  together with its directory.

### Added

- **Downloads land in the library while they run.** A gallery-dl album no
  longer waits for the whole job: each finished member is imported (moved
  and indexed) as soon as the next one starts, so it is browsable mid-
  download and staging space drains as it goes. Imports skip names that
  the destination already contains — the gallery-dl import path is
  skip-existing rather than auto-rename, so re-running an album that is
  already in the folder completes with no duplicate `(n)` copies and no
  lost files; the staged leftovers stay in staging until the job's own
  cleanup removes them. yt-dlp stays batch-imported: its files pass
  through merging after their progress finishes, so only the final
  artifacts are safe to hand over.

- **Per-file download progress.** Multi-file downloads (albums, playlists) no
  longer show as one bouncing row: every job carries a `file_progress` list
  (`name`, `done`, `percent`, sizes) built from yt-dlp's `filename` progress
  lines and gallery-dl's per-file output lines, the job percent is weighted
  across all known files instead of resetting per item, and the Downloads
  page adds an expandable "N / M files" list on active rows plus the file
  list on finished rows ("stopped after N files" for cancelled jobs).

- **The Duplicates page can ignore images.** `GET /api/dedupe/groups`,
  `GET /api/dedupe/status` and `POST /api/dedupe/hash` accept
  `include_images=false` (the new Images switch on the page), which removes
  image rows from groups, counts and hash batches. The page's Show images
  switch defaults to off; the API keeps images unless asked to omit them.

### Changed

- **Duplicate groups preselect the largest file to keep.** The highlighted
  choice and the delete preview follow that default until another copy is
  clicked; equal sizes keep the first file in the group.

- **Five-frame checks are kept for the file's lifetime.** A cached
  `visual_frames` signature is no longer discarded when a file's `mtime` or
  size changes; renames and moves carry it to the new path, while deletes,
  prunes and the Reset action still clear it.

- **Media verification queue.** A new owned worker pages through the index
  (keyset batches of 200 paths, never a filesystem walk) and flags
  suspicious files into a `file_issues` table: `zero_size` (0-byte
  videos/images), `probe_failed` (the definitive 0×0 ffprobe verdict) and
  `thumb_failed` (`has_thumb = -1`). Rows whose index entry moves away are
  pruned, dismissed findings keep their marker, and `POST /api/verify/recheck`
  re-runs the real prober and clears an issue that no longer holds. The API:
  `GET /api/verify/status` (worker state and per-issue counts),
  `GET /api/verify/issues` (paginated, joined to the files row for display),
  `POST /api/verify/rescan` (409 unless `OWN_WORKERS`), `POST /api/verify/recheck`
  and `POST /api/verify/dismiss` (503 on a read-only index). ZIP members are
  never flagged. Worker state is visible in `/api/system/index/status`.

- **Library growth history.** A new owned worker upserts a daily rollup into a
  `growth_history` index table (`files_count`, `video_count`, `image_count`,
  `bytes_total`) at process start and whenever the UTC day rolls over, so the
  analytics dashboard can chart how the library grows. `GET /api/analytics/growth?days=30`
  returns the recorded days oldest first (capped to `days`, 1–3650) plus today's
  live counts, so a chart's final point is always fresh, and
  `POST /api/analytics/growth/refresh` recomputes and upserts today's row on
  demand (the write runs off the event loop; 503 when the index is read-only).
  Worker state is visible in `/api/system/index/status` and in the growth
  response itself.

### Fixed

- **Analytics goes horizontal on narrow viewports.** The card grids had no
  column template below the `lg` breakpoint, so the implicit auto tracks took
  their width from the chart canvases; a chart first painted at a wider
  layout (viewport change, window resize, rotation) pinned the page open
  sideways and the `ResizeObserver` could never shrink it back. The rows now
  declare `grid-cols-1` (a `minmax(0, 1fr)` track) on mobile and the chart
  container is `min-w-0`, so the container tracks the viewport and every
  chart re-sizes with it.
- A code audit's findings are fixed and pinned by tests (see
  ``tests/test_audit_round2.py``): batch moves fail one item instead of
  aborting the whole request on a rejected path; a suffix byte-range on an
  empty file is no longer answered with an invalid ``0--1`` range;
  concurrent upload chunks for one upload id no longer interleave in the
  staged file (per-id lock, pruned at finalize/cancel); posters only mark
  ``has_thumb`` for the ``mtime``/``size`` the key was built from, and a
  poster that exists while the row still says 0 self-heals; ffprobe/decode
  timeouts and mount I/O errors leave ``width NULL`` (retried) instead of
  freezing ``0×0``; ZIP members can no longer starve the content-hash sweep
  window; oversized images are copied through instead of decoded into
  hundreds of MB during WebP conversion (library and archive rewrite);
  the x265 encoder gets a 5-minute output-inactivity timeout so a wedged
  ffmpeg cannot deadlock the serial queue, re-stats the archive for the
  crawl race and sample-verifies the cross-device copy; unreadable folders
  no longer abort the whole crawl; the frame/candidate/poster endpoints
  clamp paths to the library root; unexpected mutation errors return a
  generic 500 (no filesystem paths in the body); ``/api/health`` and
  ``/api/system/status`` do their SQLite reads off the event loop; login
  failures throttle repeat offenders; transcode shutdown bounds its wait.
- The poster-chooser/frame endpoints clamp paths to the library root the
  same way every other media read does, so index rows from another root
  can never drive an FFmpeg read outside the library.
- The session cookie carries ``Secure`` when the request is HTTPS (plain
  HTTP keeps working unchanged), and the test suite fails below 85%
  coverage.
- A real directory literally named ``foo.zip`` is no longer mistaken for a
  ZIP virtual folder: ``is_zip_member`` consults the index's ``is_zip``
  flag for the archive component (string shape alone cannot tell them
  apart), so children of such a folder stream, transcode and thumbnail like
  any regular file while true archive members keep working.
- HEIC/HEIF images are decodable: `pillow-heif` was declared in
  `requirements.txt` (and the HEIC upload feature is advertised) but never
  registered with Pillow, so iOS photos got no poster, no probed dimensions
  and could not convert to WebP. The opener is now registered in the four
  image-entry points, and `.heic`/`.heif` archive members convert too.
- The combined dedupe list no longer rebuilds content and visual groups on
  every page turn: the merged universe is cached for ten seconds, keyed on
  the index shape (file/ignored counts) and invalidated by ignore/
  unignore/reset/resolve/hash operations, so huge backlogs stop paying the
  full group walk per request.
- A lightbox whose underlying grid shrank (move, delete, filter) now closes
  itself instead of leaving an invisible fullscreen overlay with the body
  scroll locked and keyboard captured.
- Justified-grid rows never overflow the container: the 40 px row floor
  no longer produces a wider row on narrow containers full of
  extreme-aspect items, and per-tile rounding surplus (≈½ px per tile) is
  absorbed by the widest tile.
- The animated-preview queue and crawler counters keep their read-modify-
  writes under the state lock, so queued folder gauges count correctly.

- The sorter's path validation now checks containment on resolved (real)
  paths, so a symlink inside the incoming folder can no longer stream or
  import a file from outside it (`403`).
- A non-ASCII password attempt reads as a wrong password (`401`) instead of
  raising `TypeError` and answering `500`.
- `/system/index/sync` decides `409` from the crawl lock itself; two
  concurrent requests can no longer both report "started" while one dies
  silently.
- Every JSON route now carries an explicit `response_model`
  (`/downloads/cancel`, `/system/index/*`, `/sorter/duplicate`,
  `/thumbnails/exists`, `/transcode/x265/cancel`, `/favorites/counts`), so the
  OpenAPI document stopped describing six endpoints as shapeless objects.
- `/thumbnails/exists` clamps its path to the library root like every other
  media read.
- A staging copy that turns out truncated no longer leaks its
  `.mllibtmp-*` temp file in the destination folder.
- `Content-Disposition` on downloads is RFC 5987-quoted: quotes and control
  characters in filenames can no longer break the download or tear down the
  connection; non-ASCII names get an encoded `filename*`.
- The archive WebP rewrite streams members between archives instead of
  reading whole members into memory; a 100 MB+ TIFF no longer spikes the
  process by several times its size.
- The ZIP member cache sweep survives a concurrent trim removing an entry
  mid-scan instead of turning a successful member serve into a `500`.
- Sorter queue thumbnails are written to a temp file and renamed into place,
  so a racing request can no longer serve a half-written JPEG.
- Client: the command palette and the Search page no longer share one query
  cache entry, so navigating to `/search?q=…` right after using the palette
  cannot render 8 truncated rows as if they were the full result.
- Client: the sorter's create-and-move dialog now owns the keyboard like the
  other modals, Enter can no longer double-fire a move through a focused
  button, and the advertised `M` mute key actually toggles muting.
- Client: uploads honour their abort signal while a 4 MB chunk is in flight
  (cancel acts immediately, staging gets cleaned up), an idle dedupe check
  polls every 30 s instead of 2 s, and the batch animated-preview generation
  refreshes library rows like the single-file variant.
- Client: the recovery command shown on the Backups page is
  `python -m media_library.restore --yes` again, and stale `V2_*` / "v2
  preview" labels were replaced with the plain names.

### Changed

- The crawler and the archive unpack keep local/remote filesystem reads
  (scandir, `stat`, ZIP central directories, missing-item retries) out of
  their SQLite write transactions; writes now run for milliseconds per folder
  instead of holding the single writer lock across the traverse.
- `/library/favorites` accepts `limit`/`offset` (documented) and returns
  `total`; search, folder search, favorites and the system/onboarding index
  reads run off the event loop like browse and folders already did.
- yt-dlp / gallery-dl jobs that stop producing output for five minutes are
  killed instead of wedging a concurrency slot forever.
- `/media/transcode` and `/sorter/transcode` share a concurrency cap of two
  encoders, and the command palette does not search on the very first
  keystroke.
- The test suite now covers 100% of `media_library`: eleven new
  `tests/test_cover_*.py` modules drive the previously uncovered defensive
  branches (x265 commit/validation paths, writes rollback paths, dedupe
  verification and merging, indexer pruning, downloads queueing, sorter
  streams, thumbnails and animated previews, ZIP operations, backup snapshots
  and restore, analytics, runtime workers, routers and app wiring), and
  pytest's coverage gate is raised from 85% to 100%. The `__main__` guard in
  `app.py`/`restore.py` is excluded via `.coveragerc`; three provably dead
  lines in `dedupe.py`/`paths.py` carry `# pragma: no cover` with their
  justification inline.

## [3.1.0] - 2026-11-07

### Added

- **Server-backed watch history.** Opening a file in the fullscreen viewer
  records one event (`POST /api/history/record`), stored as a per-path rollup
  in a new `watch_history` index table (`first_viewed_at`, `last_viewed_at`,
  high-water `seconds_watched`, `play_count`) — scrubbing shorter or
  rewatching never erases remembered progress. Two listings serve the new
  View history page: `GET /api/history/recent` (newest view first, with an
  optional `type` filter), `GET /api/history/resume` (videos still in
  progress) and `GET /api/history/unwatched` (never opened, newest files
  first). Rename, move and delete keep the rollups aligned with the index the
  same way favorites are, `/api/system/info` counts gains a `watched` total,
  the Library page grows a "Continue watching" rail, and the viewer resumes
  playback where you left off. Writes require a writable index like every
  other mutation (503 otherwise); rows land off the event loop (Rule 3).

## [3.0.1] - 2026-11-07

### Fixed

- **`OWN_WORKERS` now defaults to `true`**, matching the documentation; a fresh
  installation starts the crawler, metadata prober and automatic snapshots
  without setting anything.
- The media streaming hot path no longer does remote-filesystem `stat`s on the
  event loop: `/api/media` and `/api/media/transcode` resolve and stat through
  a worker thread, as do the x265 and download queue endpoints.
- The automatic shutdown snapshot runs off the event loop, so a large library
  can no longer stall requests while the process exits.
- ZIP compression, unpacking and WebP rewrites share a per-archive lock, so
  two operations on the same archive can no longer race on `os.replace`.
- Duplicate groups keep working on very large libraries with more distinct
  duplicated sizes than SQLite's variable limit (query now chunked).
- A path outside the library only fails its own entry in bulk delete instead
  of rejecting the whole request; bulk move now rejects over-500 requests the
  same way bulk delete does (no silent truncation).
- The sorter preview gen ffmpeg call (and the x265 duration probe) carry a
  timeout, so a wedged stream no longer hangs a request indefinitely.
- `resolve_library_path` follows symlinks for the containment check: a link
  inside the library pointing outside is a 403 now.
- SVG files stream with `Content-Disposition: attachment` and
  `X-Content-Type-Options: nosniff`, so they never execute in the app origin.

### Changed

- Client: keyboard shortcuts in the viewer and the video sorter no longer
  fire through modal dialogs (Escape closes exactly the topmost one), gallery
  tiles only re-render the tiles whose data changed, the dedupe frame-check
  status and download-history polls idle down when there is nothing running,
  and uploads are cancelled when the dialog is left. Shared `formatBytes` and
  the two backup-download links routed through the API module; dead code
  removed.

## [Unreleased]

### Added

- **Visual duplicates are double-checked automatically against five frames.**
  Sampling uses 10%, 30%, 50%, 70% and 90% of each video. Fetching duplicate
  groups queues the check for every unchecked visual group on the page;
  `POST /api/dedupe/verify-visual` still checks one group on demand. Results
  are cached in `visual_frames`, and a group is confirmed when at least three
  samples agree. Batches run on a daemon thread with progress (group, file and
  sampled frame) at `GET /api/dedupe/verify-visual/status`, so it survives
  leaving and returning to the page. Groups whose frames disagree are hidden
  by default; the page offers a “Show frame mismatches” toggle.
- **The Duplicates page compares videos side by side.** A compare dialog plays
  every file in the group, keeps the selected copy highlighted and shows the
  per-pair frame agreement.
- **`POST /api/dedupe/reset`** clears every ignored pair and cached frame
  check, exposed as the page's Reset action.

### Changed

- **Duplicates are one list.** `GET /api/dedupe/groups` defaults to `mode=all`
  and folds content and visual matches together (content groups still win where
  they overlap); the client's content/visual tabs are gone.

### Removed

- **The previous application is gone.** Its package, `app.py` entrypoint,
  HTML templates, Vanilla-JS client, Tailwind/Vite/Vitest configuration and
  52 test modules were deleted. The rewrite has served `/` since 3.0.0; the
  fallback it was kept for no longer exists. `media_library_v2/` and `web/`
  now live at `media_library/` and `frontend/`, `v2_app.py` is `app.py`, the
  API base is `/api` (docs at `/api/docs`) and the client builds into
  `static/dist/`. Old `/v2` and `/api/v2` URLs are a clean break — no
  redirects.

### Changed

- **Configuration names lost the `V2_` prefix** (`OWN_WORKERS`,
  `DB_READ_ONLY`, `WRITES`, `UPLOAD_STAGING_DIR`, `SORTER_STATE`, …). Every
  old name is still read as a fallback, so an existing `.env` keeps working.
- **On-disk defaults follow the rename:** `upload_staging`, `download_staging`,
  `zip_staging`, `backup_staging`, `sorter_state.json`, and snapshots in
  `.media_library_backup/` instead of a `v2/` subdirectory. Startup
  auto-restore reads that single location.
- **The app owns its index schema.** `media_library/schema.py` creates the five
  tables at startup (threaded, writes mode only) and drops three indexes no
  query can use, so an upgraded index and a fresh one share one shape. It never
  rewrites a row. The People-table cleanup, the one-shot face-artifact purge
  and the per-startup `webp_optimized` backfill are gone.
- **`/api/health` answers 503 when the index cannot be read** (it reported
  `status: degraded` with 200 after the app-level alias was folded into the
  system router).
- **The client is one npm project at the repository root** (`npm ci`,
  `npm run build`, `npm test`); CI runs `lint`, `backend` and one `frontend`
  job.
- The `v2`-prefixed entries below predate this change and describe the rewrite
  while it was still a second app.

### Fixed

- **v2 folder/ZIP moves rewrote a sibling subtree when a name contained `_` or
  `%`.** The index rewrite matched the moved path's prefix with SQL `LIKE`, so
  `my_folder` also matched `myXfolder` and relocated that folder's files,
  favorites and ignored pairs. Prefix edits now compare with
  `substr(path, 1, N) = prefix`, so only the moved subtree changes.
- **v2 x265 conversion no longer stalls the event loop.** Committing the encoded
  file (a full copy over the library mount when it is a different filesystem)
  and its SQLite rewrite now run in worker threads, so a large conversion
  cannot freeze every HTTP request while it copies.
- **v2 abandoned upload staging is swept.** A closed upload tab used to leave its
  `.part` file forever; staged uploads older than `UPLOAD_STAGING_TTL_HOURS`
  (default 24 h) are now removed by a throttled hourly sweep, while a resumable
  upload within the window is untouched.
- **v2 animated preview queue could strand a job.** A video enqueued while the
  worker was draining saw the dying thread as still alive and never started a
  replacement; the worker now clears its thread reference under the queue lock.
- **v2 cancelled queued downloads land in history once** instead of being
  appended a second time when the spawned task observes the cancel.
- **v2 browse/folder listing checks the directory off the event loop**, so the
  one FUSE round trip can no longer block the server.
- **v2 rejects control characters in file/folder names** at rename, create and
  upload-finalize, which also keeps an uploaded name safe in download headers.
- **v2 x265 progress no longer jumps to 99%.** ffmpeg's `out_time_ms` is a legacy
  name for microseconds (like `out_time_us`); both are divided by 1e6 now, as the
  current app does.
- **v2 sorter undo keeps its stack entry when the moved file is unreachable**
  instead of consuming it and losing the ability to retry or undo an earlier move.
- **v2 poster chooser and "Set poster" work for videos inside ZIP archives** by
  extracting the member into the bounded local cache before ffmpeg reads it,
  instead of reading the virtual member path and failing.
- **v2's owned crawler sweeps orphaned artwork.** Posters, animated previews and
  chooser frames whose index rows are gone (a delete, rename or archive rewrite
  made outside the app) are removed after a crawl, mirroring the current app's
  prune; an empty index never triggers a sweep.
- **v2 upload errors keep one row per file.** A failed or cancelled upload used to
  be reported under an empty upload id, so a second failure overwrote the first
  row and a cancelled file left its original row spinning forever; errors now
  update the file's own row.
- **v2's standalone UI dropped its leftover preview links and copy.** The "current
  app" / "v1" shortcuts only appear when v2 is actually mounted beside v1, the
  Settings roadmap lists the real remaining gaps (ZIP-member editing and
  pre-transcoding) instead of the finished cutover, and the backup/index guidance
  matches the owned runtime.
- **v2's thumbnail endpoint clamps its path to the library root** before looking
  the row up, so a stale index row from another library root can never turn it
  into an arbitrary-file reader.

## [3.0.0] - 2026-11-06

### Added

- **v2 owns the housekeeping sweeps.** With `V2_OWN_WORKERS=true` the runtime now
  also drains the loose-image WebP queue and the content/visual duplicate hash
  queues in small batches, writes a snapshot on shutdown, and redirects old
  `/v2/...` bookmarks to the same path at `/`.
- **v2 folder rename and startup auto-restore.** Folders and ZIP archives can be
  renamed from the library header; every indexed descendant folder, file,
  favorite and ignored duplicate pair is rewritten in one transaction and cached
  posters move to their new keys, so a rename never regenerates artwork or
  orphans favorites. The standalone app now restores a missing or empty index
  from the v2 snapshot at startup, falling back to the current app's snapshot,
  and never touches a non-empty index. The Tools hub also queues animated
  previews for every video that lacks one.
- **v2 sorter duplicate warning.** The sorter compares the current incoming clip's
  quick hash (128 KB head + size, the same format the dedupe tools use) with the
  shared index and shows an amber "Already in the library" banner with the first
  matching path and a link to its folder, so a duplicate is visible before the
  move key is pressed (`GET /sorter/duplicate`).
- **v2 poster chooser and animated previews.** Video slides gained a thumbnail
  chooser: eight-or-more evenly spaced frames sampled from the clip, a "use
  current position" shortcut, and a poster that cache-busts immediately after it
  is set. Videos can also generate a looping animated WebP preview on demand,
  and the library toolbar queues every preview-less video in the current folder.
  Frames are cached per video/timestamp, previews use the same cache key as the
  current app (so a move or delete keeps or removes the artwork correctly), and
  failures are recorded so a broken file is not retried forever. New endpoints:
  `/thumbnails/candidates`, `/thumbnails/frame`, `/thumbnails/poster` and the
  `/thumbnails/animated` queue.
- **v2 multi-select and bulk actions.** The library grid gains checkbox,
  Ctrl/Cmd-click and Shift-range selection, a Select media toggle, Ctrl/Cmd+A,
  Delete/Escape shortcuts and a floating action bar: star or unstar, move,
  delete, optimise the selected images to WebP, and batch rename (prefix,
  suffix or find-and-replace) with a live preview. New endpoints:
  `POST /favorites/batch`, `POST /manage/bulk-rename`, and `paths` on
  `POST /webp/optimize` so a selection converts exactly itself.
- **v2 standalone app and shared-password gate.** `media_library_v2/auth.py`
  implements the same stateless `ml_auth` cookie contract as the current app
  (same name, payload, key derivation and 400-day sliding lifetime), so a
  signed-in browser carries over to v2 unchanged. `GET /api/v2/auth/session`
  reports whether a password is required, `POST /api/v2/auth/login` sets the
  cookie and `POST /api/v2/auth/logout` clears it for that browser. `v2_app.py`
  (or `python -m media_library_v2.standalone`) assembles a complete app that
  serves the React client at `/` — the post-cutover entrypoint — with the API
  still at `/api/v2`, `/api/health` for the watchdog, and the sign-in screen
  rendered by the client instead of a server-side page.

- **The v2 rewrite, served beside the current app at `/v2`.** A new
  `media_library_v2/` package (which never imports v1) and a new React 19 +
  TypeScript client in `web/`, built into `static/dist/v2/`. It reads the same
  library and index as v1, with the index opened read-only, and reuses v1's
  thumbnail cache byte-for-byte. Live now: browse, folder navigation, search,
  favorites, byte-range media streaming, thumbnails, the new app shell, the
  gallery, the lightbox, the command palette and light/dark/system themes.
  Everything else is reported as `features: false` and shown as roadmap until
  its phase lands. See `AGENTS.md` §16.
- `V2_ENABLED`, `V2_LIBRARY_DIR`, `V2_DB_READ_ONLY`, `V2_WRITES`,
  `V2_UPLOAD_STAGING_DIR`, `V2_BROWSE_LIMIT` and `V2_SEARCH_LIMIT` configuration,
  plus `npm run build:v2`.
- The v2 client's sign-in screen (`AuthGate`), shown whenever the session endpoint
  says a password is configured and this browser has no valid cookie; a Sign out
  control in Settings; and a mount-aware basename so one bundle serves both `/v2`
  and, after cutover, `/`.
- **The v2 write path.** Favorites toggle, in-place rename, move (auto-rename /
  skip / error), delete (files and recursive folders) and chunked resumable
  uploads all write to the library and update the shared SQLite index, moving
  or removing cached posters with the file. Every mutation runs off the event
  loop and rolls the filesystem change back if the index write fails.
- The v2 client gains a tile action menu (right-click or ⋯), inline rename and
  delete dialogs, a folder picker for moves, a new-folder dialog, an upload
  dialog with per-file progress and a visible favorite toggle in the viewer.
- **The v2 Video Sorter.** A dedicated `/v2/sorter` workspace: the incoming
  folder scanned one level deep, a preloaded player with live FFmpeg transcoding
  for non-web containers, six remembered destinations bound to keys 1–6, folder
  search with Enter-to-move, create-and-move, undo, rename and delete, session
  count and progress. Moves import through the same atomic, indexed write path as
  uploads; undo moves the clip back and removes its index row.
- `V2_SOURCE_DIR` and `V2_SORTER_STATE` configuration.
- **The v2 Downloads page.** `/v2/downloads` queues one or more URLs (validated
  client and server side) into a chosen library folder, with recent-target
  chips, a folder picker, live per-job progress (speed/ETA/playlist position),
  cancel, retry of failed/cancelled jobs, paginated history, clear, and
  downloader availability badges. yt-dlp runs as an isolated subprocess with
  the gallery-dl fallback (unsupported sites, piracy blocklist refusals and
  `gofile.io` hosts), finished files import through the atomic indexed write
  path, and `V2_DOWNLOAD_STAGING_DIR` holds in-flight files.
- **The v2 Analytics dashboard.** `/v2/analytics`: KPI strip, zoomable/roamable
  storage treemap with click-through to the folder, largest-folders ranking,
  media-mix donut, top extensions, reclaimable space (duplicate candidates,
  WebP savings, ZIP-eligible folders, empty folders), largest files with
  thumbnails, resolution histogram and health pills. Every figure is computed
  from SQLite alone — the test suite poisons `os.walk`/`os.scandir`/`os.stat`
  during the request — and the payload is memoised for 30 seconds (`refresh=1`
  bypasses). ECharts is lazy-loaded with the page, so it never weighs on the
  rest of the app.
- **Dedupe and the Tools hub.** `/v2/tools/dedupe` groups byte-identical files
  by the shared `quick_hash` and near-identical frames by `thumb_hash`,
  computes missing hashes in batches off the request thread, verifies content
  byte-for-byte before deleting, and manages ignored false-positive pairs.
  Visual clusters use complete linkage and are capped at 12 files, so a chain
  of loosely similar scenes can never become a 50-file deletion suggestion.
  `/v2/tools` gathers duplicates (native) and the not-yet-ported maintenance
  tools (ZIP, WebP, empty folders, backups) with links to the current app.
- **ZIP virtual folders, on-demand posters, WebP optimisation and empty-folder
  cleanup in v2.** Archives indexed by the shared crawler now browse in the
  Library; a member's media is streamed after extracting just that member into a
  bounded local cache (`ZIP_CACHE_DIR`, LRU-trimmed, hash-named so Zip Slip is
  impossible). Missing image posters are generated on demand for loose images
  and ZIP members. `/v2/tools/images` converts loose images to WebP in batches
  (atomic replace, index + poster update, `WEBP_QUALITY`/`WEBP_SKIP_LARGER`), and
  `/v2/tools/empty` removes selected empty leaf folders while tolerating OS junk.
- **ZIP compression and unpacking in v2.** `/v2/tools/zip` lists image folders
  ready to archive and indexed archives. Compression builds on local staging,
  verifies with `testzip()`, commits atomically, deletes the source folder and
  replaces its index subtree with archive members; unpacking extracts safely to
  a sibling folder, deletes the archive and re-indexes the extracted tree. The
  same actions are available in the Library toolbar for the folder on screen.
- **Library snapshots in v2.** `/v2/tools/backup` creates a compacted copy of
  the SQLite index plus a tar of cached posters in
  `.media_library_backup/v2/` (v1's automatic snapshot is left untouched),
  reports counts/sizes, verifies both databases, and offers the two files for
  download. Restore is deliberately disabled while v1 owns the index; the page
  documents `python -m media_library_v2.restore --yes` for a service-stopped
  recovery, and `V2_ALLOW_LIVE_RESTORE` enables the in-app path after cutover.
- **First-run onboarding in v2.** A four-step tour (welcome, library/index health,
  what is live versus still in the current app, appearance) backed by
  `GET /api/v2/system/onboarding`. It opens once, is replayable from Settings and
  the command palette, and persists dismissal in `v2_onboarding_done`.
- **In-place x265 conversion in v2.** The viewer's "Convert to H.265" action
  (`Minimize2`) queues a serial `libx265 -preset medium -crf 18` re-encode with
  audio copied when MP4-compatible, shows live percent/fps/speed/ETA in a
  floating panel, and supports cancel. The commit is atomic (cross-device safe),
  non-MP4 sources become an `.mp4` sibling, and the index/poster/animations are
  rewritten. `X265_PRESET` and `X265_CRF` tune it.
- **Batch ZIP compression and unpacking in v2.** `/v2/tools/zip` can compress
  every eligible leaf image folder or unpack every indexed archive, one at a
  time, with live progress, current item, error list, pause/resume between
  items and cancel. Each item reuses the validated single-archive path, so a
  batch never leaves a half-written archive.
- **Archive WebP rewriting in v2.** Each archive in the ZIP tools has a
  "Convert images" action: JPEG/PNG/BMP/TIFF members become WebP (PNG
  losslessly), other members are copied unchanged, favorites are remapped, the
  member cache is purged and the index is re-synced. The archive is rebuilt on
  local staging, verified with `testzip()` and swapped in atomically;
  `WEBP_SKIP_LARGER` keeps an original when the WebP would be bigger.
  The viewer picks it up through the new `transcode_url` field and plays the clip
  live instead of showing the old "needs transcoding" notice.
- **On-demand video posters in v2.** A video with no cached poster gets one the
  first time a tile or the viewer requests it (ffmpeg at 1 s, 360 px, falling
  back to frame 0), so new uploads never sit behind a "No preview" placeholder.
  Images and ZIP members share the same path.
- **v2-owned background workers.** A new runtime (`V2_OWN_WORKERS=true`, off
  while v1 runs) starts v2's incremental crawler, metadata prober, poster sweep
  and automatic snapshots. The crawler indexes folders, files and ZIP archives,
  re-checks archives even when their parent is unchanged, skips folders by
  `dir_mtime`, and prunes with retries plus a mount-safety guard that aborts when
  more than half of a folder's children look missing. `/api/v2/system/index/status`
  exposes progress, revision and stale counters; Settings shows the state and a
  "Sync now" action when v2 owns the crawler.
- **Deleting a ZIP archive now removes its virtual members** from the index
  (and their cached posters and favorites), instead of leaving orphan member
  rows for the crawler to prune.

## [2.0.2] - 2026-11-03

### Changed

- **Tailwind CSS is compiled at build time instead of in the browser.** Every
  page used to pull `https://cdn.tailwindcss.com`, a runtime JIT that re-scans
  the document on DOM mutations — so the 2–6s status poll alone made it
  recompile, and scrolling a long gallery recompiled again for every batch of
  cards. `tailwind.config.js` now scans the templates and frontend sources, and
  `npm run build` runs the Tailwind CLI after Vite into
  `static/dist/tailwind.css`, which the pages link directly: one cached static
  file, no third-party script, no compiler in the page. The utilities resolve
  their colours through CSS variables (`rgb(var(--surface-900) /
  <alpha-value>)`), so the accent and OLED/navy palettes are still swapped
  instantly at runtime — and the hand-maintained `!important` override sheet
  that used to be regenerated on every theme switch is gone, which also stops
  it drifting from the classes actually in use.

### Fixed

- **The status poll no longer rewrites the page when nothing changed.** Each
  subsystem's payload (and the derived header badge) is compared with what was
  last rendered and skipped when identical, so idle workers stop touching the
  tasks modal's lists, classes and progress widths every six seconds. On a
  gallery with hundreds of tiles that DOM churn was a full style recalculation
  and repaint for no visible change.

## [2.0.1] - 2026-11-03

### Fixed

- **High GPU usage in Firefox while browsing the library.** The gallery was
  the worst case: every tile carried up to five `backdrop-blur` badges (ext,
  resolution, codec, fps, duration), infinite scroll appends 50 cards at a time
  and never removes any, so a large folder ended up holding thousands of live
  blur layers — each one forcing Firefox to re-sample and re-blur its backdrop
  whenever anything underneath changed. The badges are now plain high-alpha
  fills, and `backdrop-filter` is gone from every other layer that scrolls or
  sits over playing video: the pinned library top bar and its two inner rows
  (sticky, so they re-blurred the whole grid on each scroll frame), the mobile
  top bars on Library/Downloads/Analytics, the back-to-top button (which kept a
  live backdrop copy even at `opacity-0`), the ZIP-extract banner, the sorter's
  sticky video column and mobile bottom action bar, and the lightbox's `.lb-meta`
  control bar plus the x265 progress panel (which were re-blurring the current
  video frame as it played). All of those surfaces were 82–95% opaque, so the
  blur was invisible anyway; their fills were nudged up a few percent to keep
  the same contrast. Frosted glass remains on modal panels, the drag-and-drop
  overlay and the mobile sidebar backdrop, none of which scroll. Gallery and
  folder-cover images also decode asynchronously so a burst of decodes cannot
  stall scrolling.

## [2.0.0] - 2026-10-26

### Removed

- **Face recognition and the People pages are gone.** The opt-in feature added in
  1.x is removed in full: the `FaceWorker` sweep, the YuNet + SFace engine
  (`services/face_engine.py`), the clustering/curation service
  (`services/face_service.py`), the `/api/people*` endpoints, the `/people` and
  `/people/review` pages and their bundles, the People nav tabs, sidebar and
  drawer links, the Background Tasks card, the Library search `person:` syntax
  and the `faces` block of `/api/system/status` all no longer exist.
- **Its data goes with it.** Startup drops the `people`, `faces`, `face_scans`
  and `dismissed_faces` tables, and a one-shot migration deletes the face crop
  cache (`THUMBNAIL_CACHE_DIR/faces`) and the two ONNX weights
  (`DATA_DIR/models`). Moves, renames, deletes, pruning and backup restore no
  longer touch face rows.
- **`opencv-contrib-python-headless`** leaves `requirements.txt`: it was only the
  inference runtime for the face engine. `scipy` stays (imagehash's pHash, i.e.
  visual duplicate detection, computes its DCT with it).

## [1.2.5] - 2026-10-22

### Fixed

- **False face matches are split off their cards.** Every join is pairwise, so a
  chain of "each face was close enough to the last one" could put several people
  on one card: the previous pass left "Chloe lamb" holding 16,952 faces whose
  mean distance to their own centroid was 0.42 (median pairwise distance 0.66 —
  they are mostly unrelated), and "Dare Taylor" 11,191 at 0.53. A cluster's
  spread (`1 - |sum of embeddings| / count`, now exact from the block sums the
  pass already carries) is checked against `FACE_CLUSTER_MAX_SPREAD` (default
  0.25): anything looser is split by re-clustering it at a tighter threshold,
  recursing while a piece is still loose and falling back to singletons for a
  piece that will not come apart. A coherent cluster is left alone, and a named
  person goes to the tightest piece nearest their centroid. Scanning obeys the
  same ceiling, so a cluster stops accepting faces once a join would push it
  past the cap instead of drifting into a blob between re-groups. On a 63k-face
  sample of a real index the pass left no cluster above the cap and the biggest
  card dropped from thousands of faces to a few hundred; on a 317k-face library
  it runs in ~165 s with the service near 1.5 GB. The cap is the dial: raise
  `FACE_CLUSTER_MAX_SPREAD` (0.30–0.35) for fewer, broader cards and more risk of
  a wrong match, lower it for stricter ones.

### Changed

- `/people`'s **Re-group all** confirmation and toast now say the pass can take a
  few minutes on a large library, since it runs off the event loop rather than
  freezing the page.

## [1.2.4] - 2026-10-21

### Fixed

- **"Re-group all" works on a large face index.** The pass hard-refused any
  library above 200,000 faces (`409 Too many faces to re-cluster in one pass`),
  and the clustering behind it could not have coped anyway: the chunk-centroid
  stitch built one distance matrix for *all* chunk centroids, which on a
  mostly-singleton library is tens of gigabytes (the log's
  `Unable to allocate 45.8 GiB`), and the name re-attachment compared every old
  person against every new cluster in a Python double loop — billions of pairs
  on a library with 163,000 clusters. The stitch now recurses while the centroid
  set does not fit in a chunk (bounded memory at any face count, with a depth
  guard so all-singleton input still terminates), embeddings are streamed from
  the table a page at a time instead of read as one list plus one matrix, name
  re-attachment compares **named** people only (unnamed clusters are rebuilt by
  the pass anyway) through a blocked matmul, faces are grouped with one sort
  instead of a linear scan per cluster, and clusters are written in batched
  transactions so the pass no longer holds SQLite's write lock for minutes. A
  name that ended up on two clusters no longer leaves an empty duplicate card
  behind either (only one of them can win a group). A 317k-face library now
  re-groups in ~200 s at ~1.6 GB peak instead of failing, and the endpoint runs
  the pass off the event loop (`asyncio.to_thread`) with a single-pass lock, so
  the server keeps answering while it works.

## [1.2.3] - 2026-10-20

### Fixed

- **A face scan can no longer wedge the whole server.** A sweep reading a clip
  over a remote mount that stops answering would hold its decode slot for as
  long as the mount stayed wedged, and the ordered pipeline waited forever for a
  detection that never came back — while OpenCV's own per-call thread pool
  spawned (and spin-waited) a worker per core for every detection. A frame pass
  now gives up after `FACE_FRAME_STALL_TIMEOUT` (60 s) without a frame, the
  pipeline stops waiting for a detection on the same limit, and OpenCV's pool is
  capped at one thread (`cv2.setNumThreads(1)`) since the sweep already
  parallelises one whole frame at a time. A cut-short pass reports why it ended
  and leaves the file pending for the next sweep instead of recording a partial
  scan as final.

### Added

- **Health watchdog for a hung process.** `Restart=always` only helps when the
  process exits; one that is alive and holding its port but answering nothing is
  invisible to systemd. `scripts/media-library-watchdog.sh` polls
  `/api/health` and restarts the unit after two consecutive failures, driven by
  the new `media-library-watchdog.service.example` /
  `media-library-watchdog.timer.example` pair.

## [1.2.2] - 2026-10-20

### Fixed

- **File-host URLs no longer fail on a yt-dlp blocklist refusal.** yt-dlp now
  ships a `[Piracy]` pseudo-extractor that refuses some hosts outright
  (`ERROR: [Piracy] This website is no longer supported…`, exit code 1), e.g.
  `gofile.io`. The download worker only recognised `Unsupported URL` and
  `No video formats found` as "try gallery-dl", so these jobs died with
  `yt-dlp exited with code 1` even though gallery-dl serves the same link. Both
  blocklist phrasings now trigger the gallery-dl fallback.
- Hosts only gallery-dl can serve (`gofile.io`) skip the yt-dlp attempt
  altogether, so the job shows a single gallery-dl run instead of a failing
  yt-dlp pass first, and a job on such a host with gallery-dl missing now says
  so instead of "no downloadable media found".

## [1.2.1] - 2026-10-20

### Changed

- **The face sweep's ETA is measured in footage, not files.** A queue of
  2-second phone clips and a queue holding a 90-minute film are not the same
  work, so files-per-minute made the estimate meaningless. `face_stats()` now
  returns `total_seconds`/`pending_seconds` alongside the counts and the card
  shows a **realtime factor** (e.g. `2.4x realtime`, footage cleared per second
  of wall clock) with the ETA derived from the footage still owed
  (`realtime_factor` and `pending_footage_s` in the `faces` status payload).
  Files per minute survives as the rate line's tooltip.

## [1.2.0] - 2026-10-20

### Changed

- **The face sweep now decodes and detects in parallel.** It used to be strictly
  serial — one file, one FFmpeg frame, one detection, in turn — which left seven
  of this box's eight cores idle and took ~65 h for the whole library. Now two
  FFmpeg streams run at once, each feeding a shared pool of detection workers,
  and the frames of a clip are detected while the next frames are still being
  decoded. The sampling is untouched (same `FACE_VIDEO_SAMPLE_INTERVAL` grid,
  same `FACE_SCAN_WIDTH`), and detections are folded into tracks **in frame
  order**, so the stored faces are identical to a serial scan's — a test asserts
  it. Each detection thread leases its own engine instance, because YuNet keeps
  the input size on the detector and two threads sharing one would resize it
  under each other.
- **Concurrency is derived from the core count, not configured.** One core is
  left to the server, one per decode stream to FFmpeg and the rest go to
  detection (`scan_concurrency`), so a small machine simply gets fewer of each
  instead of a new environment variable to get wrong. Database writes stay
  serialized behind a lock, so the sweep cannot starve other writers.

### Added

- **Throughput and ETA on the face card.** The background-tasks modal (both the
  Library and the Sorter) now shows files per minute over a rolling window and
  the time left at that rate, from `files_per_min` / `eta_seconds` in the
  `faces` status payload, alongside the existing counts.

### Fixed

- The file gate and the frame-stream gate are deliberately **different**
  semaphores: sharing one had each concurrent video holding a permit while its
  own frame pass waited for a second, so the sweep sat at "scanning" with zero
  progress and no decoder running. `tests/test_face_service.py` covers it.

## [1.1.9] - 2026-10-18

### Added

- **A folder chooser for the face scan scope.** The People page's "Scan folder"
  took a typed path, which is a poor fit on a phone and easy to get wrong; the
  new **Browse…** button opens a drill-down chooser over the library index
  (`/api/backup/folders`, so opening it never walks the mount) with an up
  control, the current path, and ZIP archives filtered out. Picking a folder
  fills the box and queues that folder in one tap, and the typed box stays for a
  pasted path or a `?folder=` deep link.

### Changed

- **The folder chooser is now one shared component.** It moved to
  `frontend/src/shared/folder_picker.js` (its own sheet, no template markup) and
  the Downloads page's hand-rolled copy — markup, handlers, state and all — was
  removed in favour of it, so both pages use the same chooser and the same
  index-only endpoint. `downloads.js` got smaller for it; the People page passes
  the icon registry's folder glyph in, so a bundle that needs no other icons
  does not have to carry the whole set. The Downloads page also boots like the
  other pages now (`readyState` guard) instead of only on `DOMContentLoaded`,
  and it has a smoke test for the first time.

## [1.1.8] - 2026-10-18

### Changed

- **The video face sweep samples every 2 seconds instead of every 30, and reads
  each video once instead of seeking into it per frame.** A 30 s grid looked at
  about 25 moments of a 12-minute clip and found faces in 4 of them, so a person
  on screen for twenty seconds was usually missed. FFmpeg now decodes the file
  **once** and samples the timeline itself (`fps=1/2`), which is what makes the
  denser grid affordable: the mount is read sequentially once rather than
  re-sought for every frame, and nothing is written to disk — the sweep streams
  the frames and keeps only the face crops, so it no longer fills (or evicts)
  the candidate-frame cache the thumbnail chooser and animated previews share.
  `FACE_VIDEO_MAX_FRAMES` becomes a frame *budget*: a clip long enough to blow
  it is sampled evenly rather than stopping early.
- **Detection now runs at 960px, and a person is recorded once per appearance.**
  The recogniser aligns every crop to 112×112, so a face that was 48px on the
  480px frame reached it as a 2.3× upscale — that blur is why one person's faces
  landed up to 0.46 apart and split into separate clusters, and why a denser grid
  on its own would have multiplied the backlog instead of clearing it. Frames are
  decoded at `FACE_SCAN_WIDTH` (960, never upscaling a smaller source) and
  `FACE_MIN_FACE_PX` moves to 96 to keep the same physical floor with twice the
  pixels; loose photos are read from the source (downsized, falling back to the
  cached thumbnail) for the same reason. Per-frame detections are then folded
  into a **track** — same person, within `FACE_TRACK_DISTANCE` and
  `FACE_TRACK_GAP_S` — so a shot of someone contributes one row carrying their
  sharpest frame and their **mean** embedding, instead of ninety near-identical
  rows that skewed the cluster centroid towards whichever shot was longest. A
  person who leaves and returns still gets separate moments to jump to.

### Added

- **A rejection now survives a change of sampling.** `dismissed_faces` stores the
  embedding of the face the user rejected (idempotent `ALTER TABLE`), so
  re-scanning with a different grid — which moves the timestamp and box of the
  detection representing that same face — cannot resurrect a "not a person"
  decision. A fresh detection is skipped when it is within `FACE_TRACK_DISTANCE`
  of a dismissed embedding for that file, or matches the box exactly.

## [1.1.7] - 2026-10-18

### Added

- **An "Auto-match" pass that clears the part of an unnamed-cluster backlog you
  would otherwise review by hand.** A face joins the nearest person within the
  match threshold as it is scanned — but only the people that existed at that
  moment, so a person named afterwards (or one whose cluster has grown since)
  leaves unnamed clusters behind that a fresh assignment would have merged.
  `POST /api/people/auto-match` re-asks that question for every unnamed cluster
  at once, against the **named** people only (folding a cluster into another
  unnamed one would just move the problem). The toolbar's **Auto-match** button
  always runs it as a dry run first: it shows how many clusters and faces would
  move, the biggest gains per person (`Dare Taylor +338, Emily black +286, …`),
  what would be left unnamed and the threshold it used, and only then asks. A
  merge is exactly what the merge sheet does, so a wrong match is undone with
  Split.

## [1.1.6] - 2026-10-18

### Changed

- **Face scanning of videos now scales with the clip's length instead of
  stopping at 24 frames.** The sampling loop already kept a frame every
  `FACE_VIDEO_SAMPLE_INTERVAL` seconds and only widened the spacing once a video
  passed the cap, so the cap was the whole rule: at 24 it meant anything over 12
  minutes got one frame every few minutes (a two-hour film: one per 5 minutes).
  The default is now **240**, i.e. one frame every 30 seconds up to a two-hour
  clip and a steadily widening spacing past that (8 hours: one per 2 minutes) —
  so a long recording is swept at a density a face is unlikely to slip through,
  while clips under 12 minutes are untouched.

### Added

- **A ceiling and an orphan sweep for the candidate-frame cache.** The 480px
  stills the thumbnail chooser, the animated preview compiler and the face
  worker extract from a video are now binned per video
  (`thumbnails/candidates/v<path hash>/cand_<second>.jpg`), which makes the
  cache sweepable: the index prune and deep clean drop the bins of videos that
  are gone (reported as `pruned_orphaned_frames`) along with the flat files the
  old layout wrote, and `CANDIDATE_CACHE_MAX_SIZE_GB` (default 5 GB) trims the
  least recently written frames after each generation, throttled to once a
  minute. Raising the frame cap multiplies this cache, so it no longer grows
  without bound — evicted frames simply regenerate on the next view. The faces
  status payload reports the cache's usage (`frame_cache_files`,
  `frame_cache_bytes`, `frame_cache_size_formatted`, `frame_cache_max_bytes`,
  `frame_cache_max_size_formatted`).

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
  **Name** turns the cluster into a new person; **Ignore** records it as "not a
  face" so no future scan re-adds it, straight away and with no popup (and no toast
  anywhere on the page — the card advancing is the feedback). Every action removes
  the cluster from the unnamed set, so the queue needs no state of its own: the next
  request is simply the largest remaining cluster, and a reload lands where a fresh
  queue would. `M`/`N`/`X` and `1`–`9` drive it, `?` opens a key reference rendered
  from one table, and the card is laid out to fill one phone screen with no page
  scrolling — the cover, the counter chips and the intro are desktop-only, and the
  crop strip takes the slack so the faces grow with the screen while the actions sit
  in the thumb zone. A phone keyboard can no longer leave the page scrolled: the
  stage takes its height from the *visual* viewport (which follows the keyboard,
  unlike the layout viewport iOS keeps), the face strip steps aside while a name is
  being typed, and any scroll a browser performed is undone. `/people` gained a
  **Review unmatched** button badged with the count of unnamed clusters, and
  `GET /api/people` now also reports `folder_names` per row and takes `samples`
  (1–12) for how many crops a row carries, so the same endpoint serves both the
  pills and the full list.

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
