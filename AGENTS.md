# 🤖 AGENTS.md — Media Library & Video Sorter Agent Guide

> **CRITICAL DIRECTIVE FOR ALL AI AGENTS**:
> **This document is the authoritative architectural and operational specification for this codebase. It MUST ALWAYS BE KEPT UP TO DATE.**
> Whenever you add, modify, or remove API endpoints, database schema fields, background workers, environment variables, or core workflows, you **MUST** update this file in the same task to reflect your changes.

---

## 1. System Overview & Technology Stack

**Media Library & Video Sorter** is a high-performance, web-based media asset management system and rapid keyboard-driven video sorter designed to handle tens of thousands of video and image files efficiently across local disks and remote FUSE mounts (e.g. pCloud, network shares).

### Core Capabilities
1. **Media Library & Gallery View**:
   - Sub-millisecond SQLite-indexed browsing, folder hierarchy navigation, and multi-word full library search with configurable `SEARCH_LIMIT` (default 1,000 items), truncation detection (`1,000+` badge indicator), and query refinement alerts. Each query word may match the file name **or the file's folder path inside the library** (the root and its ancestors are excluded), so "iceland 2023" finds the clips in `…/Iceland 2023/`; every result card already carries its `rel_folder` badge, so a folder match says where it came from.
   - Row-ordered natural-aspect-ratio layout for media files and folder covers: tiles are packed into **justified rows** — every tile in a row shares one row height chosen so the row fills the container width exactly — so reading order runs strictly left to right, then down. Tile widths follow probed `width`/`height` clamped to 2:3–3:2, unknown dimensions fall back to a 1:1 box filled with `object-cover`, and a trailing partial row keeps the target height until more items arrive. Tiles feature video duration pills (`MM:SS` / `HH:MM:SS`), resolution badges (`4K`, `1080p`, etc.), video-codec badges (`H.264` / `H.265`, the latter emerald-tinted, from the probed `video_codec`; hidden until probed), high-framerate tags (`60fps`), dimensions in card footers, and a hover `ℹ️` Info button. A thumbnail-size control in the library toolbar steps through five presets (XS–XL, XL scaled up to ~640px-tall rows) that adjust the row height; the choice is persisted in `localStorage` (`lib_tile_scale`). The library stage spans the full viewport width (no centered 1920px cap).
   - Proactive background thumbnail/poster frame generation using FFmpeg, alongside on-demand looping animated WebP video previews (~500ms per frame, 2 fps, loop=0) generated from up to 24 candidate frames sampled across video duration at 480px resolution; the auto-play preference is toggled via the mobile control sheet (`🎞️ Animated Previews`) — the desktop toolbar `🎞️ Animated` toggle is hidden on hover-capable devices, where hover previews always play — with active animated video tiles displaying a subtle `ANIM` badge indicator. Animations are viewport-aware via a dedicated `IntersectionObserver` and only play when on-screen on touch devices (gated by the toggle), while hover-capable desktops play a tile's loop only while the pointer is over it; both automatically pause/swap back to static posters when no longer previewing (scrolled off-screen, mouse-out), when hidden in a background tab, or when viewing a media item in the lightbox to minimize CPU/GPU usage. A dedicated Service Worker (`/sw.js`) intercepts animated thumbnail requests and caches them in client-side browser `CacheStorage` (`media-library-anim-v1`) for instant 0ms recall, paired with `navigator.storage.persist()` for durable browser storage. Server-side, `THUMBNAIL_CACHE_DIR/animated/` is kept in shape by `services/anim_cache.py`: the index prune/deep clean sweeps previews no indexed file can reference (a delete, rename, move or x265 re-encode orphans the old key), an LRU ceiling (`ANIMATED_CACHE_MAX_SIZE_GB`, default 5 GB) trims the least recently written previews after each generation — throttled to once a minute — and clears their `has_animated` flag so the next view regenerates them, and the Background Tasks modal's Animated Preview Generator card shows that cache's size against its cap.
   - Fullscreen lightbox viewer (Fancybox 5) with HTTP 206 byte-range media streaming, playback speed control (`0.5x`–`2.0x`), loop toggle (`L`), custom video poster frame capture (`P` / "Set Poster"), dedicated Video Thumbnail Chooser modal (`🎞️` / "Thumbnail") featuring candidate frames sampled every 30s across the video duration at 480px resolution, interactive timeline scrubber with live preview, and "Use Current Position" shortcut button, persistent volume/mute (`M`), direct file download, in-viewer "Move to...", inline rename (`R`), deletion (`D`), frame stepping (`,` and `.`), and quick metadata inspection (`I`). **Video slides are sized to the clip's own aspect ratio**, not Fancybox's default fixed 16:9 box (which capped the frame at 960×540 and letterboxed portrait/ultrawide clips): `openFancybox` passes the probed `width`/`height` of each video as Fancybox's per-item `videoRatio` (unprobed files keep the 16:9 fallback), and `templates/library.html` overrides `.fancybox__slide.has-html5video .fancybox__content` to measure against the whole slide instead of 960×540, so Fancybox's own sizing fills the available stage at that ratio and stays centred (width- and height-constrained cases both fit), and the same rule block reserves the caption bar's own band as a slide `padding-bottom` (`6.5rem`, `9rem` under 768px) so the bar never covers the picture it now fills. The caption is a **bottom overlay control bar** (`.lb-meta`): it is taken out of the slide/footer flow (`.fancybox__caption` positioned absolutely) so the media stays full-bleed, floats above the browser's native video control strip (`margin-bottom: 3.5rem` on video slides, whose slide reserves that band so the bar sits clear of the picture rather than over it; images rest on the bottom edge) and always clears the Fancybox thumbnails strip. Left side shows filename, folder path (full path on desktop, just the containing folder name on phones), size, resolution badge (accent-tinted), video codec pill (H.264/H.265/MPEG-4/…, with H.265 tinted emerald so the codec the x265 action targets reads at a glance; omitted until the file is probed) and duration; right side is a single non-wrapping row of icon-only actions (video: speed selector, loop, set poster, thumbnail chooser; all: info, download, move, rename, delete) with `title`/`aria-label` tooltips, sized 32–38px on phones so all nine fit a 360px viewport without sideways scrolling (320px widths wrap to a second row). It follows the theme accent + OLED tokens (`--accent`, `--accent-soft`, `--glass-*`), reveals on pointer/touch/keyboard activity and fades out after ~3.5s idle via the `lb-bar-idle` class on the lightbox container, staying up while the pointer rests on the bar and respecting `prefers-reduced-motion`. Icons are inline SVGs from `lucide-static` (see `frontend/src/shared/icons.js`) rather than emoji.
   - **In-place x265 (HEVC) conversion**: video slides carry a `shrink` "Convert to x265 / HEVC" action (`S` shortcut) that queues a background HEVC re-encode of the file on screen via `POST /api/backup/transcode/x265`. A floating progress panel (`.lb-x265-panel`) is overlaid on the viewer with a live percentage bar, fps/speed/ETA and a Cancel button, polled every second from `GET /api/backup/transcode/x265/status?path=…` until the job reaches a terminal state. On completion the original is atomically replaced (a `.mp4` sibling when the source used another container) and the lightbox re-opens on the converted clip with a fresh cache-busted URL; the grid tile is updated in place. Once a file is already HEVC the action reports it and does nothing. See **In-place H.265 (x265) Conversion** under §7.
   - Media Details & EXIF Inspector Modal: Technical specs breakdown (resolution, aspect ratio, duration, estimated bitrate, video/audio codecs, framerate, container), EXIF capture time vs modification timestamp, and full path with one-click clipboard copying.
   - **Keyboard shortcut reference** (`?`, the `keyboard` Shortcuts button beside Theme in the desktop header, or the *Keyboard Shortcuts* card in the mobile Controls sheet's System Status tab): a full-height sheet (`.ml-sheet`, swipe-to-dismiss like the other mobile sheets) listing every grid and viewer binding, rendered from the one `SHORTCUT_GROUPS` table in `frontend/src/library/shortcuts.js` — markup in `templates/partials/library_shortcuts_modal.html` — so the help cannot drift from the keydown handler in `frontend/src/library/index.js`. `?` is ignored while a text field has focus and is not offered over the lightbox (whose own `Esc` handling would fight the sheet).
   - File management: right-click (desktop) or tap-and-hold (mobile) context menu with Media Info / Set Poster Frame / Generate Animated Preview / Download / Move / Rename / Delete; multi-select (checkbox, Ctrl/Cmd-click, Shift-range, a Select All / Deselect All toolbar button, or Ctrl/Cmd+A) bulk Move / Delete / Rename / WebP / Animated Previews; plus folder creation, rename, recursive delete, empty folder cleanup via the "Clean Empty Folders" card, and batch animated video preview compilation via the "Generate Animated Previews" tool card ("All Videos" and "Current Folder" triggers), background worker trigger buttons, and mobile sheet in the Background Tasks modal.
   - Chunked File & Mobile Upload: In-browser chunked upload engine supporting single/multi-file uploads, drag-and-drop, and mobile Safari/iOS native photo and video picking with full `.heic`/`.heif`/`.dng` photo support and `.mov`/`.mp4` video support; atomic staging with FUSE-safe renaming, collision handling (`auto-rename`, `skip`, `overwrite`), and instant post-upload folder re-indexing. Uploads are **resumable**: the browser derives a stable id per `(relative path, size, mtime)` (`makeUploadId`, with a plain-hash fallback because `crypto.subtle` only exists in a secure context), probes `GET /api/backup/upload/status` before the first chunk, and sends every chunk with its explicit byte `offset`. The server appends only at that offset — a retried chunk whose bytes it already holds is acknowledged as a duplicate instead of being written twice, a chunk that starts past the staged end is rejected with `409` rather than leaving a hole, and a partial tail from a crash mid-write is truncated and rewritten. Finalize compares the staged size against `total_size` and refuses an incomplete upload (`400`) instead of committing a silently truncated file, keeping the staged bytes so the client can finish and retry. Abandoned `<upload_id>.part` files older than `UPLOAD_STAGING_TTL_HOURS` (default 24 h) are garbage-collected at startup by `gc_stale_uploads` (which leaves the URL download worker's `dl_*` directories alone).
   - Background Tasks & System Status Modal: Centered solid dark dialog (replacing legacy glassmorphic popover; full-height bottom sheet with rounded top corners on phones) structured into 3 categorized tabs ("Active & Queues", "Background Workers", and "Tools & Maintenance") with live badge counts, a tab strip that collapses to icon-only on phones (< `md`) so all three fit without sideways scrolling (full names kept as `aria-label`/`title`), smart default tab selection that never overwrites the saved tab, uncluttered empty states, responsive 2-column card layouts, and maintenance tools for folder thumbnails, WebP conversion, batch animated preview generation, browser animated preview cache management (live CacheStorage count, usage badge, "Pre-cache Folder", and "Clear" cache), ZIP archives, and empty folder pruning, and the Background Workers pane shows a card for **every** background indexer/converter — Thumbnail Generator, Animated Preview Generator (whose card also reports the server-side animated preview cache's size against its LRU cap, from `anim_cache_payload()`), WebP Image Optimizer, Duplicate Hash Verifier, **Media Metadata Probe** (dimensions/duration/codec indexing, with the ZIP-member sweep shown as a separate pending count) and **Visual Hash Indexer** (perceptual pHash) — so no worker's progress is only visible as a header badge count; the Library Indexer card reports a truthful crawl/index state (idle: reachable / indexed folders against the DB total; amber "Index stale" with missing folder/file counts and a "Remove missing folders from index" button when the crawler's mount-safety guard blocked a prune of remotely deleted folders). URL download jobs are listed here as a compact **URL Downloads** card in the Active & Queues pane — live + queued jobs only, each with its per-job cancel control, plus an "Open page →" link to the full `/downloads` surface (finished history, retry and clear live there). Every icon in the modal — tab strip, section headers, worker/tool cards, buttons, and the JS-rendered rows (upload queue, transcode/transfer history) — is an inline SVG from the shared `frontend/src/shared/icons.js` registry rather than an emoji; the markup lives in `templates/partials/tasks_modal.html` (Media Library) and `templates/partials/sorter_tasks_modal.html` (Video Sorter), both pulled in with `{{include}}`, with a vitest asserting every `data-lb-icon` placeholder resolves and that no emoji remain.
   - Downloads Page (`/downloads`, `templates/downloads.html`, `frontend/src/downloads/index.js`): the first-class desktop URL/playlist downloader, reached from a **Downloads** nav tab in the Library and Sorter desktop headers and from a **Downloads** link in the mobile sidebar drawer. Left panel queues **multiple URLs at once** (newline/whitespace-split, blanks & duplicates dropped, each http(s) URL validated client-side and queued individually, with the count of skipped/failed lines reported); the target folder is chosen from **recent-target chips** (last folders actually downloaded into, persisted in `localStorage` `lib_dl_recent_targets`, cap 8 — `frontend/src/library/recent_targets.js`) or the shared **folder chooser** (`frontend/src/shared/folder_picker.js`: drills the library via `/api/backup/folders`, ZIP archives filtered out, and is the same sheet the People page scans a folder with). It also shows a **downloader availability line** (yt-dlp / gallery-dl detected, from the `tools` key on the status payload). Right panel is the live jobs list: active + queued pinned on top, then **paginated finished history** (`/api/backup/upload/url/history`, newest first, "Load older downloads" paging through the worker's 500-row history) with per-job **cancel**, **retry** (failed/cancelled only — clones the job) and a **Clear history** button that empties finished jobs (active/queued untouched). Polls `/api/backup/upload/url/status` every second; rows reuse the shared `frontend/src/library/url_downloads.js` renderer (`retryable: true`). Finished jobs are folded in live from the status payload's history tail (and the paginated history endpoint is refreshed whenever the live active+queued count drops), so a job that completes while the page is open never disappears before the next explicit page fetch. The page includes the shared theme customizer (`theme_modal.html` + `window.THEME_MANAGER`) so accent/contrast settings stay consistent, opened from either header trigger — both the mobile and desktop buttons carry `data-theme-modal-btn` (and the mobile one owns the `themeModalBtnMobile` id), so the duplicate-id collision that left the desktop button dead is gone and the `T` shortcut clicks whichever trigger is visible. Mobile layout: the fixed mobile top bar (`md:hidden fixed top-0`, `3rem` tall plus `env(safe-area-inset-top)`) has a matching `md:hidden` spacer div before the scroll stage, so the page intro and first panel start below the bar instead of being permanently covered by it. The old desktop Library behaviors — the Upload dropdown's "🔗 URL" dialog (removed) and the Background Tasks modal's full URL Downloads section (reduced to a compact live-only card) — now funnel to this page: the dropdown item navigates to `/downloads?folder=<current>` preserving the browsed folder as the target.
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
   - Mobile single-line responsive header (`#mobileHeader` on screens `< md` / `< 768px`): Compact bar featuring the drawer menu `[ ☰ ]`, subfolder back arrow `[ ‹ ]`, folder title `[ 📁 Title ▾ ]` with one-tap ancestor breadcrumb popover sheet, a one-tap **Library Analytics** link (`#mobileAnalyticsBtn`, a hydrated `gauge` icon navigating to `/analytics` — the phone's direct path to the dashboard), a one-tap Background Tasks button (`#mobileTasksBtn`, an icon that swaps to a spinner while work is in flight with a live active+queued item-count badge that caps at `99+`) opening the shared tasks modal directly, and unified `[ 🎛️ Controls ]` trigger with an ambient pulse badge for active filters only (background activity moved to the tasks button). Desktop multi-row headers are hidden on mobile to eliminate clutter.
   - Native scroll-aware auto-hide: scrolling down translates `#mobileHeader` offscreen (`-translate-y-full`) to devote 100% of the viewport to media; scrolling up (> 8px) or reaching stage top (`scrollTop <= 20`) smoothly slides the header down into view (`translate-y-0`).
   - 5-tab slide-up bottom sheet control modal (`#mobileControlsSheet`): a fixed 5-equal-column **icon-only** tab strip (search, palette, folder, link, settings — rendered from the shared inline SVG set, full names kept as `aria-label`/`title`) that always fits the phone width and never scrolls sideways; the sheet header and the 'Folder Actions' tiles (upload files/folder, URL, new folder, refresh, cache thumbs, animated previews, convert non-MP4s, optimise images, compress/unpack ZIP), 'Downloads' actions and the breadcrumb sheet header use the same icons. Every icon lives in the template as an emoji fallback inside a `[data-lb-icon]` placeholder that `hydrateIcons()` swaps for SVG at init, so the sheet still renders if the bundle fails to load. In addition it integrates 'Search & Filter' (filename search, scope toggle, media filter pills, sort select), 'Display & Theme' (XS-XL tile scale, accent colors, OLED black mode, double-tap seek toggle), 'Folder Actions' (upload files/folders, new folder, refresh, cache thumbs, WebP, ZIP tools), 'Downloads' (an inline URL download form — URL input, read-only current-folder target, yt-dlp/gallery-dl explanation and ZIP warning — above the live job list of active, queued, and recent finished downloads with progress/speed/ETA/downloader and per-job cancel; the tab carries an active+queued count badge), and 'System Status' (duplicates count, index status, a **Library Analytics** card linking to `/analytics`, a **Keyboard Shortcuts** card opening the `?` reference sheet, video sorter link — the former Background Tasks "Open" card was removed because the header's one-tap `#mobileTasksBtn` now reaches the same modal directly). The Actions tab's "URL" tile is a shortcut that switches the sheet to the Downloads tab; the sheet stays open after starting a download so the new job appears immediately. This tab is the phone's quick path and is unchanged by the `/downloads` page work: the desktop now uses the dedicated `templates/downloads.html` page, and the Background Tasks modal keeps only a compact live-only URL Downloads card (`#urlDownloadsSection` in `templates/partials/tasks_modal.html`) with a link to the full page (the old `#urlDownloadsSection` media-query rule in `static/styles.css` was removed with the old section).
   - Selection toolbar (`#selectionToolbar`): responsive placement that sits pinned below the desktop sticky toolbar on desktop and floats as an ergonomic bottom bar (`fixed left-3 right-3`) on mobile, with browser chrome clearance and iOS safe-area clearance (`bottom: max(4.5rem, calc(var(--sab, env(safe-area-inset-bottom, 0px)) + 3.75rem))` to float cleanly above mobile bottom address bars and home indicators).
   - Full-Height Mobile Sheets (`.ml-sheet` / `.ml-sheet-card` / `.ml-sheet-grabber`): On phones (`< 768px`), the library's tall panels — Background Tasks (`#transferDropdown`/`#tasksModalCard`), Mobile Controls sheet (`#mobileControlsSheet`), Theme customizer, Duplicates + Ignored Duplicates, Batch ZIP / Batch Unpack / Empty Folders, Media Info, and the Video Thumbnail Chooser — become bottom-anchored full-viewport-height sheets instead of centered dialogs. A shared `@media (max-width: 767px)` block in `static/styles.css` overrides the wrapper to `align-items/justify-content: flex-end` with zero padding and sets the card to `height: 100dvh` with square bottom corners and a `mlSheetUp` slide-in animation (fill-mode `backwards` so inline drag transforms win). Inside a sheet, capped lists flow in the single sheet scroll region: nested queue lists get `.ml-flatten` (cap + overflow removed) and the tasks lists (queued transfers, library ops, transcodes, animated previews, transcode history) render "Show all N ↓ / Show fewer ↑" expander buttons (`renderListExpander` + `expandedSheetLists` set in `frontend/src/library/index.js`) instead of passive "+N more" footers; batch candidate lists get `.ml-unbound` (cap removed so `flex-1` fills the sheet). Swipe-to-dismiss (`initSheetDrag`): pointer-driven, drags the card via inline `translateY`, starting only from the grabber handle (`[data-sheet-grabber]`) or the header background (never on buttons/inputs/sliders — protecting the thumbnail-chooser scrubber), dismisses past 120px with a 240ms slide-down, otherwise snaps back; disabled under `prefers-reduced-motion`. Short confirm dialogs (rename, new folder, delete, move, conflict, upload review) and the sorter UI keep their centered shapes untouched, and desktop (≥ 768px) renders exactly as before.
9. **UI Stacking Context, Solid Header & Viewport Scroll Lockdown**:
   - Navigation header (`<header class="bg-slate-950 border-b border-slate-800/80 sticky top-0 z-50 ... shrink-0">`) is pinned at `z-50` with an explicit CSS declaration (`header.sticky { position: sticky !important; top: 0 !important; z-index: 50 !important; }`), rendered with a solid opaque dark surface (`s[950]` / `#000000` in OLED) so content never bleeds through.
   - Dual-pane Media Library layout locks document scrolling to prevent body overflow: `body` uses `h-screen h-[100dvh] overflow-hidden flex flex-col`, header takes `flex-none`, sidebar is `md:h-full`, and main stage (`<main id="mainStage">`) is `h-full min-h-0 flex-1 overflow-y-auto relative`.
   - Pinned Library Top Bar (`#libraryStickyHeader`): Sticky header pinned at `z-30` directly below the navigation bar containing breadcrumbs, folder/upload actions, search scope, filename search, sort, thumbnail size presets, filter pills, ZIP banner, and selection toolbar with solid theme-reactive surface (`s[950]` / `#000000` in OLED) ensuring media tiles scroll cleanly underneath without margin gaps.
   - Isolated Scrollable Gallery Area (`#galleryContentArea`): Holds subfolder cover cards, empty state, justified media grid, and infinite scroll sentinel with consistent responsive stage padding (`p-2.5 sm:p-4 md:p-6 pb-28 sm:pb-32 md:pb-6`). On mobile (`< md`), applies `padding-bottom: max(8.5rem, calc(var(--sab, env(safe-area-inset-bottom, 0px)) + 7.5rem))` (and `max(4.5rem, calc(var(--sab, 0px) + 3.5rem))` in landscape) so when scrolling to the end of the gallery, the bottom-most media tiles, duration pills, metadata footers, and checkboxes are never covered by mobile browser bottom address bars, bottom navigation toolbars, or floating action bars. Folder navigation resets `mainStage.scrollTop = 0` automatically.
   - Floating Back to Top Button (`#scrollToTopBtn`): Spring-animated, theme-accented floating button (`fixed bottom-6 right-6 z-30` on desktop, lifted to `bottom: max(5.5rem, calc(var(--sab, env(safe-area-inset-bottom, 0px)) + 4.5rem))` on mobile `< md`) that appears when `mainStage.scrollTop > 350` and smoothly scrolls the gallery back to top (`scrollTop = 0`). Respects safe-area insets (`--sab`, `--sar`), sits cleanly above mobile browser address bars, and sits beneath mobile overlay backdrops.
   - Mobile Sidebar Drawer & Backdrop Isolation: On mobile (`< md`), `#sidebarBackdrop` sits at `z-40` (`fixed inset-0 bg-slate-950/80 backdrop-blur-sm`) and `#sidebarDrawer` sits at `z-[45]` (`fixed top-0 bottom-0 left-0 w-72 sm:w-80`) with solid theme-reactive surface (`bg-slate-900` / `#050506` in OLED mode) and mobile close button (`#closeSidebarBtn`), sliding over the blurred background so the drawer itself is never blurred or obscured. On phones the drawer is **full-bleed**: the `@media (max-width: 767px)` block in `static/styles.css` pins it to `top: 0` / `bottom: 0` with `height: 100dvh` (covering the mobile top bar, which is behind the backdrop and unusable while open) instead of starting 48px/52px down at the top bar's own height — that hardcoded offset left a header-height band (plus the safe-area inset) uncovered above the drawer, and its old `4.5rem` bottom padding left the footer stranded high above the bottom edge. Padding is now safe-area aware (`padding-top: calc(0.875rem + var(--sat))` so the "Categories"/close row clears a cutout the page renders under with `viewport-fit=cover`, `padding-bottom: max(1rem, var(--sab) + 0.75rem)`), so the panel reaches both screen edges and its footer sits at the bottom.
   - Modal scroll isolation: modals apply `.modal-scroll-lock` (`overflow: hidden !important`) to `document.body` and set `overscroll-behavior: contain !important` on scrollable bodies, preventing theme/task modal interactions from causing background overscroll.
   - Transfer dropdowns sit at `z-[70]` with backdrops at `z-[60]`. Full-page modals sit at `z-[80]` and high-priority action dialogs at `z-[1200]`.
10. **Library Analytics Dashboard (`/analytics`, `templates/analytics.html`, `frontend/src/analytics/index.js`)**:
   - A read-only, index-only overview of the whole collection, reached on desktop from an **Analytics** nav tab in the Library, Sorter and Downloads headers, and on phones from a one-tap **📊 button in the Library's mobile top bar** (`#mobileAnalyticsBtn`), an **Analytics** shortcut in the mobile sidebar drawer footer (next to Downloads), and a **Library Analytics** card in the mobile Controls sheet's *System Status* tab. Every figure is computed from SQLite alone — no `os.walk`, no `os.scandir`, no remote `os.stat`, no media decode — so the page stays instant over FUSE; `tests/test_analytics_api.py` asserts the no-filesystem-touch contract by poisoning `os.walk`/`os.scandir` during the request.
   - **One endpoint, revision-keyed memo**: `GET /api/analytics/overview` (filters `folder_limit` 1–100 default 15, `file_limit` 1–100 default 25, `refresh` bool) returns `{generated_at, library_root, limits, totals, by_type, by_extension, folders{root_bytes, root_files, top, largest}, largest_files, quality, reclaimable, health}`. The payload is memoized for `INDEX_COUNT_CACHE_TTL_S` (30s) keyed on `indexer_state["revision"]` — the same invalidation key the unified status API's count cache uses — so a crawl or forced prune can never serve a stale snapshot; `?refresh=1` drops the memo for the page's **Refresh** button (library ops such as moves/deletes do not bump `revision`).
   - **Recursive folder rollup** (`services/analytics_service.py`): every indexed folder is linked to its nearest included ancestor (so an unindexed intermediate directory cannot orphan a subtree), file totals per `folder_path` are attached to their node (orphan member folders such as ZIP-internal paths roll up into the archive node), and subtree sizes/counts accumulate deepest-first. The library root is the configured `destination_dir` when it is indexed, else the shallowest indexed folder; folders outside it are dropped from the ranking. `.media_library_backup` is excluded from every ranking. `folders.largest` is a **partition** — a folder appears only when none of its ancestors is already listed — so the share percentages cannot double-count; `folders.top` is the treemap's top-level data with one level of children (capped at 12 per node), and everything past the top 40 is folded into a `is_group` "Smaller folders (N)" node so the treemap stays complete in area but bounded in payload.
   - **Cards**: KPI strip (total bytes, files, videos, images, folders, total runtime); *Storage by Folder* ECharts **treemap** (click a block to open that folder in the Library via `/?folder=<path>`, aggregate node not clickable; the treemap is **roamable** — every top-level folder is in the chart (nothing folded away) and `roam: true` with `scaleLimit` 0.35–12 lets a phone pinch or a desktop scroll to zoom and drag to pan, while `visibleMin` 40 / `childrenVisibleMin` 12000 hide what cannot be read at the zoomed-out size (each zoom re-lays out, so detail and its labels come back as a block grows); a top-right button stack (zoom in / out / reset, wired to `treemapRender` with `rootRect` maths that keeps the point under the box centre fixed) is the mouse-free path and reset fits the whole library back into the card, a stage of `h-[520px] sm:h-[440px] lg:h-[360px]` gives the roaming room, and a tap that moved more than 8px (a pan) does not navigate to the folder under the pointer) plus a ranked **Largest Folders** table with share bars and `Open →` links; *Media Mix* bytes **donut** + count/bytes legend + top-extensions **bar** chart; *Reclaimable Space* (duplicate size-match candidate groups/files/upper-bound bytes, WebP saved + loose images still to convert, ZIP-eligible leaf image folders, indexed empty leaf folders) each linking into the Library panel that does the work; *Largest Files* table (top N, resolution/duration/codec) where a row opens an in-page **preview modal** with byte-range-streamed media, size/resolution/codec metadata and an "Open folder in Library →" link; *Quality & Runtime* resolution **histogram** (videos bucketed by longest edge — 4K/1440p/1080p/720p/SD/n-a, so a portrait 2160x3840 clip reads as 4K) with probed/high-fps/runtime notes; and *Health & Housekeeping* pills (index fresh/stale/crawling from `indexer_state`, missing thumbnails, probed, visual hashes, missing animated previews, needing transcoding, empty folders, ignored duplicate pairs) that turn amber when work is outstanding.
   - **Charts** are a tree-shaken **ECharts 6** build (a build-time devDependency bundled into `analytics.js` like `lucide-static`, so runtime dependencies stay at zero: `echarts/core` + Bar/Pie/Treemap charts + Grid/Tooltip components + `LegacyGridContainLabel` (required for `grid.containLabel` on ECharts 6) + CanvasRenderer). Chrome colours and the block palette derive from the live theme tokens (`THEME_MANAGER.ACCENTS`/`BASES`) and are rebuilt on the `themechange` event, so accent and OLED/surface switches apply immediately; the stage is revealed *before* charts mount (ECharts measures its container on init) and a `visibilitychange`/debounced `resize` listener re-measures (the treemap's roam state survives, since the option no longer depends on the viewport). The dashboard keeps its own theme-customizer wiring (mirroring `/downloads`) rather than importing the Library's `theme.js`, which would pull in the whole Library bundle.
   - Read-only by design: no mutation endpoints, so duplicate resolution, WebP, ZIP and empty-folder cleanup stay in the Library's existing panels. The page is a single entrypoint (`static/dist/analytics.js?v=4`) with its own version string in `templates/analytics.html`, plus `frontend/tests/analytics_smoke.test.js` (ECharts mocked) asserting the KPI/table/pill rendering, HTML escaping of filenames, the treemap's clickable-vs-aggregate data, that every top-level folder stays in the chart with roam/scale limits set, the zoom-in / zoom-out / reset button maths (centre fixed, limits held), the pan-does-not-navigate guard, the `refresh=1` reload, the preview modal, and the error state.


11. **Face Recognition & People (`/people`, `/people/review`, `templates/people.html`, `templates/people_review.html`, `frontend/src/people/index.js`, `frontend/src/people/review.js`)** — opt-in (`FACE_RECOGNITION_ENABLED`, default `false`), off by default and entirely local:
   - **Scanning**: `FaceWorker` sweeps the index and detects faces in **loose images** (read from the source once and downsized to `FACE_SCAN_WIDTH`, falling back to the cached 360px thumbnail) and **videos** (decoded **once** each, sampled by FFmpeg itself at `FACE_VIDEO_SAMPLE_INTERVAL` — 2 s by default — up to a `FACE_VIDEO_MAX_FRAMES` frame budget, with a person recorded **once per appearance** rather than once per frame). The sweep is **parallel**: `scan_concurrency()` derives two decode streams and a detection pool from the core count (one core kept for the server, one per stream for FFmpeg, the rest detect), so a clip's frames are detected while the next ones are still being decoded; detections are applied to the track builder **in frame order**, so a serial and a parallel scan store identical faces, and each detection thread leases its own engine because YuNet keeps the input size on the detector. The face card reports a realtime factor (`2.4x realtime`, footage per second of wall clock) and the footage-based ETA so the gain is visible. Both sources are detected at 960px rather than the 480px stills the chooser uses, because the recogniser aligns every crop to 112×112: a sharper face is what keeps one person in one cluster instead of spawning a new one per frame. Images inside ZIP archives are skipped entirely (detecting on them would force an archive extraction). Each file is scanned at most once per `mtime`/`size` (`face_scans`), so a re-run resumes instead of restarting, and a changed file has its detections replaced.
   - **Engine**: OpenCV only — `cv2.FaceDetectorYN` (YuNet) + `cv2.FaceRecognizerSF` (SFace, 128-d embeddings), imported lazily, so there is no torch/onnxruntime dependency. The two ONNX files are downloaded once into `FACE_MODEL_DIR` (default `DATA_DIR/models`) and accepted only when they match a pinned SHA-256 + size; a failed download retries every ten minutes and the card explains why. `opencv-contrib-python-headless` is in `requirements.txt` but a missing install simply reports the engine as unavailable.
   - **Clustering**: a new face joins the nearest person within `FACE_MATCH_THRESHOLD` cosine distance (default 0.36) and otherwise starts a new unnamed cluster; centroids are cached in-process (one pass over the embeddings, keyed on the database path + a revision bumped by every external mutation) and updated incrementally as the worker assigns. `record_scan` matches a file's faces through a per-file `_CentroidMatcher`, which builds the `(people x 128)` matrix **once per file** and keeps clusters created during that file in a short side list — rebuilding it per face (the old `assign_person` call) cost ~30 ms a face at 16k clusters, which held SQLite's single write lock for seconds inside the transaction and starved every other writer, so a "Scan library" request failed with `database is locked` and looked like a dead button. "Re-group all" re-clusters every face with a chunked agglomerative pass (scipy, bounded memory) and re-attaches existing names by nearest centroid, so naming work is never lost.
   - **Curation**: rename, merge clusters, split faces out, dismiss faces or a whole cluster as "not a person" (`dismissed_faces` — a persisted rejection, so a re-scan never re-adds them), re-scan one person's files, and a Strict/Balanced/Loose strictness control whose choice is stored in `db_meta` and therefore applies to the next scan without a restart (it wins over the env default). The toolbar's **Scan library** queues everything, **Scan folder** takes a path typed into the box (or handed over by a `?folder=` deep link) and **Browse…** opens the shared folder chooser (`frontend/src/shared/folder_picker.js`, index-only via `/api/backup/folders`, ZIP archives filtered out) which scans the chosen folder in one tap. The toolbar's **Auto-match** button (`POST /api/people/auto-match`) clears the part of a backlog that needs no judgement: a face joins the nearest person within the threshold as it is scanned, but only the people that existed *then*, so a person named afterwards leaves unnamed clusters a fresh assignment would have merged. The pass re-asks that for every unnamed cluster at once against named people only, and the button always runs it as a **dry run** first — the confirm shows how many clusters/faces would move, the biggest gains per person, what would be left unnamed and the threshold used — with Split as the undo. The merge picker is a mobile full-height sheet (`.ml-sheet` / `.ml-sheet-card`, i.e. `100dvh`, bottom-anchored, grabber + swipe-to-dismiss via a local `initMergeSheetDrag`) so its header and its Cancel/Merge footer cannot fall outside a phone's visible viewport. It lists **unnamed clusters only** (`GET /api/people?named=false&near=<this cluster>`, its own paged list with a "Show more" row — not the grid's current page), ranked so the clusters filed in the same folders as this one come first (media is organised per folder, so the true duplicate is nearly always filed next to it) and badged with the shared folder's name; its filter box is a **folder path** search (`GET /api/people?…&folder=…`, debounced, server-side so it reaches every unnamed cluster rather than the loaded page, with the matched folder labelled on each row), shows three of each candidate's face crops instead of one cover so the right cluster can be recognised, and states the direction of the merge ("their faces move into <target cluster>"), since the picked clusters' faces are folded into the cluster the sheet was opened from.
   - **Review queue (`/people/review`)**: the way through a backlog of thousands of unnamed clusters, one at a time, biggest first (the toolbar's **Auto-match** pass on `/people` first clears the clusters that are already a named person's lookalike, so the queue starts shorter). The queue holds no state: `GET /api/people?named=false&limit=1&offset=0&samples=8` is the next cluster (the server orders by face count), and each action removes the cluster from that set, so the following request is simply "the next one" — a reload lands where a fresh queue would. The card shows the cover, up to 8 of the cluster's crops (`samples`), the face/file counts and `folder_names`, and offers three ways out. **Match** is offered as **name pills**: `GET /api/people?named=true&near=<this cluster>&limit=6&samples=1` ranks the people the user has already named by the folders they share with this cluster (media is filed per folder, so the true duplicate is usually first) and the six best guesses are rendered as name-only pills — one tap calls `POST /api/people/merge` with that person as the target straight away, no confirm, and each pill carries the digit (`1`–`9`) that does the same from the keyboard; a **More…** pill (with the count of the rest, shown whenever more people are named than fit) opens the picker sheet — the merge sheet's mirror image, with every named person, their crops, the folder-path search and the same 1–9 rows. Unnamed clusters are never offered as a target (folding into one would just move the problem), and the cluster's own page on `/people` is where a wrong match is unpicked with Split / Not a person. **Name** (`POST /api/people/rename`) turns the cluster into a person the next ones can be matched against, and **Ignore** (`POST /api/people/dismiss` with `person_id`) records the cluster's faces as "not a person" so no future scan re-adds them — it fires straight away with no confirm and no toast, on the user's call, even though a dismissal cannot be undone (there is no un-dismiss endpoint). Keys drive it (M / N / Enter / X, `1`–`9` for the numbered pill or list row, `/` for the sheet's folder box, `?` for the reference sheet rendered from the one `REVIEW_SHORTCUTS` table in `frontend/src/people/review.js`, `Esc` to close); a stale count (someone renamed or merged elsewhere) is explained rather than shown as an empty picker, and a failed pill fetch says to press M instead of showing a dead row. The page raises **no toasts at all** — the card advancing is the feedback, and a failed action uses the inline error box with its "Try again" button. The card is laid out to **fill one phone screen with no page scrolling**: the stage is one screen tall, the card fills it, and the crop strip is what takes the slack (`grid-auto-rows: minmax(0, 1fr)`, three crops across, cells growing from ~57px to ~150px tall as the viewport grows), with the actions row pushed to the thumb zone. That chain needs the panel to stretch its column (`items-stretch`, not `items-start`, or the column sizes to its content and pushes the card past the fold). The biggest-face cover is desktop-only (the crop strip leads with that same face), the crops are square on desktop and the counter chips and intro are desktop-only (the header subtitle carries "N unmatched · M named · D dismissed"). **A phone keyboard cannot scroll the page**: iOS does not shrink the layout viewport for the keyboard, so a card sized to the layout viewport would sit behind it and the browser would scroll the stage to reach the name box and leave it scrolled (card half off the top, empty space below). The stage therefore takes its height from the *visual* viewport (`syncVisualViewport()` sets `--pr-vv-height`, `visualViewport.resize` re-runs it, `window.resize`/`orientationchange` too), which keeps the whole card inside what is actually visible; a short visual viewport (keyboard up) also adds `pr-kb` to `<html>`, which hides the face strip while a name is being typed, and `resetStageScroll()` (on `focusout` and after the keyboard settles) undoes any scroll a browser performed anyway. Nothing follows the Ignore button — no hint line, no footer, no keyboard-reference button, so `?` is the only path to the key reference. The key reference sheet itself stays (both sheets are phone full-height sheets with the same grabber + swipe-to-dismiss). Reads stay available when `FACE_RECOGNITION_ENABLED` is off, so an existing index can still be curated.
   - **Face crops**: one ~160px square JPEG per face under `FACE_CROP_DIR` (default `THUMBNAIL_CACHE_DIR/faces`), so covers and review grids need no source reads. Crops are orphan-swept with the prune/deep clean and LRU-trimmed to `FACE_CROP_MAX_SIZE_GB` (default 2 GB), evicted faces being re-queued for re-scan.
   - **Person pages**: a video is listed once (not once per sampled frame) with its hit timestamps, and each moment chip opens the lightbox at that second; rows are shaped like Library browse entries so the page reuses the same thumbnails, media URLs and Fancybox gallery. The page scrolls inside its own `<main>` stage (the body is a viewport-locked column), so switching between the cluster grid and a person resets that stage's `scrollTop` — `window.scrollTo` is a no-op here and would leave the new view scrolled down behind the mobile top bar. Crop, cover and thumbnail URLs go into `src` attributes HTML-escaped, never percent-encoded, and the status chips shrink/wrap while the progress filename truncates so a long path cannot make the phone layout scroll sideways.
   - **Search by person**: the Library search box understands `person:anna` / `person:"anna smith"` (composing with the existing filename and relative-folder words), reports an unknown name instead of looking like an empty search, and the person page deep-links into it as `/?q=person:Anna`.
   - **Storage & privacy**: embeddings (128 float32), boxes, crops and names live in the local SQLite DB and thumbnail cache only. Nothing is uploaded, and no analytics/cloud service is involved. Moves, renames and deletes rewrite the `faces`/`face_scans`/`dismissed_faces` rows through the same index hooks favourites use, so a person's media list survives sorting the library; a backup restore drops the centroid cache rather than serving the previous index's clusters.


### Tech Stack
- **Language & Runtime**: Python 3.10+ (PEP 604 union syntax in annotations is evaluated at runtime, so 3.9 cannot import the package; tested on Python 3.13)
- **Web Framework**: FastAPI (v0.110+) with Uvicorn ASGI server and Starlette
- **Concurrency & Threads**: AnyIO (expanded threadpool tokens to 100), asyncio, Python `threading`
- **Database**: SQLite 3 with Write-Ahead Logging (`WAL`), `synchronous=NORMAL`, memory temp store, mmap, and optimized index structures
- **Media Processing**: FFmpeg & FFprobe (CLI subprocesses), Pillow (PIL) with `pillow-heif` (HEIC/HEIF/DNG native decoding), ImageHash, NumPy, SciPy
- **Face Recognition (opt-in)**: OpenCV (`opencv-contrib-python-headless`) — `FaceDetectorYN` (YuNet ONNX detector) + `FaceRecognizerSF` (SFace 128-d embeddings) — with scipy agglomerative clustering. Imported lazily by `services/face_engine.py`; no torch and no onnxruntime anywhere in the tree. The two ONNX weights are downloaded once into `DATA_DIR/models` and verified by SHA-256.
- **URL Playlist Downloading**: yt-dlp (run as an isolated subprocess: `python -m yt_dlp`, never imported in-process) with an automatic **gallery-dl** fallback (`python -m gallery_dl`) for sites yt-dlp does not support (image boards / file hosts such as bunkr album links)
- **Frontend**: Vanilla JavaScript (ES6+), raw HTML templates (served via `HTMLResponse`, no Jinja2 dependency), CSS3, native HTML5 Video. Icons are inline SVGs inlined at build time from `lucide-static` (ISC, a devDependency) via Vite `?raw` imports into the shared registry `frontend/src/shared/icons.js` (`icon(name, class)` for JS-rendered markup, `hydrateIcons()` for static `[data-lb-icon]` placeholders) — no icon font, no runtime icon library, and no CDN request; only the icons actually imported are bundled, and both entrypoints (`library.js`, `app.js`) share the one registry. The only other build-time dependency is **ECharts 6** (a devDependency, tree-shaken into the `analytics.js` bundle only: Bar/Pie/Treemap charts, Grid/Tooltip components, `LegacyGridContainLabel`, canvas renderer) — the `/analytics` dashboard's charts, still with no runtime dependency and no CDN request on any page.
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
6. **Version or Release Changes**: `media_library/__init__.py`'s `__version__` is the **only** place the version is written down. Bump it there and nowhere else (the FastAPI app metadata, the `/api/system/status` payload, the served-page `{{VERSION}}` token and the backup snapshot metadata all read it), then update `CHANGELOG.md` and the `**Current release: vX.Y.Z**` line in `README.md`. `tests/test_version.py` fails if any of those drift, and `tests/test_repo_hygiene.py` fails if personal data, credentials or local state can reach a commit.

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
- **Never run a mutating `db_conn()` block on the event loop.** SQLite admits one writer at a time and a connection waits for that write lock for its whole `timeout` (30s) before raising `database is locked`, so awaiting such a write inside a coroutine stalls the *entire* server for the duration — the unit stays `active` and bound to its port while answering nothing, and nothing appears in the log because the loop never returns to the socket. Worker loops and request handlers must hand the transaction to a thread: `await run_db_write(fn, *args)` (`media_library/db/connection.py`) or an equivalent `await asyncio.to_thread(fn, …)` with the transaction in a module-level sync function. Reads are fine on the loop — WAL lets them run alongside a writer. `tests/test_no_blocking_db_writes.py` fails the build when a mutating `with db_conn() as conn, conn:` reappears directly in a coroutine.
- **Never hold a write transaction across remote I/O.** A `Path.exists()`/`os.stat()` on the library mount inside a `with db_conn() as conn, conn:` block spends its FUSE round trip holding SQLite's single write lock, which starves every other writer for that long (see Rule 2). Resolve such probes *before* opening the transaction.
- Schema migrations must be idempotent: modify `init_backup_db()` in `media_library/db/schema.py` using `ALTER TABLE ... ADD COLUMN ...` wrapped in `try...except sqlite3.OperationalError: pass`.
- **Never import a `media_library.services.*` module at import time from `media_library/db/*`.** `services/__init__.py` eagerly imports the dedup facades, which import back from `media_library.db.indexer`; a module-level service import inside the DB layer therefore creates an import cycle that makes `import media_library.db.indexer` an `ImportError` as soon as the facade's import order changes (this broke once when a naive import sort moved `indexer_crawl` above `indexer_files`). Service helpers needed by the DB layer (`db.indexer_prune`'s animated-cache cleanup, the dedup cache invalidation) are imported **lazily inside the functions** that use them.

### Rule 4: Test Suite Verification
Before completing any task, execute the backend and frontend test suites:
```bash
./venv/bin/pytest
npm test
```
All existing tests must pass, and new features must include unit or integration tests under `tests/` or `frontend/tests/`.

The tree is kept lint-clean **and** format-clean, and the same three gates run on every push and pull request in `.github/workflows/ci.yml` (job `lint`: `ruff check` + `ruff format --check`; job `backend`: `pytest`; job `frontend`: `npm ci` + `npm test` + `npm run build`). Run them locally before pushing so CI stays green:
```bash
./venv/bin/ruff check . && ./venv/bin/ruff format --check .   # lint + formatting gate
```
Note: adding or changing a file under `.github/workflows/` requires the pushing credential to carry the `workflow` scope (`gh auth refresh -s workflow`); without it GitHub rejects the push with "refusing to allow an OAuth App to create or update workflow".

### Rule 5: Keep Live Server Running Latest Code
Because HTML templates are cached in memory for the process lifetime (`media_library.core.templating.load_template`) and Python modules require process reloading, **always restart the systemd service** whenever making code, template, or asset changes so the live server serves the latest code:
```bash
systemctl restart media-library && systemctl is-active media-library
```
Confirm the service is active and responding after the restart.

### Rule 6: Frontend Cache Busting
When modifying frontend JavaScript assets (`frontend/src/**/*.js`), remember that the application uses hardcoded cache buster strings in the HTML templates to prevent browsers from using stale client code. After running `npm run build`, you **must** manually increment the version string for the modified entrypoint:
- If you modify Sorter JS (`frontend/src/app/*`), increment `src="/static/dist/app.js?v=XX"` in `templates/index.html`. The sorter UI is split into feature modules under `frontend/src/app/` (entrypoint `index.js` plus `elements`, `state`, `config`, `folders`, `video`, `duplicates`, `moves`, `polling`, `empty_folders`, `browser`, `modals`, `events`); editing any of them still needs only the single `app.js` bump.
- If you modify Library JS (`frontend/src/library/*`), increment `src="/static/dist/library.js?v=XX"` in `templates/library.html`. The Library UI is split into feature modules under `frontend/src/library/` (entrypoint `index.js` plus `layout`, `folders`, `search`, `gallery`, `lightbox`, `thumbnail_chooser`, `management`, `duplicates`, `tasks`, `workers`, `zip`, `animations`, `theme`, `sheet`, `uploads`); editing any of them still needs only the single `library.js` bump.
- If you modify Downloads JS (`frontend/src/downloads/*`), increment `src="/static/dist/downloads.js?v=XX"` in `templates/downloads.html`. (Modules under `frontend/src/library/` are shared: `url_downloads.js` and `recent_targets.js` feed both `library.js` and `downloads.js`, and `frontend/src/shared/tasks_badge.js` plus `frontend/src/shared/icons.js` feed both `library.js` and `app.js` — touching either shared module means bumping **both** entrypoint versions. `frontend/src/shared/folder_picker.js` is imported by `people.js` and `downloads.js`, so touching it means bumping both of those. `templates/partials/tasks_modal.html` / `sorter_tasks_modal.html` edits land in `library.js` / `app.js` respectively.)
- If you modify Analytics JS (`frontend/src/analytics/*`), increment `src="/static/dist/analytics.js?v=XX"` in `templates/analytics.html`. It imports the shared `frontend/src/library/utils.js` (`showToast`/`escapeHtml`/formatting), so a change to that helper affects `library.js`, `downloads.js` and `analytics.js` alike — bump every entrypoint whose bundle you rebuilt.
- If you modify People JS (`frontend/src/people/*`), increment `src="/static/dist/people.js?v=XX"` in `templates/people.html`, and `src="/static/dist/people_review.js?v=XX"` in `templates/people_review.html` for the review queue (`review.js`; `index.js` is the `/people` page). Both import the shared `frontend/src/library/utils.js` and `frontend/src/shared/icons.js`, so touching either means bumping **every** entrypoint whose bundle contains it (`library.js`, `app.js`, `people.js`, `people_review.js`).

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
├── ruff.toml                        # Ruff lint configuration (with the rules intentionally excluded, and why)
├── .github/workflows/ci.yml         # CI gates: ruff lint + format check, pytest, vitest + Vite build
├── config.json                      # Persistent runtime user preferences (recent destinations, source dir)
├── config.example.json              # Example configuration file
├── .env.example                     # Reference environment variables
├── .env                             # Local environment configuration (git-ignored)
├── media-library.service.example    # Systemd service unit template
├── LICENSE                          # MIT License
├── README.md                        # User-facing project documentation
├── CHANGELOG.md                     # Release history (Keep a Changelog / SemVer)
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
│   │   ├── build_info.py            # Best-effort git commit SHA for the /api/health probe
│   │   ├── constants.py             # File extension sets, default folder candidates
│   │   ├── http_stream.py           # HTTP byte-range and media streaming response helpers
│   │   ├── state.py                 # In-memory state, AppState container, stat cache
│   │   └── templating.py            # Cached raw HTML template loader & injection helpers
│   │
│   ├── db/                          # Database connection, schema, and filesystem crawler
│   │   ├── __init__.py
│   │   ├── connection.py            # SQLite connection factory with WAL PRAGMAs + run_db_write (off-loop write helper)
│   │   ├── schema.py                # Schema tables, indexes, idempotent migrations
│   │   ├── indexer_state.py         # Shared indexer_state dict + asyncio lock
│   │   ├── indexer_scan.py          # Folder/entry scanning, ZIP archives, index_folder
│   │   ├── indexer_files.py         # Per-file index mutations (add/update/move/remove)
│   │   ├── indexer_prune.py         # Pruning, health reconciliation, deep clean
│   │   ├── indexer_crawl.py         # Background crawler loop
│   │   └── indexer.py               # Indexer facade: re-exports the modules above
│   │
│   ├── routers/                     # FastAPI route modules
│   │   ├── __init__.py              # Export of all_routers list
│   │   ├── views.py                 # HTML page endpoints (/, /sorter, /downloads, /analytics, /people, /library)
│   │   ├── auth.py                  # Shared-password login page/routes and auth middleware
│   │   ├── analytics.py             # Library analytics overview API (index-only, read-only)
│   │   ├── library_browse.py        # Library browsing, folder tree, search, index controls
│   │   ├── library_media.py         # Library file/thumbnail byte-range streaming
│   │   ├── library_admin.py         # Library rename/delete + thumbnail controls
│   │   ├── library_manage.py        # Library page file/folder management (move, folders, undo)
│   │   ├── library_upload.py        # Web browser chunked file and folder uploading
│   │   ├── people.py                # Face recognition / People API (clusters, curation, crops, scans)
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
│   │   ├── library_query.py         # Index-only row→dict shaping for library browsing (+ largest-files reader)
│   │   ├── anim_cache.py            # Animated WebP preview cache: orphan sweep, LRU ceiling, size payload
│   │   ├── analytics_service.py     # Index-only analytics rollups: recursive folder rollup, quality, reclaimable, health
│   │   ├── face_engine.py           # YuNet + SFace engine, model download/SHA-256 verification (lazy OpenCV)
│   │   ├── face_service.py          # Face storage, clustering/curation, crop cache, People queries
│   │   ├── status.py                # Shared worker/index/transfer/ZIP status payload builders
│   │   ├── ffmpeg_service.py        # FFmpeg facade: re-exports the helpers below + WebP conversion
│   │   ├── ffmpeg_probe.py          # FFprobe metadata (dimensions, codecs, duration, capture time)
│   │   ├── ffmpeg_transcode.py      # Live transcode command build + stdout relay
│   │   ├── ffmpeg_posters.py        # Poster frame extraction / thumbnail generation
│   │   ├── ffmpeg_frames.py         # Candidate frames + animated WebP preview generation
│   │   ├── deduplication_service.py # Dedup facade: re-exports the modules below
│   │   ├── dedup_common.py          # Quick-hash, pHash/dHash, pair normalisation, scoring
│   │   ├── dedup_visual.py          # Visual (pHash) clustering and group filtering
│   │   ├── dedup_backup.py          # Backup duplicate listing
│   │   ├── dedup_resolve.py         # Resolve groups / autodelete verified duplicates
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
│       ├── face_worker.py           # FaceWorker: image/video face detection, clustering, crops
│       ├── webp_worker.py           # WebPOptimizerWorker for background image conversion
│       ├── library_ops_worker.py    # LibraryOpsWorker for queued moves/deletes/renames + undo
│       ├── url_download_worker.py   # URLDownloadWorker: queued yt-dlp/gallery-dl playlist downloads
│       └── backup_watcher.py        # Periodic & shutdown library backup snapshotting
│
├── frontend/                        # Vite frontend source code & Vitest test suite
│   ├── src/                         # ES modules for app and library
│   │   ├── app/                     # Video Sorter UI logic, split into feature modules:
│   │   │                            #   index.js (entrypoint: DOMContentLoaded bootstrap)
│   │   │                            #   elements.js (cached DOM refs), state.js (app state + helpers),
│   │   │                            #   config.js, folders.js, video.js, duplicates.js, moves.js,
│   │   │                            #   polling.js, empty_folders.js, browser.js, modals.js, events.js
│   │   ├── downloads/               # Downloads page UI logic (index.js)
│   │   ├── analytics/               # Analytics dashboard UI logic (index.js: ECharts treemap/donut/bars, cards, preview)
│   │   ├── people/                  # People page UI logic (index.js: cluster grid, curation, person media;
│   │   │                            #   review.js: the unmatched-cluster review queue)
│   │   ├── library/                 # Media Library UI logic, split into feature modules:
│   │   │                            #   index.js (entrypoint: shortcuts, initEvents, bootstrap, window.libraryApp)
│   │   │                            #   layout.js, folders.js, search.js, gallery.js, lightbox.js,
│   │   │                            #   thumbnail_chooser.js, management.js, duplicates.js, tasks.js, workers.js,
│   │   │                            #   zip.js, animations.js, theme.js, sheet.js, uploads.js, shortcuts.js
│   │   │                            #   plus shared state.js, utils.js, url_downloads.js, recent_targets.js, service_worker.js
│   │   └── shared/                  # Cross-page modules (icons.js, tasks_badge.js, folder_picker.js)
│   └── tests/                       # Vitest browser/DOM unit tests (setup.js, sources, icons, library_utils, library_state, library_lightbox_sizing, recent_targets, service_worker, tasks_badge, url_downloads, tasks_modal, library_shortcuts, library_favorites_badge, uploads_resume, library_smoke, sorter_smoke, analytics_smoke, downloads_smoke, people_smoke, people_review_smoke)
│
├── vitest.config.js                 # Vitest test runner configuration (happy-dom environment)
├── static/                          # Static assets and built bundles
│   ├── styles.css                   # Main application stylesheet (source)
│   └── dist/                        # Vite build output (app.js, library.js, downloads.js, analytics.js, people.js, people_review.js, styles.css)
│
├── templates/                       # Raw HTML templates (served via HTMLResponse)
│   ├── index.html                   # Video Sorter user interface
│   ├── login.html                   # Shared-password sign-in page
│   ├── downloads.html               # URL/playlist Downloads page user interface
│   ├── analytics.html               # Library Analytics dashboard user interface (bundle analytics.js)
│   ├── people.html                  # Face clusters / People page user interface (bundle people.js)
│   ├── people_review.html           # Unmatched-cluster review queue user interface (bundle people_review.js)
│   ├── library.html                 # Media Library & Gallery user interface
│   └── partials/                    # HTML fragments (modals, sidebar, tasks_modal.html, sorter_tasks_modal.html,
│                                    #   library_shortcuts_modal.html) used via {{include}}
│                                    #   library modals are split: library_modals_media / _bulk / _manage /
│                                    #   _duplicates / _zip / _viewer.html, included in that order by library.html
│
├── tests/                           # Automated test suite
│   ├── conftest.py                  # Sandboxed fixtures (sandbox_env, sample_media)
│   ├── test_analytics_api.py        # /api/analytics/overview shape, recursive folder rollup/partition, limits, refresh cache bypass, index-only contract
│   ├── test_animated_cache.py       # Animated preview cache: orphan sweep (keeps live + in-flight temp files), LRU ceiling, has_animated reset, status payload
│   ├── test_api_routes.py           # HTTP endpoints & status verification
│   ├── test_auth.py                 # Shared-password authentication & session protection
│   ├── test_backup_and_admin_operations.py # Backup snapshot/restore, rename/delete, poster capture
│   ├── test_backup_service.py       # Backup snapshot & restore tests
│   ├── test_build_info.py           # Build revision resolution (git HEAD/refs/packed-refs, env override, linked worktrees)
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
│   ├── test_face_service.py         # Face engine/model verification, clustering, crop cache/LRU, curation, index propagation
│   ├── test_people_api.py           # /api/people clusters, curation, scans, crops, person: search token, status block
│   ├── test_library_manage.py       # File management, empty folder cleanup & undo operations
│   ├── test_library_upload.py       # Chunked upload: offset-verified chunks, resume status, duplicate/gap handling, incomplete finalize, staging GC
│   ├── test_library_media_and_http_stream.py # Byte-range streaming, ETag 304 caching, thumbnail candidate frames
│   ├── test_media_dimensions.py     # Dimension/metadata probing, natural-ratio tile data, folder totals
│   ├── test_media_service_and_ops_worker.py # LAN IP, token search, ops worker queue/undo, debounced sync-back
│   ├── test_media_services.py       # FFmpeg service, poster extraction, media utils
│   ├── test_no_blocking_db_writes.py # Event-loop guard: no mutating db_conn() in a coroutine; run_db_write runs off-loop
│   ├── test_performance_features.py# In-memory stat cache, subfolder caching, status API
│   ├── test_search_folder_match.py  # Search matches folder names (relative to the library root), not just filenames
│   ├── test_sorter_browse.py        # Video sorter browsing, queue scanning & destination listing
│   ├── test_sorter_media_and_queue.py # Sorter streaming, queue refresh, transfer abort, tolerant path resolution
│   ├── test_sorter_move.py          # Sorter moves, undo stack, file rename, delete & transfers
│   ├── test_thumbnail_chooser.py    # Video poster selection, candidate generation & timeline scrubbing
│   ├── test_transcode_comprehensive.py # Transcoding pause/resume, cancel, queue, and worker execution
│   ├── test_webp_memory_guard.py    # WebP conversion memory safeguards and skip logic
│   ├── test_workers.py              # Worker lifecycle, pause/resume, queue execution
│   ├── test_url_download.py         # URL download worker (mocked subprocess), gallery-dl fallback, retry/clear-history/paginated history, tools status, router validations
│   ├── test_system_status_transcode.py # /api/system/status transcode payload shape (queued/is_paused/target_filename) shared with transfers.transcode
│   ├── test_version.py              # Single-source version contract (__version__ vs package.json/README/FastAPI/status/pages)
│   ├── test_repo_hygiene.py         # No personal paths/emails/credentials in tracked files; local state stays gitignored
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
| `UPLOAD_STAGING_TTL_HOURS` | N/A | `24.0` | How long an interrupted browser upload keeps its staged chunks before startup garbage collection removes them (uploads resume within this window) |
| `ANIMATED_CACHE_MAX_SIZE_GB` | N/A | `5.0` | Ceiling for the extracted animated WebP preview cache; the least recently written previews past it are evicted and regenerated on the next view |
| `WEBP_SKIP_LARGER` | N/A | `false` | If `true`, abort conversion if WebP exceeds original file size |
| `DOWNLOAD_WORKER_CONCURRENCY` | N/A | `2` | Max parallel URL/playlist downloads (yt-dlp / gallery-dl) |
| `DOWNLOAD_COOKIES_FILE` | N/A | *(empty)* | Optional Netscape cookies file path passed to the downloader (`--cookies`) for auth-walled/private videos and playlists |
| `ZIP_STAGING_DIR` | N/A | `${DATA_DIR}/zip_staging` | Local fast-disk staging area for assembling ZIP archives before FUSE transfer |
| `ZIP_CACHE_DIR` | N/A | `${DATA_DIR}/cache/zips`| Local fast-disk cache for extracted ZIP contents |
| `ZIP_CACHE_MAX_SIZE_GB`| N/A | `5.0` | Maximum disk size in GB for extracted ZIP cache before LRU eviction |
| `QUICK_NAV_DIRS` | N/A | Auto-detected | Comma-separated quick navigation directory paths |
| `SEARCH_LIMIT` | N/A | `1000` | Global library search result limit (maximum matches returned in a single search query before truncation) |
| `FACE_RECOGNITION_ENABLED` | N/A | `false` | Master switch for the opt-in face recognition worker, `/people` page and scan endpoints. Off pays nothing (OpenCV is imported lazily) |
| `FACE_MODEL_DIR` | N/A | `${DATA_DIR}/models` | Where the YuNet + SFace `.onnx` weights live; downloaded once and verified by SHA-256 |
| `FACE_CROP_DIR` | N/A | `${THUMBNAIL_CACHE_DIR}/faces` | Face crop cache directory (one ~160px JPEG per detected face) |
| `FACE_DETECTION_CONFIDENCE` | N/A | `0.7` | YuNet detection score floor (0.1–0.99) |
| `FACE_MATCH_THRESHOLD` | N/A | `0.36` | Cosine **distance** ceiling for joining an existing person (0.05–1.2, lower is stricter). The `/people` Strict/Balanced/Loose control stores its own value in the database, which wins over this default without a restart |
| `FACE_MIN_FACE_PX` | N/A | `96` | Faces smaller than this (on the `FACE_SCAN_WIDTH`-wide scan image) are ignored — about a tenth of the frame, the same physical floor the 480px scan used, now measured with twice the pixels |
| `FACE_MAX_PER_FRAME` | N/A | `20` | Maximum faces kept per image/frame (largest first) |
| `FACE_SCAN_WIDTH` | N/A | `960` | Width each video frame is decoded at for detection (never upscaling a smaller source). The recogniser aligns every crop to 112×112, so a face that is 48px on a 480px frame reaches it as a 2.3× upscale — the blur that used to scatter one person's faces past the match threshold |
| `FACE_VIDEO_SAMPLE_INTERVAL` | N/A | `2` | Seconds between sampled frames per video. The sweep decodes each video **once** and samples on this grid, so this is the density of the coverage |
| `FACE_VIDEO_MAX_FRAMES` | N/A | `20000` | Frame *budget* per video: a clip long enough to blow it is sampled evenly (the spacing widens) rather than the sweep stopping early. 20,000 × 2 s ≈ 11 h of full-density coverage |
| `FACE_TRACK_GAP_S` | N/A | `15` | One record per person per appearance: a detection joins a track while it is within this many seconds of the track's last hit. A longer gap starts a new record, so a person who leaves and returns still has separate moments |
| `FACE_TRACK_DISTANCE` | N/A | `0.25` | Cosine distance ceiling for a detection joining a track's running mean (also the distance at which a fresh detection counts as a face the user already dismissed for that file) |
| `FACE_CROP_MAX_SIZE_GB` | N/A | `2.0` | LRU ceiling for the face crop cache; evicted faces are re-queued for re-scan |
| `CANDIDATE_CACHE_MAX_SIZE_GB` | N/A | `5.0` | LRU ceiling for the candidate-frame cache (the 480px video stills the thumbnail chooser and animated previews use); oldest frames are evicted and regenerate on the next view. The face sweep streams its own frames and never writes here |
| `APP_PASSWORD` | N/A | *(empty — auth disabled)* | Single shared password gating the entire web UI and API; empty/unset disables authentication |
| `GIT_COMMIT` / `BUILD_COMMIT` | N/A | *(empty — read from `.git`)* | Build revision reported as `commit` by `/api/health`; set it for a container/packaged deploy with no `.git` directory (a plain checkout is detected automatically) |

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

#### 6. `favorites`
Stores persistent bookmarks / favorited media files.
```sql
CREATE TABLE favorites (
    path TEXT PRIMARY KEY,
    created_at REAL NOT NULL
);
```
- Stored in a dedicated SQLite table so index rescans, crawl prunes, or mount disconnects never wipe user bookmarks.
- Automatically synchronized during file and folder renames/moves (`move_path_in_index`), and cleaned up upon file deletion (`delete_path_from_index`).
- Transferred video files sorted via the Video Sorter preserve their favorite state on move.

#### 7. `people`
Face clusters (created by the opt-in face recognition worker), optionally named by the user.
```sql
CREATE TABLE people (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT,                 -- NULL until named
    created_at REAL NOT NULL,
    updated_at REAL
);
```
- Cluster counts and cover crops are **derived** (`GROUP BY` / largest-box face) rather than stored, so a move, delete or re-scan cannot leave a stale counter behind.

#### 8. `faces`
One detected face: box on the scan image, its 128-d embedding, and the cluster it was assigned to.
```sql
CREATE TABLE faces (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_path TEXT NOT NULL,        -- rewritten on move/rename, deleted with the file
    frame_time REAL NOT NULL DEFAULT -1.0,  -- -1.0 for images, seconds for a video hit
    x INTEGER, y INTEGER, w INTEGER, h INTEGER,
    score REAL,
    embedding BLOB,                 -- 128 little-endian float32 (512 bytes), L2-normalised
    person_id INTEGER,              -- NULL until clustered
    distance REAL,                  -- distance to the assigned centroid at assignment time
    crop TEXT,                      -- face_<24 hex>.jpg in FACE_CROP_DIR
    created_at REAL NOT NULL,
    UNIQUE(file_path, frame_time, x, y)
);
```
- `frame_time` is never NULL (SQLite treats NULLs as distinct in a UNIQUE key, so an image's `-1.0` is what makes the tuple unique and prevents duplicate rows on a re-scan).

#### 9. `face_scans`
Which files have already been scanned, for the mtime/size the result is valid for — including files where **no** face was found (they have no `faces` rows and would otherwise be re-scanned forever).
```sql
CREATE TABLE face_scans (
    file_path TEXT PRIMARY KEY,
    mtime REAL,
    size INTEGER,
    faces_found INTEGER DEFAULT 0,
    scanned_at REAL NOT NULL
);
```

#### 10. `dismissed_faces`
Faces the user rejected as "not a person", kept as a rejection so a later re-scan of the same file does not re-cluster them.
```sql
CREATE TABLE dismissed_faces (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_path TEXT NOT NULL,
    frame_time REAL NOT NULL DEFAULT -1.0,
    x INTEGER, y INTEGER,
    embedding BLOB,             -- 128 float32, so a rejection survives a re-scan
    created_at REAL NOT NULL,
    UNIQUE(file_path, frame_time, x, y)
);
```
- The box identifies the detection; the embedding is what keeps a rejection durable when the sampling changes. A denser grid moves the timestamp and box of the detection that represents the same face, so `record_scan` skips a fresh detection that is within `FACE_TRACK_DISTANCE` of a dismissed face's embedding for that file as well as an exact box match. (`embedding` is added by an idempotent `ALTER TABLE`; rows written before it existed simply fall back to the box match.)

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
- `idx_faces_person ON faces(person_id)`
- `idx_faces_file ON faces(file_path)`
- `idx_dismissed_file ON dismissed_faces(file_path)`
- `idx_ignored_p1_p2 ON ignored_duplicates(path1, path2)`
- `idx_favorites_created_at ON favorites(created_at DESC)`

---

## 6. Background Workers & Lifecycles

All background workers inherit from `BaseWorker` (`media_library.workers.base`) providing:
- `start()`: Initializes and starts the background `asyncio.Task` loop.
- `stop()`: Gracefully signals cancellation and awaits completion.
- `pause()` / `resume()`: Non-destructively suspends and resumes processing.
- `is_paused()`: Returns Boolean pause state.
- `get_status()`: Returns a status dictionary for API polling.

Every worker's database mutations run off the event loop (`await run_db_write(...)` / `asyncio.to_thread(...)`) — see Rule 3. A worker that writes synchronously on the loop can stall the whole HTTP server while it waits for SQLite's write lock.

### Worker Roster

| Worker Instance | Class | Module | Responsibility |
|---|---|---|---|
| `transfer_worker_instance` | `TransferWorker` | `workers.transfer_worker` | Executes queued file moves sequentially; tracks undo operations. |
| `transcode_worker_instance` | `TranscodeWorker` | `workers.transcode_worker` | Background pre-transcoding of non-web videos to MP4. |
| `thumbnail_worker_instance` | `ThumbnailWorker` | `workers.thumbnail_worker` | Background extraction of poster frames for videos/images lacking thumbnails; skips non-WebP images inside ZIPs (deferred until WebP conversion). |
| `animated_thumb_worker_instance` | `AnimatedThumbWorker` | `workers.animated_thumb_worker` | Background sequential generation of looping animated WebP video previews; dual queues (priority queue for visible gallery tiles, batch queue for folder/library batch jobs) with pause/resume/cancel controls and live status. After each generation it trims the preview cache to `ANIMATED_CACHE_MAX_SIZE_GB` via `services.anim_cache.manage_animated_cache_lru` (throttled to once a minute), deleting the oldest previews and clearing their `has_animated` flag so the next view regenerates them. |
| `metadata_worker_instance` | `MediaMetadataWorker` | `workers.metadata_worker` | Probes source dimensions, duration, codecs, fps, and capture time once per loose file (FFprobe + EXIF); backfills ZIP-member dimensions from cached local thumbnails; powers natural-aspect-ratio tiles and avoids re-probing in the transcode path. Reports `zip_total`/`zip_pending_count` separately and keeps `status: probing` for the whole duration of a ZIP-member sweep (it never flips back to idle mid-sweep). |
| `duplicate_hash_worker_instance` | `DuplicateHashWorker` | `workers.duplicate_hash_worker` | Computes 128 KB head SHA-256 (+ size) for files with identical sizes. |
| `thumb_hash_worker_instance` | `ThumbHashWorker` | `workers.duplicate_hash_worker` | Computes perceptual `pHash` from video posters & images. |
| `webp_worker_instance` | `WebPOptimizerWorker` | `workers.webp_worker` | Background conversion of loose (non-ZIP) images to WebP. ZIP archives are excluded from the automatic sweep and are optimized on demand only — as a single archive-level unit when browsed or via the Optimize WebP button. Backs off when memory is low (measured against the tighter of host RAM and the service cgroup cap) and returns freed heap between conversions. |
| `url_download_worker_instance` | `URLDownloadWorker` | `workers.url_download_worker` | Queued URL/playlist downloads into `UPLOAD_STAGING_DIR` then the library folder; runs yt-dlp first and falls back to gallery-dl for unsupported sites (bunkr, image boards); up to 2 parallel slots, skip-and-continue per playlist item, live/premiere filtering, per-job cancel; retains a 500-row terminal history with newest-first pagination (`get_history`), clone-retry for failed/cancelled jobs (`retry`), and clear-finished (`clear_history`). |
| `library_ops_worker_instance` | `LibraryOpsWorker` | `workers.library_ops_worker` | Sequentially executes queued Library page operations — moves (file/folder/ZIP), recursive deletes, and renames — off the request thread, with per-item cancel and a move-only undo stack. |
| `face_worker_instance` | `FaceWorker` | `workers.face_worker` | Opt-in face detection + clustering ("People"). Sweeps loose images (from the source, falling back to the cached thumbnail) and videos (one decode pass at `FACE_SCAN_WIDTH`, each hit keeping its timestamp), embeds via the lazily-imported OpenCV YuNet + SFace pair, writes a small crop per face, and assigns each face to the nearest person centroid or a new cluster. Runs a derived number of decode streams feeding a detection pool (`scan_concurrency`), with database writes serialized behind a lock, and publishes `realtime_factor`/`eta_seconds`/`pending_footage_s`/`active_files`/`decode_workers`/`detect_workers` for the card (the ETA is footage-based: `face_stats()` returns `total_seconds`/`pending_seconds` next to the file counts). Scans each file once per `mtime`/`size`, retries a failed model download every ten minutes, pauses itself below its own `_MEMORY_FLOOR_MB` (256 MB of host *or* cgroup headroom — deliberately not the WebP optimizer's 1.5 GB `MEMORY_GUARD_MB`, because the sweep's models, engine instances and frame queues are a fixed cost that leaves a 3 GB service cap with under 1.5 GB free, so the old threshold made it pause itself for being alive), trims the crop cache to `FACE_CROP_MAX_SIZE_GB` after every batch, and reports `disabled`/`unavailable` instead of spinning when the feature is off or the engine is missing. |
| `h265_worker_instance` | `H265ConvertWorker` | `workers.h265_worker` | Serial in-place H.265 (x265/HEVC) re-encode of a single library video (`libx265 -preset medium -crf 18`, audio stream-copied when MP4-compatible, else AAC 192k, `-tag:v hvc1`). Replaces the original atomically (a `.mp4` sibling for non-MP4 sources, original removed), rewrites the SQLite row (`video_codec='hevc'`, content hashes cleared, `has_thumb=0` for poster regeneration), reports live percent/fps/speed/ETA, and supports per-job cancel. Skips files that are already HEVC. |
| `backup_watcher_instance` | `BackupWatcherWorker` | `workers.backup_watcher` | Manages scheduled and shutdown snapshot backups to library folder. Triggers a snapshot only after **every** background subsystem is idle — crawler, thumbnails, duplicate/visual hashing, metadata probing, WebP, transfers, transcodes, library ops, animated previews, URL downloads, x265 conversions, face scanning, single + batch ZIP jobs, and backups themselves. |
| *Batch ZIP Runner* | *Thread-based* | `services.zip_service` | Sequential batch compression of leaf image folders into ZIPs. |
| *Batch Unpack Runner* | *Thread-based* | `services.zip_service` | Sequential batch extraction of indexed ZIP archives back into folders (start/pause/resume/cancel), reusing `unpack_zip_to_folder`. |
| *Background Crawler* | *Async task* | `db.indexer` | Crawls library folder hierarchy, updates SQLite, prunes stale items. **Incremental on startup**: a folder whose recorded `dir_mtime` still matches is skipped without re-stat-ing/rewriting its files, though its known child folders are still traversed (so deep changes and pruning stay correct, and a transient `stat` failure protects rather than prunes the subtree). A manual sync forces a full re-scan. Prune is skipped for incomplete crawls and aborts when an implausibly large share of folders appears missing (transient mount protection) — it then records the shortfall (`indexer_state["stale_folders"]`/`stale_files`, `prune_aborted`, `prune_reason`) so the status panel reports the stale index instead of a false 100%. A genuinely large remote deletion is cleared by the explicit forced prune (`POST /api/backup/index/prune`, or the indexer card's prune button). |

### Application Lifespan (`media_library.app_factory.create_app`)
1. **Startup Sequence**:
   - AnyIO thread limiter set to `100` tokens for concurrent FUSE I/O.
   - `check_and_auto_restore_library_backup()`: Detects if database or thumbnails are missing, restoring snapshot if found.
   - `init_backup_db()`: Initializes database schema and runs idempotent migrations.
   - Workers started: `transfer`, `transcode`, `crawler`, `thumbnail`, `animated_thumb`, `metadata`, `duplicate_hash`, `thumb_hash`, `webp`, `library_ops`, `backup_watcher`, `h265`, `face`.
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
5. Cache housekeeping (`media_library.services.anim_cache`): previews are keyed on `path_mtime_size`, so deleting, renaming, moving or x265-re-encoding a video orphans its old file (`h265_worker` drops that file explicitly on commit). The index prune and deep clean sweep every `anim_<24 hex>.webp` no indexed row can reference (`clean_orphaned_animated_previews`, run alongside the orphaned-thumbnail sweep and reported as `pruned_orphaned_anims`; the strict name pattern keeps a generator's in-flight `anim_<hash>.<uuid>.tmp.webp` safe). After each generation `manage_animated_cache_lru` trims the directory under `ANIMATED_CACHE_MAX_SIZE_GB` (default 5 GB, freeing to 90% of the cap so one run evicts a batch), oldest written first, then clears `has_animated` for the videos whose previews it removed so the next view regenerates them; it is throttled to one check a minute and memoizes the directory's usage for a minute (`anim_cache_usage`), which `AnimatedThumbWorker.get_status()` reports as `cache_files`/`cache_bytes`/`cache_size_formatted`/`cache_max_bytes`/`cache_max_size_formatted` for the tasks modal's card. The module reads `THUMBNAIL_CACHE_DIR`/`ANIMATED_CACHE_MAX_SIZE_GB` from `core.config` at call time (never bound at import) so sandboxed tests cannot touch a real cache directory.
6. Candidate-frame cache housekeeping (`media_library.services.candidate_cache`): the 480px stills `extract_candidate_frame` produces — the video thumbnail chooser's frames and the animated preview compiler's frames (the face sweep streams its own, see §7) — are binned per video as `THUMBNAIL_CACHE_DIR/candidates/v<sha256(video_path)[:24]>/cand_<seconds>.jpg`. Binning is what makes the cache sweepable: a frame's own hash (path + timestamp) is not invertible and the scrubber makes the set of timestamps a video *can* have unbounded, so the per-video directory is the only reliable unit. The prune and deep clean drop the bins of videos with no indexed row plus the flat `cand_<24 hex>.jpg` files the earlier layout wrote (`clean_orphaned_candidate_frames`, reported as `pruned_orphaned_frames`; an empty or unreadable index aborts the sweep rather than emptying a good cache), `_remove_stale_folders` drops a pruned folder's bins outright (`unlink_candidate_frames`), and after every generation `manage_candidate_cache_lru` (throttled to one check a minute, usage memoized for a minute) trims the tree under `CANDIDATE_CACHE_MAX_SIZE_GB` (default 5 GB) oldest-frame-first to 90% of the cap and removes the bins it emptied. Frames are regenerable, so an evicted one simply returns the next time that second of that video is needed, and the faces payload reports the usage as `frame_cache_files`/`frame_cache_bytes`/`frame_cache_size_formatted`/`frame_cache_max_bytes`/`frame_cache_max_size_formatted`.

### Media Metadata Probing (natural-ratio tiles)
`MediaMetadataWorker` (`workers.metadata_worker`) probes each loose indexed file once via `probe_media_metadata` (`services.ffmpeg_service`):
- Single FFprobe pass captures display `width`/`height` (rotation applied), `duration`, `video_codec`, `audio_codec`, `fps`, and container `creation_time`. Images without a container timestamp fall back to an EXIF `DateTimeOriginal` read via Pillow.
- `width IS NULL` marks a file as not-yet-probed; the worker fills all probe columns together and writes `0 x 0` on failure so the file is not retried until the indexer sees it change (carry-over in `index_folder`/`update_file_in_index` keeps results across crawls).
- ZIP members are probed separately: rather than force archive extraction over FUSE, the worker reads their dimensions from the cached local thumbnail (`_backfill_zip_batch`, one batch per loop alongside loose probing, with a 60s sweep cooldown). Members without a thumbnail yet fall back to a 1:1 box filled with `object-cover` (the justified grid no longer re-flows on image load). The ZIP-member sweep keeps `status: probing` until every batch is walked, and its `zip_total`/`zip_pending_count` are reported separately so the Library's Media Metadata Probe card can show it.
- Progress is exposed under `metadata` in `/api/system/status`.

### Face Detection, Clustering & People
`FaceWorker` (`workers.face_worker`) drives the opt-in People feature through `services/face_engine.py` (detection + embeddings) and `services/face_service.py` (storage, clustering, crops, queries):
- **Parallel sweep**: `scan_concurrency(cores)` decides how many videos to decode at once and how many frames to detect at once — one core is left for the server and one per decode stream goes to FFmpeg (`_MAX_DECODERS = 3`), the rest detect up to a cap of 4 (decoding is the wall; detection needs roughly one core in total, and every detection worker pays for its own engine instance), so an 8-core box runs 2 streams and 4 detection workers. Its memory guard is `_MEMORY_FLOOR_MB` (256 MB), not the WebP optimizer's 1.5 GB `MEMORY_GUARD_MB`: the sweep's own fixed footprint would otherwise leave the service below that comfort threshold and it would stop itself. `_scan_batch` bounds concurrent files with a *file* semaphore and each clip's frame pass takes a **separate** stream semaphore (sharing one deadlocks: the video would hold a permit and wait for its own frame pass to acquire a second). Frames go through a bounded queue (`_QUEUE_FRAMES_PER_WORKER` ahead of the pool, so an hour-long clip cannot pile decoded frames in RAM), detections land in a reorder buffer and are applied to the `TrackBuilder` in frame order, and `_DetectPool` leases **one engine per detection thread** because `FaceEngine.detect` calls `setInputSize` on the detector — sharing one instance races and OpenCV fails the frame with `buf.shape() == m.shape()`. `record_scan` runs under `_db_lock` so concurrent files queue on SQLite's single writer instead of contending for it. `iter_video_scan_frames(..., slots=...)` lets the sweep size its own stream gate; the module default stays one pass per process for every other caller.
- **Scan sources**: loose images resolve to their **source**, read once and downsized to `FACE_SCAN_WIDTH` (falling back to the cached 360px thumbnail when the source cannot be read, and staying pending when neither is available — the sharpness matters because the recogniser aligns every crop to 112×112); videos are **decoded once each** and sampled by FFmpeg itself (`iter_video_scan_frames`, `fps=1/FACE_VIDEO_SAMPLE_INTERVAL` — 2 s by default — at `FACE_SCAN_WIDTH`, never upscaling a smaller source), so the mount is read sequentially once instead of being re-sought for every frame, and nothing is written to the shared candidate-frame cache. `FACE_VIDEO_MAX_FRAMES` is a frame budget: a clip long enough to blow it is sampled evenly (the spacing widens) rather than stopping early. Images inside ZIP archives are excluded from the eligible set entirely.
- **One record per appearance**: a 2 s grid detects the same face dozens of times inside one shot, so detections are folded into a **track** (`TrackBuilder`) while they stay within `FACE_TRACK_DISTANCE` of the track's running mean and within `FACE_TRACK_GAP_S` of its last hit. Each track stores one row: its **sharpest, most confident** frame (which keeps the crop and the `frame_time` a person's page jumps to) and its **mean** embedding — a cleaner cluster vote than any single frame, and the reason a dense grid no longer multiplies the database or skews a centroid towards whichever shot was longest. A longer gap starts a new track, so a person who leaves and returns still has separate moments.
- **Detection & embeddings**: `cv2.FaceDetectorYN` (YuNet, score floor `FACE_DETECTION_CONFIDENCE`, faces smaller than `FACE_MIN_FACE_PX` dropped, at most `FACE_MAX_PER_FRAME` largest kept) then `cv2.FaceRecognizerSF.alignCrop` + `.feature` for a 128-d embedding, L2-normalised before storage so cosine distance is a plain dot product (the table stores 512 bytes of float32 per face). The engine is built once per process and imported lazily; missing OpenCV or missing/unverified models report `unavailable` with a reason instead of failing.
- **Grouping**: `assign_person` finds the nearest person centroid within the effective threshold (a one-row cosine dot product over cached centroids) and otherwise a new unnamed cluster is created. The centroid cache is one pass over the embeddings, keyed on the database path + a revision bumped by merge/split/dismiss/re-cluster/restore, and updated incrementally as the worker assigns. `recluster_people` re-groups everything with `scipy.cluster.hierarchy` in `_CLUSTER_CHUNK`-sized blocks (so a 50k-face library never materialises one huge distance matrix) and re-attaches names by nearest centroid — one new cluster per person at most, best match first.
- **Curation**: rename (name `NULL` = unnamed), merge (`UPDATE faces SET person_id`, empty unnamed clusters dropped), split (new cluster from selected faces), dismiss (faces copied to `dismissed_faces` and removed, crops deleted, and a re-scan skips those boxes **and** anything within `FACE_TRACK_DISTANCE` of their embeddings, so a change of sampling grid cannot resurrect a rejection), the strictness control (`db_meta['face_match_threshold']`, read with a 10s memo so it applies to the next scan without a restart), and `auto_match_people` — the batch re-assignment behind the toolbar's **Auto-match**: every unnamed cluster whose centroid is within the threshold of a *named* person's centroid is merged into that person (named targets only; a `dry_run` reports the counts, the biggest gains and a few samples first). It exists because a face is assigned to the nearest person as it is scanned, against the people that existed at that moment, so naming someone later strands clusters that a fresh assignment would have claimed.
- **Crops & housekeeping**: `FACE_CROP_DIR` holds one `face_<sha256(path|frame|box)[:24]>.jpg` per face (stable across re-scans, so an unchanged detection reuses the file), written atomically via a `.tmp` sibling; `clean_orphaned_face_crops` runs with the prune/deep clean, `manage_face_crop_lru` trims to `FACE_CROP_MAX_SIZE_GB` after each batch and re-queues the drained files' `face_scans` rows so a later sweep rebuilds them, and the strict `face_<24 hex>.jpg` pattern keeps an in-flight temp file safe.
- **Store & index discipline**: `record_scan` replaces a file's rows in one transaction (upsert on the `UNIQUE(file_path, frame_time, x, y)` key), never resurrects a dismissal, and always writes the `face_scans` marker (including `faces_found = 0`). Moves/renames/deletes rewrite the three tables through `move_path_in_index`/`delete_path_from_index` (folder and ZIP moves rewrite the whole subtree), the prune reports `pruned_orphaned_crops` (and `pruned_orphaned_frames` for the candidate-frame cache), and a backup restore calls `invalidate_centroids()` so the previous index's clusters can never be served.

### In-place H.265 (x265) Conversion
Drives the Library lightbox's "Convert to x265 / HEVC" action via `H265ConvertWorker` (`workers.h265_worker`):
- **Queueing**: `POST /api/backup/transcode/x265` validates that the path is a library video (via `validate_backup_path`) and enqueues a single job; a second request for the same file while one is queued/running returns `409`. One job runs at a time (x265 is CPU heavy) and each is individually cancellable (`POST /api/backup/transcode/x265/cancel`).
- **Encoding**: `libx265 -preset medium -crf 18 -pix_fmt yuv420p -tag:v hvc1` in an MP4 container — CRF 18 targets *no visible loss* while usually still shrinking the H.264 source. Audio is stream-copied when the source codec is MP4-compatible (`aac`/`mp3`/`ac3`/`eac3`/`alac`), otherwise re-encoded to AAC 192k so the result stays web-playable. Already-HEVC inputs are skipped (reported, nothing written).
- **Progress**: parsed from ffmpeg `-progress pipe:1` (`out_time_us`, `fps`, `speed` → `percent`/`fps`/`speed`/`eta`), surfaced on `GET /api/backup/transcode/x265/status?path=…` (that file's job) and under `h265` in `/api/system/status`.
- **Commit**: the finished file is written to `TRANSCODE_TEMP_DIR` and committed with `os.replace` (cross-device: verified copy + replace). MP4 sources are replaced in place (same path); other containers produce a `<base>.mp4` sibling (never clobbering an existing same-stem file) and the original is removed. The SQLite row is rewritten (`video_codec='hevc'`, `needs_transcode=0`, content hashes cleared, `has_thumb=0` — the ThumbnailWorker's sweep regenerates the poster for the new file key) and the stale poster file is deleted.
- **Frontend**: the lightbox button (`S`) shows a floating `.lb-x265-panel` progress bar with fps/speed/ETA and Cancel; on completion the viewer re-opens on the converted clip with a cache-busted URL and the grid tile is updated in place. The Background Tasks modal's Active & Queues pane shows an "x265 Conversions" card (active + queued, with Cancel) and the job counts toward the header task badge.

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
- **Coverage & enforcement**: `auth_middleware` (registered in `app_factory.create_app`, active only when a password is set) gates **every** route — all pages, all `/api/...` endpoints, and all media/thumbnail/stream endpoints. Only `GET`/`HEAD`/`POST /login`, `/favicon.ico`, `/sw.js`, `/api/health` (the credential-free liveness probe, which exposes reachability, the build revision and worker liveness only) and `/static/*` stay public so the login page can render. Unauthenticated `/api/*` requests receive `401 JSON`, while page requests are `302`-redirected to `/login?next=...`. Because the cookie is sent automatically on same-origin `fetch()` and `<video>`/`<img>` src requests, no frontend JavaScript changes are required.
- **No CORS**: the app is same-origin only (pages, media streaming, thumbnails and the API are all served by it), so `create_app` installs no `CORSMiddleware`. A wildcard `allow_origins` combined with credentialed requests is rejected by browsers anyway and would only widen the surface for third-party pages. `tests/test_api_routes.py::test_no_cors_middleware_is_installed` guards this.
- **Not covered**: no rate limiting/lockout on failed logins, and the cookie is not marked `Secure` (the app is typically served over plain LAN HTTP).

---

## 12. Consolidated System API (`/api/system/status`)

To eliminate excessive polling across multiple widgets in the frontend, all background subsystem states are aggregated into a single endpoint:

**Endpoint**: `GET /api/system/status`
**Returns**:
```json
{
  "is_active": true,
  "version": "1.0.0",
  "transfers": { "active": null, "queued": [], "completed": [] },
  "transcode": { "active": null, "queued": [], "queued_count": 0, "history": [], "is_paused": false, "total_pending": 0 },
  "index": { "status": "idle", "scanned_folders": 243, "skipped_folders": 242, "total_folders": 243, "percent": 100, "stale_folders": 0, "stale_files": 0, "prune_aborted": false, "prune_reason": "", "is_complete": true },
  "thumbnails": { "status": "idle", "pending_count": 0, "percent": 100 },
  "animated_thumbnails": { "status": "idle", "pending_count": 0, "priority_count": 0, "batch_count": 0, "completed_count": 10, "percent": 100.0, "active_file": null, "recent_completed": [], "cache_files": 128, "cache_bytes": 183500800, "cache_size_formatted": "175.0 MB", "cache_max_bytes": 5368709120, "cache_max_size_formatted": "5.0 GB" },
  "metadata": { "status": "idle", "pending_count": 0, "zip_total": 0, "zip_pending_count": 0, "processed_count": 0, "percent": 100 },
  "duplicate_hash": { "status": "idle", "pending_count": 0, "percent": 100 },
  "thumb_hash": { "status": "idle", "pending_count": 0, "percent": 100 },
  "webp": { "status": "idle", "saved_formatted": "42.1 MB", "converted_count": 140 },
  "library_backup": { "status": "idle", "backup_exists": true, "last_backup_str": "Oct 06, 12:00:00" },
  "zip": { "is_active": false, "tasks": [] },
  "batch_zip": { "status": "idle", "succeeded": 12, "overall_percent": 100.0 },
  "batch_unzip": { "status": "idle", "succeeded": 0, "failed": 0, "errors": [], "overall_percent": 0.0 },
  "library_ops": { "status": "idle", "active": null, "queued": [], "undo_count": 0 },
  "url_downloads": { "status": "idle", "concurrency": 2, "active": [], "queued": [], "history": [], "tools": { "ytdlp": true, "gallery_dl": true } },
  "h265": { "status": "idle", "active": null, "job": null, "queue_length": 0, "active_count": 0, "queued_count": 0, "queued": [], "jobs": [], "history": [] },
  "faces": { "status": "idle", "enabled": false, "files_per_min": 0.0, "realtime_factor": 0.0, "pending_footage_s": 0.0, "eta_seconds": 0, "active_files": 0, "decode_workers": 0, "detect_workers": 0, "engine": { "available": false, "ready": false, "reason": "Face models not downloaded (…)", "model_dir": "…/models" }, "total_files": 0, "pending_count": 0, "processed_count": 0, "percent": 100.0, "current_file": "", "faces_total": 0, "people_total": 0, "unnamed_people": 0, "dismissed_faces": 0, "is_paused": false, "cache_files": 0, "cache_bytes": 0, "cache_size_formatted": "0 B", "cache_max_bytes": 2147483648, "cache_max_size_formatted": "2.0 GB", "frame_cache_files": 0, "frame_cache_bytes": 0, "frame_cache_size_formatted": "0 B", "frame_cache_max_bytes": 5368709120, "frame_cache_max_size_formatted": "5.0 GB" }
}
```
- The top-level `faces` key is the opt-in face recognition worker (off by default). It carries the scan coverage (`total_files`/`pending_count`/`percent`/`current_file`, plus `realtime_factor`/`eta_seconds`/`pending_footage_s` from the rolling rate window and `active_files`/`decode_workers`/`detect_workers` describing the parallel sweep; the ETA is footage still owed divided by the footage-per-second rate, because a file count is a poor clock when clips range from seconds to 90 minutes, and `files_per_min` is kept as the card's tooltip), the engine's availability and *why* it is unavailable, the cluster totals (`faces_total`/`people_total`/`unnamed_people`/`dismissed_faces`), the face crop cache's usage against its cap (`cache_*`) and the candidate-frame cache's usage against its own cap (`frame_cache_*`, the 480px video stills the video sweep reads). `status == "scanning"` also sets `is_active`. Only image and video files that can actually be scanned are counted (`total_files` excludes ZIP members and images without a cached thumbnail).
- The top-level `version` key reports `media_library.__version__` — the single source of truth for the release version (see Rule 1.6). The liveness probe's `commit` key reports the checkout's short git SHA, which is the finer-grained check of *which* build a running server is on.
- The frontend uses `is_active` to adjust polling frequency: **2000 ms** when active background work is in flight, **6000 ms** when idle, **8000 ms** on error.
- `url_downloads.active_count > 0` or `queued_count > 0` also sets `is_active`.
- The top-level `h265` key is built by `H265ConvertWorker.get_status()`: the serial in-place HEVC re-encode queue driving the lightbox's "Convert to x265" action. It carries the `active` job, the `queued` list, `active_count`/`queued_count`, and a sanitized `jobs`/`history` (the threading `abort_event` is stripped). Either an active or queued x265 job also sets `is_active`.
- The top-level `transcode` key is the **same shaped payload** as `transfers.transcode` (built by `services.status.transfers_payload`): it carries the `queued` list (with `source_path`), `is_paused`, `total_pending`, and a `history` whose entries include `target_filename`, `source_path`, `target_path` and `target_size_formatted`. The Library's transcode panel and the sorter read this one shape; a queued transcode also sets `is_active`.
- The `index` body is built by `services.status.index_status_payload()` and never mixes snapshots: while crawling, `scanned_folders`/`total_folders` are the live crawl counters (percent capped at 99%); when idle, `total_folders` is the DB row count and `scanned_folders` is `total - stale_folders`, so the percent only reaches 100 when the index matches the mount. `stale_folders`/`stale_files` (rows pointing at folders the mount no longer has), `prune_aborted` and `prune_reason` come from the last prune pass, and `is_complete` is `!crawling && stale_folders == 0`. Both task modals render an amber "Index stale" state plus a "Remove missing folders from index" button calling the forced prune when `stale_folders > 0`; the 30s count cache is keyed on `indexer_state["revision"]` so post-crawl/post-prune counts are never served from a stale snapshot.

---

## 13. Complete API Routing & Endpoint Map

### HTML Views (`media_library.routers.views`)
- All five page routes are served through `_serve_page()`, which loads the cached template from `core/templating.load_template` and substitutes the `{{VERSION}}` token with `media_library.__version__`; the Library page's sidebar footer and the login page render it (the login page does the same substitution in `routers/auth._render_login`).
- `GET /` — Serves Media Library UI (`templates/library.html`)
- `GET /sorter` / `GET /sort` — Serves Incoming Video Sorter UI (`templates/index.html`)
- `GET /downloads` — Serves the URL/playlist Downloads page (`templates/downloads.html`; bundle `static/dist/downloads.js`)
- `GET /analytics` — Serves the Library Analytics dashboard (`templates/analytics.html`; bundle `static/dist/analytics.js`)
- `GET /people` — Serves the Face clusters / People page (`templates/people.html`; bundle `static/dist/people.js`)
- `GET /people/review` — Serves the unmatched-cluster review queue (`templates/people_review.html`; bundle `static/dist/people_review.js`; reached from the **Review unmatched** button on `/people`, badged with the unnamed-cluster count)
- `GET /library` — 302 Redirect to `/`
- `GET /sw.js` — Serves Service Worker script (`static/sw.js`) with `Service-Worker-Allowed: /` and `Cache-Control: no-cache, no-store, must-revalidate` for client-side animated WebP thumbnail interception and CacheStorage caching

### Authentication (`media_library.routers.auth`)
- `GET /login` — Serves the shared-password sign-in page (`templates/login.html`); redirects to `/` when auth is disabled or already signed in
- `POST /login` — Validates the shared password and sets the signed `ml_auth` cookie (303/302 redirect to the validated `next` target)

### Media Library & Browser (`media_library.routers.library_browse` + `library_media` + `library_admin`)
- `GET /api/backup/browse?path={folder_path}&refresh={bool}` — List subfolders, files, breadcrumbs, ZIP indicators. Never indexes inline: for a folder not yet in the index it schedules indexing on a background thread and returns the cached view with `indexing: true` (the client re-polls). Returns per-file `thumb_url`/`media_url` plus probed `width`/`height`/`duration`/`video_codec`/`audio_codec`/`fps`/`capture_time` (dimensions drive natural-aspect-ratio tiles). Each subfolder entry carries `file_count`/`subfolder_count`/`total_size`, a representative cover `thumb_url` (alphabetically-first direct-child image by `LOWER(name)`, falling back to the first direct-child video's poster, index-only), the cover's `width`/`height`, and `has_thumb`; folders with no media get `thumb_url: null`, and the client card falls back to an icon until the on-demand thumbnail is generated. (`library_browse`)
- `GET /api/backup/folders?path={folder_path}` — List only a folder's immediate child folders (index-only, FUSE-safe) with `file_count`/`subfolder_count`/`is_zip`. Powers the sidebar folder tree's lazy expansion; never reads file entries nor triggers ZIP extraction, and schedules background indexing with `indexing: true` for unindexed folders. (`library_browse`)
- `GET /api/backup/search?q={query}&type={type}&sort={sort}&limit={limit}` — Search media files by multi-word query across SQLite index; respects `limit` (defaults to `SEARCH_LIMIT`, default 1000) and returns `limit`, `total`, `truncated` (bool indicating if results exceeded limit), and matching `results` with probed `width`/`height`/`duration`/`video_codec`/`audio_codec`/`fps`/`capture_time`. Truncated searches display a visual indicator (`1,000+` badge) and query refinement notice. The query may also carry `person:<name>` / `person:"<full name>"` tokens: they are stripped from the free-text words, resolved against named face clusters, and returned in a `person` key (`{terms, matched, people}`), so an unknown name reads as "no such person" rather than an empty search. (`library_browse`)
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
- `POST /api/backup/index/prune` — Explicit forced prune: removes index rows for folders/files that no longer exist on the mount (bypasses the crawler's mount-safety limit, which aborts when >10% of the index looks missing) plus the caches those rows backed (orphaned thumbnails, animated previews, face crops, candidate frames); the status panel's "Remove missing folders from index" button calls it (`library_browse`)

### Face Recognition / People (`media_library.routers.people`)
- `GET /api/people` — Person clusters with derived counts (faces, files) and cover crops, biggest first (`limit` 1–2000, `offset`, `named=true|false` to narrow to clusters the user has or has not named, `samples` 1–12 (default 4) for how many crop URLs each row carries, and `near=<person_id>` to rank the clusters sharing folders with that person's faces first (weighted by how many faces the person has in that folder, so a folder they are all over beats one they merely pass through), annotating each row with `shared_folders`/`shared_faces`/`shared_folder_names`/`affinity`, and `folder=<text>` to narrow the list to clusters with a face in a folder whose path contains that text (case-insensitive, `%`/`_` escaped, rows annotated with `match_folders` — labels relative to the library root, last two segments); every row also carries `folder_names` (the folders its faces are filed in, busiest first, one grouped query per page) and the body carries `counts`, `total` and the full status payload. The merge picker asks for `named=false&near=<the cluster it was opened from>`, so a named person is never offered as a merge source (which would throw its name away) and the true duplicate — filed next to the original, since the media collection is organised per folder — is at the top instead of buried under thousands of unrelated clusters; the **review queue** asks the same question the other way round (`named=true&near=<the unnamed cluster being reviewed>&limit=6&samples=1` for the six name pills on its card, `named=true&near=…&samples=3` behind its "More…" sheet, and `named=false&limit=1&samples=8` for the next cluster to review); `total` is computed by `face_service.count_people()` with the same filter.
- `GET /api/people/{person_id}` — One person: their media, grouped by file (a video appears once with its `moments` — the sampled timestamps that person appears at — and `first_frame_time`), shaped like Library browse rows so the page reuses the same thumbnails/media URLs; plus their first faces for the cover and review strip (`limit`, `offset`, `sort=count|name|date|size`). `404` for an unknown cluster
- `GET /api/people/{person_id}/faces` — The review grid: that cluster's faces, best score first (`limit`, `offset`)
- `GET /api/people/crop?face_id={n}&v={crop}` — Streams one face crop from the local cache (never touches the mount). `404` when the face or its crop is gone
- `GET /api/people/status` — Worker + engine + cluster + crop-cache summary (the same body embedded in `/api/system/status` as `faces`)
- `POST /api/people/rename` — Name (or clear the name of) a cluster (`person_id`, `name`); `404` for an unknown cluster, `422` without `person_id`
- `POST /api/people/merge` — Fold clusters into a target (`target_id`, `source_ids`); empty unnamed sources are removed, named ones are kept
- `POST /api/people/split` — Move selected faces into a new (optionally named) cluster (`face_ids`, `name`)
- `POST /api/people/dismiss` — Mark faces (`face_ids`) or a whole cluster (`person_id`) as "not a person": they are recorded in `dismissed_faces` so no future scan re-adds them
- `POST /api/people/threshold` — Set match strictness (`preset=strict|balanced|loose` or an explicit `threshold`), stored in `db_meta` so it applies to the next scan/re-cluster without a restart
- `POST /api/people/recluster` — Re-group every face and re-attach existing names by nearest centroid (optional `threshold`); `409` when the face count exceeds the one-pass cap
- `POST /api/people/auto-match` — Fold every unnamed cluster that already sits within the match threshold of a **named** person into that person (an unnamed target would just move the problem); `preset` (`strict`/`balanced`/`loose`) or an explicit `threshold` (clamped 0.05–1.2) selects the distance, `dry_run: true` reports what the pass would do without moving anything (`matched`/`faces`/`people`/`remaining_unnamed`, the biggest gains as `by_person`, and a few `samples`), and `422` rejects an unknown preset. Curation of an existing index, so it works while `FACE_RECOGNITION_ENABLED` is off
- `POST /api/people/scan` — Queue (or re-queue) a `folder`, a `paths` list, or the whole library (`all: true`) by dropping the matching `face_scans` markers; `409` while `FACE_RECOGNITION_ENABLED` is off, `422` without a scope
- `POST /api/people/pause` — Pause or resume the face-scanning sweep (`paused`)

### Library File & Folder Management (`media_library.routers.library_manage` + `library_upload`)
- `POST /api/backup/upload/chunk` — Receive a chunk of a browser upload file (staged locally). The optional `offset` form field is the chunk's byte position; the server appends only there, acks an already-held chunk as a duplicate (`duplicate: true`), rejects a chunk that starts past the staged end with `409`, and truncates a partial tail from a crash before rewriting it
- `GET /api/backup/upload/status?upload_id={id}` — Bytes already staged for an upload (`received_bytes`, `exists`), so the browser can resume an interrupted transfer instead of restarting it
- `POST /api/backup/upload/finalize` — Assemble, atomically write to target, and index the uploaded file; refuses to commit when the staged size does not match `total_size` (`400`, staged bytes kept so the client can finish)
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

### Favorites & Bookmarking (`media_library.routers.favorites`)
- `POST /api/backup/favorites/toggle` — Toggle favorite status for a single media file (`path`); returns `{status: "success", path, is_favorite: bool, counts}` where `counts` is the fresh `{all, videos, images}` totals so the client badge needs no extra request
- `POST /api/backup/favorites/batch` — Bulk star, unstar, or toggle favorite status on multiple files (`paths`, `action`: `"add"` | `"remove"` | `"toggle"`); returns `{status: "success", action, updated: int, counts}`
- `GET /api/backup/favorites/count` — Favorites totals only (`{counts: {all, videos, images}}`) from a single aggregate over the `favorites` join (`services/library_query.read_favorites_counts`), index-only and row-free. This is what the Library sidebar badge polls on page load; the full list endpoint is reserved for the Favorites view, which actually needs the rows
- `GET /api/backup/favorites?type={video|image}` — List favorited media files library-wide sorted by favorited timestamp descending, with `files` (only the requested type is read) and global `counts` (`all`/`videos`/`images`, independent of the filter)
- `GET /api/backup/favorites/status?path={path}` — Check favorite status for a single file; returns `{path, is_favorite: bool}`

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
- `POST /api/backup/transcode/x265` — Queue an in-place H.265 (HEVC) re-encode of a library video (`path`); `409` when one is already queued/running for that file, `400` for a non-video, `404` for a missing file
- `GET /api/backup/transcode/x265/status?path={file_path}` — x265 conversion status: active job, queued jobs, and the job for `path` (`job`) when given
- `POST /api/backup/transcode/x265/cancel` — Cancel an x265 conversion by `job_id` (or `path` of the active/queued job)

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

### Library Analytics (`media_library.routers.analytics`)
- `GET /api/analytics/overview?folder_limit={n}&file_limit={n}&refresh={bool}` — Index-only analytics body for the `/analytics` dashboard (read-only; no mutation endpoints). Returns `generated_at`, `library_root`, `limits`, `totals` (bytes/files/videos/images/folders/ZIP counts with formatted sizes), `by_type`, `by_extension` (top 12 by bytes plus a rolled-up remainder so shares sum to 100%), `folders` (`root_bytes`/`root_files`, `top` = treemap top-level nodes with one level of children and a `is_group` aggregate for anything past the top 40, `largest` = the top-N recursive subtrees pruned to a non-overlapping partition), `largest_files` (browse-shaped entries with `thumb_url`/`media_url`), `quality` (resolution buckets by longest edge, probed/high-fps counts, total runtime), `reclaimable` (duplicate size-match candidates, WebP saved/pending, ZIP-eligible leaf folders, empty leaf folders) and `health` (outstanding thumbnails/probes/visual hashes/animated previews/transcodes plus `indexer_state` staleness). `folder_limit`/`file_limit` are 1–100 (defaults 15/25, out-of-range → `422`). Memoized for 30s keyed on `indexer_state["revision"]`; `refresh=true` drops the memo for the manual Refresh button. Never walks or scans the filesystem. (`analytics`)

### Unified System Status (`media_library.routers.system`)
- `GET /api/system/status` — Consolidated status for all background subsystems
- `GET /api/health` — Cheap, **credential-free** liveness probe for systemd, uptime monitors and container healthchecks. Returns `{status, version, uptime_s, database, workers: {running, total}}` plus `commit` (the checkout's short git SHA, or the `GIT_COMMIT`/`BUILD_COMMIT` env value; omitted when unknown) — reachability, the build revision and worker liveness only, never a library path, count or filename — and `503` with `status: "degraded"` when the database cannot be read (`SELECT 1`). It is listed in `_PUBLIC_PATHS` in `media_library/routers/auth.py` so it stays reachable when `APP_PASSWORD` is set. The revision is resolved by `media_library/core/build_info.py` at first call (following a linked worktree's `gitdir:`/`commondir` and `packed-refs`, no git binary and no per-request I/O) and cached for the process lifetime, so it answers "is the live server on today's commit?" without an mtime comparison.
- `POST /api/system/clean-db` — Trigger background deep database scan and thumbnail cleanup

---

## 14. Development, Testing, and Maintenance Workflows

### Setting Up the Environment
```bash
# Clone the repository and enter directory
cd /path/to/media-library

# Ensure Python 3.10+ is available
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

In production the app is managed by the `media-library.service` systemd unit (`ExecStart=/path/to/media-library/venv/bin/python /path/to/media-library/app.py --host 0.0.0.0 --port 8888`, `Restart=always`). **Never start a second instance by hand** — doing so races for port `8888` and can make systemd crash-loop. Restart the service instead; SIGTERM triggers a clean worker shutdown and shutdown snapshot (can take ~15s):

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

### Linting, Formatting & CI
`ruff.toml` is the lint configuration (default rules minus the three intentionally excluded ones documented in its header, with `target-version = "py310"` matching the package's real floor). The tree is kept lint-clean **and** format-clean, and `.github/workflows/ci.yml` fails the push/PR when either drifts:
```bash
./venv/bin/ruff check .            # lint
./venv/bin/ruff check --fix .      # apply the safe fixes
./venv/bin/ruff format .           # apply formatting
./venv/bin/ruff format --check .   # what CI runs
```
The workflow's three jobs are `lint` (ruff check + format check), `backend` (`pytest`, after `apt-get install ffmpeg` — the runner image no longer ships FFmpeg — and an `ffmpeg -version` sanity check) and `frontend` (`npm ci` + `npm test` + `npm run build`). The lint job installs **the ruff version pinned in `requirements.txt`** (`ruff==`), never a floating one: an unpinned ruff once failed CI on rules its older local counterpart did not know (`UP041`, which newer ruff applies because its default target version is 3.11+ while this package targets 3.10).

### Writing New Tests
All test modules must use the sandboxed environment fixtures defined in `tests/conftest.py`:
- `isolate_index_database` (autouse): pins `DB_PATH` to a per-test throwaway database. `get_current_db_path()` otherwise falls back to the configured (production) index DB, so a test that never requests `sandbox_env` would write real folder/file rows into the live `backup_index.db` — `test_db_indexer.py::test_suite_never_writes_to_the_configured_database` guards this.
- `sandbox_env`: Creates isolated temporary directories for `media`, `incoming`, `thumbnails`, and a dedicated SQLite database. Never touches production directories.
- `sample_media`: Pre-populates the sandbox with sample category directories, video clips, and poster images.
- `fake_face_engine` / `faces_enabled` / `face_image_factory` / `scan_face_batch` / `people_client`: the face recognition doubles. A deterministic `FakeFaceEngine` (colour-coded detections, one-hot embeddings) replaces YuNet/SFace so the clustering, crop cache, curation and API paths are all covered **without** OpenCV, ONNX models or a network fetch; `face_image_factory` writes an indexed image plus the cached thumbnail the worker actually reads, and `people_client` is a `TestClient` with the feature enabled. `patch_runtime_directories` also pins `FACE_MODEL_DIR`/`FACE_CROP_DIR` into the sandbox, and the engine's path helpers only `mkdir` when a file is actually about to be written (`get_model_dir(create=False)` / `get_face_crop_dir(create=False)` on read paths), so no test can download a model or write a crop outside its own tmp dir — `test_face_service.py::test_suite_keeps_face_paths_inside_the_sandbox` guards it.

Two cross-cutting guards run with the suite and should stay green:
- `tests/test_version.py` enforces the single-source version contract (`media_library.__version__` vs `package.json`, `README.md`, `CHANGELOG.md`, the FastAPI metadata, the `/api/system/status` payload and the served-page `{{VERSION}}` token).
- `tests/test_repo_hygiene.py` fails if any tracked file contains this machine's home path, a real email address or a credential-shaped string, if local state (`data/`, `*.db`, `.env`, `config.json`, `plans/`, `REJECTED_IMPROVEMENTS.md`) becomes tracked, or if the `.gitignore` rules covering them are removed.

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
8. [ ] **Lint & Format Clean**: `./venv/bin/ruff check .` and `./venv/bin/ruff format --check .` both pass (CI gates on them).
9. [ ] **Frontend Escaping**: Every user-controlled string rendered into the DOM (filename, folder name, download title, server error text) goes through `textContent` or `escapeHtml()`; the toast helpers (`frontend/src/library/utils.js`, `frontend/src/app/state.js`) are text-only by construction and must stay that way — a filename is not trusted input.
10. [ ] **Opt-in Heavy Features Stay Local**: Anything that adds an inference runtime, a downloaded model or real CPU cost (face recognition is the current example) stays **off by default** behind its env switch, imports its dependency lazily so a missing install degrades to a clear status instead of a crash, verifies any downloaded artefact, and keeps its data (embeddings, crops, names) on this machine.
