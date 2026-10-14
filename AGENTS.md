# 🤖 AGENTS.md — Media Library & Video Sorter Agent Guide

> **CRITICAL DIRECTIVE FOR ALL AI AGENTS**:
> **This document is the authoritative architectural and operational specification for this codebase. It MUST ALWAYS BE KEPT UP TO DATE.**
> Whenever you add, modify, or remove API endpoints, database schema fields, background workers, environment variables, or core workflows, you **MUST** update this file in the same task to reflect your changes.

---

## 1. System Overview & Technology Stack

**Media Library & Video Sorter** is a high-performance, web-based media asset management system and rapid keyboard-driven video sorter designed to handle tens of thousands of video and image files efficiently across local disks and remote FUSE mounts (e.g. pCloud, network shares).

### Core Capabilities
1. **Media Library & Gallery View**:
   - Sub-millisecond SQLite-indexed browsing, folder hierarchy navigation, and multi-word full library search with configurable `SEARCH_LIMIT` (default 1,000 items), truncation detection (`1,000+` badge indicator), and query refinement alerts.
   - Row-ordered natural-aspect-ratio layout for media files and folder covers: tiles are packed into **justified rows** — every tile in a row shares one row height chosen so the row fills the container width exactly — so reading order runs strictly left to right, then down. Tile widths follow probed `width`/`height` clamped to 2:3–3:2, unknown dimensions fall back to a 1:1 box filled with `object-cover`, and a trailing partial row keeps the target height until more items arrive. Tiles feature video duration pills (`MM:SS` / `HH:MM:SS`), resolution badges (`4K`, `1080p`, etc.), high-framerate tags (`60fps`), dimensions in card footers, and a hover `ℹ️` Info button. A thumbnail-size control in the library toolbar steps through five presets (XS–XL, XL scaled up to ~640px-tall rows) that adjust the row height; the choice is persisted in `localStorage` (`lib_tile_scale`). The library stage spans the full viewport width (no centered 1920px cap).
   - Proactive background thumbnail/poster frame generation using FFmpeg, alongside on-demand looping animated WebP video previews (~500ms per frame, 2 fps, loop=0) generated from up to 24 candidate frames sampled across video duration at 480px resolution; the auto-play preference is toggled via the mobile control sheet (`🎞️ Animated Previews`) — the desktop toolbar `🎞️ Animated` toggle is hidden on hover-capable devices, where hover previews always play — with active animated video tiles displaying a subtle `ANIM` badge indicator. Animations are viewport-aware via a dedicated `IntersectionObserver` and only play when on-screen on touch devices (gated by the toggle), while hover-capable desktops play a tile's loop only while the pointer is over it; both automatically pause/swap back to static posters when no longer previewing (scrolled off-screen, mouse-out), when hidden in a background tab, or when viewing a media item in the lightbox to minimize CPU/GPU usage. A dedicated Service Worker (`/sw.js`) intercepts animated thumbnail requests and caches them in client-side browser `CacheStorage` (`media-library-anim-v1`) for instant 0ms recall, paired with `navigator.storage.persist()` for durable browser storage.
   - Fullscreen lightbox viewer (Fancybox 5) with HTTP 206 byte-range media streaming, playback speed control (`0.5x`–`2.0x`), loop toggle (`L`), custom video poster frame capture (`P` / "Set Poster"), dedicated Video Thumbnail Chooser modal (`🎞️` / "Thumbnail") featuring candidate frames sampled every 30s across the video duration at 480px resolution, interactive timeline scrubber with live preview, and "Use Current Position" shortcut button, persistent volume/mute (`M`), direct file download, in-viewer "Move to...", inline rename (`R`), deletion (`D`), frame stepping (`,` and `.`), and quick metadata inspection (`I`). The caption is a **bottom overlay control bar** (`.lb-meta`): it is taken out of the slide/footer flow (`.fancybox__caption` positioned absolutely) so the media stays full-bleed, floats above the browser's native video control strip (`margin-bottom: 3.5rem` on video slides; images rest on the bottom edge) and always clears the Fancybox thumbnails strip. Left side shows filename, folder path (full path on desktop, just the containing folder name on phones), size, resolution badge (accent-tinted) and duration; right side is a single non-wrapping row of icon-only actions (video: speed selector, loop, set poster, thumbnail chooser; all: info, download, move, rename, delete) with `title`/`aria-label` tooltips, sized 32–38px on phones so all nine fit a 360px viewport without sideways scrolling (320px widths wrap to a second row). It follows the theme accent + OLED tokens (`--accent`, `--accent-soft`, `--glass-*`), reveals on pointer/touch/keyboard activity and fades out after ~3.5s idle via the `lb-bar-idle` class on the lightbox container, staying up while the pointer rests on the bar and respecting `prefers-reduced-motion`. Icons are inline SVGs from `lucide-static` (see `frontend/src/shared/icons.js`) rather than emoji.
   - Media Details & EXIF Inspector Modal: Technical specs breakdown (resolution, aspect ratio, duration, estimated bitrate, video/audio codecs, framerate, container), EXIF capture time vs modification timestamp, and full path with one-click clipboard copying.
   - File management: right-click (desktop) or tap-and-hold (mobile) context menu with Media Info / Set Poster Frame / Generate Animated Preview / Download / Move / Rename / Delete; multi-select (checkbox, Ctrl/Cmd-click, Shift-range, a Select All / Deselect All toolbar button, or Ctrl/Cmd+A) bulk Move / Delete / Rename / WebP / Animated Previews; plus folder creation, rename, recursive delete, empty folder cleanup via the "Clean Empty Folders" card, and batch animated video preview compilation via the "Generate Animated Previews" tool card ("All Videos" and "Current Folder" triggers), background worker trigger buttons, and mobile sheet in the Background Tasks modal.
   - Chunked File & Mobile Upload: In-browser chunked upload engine supporting single/multi-file uploads, drag-and-drop, and mobile Safari/iOS native photo and video picking with full `.heic`/`.heif`/`.dng` photo support and `.mov`/`.mp4` video support; atomic staging with FUSE-safe renaming, collision handling (`auto-rename`, `skip`, `overwrite`), and instant post-upload folder re-indexing.
   - Background Tasks & System Status Modal: Centered solid dark dialog (replacing legacy glassmorphic popover; full-height bottom sheet with rounded top corners on phones) structured into 3 categorized tabs ("Active & Queues", "Background Workers", and "Tools & Maintenance") with live badge counts, a tab strip that collapses to icon-only on phones (< `md`) so all three fit without sideways scrolling (full names kept as `aria-label`/`title`), smart default tab selection that never overwrites the saved tab, uncluttered empty states, responsive 2-column card layouts, and maintenance tools for folder thumbnails, WebP conversion, batch animated preview generation, browser animated preview cache management (live CacheStorage count, usage badge, "Pre-cache Folder", and "Clear" cache), ZIP archives, and empty folder pruning. URL download jobs are listed here as a compact **URL Downloads** card in the Active & Queues pane — live + queued jobs only, each with its per-job cancel control, plus an "Open page →" link to the full `/downloads` surface (finished history, retry and clear live there). Every icon in the modal — tab strip, section headers, worker/tool cards, buttons, and the JS-rendered rows (upload queue, transcode/transfer history) — is an inline SVG from the shared `frontend/src/shared/icons.js` registry rather than an emoji; the markup lives in `templates/partials/tasks_modal.html` (Media Library) and `templates/partials/sorter_tasks_modal.html` (Video Sorter), both pulled in with `{{include}}`, with a vitest asserting every `data-lb-icon` placeholder resolves and that no emoji remain.
   - Downloads Page (`/downloads`, `templates/downloads.html`, `frontend/src/downloads/index.js`): the first-class desktop URL/playlist downloader, reached from a **Downloads** nav tab in the Library and Sorter desktop headers and from a **Downloads** link in the mobile sidebar drawer. Left panel queues **multiple URLs at once** (newline/whitespace-split, blanks & duplicates dropped, each http(s) URL validated client-side and queued individually, with the count of skipped/failed lines reported); the target folder is chosen from **recent-target chips** (last folders actually downloaded into, persisted in `localStorage` `lib_dl_recent_targets`, cap 8 — `frontend/src/library/recent_targets.js`) or a **folder-tree picker modal** (drills the library via `/api/backup/folders`, ZIP archives filtered out). It also shows a **downloader availability line** (yt-dlp / gallery-dl detected, from the `tools` key on the status payload). Right panel is the live jobs list: active + queued pinned on top, then **paginated finished history** (`/api/backup/upload/url/history`, newest first, "Load older downloads" paging through the worker's 500-row history) with per-job **cancel**, **retry** (failed/cancelled only — clones the job) and a **Clear history** button that empties finished jobs (active/queued untouched). Polls `/api/backup/upload/url/status` every second; rows reuse the shared `frontend/src/library/url_downloads.js` renderer (`retryable: true`). Finished jobs are folded in live from the status payload's history tail (and the paginated history endpoint is refreshed whenever the live active+queued count drops), so a job that completes while the page is open never disappears before the next explicit page fetch. The page includes the shared theme customizer (`theme_modal.html` + `window.THEME_MANAGER`) so accent/contrast settings stay consistent, opened from either header trigger — both the mobile and desktop buttons carry `data-theme-modal-btn` (and the mobile one owns the `themeModalBtnMobile` id), so the duplicate-id collision that left the desktop button dead is gone and the `T` shortcut clicks whichever trigger is visible. Mobile layout: the fixed mobile top bar (`md:hidden fixed top-0`, `3rem` tall plus `env(safe-area-inset-top)`) has a matching `md:hidden` spacer div before the scroll stage, so the page intro and first panel start below the bar instead of being permanently covered by it. The old desktop Library behaviors — the Upload dropdown's "🔗 URL" dialog (removed) and the Background Tasks modal's full URL Downloads section (reduced to a compact live-only card) — now funnel to this page: the dropdown item navigates to `/downloads?folder=<current>` preserving the browsed folder as the target.
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
8. **Theme & Appearance Customizer + Mobile Touch Experience**:
   - Customizable accent color palettes (Amber, Emerald, Violet, Sky, Rose, Yellow) dynamically remapping UI actions, badges, borders, and ambient glow.
   - Background contrast modes: Midnight Stone (warm neutral dark), Pure OLED Black (true `#000000` pitch black with zero ambient glow for OLED displays), and Slate Navy (deep space surfaces).
   - Zero-delay live updates via CSS custom properties + dynamic style rules, zero flash on reload via `<head>` bootstrapping, and persistence in `localStorage` (`lib_theme_accent`, `lib_theme_base`).
   - Accessible via the `🎨 Theme` button in top navigation or shortcut `T`.
   - Mobile touch enhancements: double-tap video sides to seek ±5s with animated ripple bubbles (`⏪ -5s` / `⏩ +5s`), touch swipe carousel navigation, and touch-adapted tap targets.
   - Sorter mobile bottom action bar (`.mobile-bottom-bar`): fixed bottom thumb-zone bar (Prev/Skip/Undo/Rename/Delete) on phones with iOS safe-area bottom padding (`--sab`); on landscape phones (max-height 550px) it stays reachable as a compact row, while tablets (≥ `md`) keep it hidden in favour of the desktop toolbar.
   - Mobile single-line responsive header (`#mobileHeader` on screens `< md` / `< 768px`): Compact bar featuring the drawer menu `[ ☰ ]`, subfolder back arrow `[ ‹ ]`, folder title `[ 📁 Title ▾ ]` with one-tap ancestor breadcrumb popover sheet, a one-tap Background Tasks button (`#mobileTasksBtn`, an icon that swaps to a spinner while work is in flight with a live active+queued item-count badge that caps at `99+`) opening the shared tasks modal directly, and unified `[ 🎛️ Controls ]` trigger with an ambient pulse badge for active filters only (background activity moved to the tasks button). Desktop multi-row headers are hidden on mobile to eliminate clutter.
   - Native scroll-aware auto-hide: scrolling down translates `#mobileHeader` offscreen (`-translate-y-full`) to devote 100% of the viewport to media; scrolling up (> 8px) or reaching stage top (`scrollTop <= 20`) smoothly slides the header down into view (`translate-y-0`).
   - 5-tab slide-up bottom sheet control modal (`#mobileControlsSheet`): a fixed 5-equal-column **icon-only** tab strip (search, palette, folder, link, settings — rendered from the shared inline SVG set, full names kept as `aria-label`/`title`) that always fits the phone width and never scrolls sideways; the sheet header and the 'Folder Actions' tiles (upload files/folder, URL, new folder, refresh, cache thumbs, animated previews, convert non-MP4s, optimise images, compress/unpack ZIP), 'Downloads' actions and the breadcrumb sheet header use the same icons. Every icon lives in the template as an emoji fallback inside a `[data-lb-icon]` placeholder that `hydrateIcons()` swaps for SVG at init, so the sheet still renders if the bundle fails to load. In addition it integrates 'Search & Filter' (filename search, scope toggle, media filter pills, sort select), 'Display & Theme' (XS-XL tile scale, accent colors, OLED black mode, double-tap seek toggle), 'Folder Actions' (upload files/folders, new folder, refresh, cache thumbs, WebP, ZIP tools), 'Downloads' (an inline URL download form — URL input, read-only current-folder target, yt-dlp/gallery-dl explanation and ZIP warning — above the live job list of active, queued, and recent finished downloads with progress/speed/ETA/downloader and per-job cancel; the tab carries an active+queued count badge), and 'System Status' (duplicates count, index status, video sorter link — the former Background Tasks "Open" card was removed because the header's one-tap `#mobileTasksBtn` now reaches the same modal directly). The Actions tab's "URL" tile is a shortcut that switches the sheet to the Downloads tab; the sheet stays open after starting a download so the new job appears immediately. This tab is the phone's quick path and is unchanged by the `/downloads` page work: the desktop now uses the dedicated `templates/downloads.html` page, and the Background Tasks modal keeps only a compact live-only URL Downloads card (`#urlDownloadsSection` in `templates/partials/tasks_modal.html`) with a link to the full page (the old `#urlDownloadsSection` media-query rule in `static/styles.css` was removed with the old section).
   - Selection toolbar (`#selectionToolbar`): responsive placement that sits pinned below the desktop sticky toolbar on desktop and floats as an ergonomic bottom bar (`fixed left-3 right-3`) on mobile, with browser chrome clearance and iOS safe-area clearance (`bottom: max(4.5rem, calc(var(--sab, env(safe-area-inset-bottom, 0px)) + 3.75rem))` to float cleanly above mobile bottom address bars and home indicators).
   - Full-Height Mobile Sheets (`.ml-sheet` / `.ml-sheet-card` / `.ml-sheet-grabber`): On phones (`< 768px`), the library's tall panels — Background Tasks (`#transferDropdown`/`#tasksModalCard`), Mobile Controls sheet (`#mobileControlsSheet`), Theme customizer, Duplicates + Ignored Duplicates, Batch ZIP / Batch Unpack / Empty Folders, Media Info, and the Video Thumbnail Chooser — become bottom-anchored full-viewport-height sheets instead of centered dialogs. A shared `@media (max-width: 767px)` block in `static/styles.css` overrides the wrapper to `align-items/justify-content: flex-end` with zero padding and sets the card to `height: 100dvh` with square bottom corners and a `mlSheetUp` slide-in animation (fill-mode `backwards` so inline drag transforms win). Inside a sheet, capped lists flow in the single sheet scroll region: nested queue lists get `.ml-flatten` (cap + overflow removed) and the tasks lists (queued transfers, library ops, transcodes, animated previews, transcode history) render "Show all N ↓ / Show fewer ↑" expander buttons (`renderListExpander` + `expandedSheetLists` set in `frontend/src/library/index.js`) instead of passive "+N more" footers; batch candidate lists get `.ml-unbound` (cap removed so `flex-1` fills the sheet). Swipe-to-dismiss (`initSheetDrag`): pointer-driven, drags the card via inline `translateY`, starting only from the grabber handle (`[data-sheet-grabber]`) or the header background (never on buttons/inputs/sliders — protecting the thumbnail-chooser scrubber), dismisses past 120px with a 240ms slide-down, otherwise snaps back; disabled under `prefers-reduced-motion`. Short confirm dialogs (rename, new folder, delete, move, conflict, upload review) and the sorter UI keep their centered shapes untouched, and desktop (≥ 768px) renders exactly as before.
9. **UI Stacking Context, Solid Header & Viewport Scroll Lockdown**:
   - Navigation header (`<header class="bg-slate-950 border-b border-slate-800/80 sticky top-0 z-50 ... shrink-0">`) is pinned at `z-50` with an explicit CSS declaration (`header.sticky { position: sticky !important; top: 0 !important; z-index: 50 !important; }`), rendered with a solid opaque dark surface (`s[950]` / `#000000` in OLED) so content never bleeds through.
   - Dual-pane Media Library layout locks document scrolling to prevent body overflow: `body` uses `h-screen h-[100dvh] overflow-hidden flex flex-col`, header takes `flex-none`, sidebar is `md:h-full`, and main stage (`<main id="mainStage">`) is `h-full min-h-0 flex-1 overflow-y-auto relative`.
   - Pinned Library Top Bar (`#libraryStickyHeader`): Sticky header pinned at `z-30` directly below the navigation bar containing breadcrumbs, folder/upload actions, search scope, filename search, sort, thumbnail size presets, filter pills, ZIP banner, and selection toolbar with solid theme-reactive surface (`s[950]` / `#000000` in OLED) ensuring media tiles scroll cleanly underneath without margin gaps.
   - Isolated Scrollable Gallery Area (`#galleryContentArea`): Holds subfolder cover cards, empty state, justified media grid, and infinite scroll sentinel with consistent responsive stage padding (`p-2.5 sm:p-4 md:p-6 pb-28 sm:pb-32 md:pb-6`). On mobile (`< md`), applies `padding-bottom: max(8.5rem, calc(var(--sab, env(safe-area-inset-bottom, 0px)) + 7.5rem))` (and `max(4.5rem, calc(var(--sab, 0px) + 3.5rem))` in landscape) so when scrolling to the end of the gallery, the bottom-most media tiles, duration pills, metadata footers, and checkboxes are never covered by mobile browser bottom address bars, bottom navigation toolbars, or floating action bars. Folder navigation resets `mainStage.scrollTop = 0` automatically.
   - Floating Back to Top Button (`#scrollToTopBtn`): Spring-animated, theme-accented floating button (`fixed bottom-6 right-6 z-30` on desktop, lifted to `bottom: max(5.5rem, calc(var(--sab, env(safe-area-inset-bottom, 0px)) + 4.5rem))` on mobile `< md`) that appears when `mainStage.scrollTop > 350` and smoothly scrolls the gallery back to top (`scrollTop = 0`). Respects safe-area insets (`--sab`, `--sar`), sits cleanly above mobile browser address bars, and sits beneath mobile overlay backdrops.
   - Mobile Sidebar Drawer & Backdrop Isolation: On mobile (`< md`), `#sidebarBackdrop` sits at `z-40` (`fixed inset-0 bg-slate-950/80 backdrop-blur-sm`) and `#sidebarDrawer` sits at `z-[45]` (`fixed top-12 sm:top-[52px] bottom-0 left-0 w-72 sm:w-80`) with solid theme-reactive surface (`bg-slate-900` / `#050506` in OLED mode) and mobile close button (`#closeSidebarBtn`), sliding over the blurred background so the drawer itself is never blurred or obscured.
   - Modal scroll isolation: modals apply `.modal-scroll-lock` (`overflow: hidden !important`) to `document.body` and set `overscroll-behavior: contain !important` on scrollable bodies, preventing theme/task modal interactions from causing background overscroll.
   - Transfer dropdowns sit at `z-[70]` with backdrops at `z-[60]`. Full-page modals sit at `z-[80]` and high-priority action dialogs at `z-[1200]`.


### Tech Stack
- **Language & Runtime**: Python 3.9+ (tested on Python 3.13)
- **Web Framework**: FastAPI (v0.110+) with Uvicorn ASGI server and Starlette
- **Concurrency & Threads**: AnyIO (expanded threadpool tokens to 100), asyncio, Python `threading`
- **Database**: SQLite 3 with Write-Ahead Logging (`WAL`), `synchronous=NORMAL`, memory temp store, mmap, and optimized index structures
- **Media Processing**: FFmpeg & FFprobe (CLI subprocesses), Pillow (PIL) with `pillow-heif` (HEIC/HEIF/DNG native decoding), ImageHash, NumPy, SciPy
- **URL Playlist Downloading**: yt-dlp (run as an isolated subprocess: `python -m yt_dlp`, never imported in-process) with an automatic **gallery-dl** fallback (`python -m gallery_dl`) for sites yt-dlp does not support (image boards / file hosts such as bunkr album links)
- **Frontend**: Vanilla JavaScript (ES6+), raw HTML templates (served via `HTMLResponse`, no Jinja2 dependency), CSS3, native HTML5 Video. Icons are inline SVGs inlined at build time from `lucide-static` (ISC, a devDependency) via Vite `?raw` imports into the shared registry `frontend/src/shared/icons.js` (`icon(name, class)` for JS-rendered markup, `hydrateIcons()` for static `[data-lb-icon]` placeholders) — no icon font, no runtime icon library, and no CDN request; only the icons actually imported are bundled, and both entrypoints (`library.js`, `app.js`) share the one registry.
- **Testing**: Backend: Pytest (v8.0+) with `pytest-cov` and `conftest.py` sandboxing fixtures; Frontend: Vitest (v1.6+) with `happy-dom` DOM simulation under `frontend/tests/`.

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
Before completing any task, execute the backend and frontend test suites:
```bash
./venv/bin/pytest
npm test
```
All existing tests must pass, and new features must include unit or integration tests under `tests/` or `frontend/tests/`.

### Rule 5: Keep Live Server Running Latest Code
Because HTML templates are cached in memory for the process lifetime (`media_library.core.templating.load_template`) and Python modules require process reloading, **always restart the systemd service** whenever making code, template, or asset changes so the live server serves the latest code:
```bash
systemctl restart media-library && systemctl is-active media-library
```
Confirm the service is active and responding after the restart.

### Rule 6: Frontend Cache Busting
When modifying frontend JavaScript assets (`frontend/src/**/*.js`), remember that the application uses hardcoded cache buster strings in the HTML templates to prevent browsers from using stale client code. After running `npm run build`, you **must** manually increment the version string for the modified entrypoint:
- If you modify Sorter JS (`frontend/src/app/*`), increment `src="/static/dist/app.js?v=XX"` in `templates/index.html`.
- If you modify Library JS (`frontend/src/library/*`), increment `src="/static/dist/library.js?v=XX"` in `templates/library.html`.
- If you modify Downloads JS (`frontend/src/downloads/*`), increment `src="/static/dist/downloads.js?v=XX"` in `templates/downloads.html`. (Modules under `frontend/src/library/` are shared: `url_downloads.js` and `recent_targets.js` feed both `library.js` and `downloads.js`, and `frontend/src/shared/tasks_badge.js` plus `frontend/src/shared/icons.js` feed both `library.js` and `app.js` — touching either shared module means bumping **both** entrypoint versions. `templates/partials/tasks_modal.html` / `sorter_tasks_modal.html` edits land in `library.js` / `app.js` respectively.)

### Rule 7: Always Commit & Push Changes (Zero Personal Data & Secrets)
Every task involving code, template, configuration, or documentation changes must be committed and pushed to the remote repository (`origin`) upon completion:
1. **Zero Personal Data & Secret Hygiene**:
   - Never store, stage, or commit personal identifiers, real names, user profile paths (e.g. `/home/<user>`, `/Users/<user>`), IP addresses, personal email addresses, API tokens, passwords, private keys, or credentials.
   - Author metadata must remain generic (`Media Library <dev@medialibrary.local>`).
   - Clean up any ephemeral scratch scripts, test dumps, or temporary files before committing so no personal or temporary artifacts linger on the machine or in git.
   - Ensure local environment files (`.env`), user configurations (`config.json`), local service files (`media-library.service`), and databases (`*.db`, `data/`) remain ignored in `.gitignore`.
2. **Commit & Push Flow**:
   - Ensure all tests pass (`./venv/bin/pytest`) and live server is running updated code (`systemctl restart media-library`).
   - Stage changes (`git add .`) and create a concise conventional commit (`git commit -m "..."`).
   - Push immediately to the remote repository (`git push origin <branch>`).
   - Confirm with `git status` that the working tree is clean and synced with remote.

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
│   │   ├── views.py                 # HTML page endpoints (/, /sorter, /downloads, /library)
│   │   ├── auth.py                  # Shared-password login page/routes and auth middleware
│   │   ├── library_browse.py        # Library browsing, folder tree, search, index controls
│   │   ├── library_media.py         # Library file/thumbnail byte-range streaming
│   │   ├── library_admin.py         # Library rename/delete + thumbnail controls
│   │   ├── library_manage.py        # Library page file/folder management (move, folders, undo)
│   │   ├── library_upload.py        # Web browser chunked file and folder uploading
│   │   ├── url_download.py           # URL/playlist video download endpoints (yt-dlp + gallery-dl, retry/history/clear, tools)
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
│   │   ├── upload_service.py        # Chunked upload staging and FUSE-safe atomic moves
│   │   ├── library_query.py         # Index-only row→dict shaping for library browsing
│   │   ├── status.py                # Shared worker/index/transfer/ZIP status payload builders
│   │   ├── ffmpeg_service.py        # FFmpeg/FFprobe poster extraction, WebP conversion
│   │   ├── deduplication_service.py # Quick-hash, pHash/dHash, duplicate resolution
│   │   ├── backup_service.py        # SQLite live backup, thumbnail tar bundling, auto-restore
│   │   └── zip/                     # ZIP operations package
│   │       ├── core.py              # ZIP metadata & utilities
│   │       ├── compress.py          # Folder to ZIP compression
│   │       ├── extract.py           # ZIP to folder unpacking
│   │       ├── batch.py             # Batch ZIP processing workers
│   │       └── optimize.py          # WebP optimization inside ZIPs
│   │
│   └── workers/                     # Background asynchronous worker subsystem
│       ├── __init__.py
│       ├── base.py                  # Abstract BaseWorker with lifecycle & pause/resume
│       ├── transfer_worker.py       # Sequential non-blocking file moves with undo
│       ├── transcode_worker.py      # Background video pre-transcoding
│       ├── thumbnail_worker.py      # Background video poster frame extraction
│       ├── animated_thumb_worker.py # Background animated WebP video preview generation
│       ├── metadata_worker.py       # MediaMetadataWorker: dimensions/duration/codec/capture-time probing
│       ├── duplicate_hash_worker.py # DuplicateHashWorker (quick) & ThumbHashWorker (visual)
│       ├── webp_worker.py           # WebPOptimizerWorker for background image conversion
│       ├── library_ops_worker.py    # LibraryOpsWorker for queued moves/deletes/renames + undo
│       ├── url_download_worker.py   # URLDownloadWorker: queued yt-dlp/gallery-dl playlist downloads
│       └── backup_watcher.py        # Periodic & shutdown library backup snapshotting
│
├── frontend/                        # Vite frontend source code & Vitest test suite
│   ├── src/                         # ES modules for app and library
│   │   ├── app/                     # Video Sorter UI logic (index.js)
│   │   ├── downloads/               # Downloads page UI logic (index.js)
│   │   ├── library/                 # Media Library UI logic (index.js, state.js, utils.js, url_downloads.js, recent_targets.js, service_worker.js)
│   │   └── shared/                  # Cross-page modules (icons.js, tasks_badge.js)
│   └── tests/                       # Vitest browser/DOM unit tests (setup.js, icons, library_utils, library_state, recent_targets, service_worker, tasks_badge, url_downloads)
│
├── vitest.config.js                 # Vitest test runner configuration (happy-dom environment)
├── static/                          # Static assets and built bundles
│   ├── styles.css                   # Main application stylesheet (source)
│   └── dist/                        # Vite build output (app.js, library.js, downloads.js, styles.css)
│
├── templates/                       # Raw HTML templates (served via HTMLResponse)
│   ├── index.html                   # Video Sorter user interface
│   ├── login.html                   # Shared-password sign-in page
│   ├── downloads.html               # URL/playlist Downloads page user interface
│   ├── library.html                 # Media Library & Gallery user interface
│   └── partials/                    # HTML fragments (modals, sidebar, tasks_modal.html, sorter_tasks_modal.html) used via {{include}}
│
├── tests/                           # Automated test suite
│   ├── conftest.py                  # Sandboxed fixtures (sandbox_env, sample_media)
│   ├── test_api_routes.py           # HTTP endpoints & status verification
│   ├── test_auth.py                 # Shared-password authentication & session protection
│   ├── test_backup_and_admin_operations.py # Backup snapshot/restore, rename/delete, poster capture
│   ├── test_backup_service.py       # Backup snapshot & restore tests
│   ├── test_core_config.py          # Configuration loading & path resolution
│   ├── test_coverage_duplicate_hash_worker.py # Candidate quick hashing, pHash migration, worker loops, pause/resume, DB error resilience
│   ├── test_coverage_ffmpeg_and_media_service.py # FFmpeg/probe helpers, rotation, candidate frame caching, WebP rollback, scanner errors
│   ├── test_coverage_indexer_and_sorter_queue.py # Move/delete index updates (zip/folder/file), prune safety thresholds, tolerant matching, destination creation
│   ├── test_coverage_library_browse_and_manage.py # Debounced indexing, inflight pruning, folder clamp/cover fallback, bulk rename modes, move hierarchy guards
│   ├── test_coverage_library_manage_and_backup.py # Library move/delete/rename paths, empty folder inspection, backup snapshot create, restore, auto-restore
│   ├── test_coverage_sorter_move_and_media.py # Sorter destination collisions, transcode links, transfer abort, rename/delete cancellations, duplicates
│   ├── test_coverage_transcode_worker.py # Video remux, progress parser, re-encode fallback, abort cancellation, cross-device commits, pretranscode scanner
│   ├── test_coverage_webp_worker_and_ffmpeg.py # WebP byte conversions, CMYK/palette transparency, file status outcomes, base64 poster extraction, optimizer loop
│   ├── test_coverage_workers_and_more.py # Memory guard host headroom, duplicate hash worker controls, media thumbnail fallback, candidate frame errors
│   ├── test_coverage_zip_services.py # ZIP path resolution, central directory indexing, LRU cache eviction, ZipSlip protection, folder unpack
│   ├── test_db_indexer.py           # SQLite indexer & pruning tests
│   ├── test_deep_coverage_workers_services.py # Worker loops, upload service, deep clean, WebP/FFmpeg
│   ├── test_duplicates_comprehensive.py # Quick & visual deduplication, cliques, auto-delete, worker loops
│   ├── test_library_manage.py       # File management, empty folder cleanup & undo operations
│   ├── test_library_media_and_http_stream.py # Byte-range streaming, ETag 304 caching, thumbnail candidate frames
│   ├── test_media_dimensions.py     # Dimension/metadata probing, natural-ratio tile data, folder totals
│   ├── test_media_service_and_ops_worker.py # LAN IP, token search, ops worker queue/undo, debounced sync-back
│   ├── test_media_services.py       # FFmpeg service, poster extraction, media utils
│   ├── test_performance_features.py# In-memory stat cache, subfolder caching, status API
│   ├── test_sorter_browse.py        # Video sorter browsing, queue scanning & destination listing
│   ├── test_sorter_media_and_queue.py # Sorter streaming, queue refresh, transfer abort, tolerant path resolution
│   ├── test_sorter_move.py          # Sorter moves, undo stack, file rename, delete & transfers
│   ├── test_thumbnail_chooser.py    # Video poster selection, candidate generation & timeline scrubbing
│   ├── test_transcode_comprehensive.py # Transcoding pause/resume, cancel, queue, and worker execution
│   ├── test_webp_memory_guard.py    # WebP conversion memory safeguards and skip logic
│   ├── test_workers.py              # Worker lifecycle, pause/resume, queue execution
│   ├── test_url_download.py         # URL download worker (mocked subprocess), gallery-dl fallback, retry/clear-history/paginated history, tools status, router validations
│   ├── test_system_status_transcode.py # /api/system/status transcode payload shape (queued/is_paused/target_filename) shared with transfers.transcode
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
| `UPLOAD_STAGING_DIR` | N/A | `${DATA_DIR}/upload_staging` | Staging area for chunked browser uploads before finalizing |
| `MAX_UPLOAD_FILE_SIZE_GB`| N/A | `20.0` | Maximum allowed file size for browser uploads in GB |
| `WEBP_SKIP_LARGER` | N/A | `false` | If `true`, abort conversion if WebP exceeds original file size |
| `DOWNLOAD_WORKER_CONCURRENCY` | N/A | `2` | Max parallel URL/playlist downloads (yt-dlp / gallery-dl) |
| `DOWNLOAD_COOKIES_FILE` | N/A | *(empty)* | Optional Netscape cookies file path passed to the downloader (`--cookies`) for auth-walled/private videos and playlists |
| `ZIP_STAGING_DIR` | N/A | `${DATA_DIR}/zip_staging` | Local fast-disk staging area for assembling ZIP archives before FUSE transfer |
| `ZIP_CACHE_DIR` | N/A | `${DATA_DIR}/cache/zips`| Local fast-disk cache for extracted ZIP contents |
| `ZIP_CACHE_MAX_SIZE_GB`| N/A | `5.0` | Maximum disk size in GB for extracted ZIP cache before LRU eviction |
| `QUICK_NAV_DIRS` | N/A | Auto-detected | Comma-separated quick navigation directory paths |
| `SEARCH_LIMIT` | N/A | `1000` | Global library search result limit (maximum matches returned in a single search query before truncation) |
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
    capture_time REAL,                 -- Capture/creation epoch (EXIF or container creation_time), or NULL
    thumb_mtime REAL,                  -- Timestamp of custom poster frame extraction (used for URL cache busting)
    has_animated INTEGER DEFAULT 0     -- 1 if cached animated WebP video preview exists on disk
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
- `idx_files_has_animated ON files(has_animated)`
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
| `animated_thumb_worker_instance` | `AnimatedThumbWorker` | `workers.animated_thumb_worker` | Background sequential generation of looping animated WebP video previews; dual queues (priority queue for visible gallery tiles, batch queue for folder/library batch jobs) with pause/resume/cancel controls and live status. |
| `metadata_worker_instance` | `MediaMetadataWorker` | `workers.metadata_worker` | Probes source dimensions, duration, codecs, fps, and capture time once per loose file (FFprobe + EXIF); backfills ZIP-member dimensions from cached local thumbnails; powers natural-aspect-ratio tiles and avoids re-probing in the transcode path. |
| `duplicate_hash_worker_instance` | `DuplicateHashWorker` | `workers.duplicate_hash_worker` | Computes 128 KB head SHA-256 (+ size) for files with identical sizes. |
| `thumb_hash_worker_instance` | `ThumbHashWorker` | `workers.duplicate_hash_worker` | Computes perceptual `pHash` from video posters & images. |
| `webp_worker_instance` | `WebPOptimizerWorker` | `workers.webp_worker` | Background conversion of loose (non-ZIP) images to WebP. ZIP archives are excluded from the automatic sweep and are optimized on demand only — as a single archive-level unit when browsed or via the Optimize WebP button. Backs off when memory is low (measured against the tighter of host RAM and the service cgroup cap) and returns freed heap between conversions. |
| `url_download_worker_instance` | `URLDownloadWorker` | `workers.url_download_worker` | Queued URL/playlist downloads into `UPLOAD_STAGING_DIR` then the library folder; runs yt-dlp first and falls back to gallery-dl for unsupported sites (bunkr, image boards); up to 2 parallel slots, skip-and-continue per playlist item, live/premiere filtering, per-job cancel; retains a 500-row terminal history with newest-first pagination (`get_history`), clone-retry for failed/cancelled jobs (`retry`), and clear-finished (`clear_history`). |
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
   - Workers started: `transfer`, `transcode`, `crawler`, `thumbnail`, `animated_thumb`, `metadata`, `duplicate_hash`, `thumb_hash`, `webp`, `library_ops`, `backup_watcher`.
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

### Animated WebP Video Previews
1. Deterministic hashing: Animated preview path is `THUMBNAIL_CACHE_DIR / "anim_{h}.webp"` where `h` is the first 24 characters of the SHA-256 of `{file_path}_{mtime}_{size}`.
2. Frame sampling & compilation (`media_library.services.ffmpeg_service.generate_animated_video_preview`):
   - Samples up to 24 candidate frames evenly spaced across video duration at 480px resolution (`scale=480:-2:flags=fast_bilinear`).
   - Uses candidate frames cache directory when available; extracts candidate frames in batch via fast FFmpeg seeking if not already cached.
   - Compiles frames into a seamless looping WebP animation (`-loop 0`, 2 fps / ~500ms duration per frame).
   - Atomically updates `files.has_animated = 1` in SQLite upon completion.
3. Queueing & Non-blocking Tile Delivery:
   - `AnimatedThumbWorker` (`workers.animated_thumb_worker`) manages background generation across two prioritized queues: a high-priority queue for currently visible gallery cards and a background batch queue for entire folders or library sweeps.
   - When the user enables animated previews, `GET /api/backup/thumbnails/animated?path=...&wait=false` returns the static thumbnail image immediately alongside an `X-Thumbnail-Status: generating` HTTP header if the animated preview is not yet generated, while enqueuing the video into the priority worker queue.
   - Startup reconciliation (`sync_animated_thumb_db_cache`) synchronizes existing `anim_*.webp` cache files with the SQLite database to avoid redundant work.
4. Playback Triggers (`IntersectionObserver` + hover):
   - Video tiles in the media library start with their lightweight static poster thumbnail (`file.thumb_url`).
   - **Desktop (hover-capable + fine pointer, `matchMedia('(hover: hover) and (pointer: fine)')`)**: tiles stay on the static poster and play the looping preview only while the pointer is over the tile (`pointerenter`/`pointerleave`, mouse pointers only), reverting on mouse-out. Hover previews are independent of the `🎞️ Animated` toggle, so a hover always previews; an ungenerated tile is enqueued to the priority worker queue on first hover.
   - **Touch devices**: a dedicated `IntersectionObserver` (`setupAnimObserver`, rootMargin: `0px`, threshold: `0`) tracks card viewport visibility, and only tiles currently on screen play animated WebP previews (`img.src = animated_url`, `playingAnim = 'true'`) while the toggle is on.
   - When a tile stops playing — scrolled off-screen on touch, or the pointer leaves on desktop — its `src` is instantly swapped back to its static poster thumbnail (`img.dataset.fallbackThumb`), terminating multi-frame WebP decoding and freeing browser CPU, GPU, and memory.
   - Animations are also automatically paused when the browser tab is hidden or minimized (`visibilitychange` listener), and when viewing a media item in the fullscreen lightbox viewer, resuming seamlessly when returning to the visible gallery.
   - The shared decision logic lives in `shouldPlayAnimatedThumb` (`frontend/src/library/utils.js`).

### Media Metadata Probing (natural-ratio tiles)
`MediaMetadataWorker` (`workers.metadata_worker`) probes each loose indexed file once via `probe_media_metadata` (`services.ffmpeg_service`):
- Single FFprobe pass captures display `width`/`height` (rotation applied), `duration`, `video_codec`, `audio_codec`, `fps`, and container `creation_time`. Images without a container timestamp fall back to an EXIF `DateTimeOriginal` read via Pillow.
- `width IS NULL` marks a file as not-yet-probed; the worker fills all probe columns together and writes `0 x 0` on failure so the file is not retried until the indexer sees it change (carry-over in `index_folder`/`update_file_in_index` keeps results across crawls).
- ZIP members are probed separately: rather than force archive extraction over FUSE, the worker reads their dimensions from the cached local thumbnail (`_backfill_zip_batch`, one batch per loop alongside loose probing, with a 60s sweep cooldown). Members without a thumbnail yet fall back to a 1:1 box filled with `object-cover` (the justified grid no longer re-flows on image load).
- Progress is exposed under `metadata` in `/api/system/status`.

### URL Playlist Downloading (yt-dlp with gallery-dl fallback)
1. **Queueing**: `POST /api/backup/upload/url` validates the http(s) URL and the target folder (must exist, not a virtual ZIP), verifies that at least one downloader (`python -m yt_dlp` or `python -m gallery_dl`) is available, and enqueues a job on `URLDownloadWorker`.
2. **Execution**: each job downloads into a per-job staging directory `UPLOAD_STAGING_DIR / dl_<job_id>` (local disk). The yt-dlp subprocess uses `--ignore-errors` (skip-and-continue per playlist item), `--match-filter "!is_live & !is_upcoming"` (skip live streams & premieres), `-f bv*+ba/b --merge-output-format mp4` (best quality with ffmpeg merge), and an optional `--cookies DOWNLOAD_COOKIES_FILE`. When yt-dlp reports an unsupported URL (or finds nothing importable), the job automatically retries with **gallery-dl** (`gallery_dl --dest <staging> -o directory=[]`, flattened output) — covering sites yt-dlp lacks an extractor for, such as bunkr album links. If yt-dlp is not installed at all, gallery-dl is used directly. Up to `DOWNLOAD_WORKER_CONCURRENCY` (default 2) jobs run in parallel via a shared slot loop.
3. **Progress**: yt-dlp progress is parsed from `--newline --progress-template` JSON lines (`percent`, `downloaded_bytes`, `speed`, `eta`, `playlist_index/count`, `title`); gallery-dl progress is inferred from the completed file paths it prints (`current_title`, running file count — no byte percentage). Both are exposed through `/api/system/status#url_downloads` (each entry carries a `downloader` field naming `yt-dlp` or `gallery-dl`), and the payload's `tools` key reports yt-dlp/gallery-dl availability. The payload is rendered by shared helpers in `frontend/src/library/url_downloads.js` on two surfaces: the dedicated `/downloads` page (recent-target chips + folder picker, multi-URL paste, availability line, retry/clear, paginated history via `/api/backup/upload/url/history`) and the mobile Controls & Settings "Downloads" tab (full 20-row history, inline URL form, tab count badge). The former desktop Library modal dialog was removed, and the Background Tasks modal now shows only a compact live + queued card (cancel + link) while the full history/retry/clear surface lives on `/downloads`.
4. **Finalization**: completed files are moved one by one into the target folder with auto-rename collisions (`file (1).ext`) and atomic `.tmp_*` -> `os.replace` (FUSE-safe). Only video/image/audio extensions are imported; subtitle/sidecar outputs (`.vtt`, `.json`, etc.) are dropped. The target folder is re-indexed afterwards. Jobs are individually cancellable (SIGKILL of the subprocess + staging cleanup). **Retry** (`POST /api/backup/upload/url/retry`) clones a failed/cancelled job as a new queue entry after re-validating its destination (completed jobs are never re-downloaded); **Clear history** (`POST /api/backup/upload/url/clear-history`) drops terminal rows while leaving active/queued jobs alone.

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
  "transcode": { "active": null, "queued": [], "queued_count": 0, "history": [], "is_paused": false, "total_pending": 0 },
  "index": { "status": "idle", "scanned_folders": 120, "skipped_folders": 0, "total_folders": 120, "percent": 100 },
  "thumbnails": { "status": "idle", "pending_count": 0, "percent": 100 },
  "animated_thumbnails": { "status": "idle", "pending_count": 0, "priority_count": 0, "batch_count": 0, "completed_count": 10, "percent": 100.0, "active_file": null, "recent_completed": [] },
  "metadata": { "status": "idle", "pending_count": 0, "zip_pending_count": 0, "processed_count": 0, "percent": 100 },
  "duplicate_hash": { "status": "idle", "pending_count": 0, "percent": 100 },
  "thumb_hash": { "status": "idle", "pending_count": 0, "percent": 100 },
  "webp": { "status": "idle", "saved_formatted": "42.1 MB", "converted_count": 140 },
  "library_backup": { "status": "idle", "backup_exists": true, "last_backup_str": "Oct 06, 12:00:00" },
  "zip": { "is_active": false, "tasks": [] },
  "batch_zip": { "status": "idle", "succeeded": 12, "overall_percent": 100.0 },
  "batch_unzip": { "status": "idle", "succeeded": 0, "failed": 0, "errors": [], "overall_percent": 0.0 },
  "library_ops": { "status": "idle", "active": null, "queued": [], "undo_count": 0 },
  "url_downloads": { "status": "idle", "concurrency": 2, "active": [], "queued": [], "history": [], "tools": { "ytdlp": true, "gallery_dl": true } }
}
```
- The frontend uses `is_active` to adjust polling frequency: **2000 ms** when active background work is in flight, **6000 ms** when idle, **8000 ms** on error.
- `url_downloads.active_count > 0` or `queued_count > 0` also sets `is_active`.
- The top-level `transcode` key is the **same shaped payload** as `transfers.transcode` (built by `services.status.transfers_payload`): it carries the `queued` list (with `source_path`), `is_paused`, `total_pending`, and a `history` whose entries include `target_filename`, `source_path`, `target_path` and `target_size_formatted`. The Library's transcode panel and the sorter read this one shape; a queued transcode also sets `is_active`.

---

## 13. Complete API Routing & Endpoint Map

### HTML Views (`media_library.routers.views`)
- `GET /` — Serves Media Library UI (`templates/library.html`)
- `GET /sorter` / `GET /sort` — Serves Incoming Video Sorter UI (`templates/index.html`)
- `GET /downloads` — Serves the URL/playlist Downloads page (`templates/downloads.html`; bundle `static/dist/downloads.js`)
- `GET /library` — 302 Redirect to `/`
- `GET /sw.js` — Serves Service Worker script (`static/sw.js`) with `Service-Worker-Allowed: /` and `Cache-Control: no-cache, no-store, must-revalidate` for client-side animated WebP thumbnail interception and CacheStorage caching

### Authentication (`media_library.routers.auth`)
- `GET /login` — Serves the shared-password sign-in page (`templates/login.html`); redirects to `/` when auth is disabled or already signed in
- `POST /login` — Validates the shared password and sets the signed `ml_auth` cookie (303/302 redirect to the validated `next` target)

### Media Library & Browser (`media_library.routers.library_browse` + `library_media` + `library_admin`)
- `GET /api/backup/browse?path={folder_path}&refresh={bool}` — List subfolders, files, breadcrumbs, ZIP indicators. Never indexes inline: for a folder not yet in the index it schedules indexing on a background thread and returns the cached view with `indexing: true` (the client re-polls). Returns per-file `thumb_url`/`media_url` plus probed `width`/`height`/`duration`/`video_codec`/`audio_codec`/`fps`/`capture_time` (dimensions drive natural-aspect-ratio tiles). Each subfolder entry carries `file_count`/`subfolder_count`/`total_size`, a representative cover `thumb_url` (alphabetically-first direct-child image by `LOWER(name)`, falling back to the first direct-child video's poster, index-only), the cover's `width`/`height`, and `has_thumb`; folders with no media get `thumb_url: null`, and the client card falls back to an icon until the on-demand thumbnail is generated. (`library_browse`)
- `GET /api/backup/folders?path={folder_path}` — List only a folder's immediate child folders (index-only, FUSE-safe) with `file_count`/`subfolder_count`/`is_zip`. Powers the sidebar folder tree's lazy expansion; never reads file entries nor triggers ZIP extraction, and schedules background indexing with `indexing: true` for unindexed folders. (`library_browse`)
- `GET /api/backup/search?q={query}&type={type}&sort={sort}&limit={limit}` — Search media files by multi-word query across SQLite index; respects `limit` (defaults to `SEARCH_LIMIT`, default 1000) and returns `limit`, `total`, `truncated` (bool indicating if results exceeded limit), and matching `results` with probed `width`/`height`/`duration`/`video_codec`/`audio_codec`/`fps`/`capture_time`. Truncated searches display a visual indicator (`1,000+` badge) and query refinement notice. (`library_browse`)
- `GET /api/backup/media?path={file_path}` — Stream video/image with byte-range headers and caching (`library_media`)
- `GET /api/backup/thumbnail?path={file_path}` — Stream thumbnail image (`library_media`)
- `GET /api/backup/thumbnails/animated?path={file_path}&wait={bool}` — Stream looping animated WebP video preview (480px, 2 fps). If not yet cached and `wait=false`, immediately returns static thumbnail with `X-Thumbnail-Status: generating` header while enqueuing video to `AnimatedThumbWorker` priority queue (`library_media`)
- `POST /api/backup/thumbnails/generate` — Enqueue folder for poster frame extraction (`library_admin`)
- `POST /api/backup/thumbnails/generate-animated` — Generate looping animated WebP preview for a specific video (`library_admin`)
- `POST /api/backup/thumbnails/batch-animated` — Enqueue videos in a folder, selected paths, or across the entire library (`all_videos: true`) into `AnimatedThumbWorker` batch queue, resuming the worker if paused (`library_admin`)
- `POST /api/backup/thumbnails/animated-pause` — Pause or resume `AnimatedThumbWorker` (`library_admin`)
- `POST /api/backup/thumbnails/animated-cancel` — Cancel queued background animated preview generation tasks (`library_admin`)
- `GET /api/backup/thumbnails/status` — Get poster generation status (`library_admin`)
- `POST /api/backup/thumbnails/pause` — Pause/resume poster generation (`library_admin`)
- `POST /api/backup/thumbnails/set-poster` — Extract and set specific video frame timestamp (or image payload) as custom thumbnail poster frame (`library_admin`)
- `GET /api/backup/thumbnails/candidates?path={file_path}&interval={float}&count={int}` — Compute candidate frame timestamps sampled every 30s (or custom `interval` / `count`) across video duration with metadata and 480px frame URLs (`library_media`)
- `GET /api/backup/thumbnails/frame?path={file_path}&timestamp={float}` — Extract or return cached candidate video frame JPEG image at specific timestamp scaled to 480px width (`library_media`)
- `POST /api/backup/rename` — Rename media file, update on disk and in database (`library_admin`)
- `POST /api/backup/delete` — Delete media file from disk and database (`library_admin`)
- `POST /api/backup/index/sync` — Trigger a manual **full** library crawler re-index (startup crawls are incremental, keyed on per-folder `dir_mtime`) (`library_browse`)
- `GET /api/backup/index/status` — Crawler progress status (`library_browse`)
- `POST /api/backup/index/prune` — Remove stale deleted folders and orphaned thumbnails (`library_browse`)

### Library File & Folder Management (`media_library.routers.library_manage` + `library_upload`)
- `POST /api/backup/upload/chunk` — Receive a chunk of a browser upload file (staged locally)
- `POST /api/backup/upload/finalize` — Assemble, atomically write to target, and index the uploaded file
- `POST /api/backup/upload/cancel` — Cancel an ongoing upload and discard staging chunk
- `POST /api/backup/upload/url` — Queue a URL/playlist video download (yt-dlp, with automatic gallery-dl fallback for unsupported sites) into `destination_dir` (`url` + `destination_dir`); returns `{job_id}`; 503 when neither downloader is available (Downloads page, mobile sheet, and the Library Upload dropdown which links to `/downloads?folder=…`)
- `GET /api/backup/upload/url/status` — Active, queued, and recent URL download jobs plus downloader availability (`url_download_worker_instance.get_status()`; includes `tools: {ytdlp, gallery_dl}`)
- `POST /api/backup/upload/url/cancel` — Cancel a queued or running URL download job by `job_id`
- `POST /api/backup/upload/url/retry` — Clone a failed/cancelled download job as a new queue entry (`job_id`); re-validates the original destination; 404 unknown, 409 non-retryable
- `POST /api/backup/upload/url/clear-history` — Remove all finished download jobs from the worker's retained history (active/queued untouched); returns `{cleared}`
- `GET /api/backup/upload/url/history` — Newest-first paginated finished-download history (`limit` 1–500, `offset`); returns `{history, total, limit, offset}`
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
- `GET /api/backup/folders/empty/candidates` — Find all empty folders across the library (zero files and zero non-empty subfolders) with candidate list, leaf status, and count
- `POST /api/backup/folders/empty/delete` — Delete empty folders bottom-up (cleans benign OS junk like `.DS_Store`/`Thumbs.db` prior to `rmdir`, updates SQLite index, protects against deleting folders with user files)

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
- `POST /api/system/clean-db` — Trigger background deep database scan and thumbnail cleanup

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
