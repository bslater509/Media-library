# 📁 Media Library & Video Sorter

A high-performance, web-based media manager and video sorter built with **FastAPI**, **SQLite**, and modern web standards. Designed for organizing large video collections with sub-millisecond search, proactive poster caching, seamless playback, and a rapid 1-touch keyboard sorter.

**Current release: v1.1.4** · [Changelog](CHANGELOG.md) · [MIT License](LICENSE)

---

## ⚡ Key Features

### 📁 Media Library & Gallery
- **Instant SQLite Indexing**: Sub-millisecond search and filtering across tens of thousands of media files and nested category folders.
- **Natural Aspect Ratio Layout**: Row-ordered justified tile layout respecting probed aspect ratios, with 5 scale presets (XS–XL).
- **Proactive Thumbnail & Poster Caching**: Automatically extracts video poster frames in the background using FFmpeg and caches them locally for lag-free scrolling.
- **Fullscreen Lightbox Player**: Native browser playback with byte-range streaming, scrubbing, loop toggle, and inline rename/delete.
- **Batch Management**: Context menus and multi-select toolbar for batch moves, deletes, renames, and WebP optimization.
- **Multi-Word Search**: Filter by multi-word queries with category and folder badge navigation.

### ⭐ Favorites & Bookmarking System
- **Dual-Access Navigation**:
  - **In-Folder Filter Pill**: Filter any folder view instantly to show only starred media with live count badge (`⭐ Favorites`).
  - **Global Sidebar Collection**: Library-wide chronological grid showing every favorited video and image with folder jump-chips to navigate directly to containing folders.
- **Card, Lightbox & Context Controls**:
  - Star button on card overlays (hover-revealed on desktop, always visible filled gold `⭐` when favorited, touch-accessible).
  - Star toggle in fullscreen Lightbox overlay bar and keyboard hotkey `F`.
  - Right-click / tap-and-hold context menu actions ("Add to Favorites" / "Remove from Favorites").
  - 1-click bulk star / unstar toggle in selection toolbar.
- **Video Sorter Integration**:
  - Star button in desktop toolbar, header, and mobile bottom bar + keyboard hotkey `B` (keeping `F` dedicated for fullscreen video).
  - **Move Persistence**: Starred videos in Sorter automatically preserve their favorite status when transferred to destination folders.
- **Resilient SQLite Architecture**: Dedicated `favorites` table ensures index rescans never erase bookmarks, and automatically updates during file/folder moves, renames, and deletions.

### 🔗 URL & Playlist Downloads
- **Dedicated Downloads Page** (`/downloads`): a first-class downloader reached from the Library/Sorter nav tabs and the mobile drawer.
- **Batch Queueing**: Paste several URLs at once; each valid http(s) link is queued into the chosen folder (invalid/duplicate lines are reported and skipped).
- **yt-dlp + gallery-dl**: Best-quality video/playlist downloads with an automatic gallery-dl fallback for image boards and file hosts.
- **Recent Folders & Picker**: Quick chips of folders you last downloaded into, plus a library folder-tree picker.
- **Live Jobs List**: Active/queued progress with speed/ETA, paginated history, per-job cancel, retry of failed/cancelled jobs, and clear-history.

### 🎬 Incoming Video Sorter
- **Rapid Keyboard Workflow (<50ms per move)**: Review incoming downloads with autoplay and instant pre-buffering.
- **1-Touch Destination Sorting**:
  - Keys `1`–`6` for one-touch moves into your most frequent folders.
  - Press `/` to fuzzy-filter categories and hit `Enter` to move.
  - Create new subfolders on the fly with `+ Create & Move`.
- **Non-Blocking Background Transfers**: Moves and conversions are queued sequentially without blocking UI responsiveness.
- **Instant Undo**: Cancel or revert moves with `U` or `Ctrl+Z`.
- **Automatic MP4 Transcoding**: Converts incompatible formats (`.mkv`, `.avi`, `.wmv`, `.flv`, `.mov`) into web-native MP4 using stream copying or H.264 fallback.

### 📦 ZIP Virtual Folders & Archive Management
- **Zero-Extraction Browsing**: Browse ZIP archives as folders directly from their central directory index without disk extraction.
- **On-Demand Local Caching**: Extracted images and videos are cached locally with automatic LRU eviction (`ZIP_CACHE_MAX_SIZE_GB`).
- **Folder Compression & Batch Tools**: Compress entire image folders to ZIP, unpack archives back to disk, and batch compress/extract with background workers.

### 🖼️ Automatic WebP Optimization
- **Background Conversion**: Automatically converts loose images (JPEG, PNG) to optimized WebP.
- **Safety & Accounting**: Atomic file replacement with integrity checks and cumulative space-savings tracking.

### 🔍 Deduplication Engine
- **Content Hashing**: Fast 128 KB head-chunk + byte size matching with full verification prior to any deletion.
- **Visual Duplicate Detection**: 64-bit perceptual hashing (`pHash`) to identify resized, re-encoded, or visually similar images.
- **Interactive Resolution**: Group review interface with side-by-side comparison and false-positive ignore rules.

### 🧑 Face Recognition & People
- **Opt-in & Local**: Enable with `FACE_RECOGNITION_ENABLED=true`. Detection and embeddings run on this machine with OpenCV's YuNet + SFace models (no torch, no cloud, no upload) — images, embeddings, names and face crops never leave the host.
- **Images & Videos**: Faces are found in loose images (read from the cached thumbnails) and in videos, sampling a frame every 30s and keeping each hit's timestamp as a jump-to "moment".
- **Clusters You Name**: Similar faces are grouped automatically; rename, merge, split or dismiss clusters ("not a person"), then search the Library with `person:anna`.
- **Dedicated People Page** (`/people`): cluster grid, per-person media, a face review strip, and a strictness control (Strict/Balanced/Loose) you can re-group with at any time.

### 💾 Zero-Work Live Backups & Recovery
- **Hot SQLite Backups**: Non-destructive `sqlite3_backup` snapshotting of the index directly to `<LIBRARY_DIR>/.media_library_backup/`.
- **Automatic Startup Recovery**: Auto-detects and restores missing databases or thumbnail caches on startup.

### 🔒 Access Control
- **Shared Password Authentication**: Optional single password gate (`APP_PASSWORD`) with HMAC-signed session cookies and login UI.

---

## 🚀 Quickstart Guide

### 1. Prerequisites
- **Python 3.10+** (PEP 604 union syntax is evaluated at runtime, so 3.9 cannot import the package)
- **FFmpeg** and **FFprobe** (for video poster extraction and transcoding):
  ```bash
  # Debian / Ubuntu
  sudo apt update && sudo apt install -y ffmpeg

  # Fedora / RHEL
  sudo dnf install -y ffmpeg

  # macOS (Homebrew)
  brew install ffmpeg
  ```

### 2. Clone and Configure
```bash
# Clone the repository
git clone https://github.com/your-username/media-library.git
cd media-library

# Create your local environment configuration
cp .env.example .env
```

Edit `.env` to specify your media folders:
```bash
# Destination directory for your organized media library
LIBRARY_DIR=/path/to/media/library

# Source directory containing new/unsorted videos
INCOMING_DIR=/path/to/incoming/downloads

# Port and host interface
PORT=8888
HOST=0.0.0.0
```

### 3. Start the Server
Run the launch script (it will create the virtual environment and install dependencies automatically on first run):
```bash
./start.sh
```

Now open your browser:
- **Media Library**: `http://localhost:8888`
- **Incoming Sorter**: `http://localhost:8888/sorter`
- **Downloads**: `http://localhost:8888/downloads`
- **People**: `http://localhost:8888/people` (after `FACE_RECOGNITION_ENABLED=true`)
- **Analytics**: `http://localhost:8888/analytics`

---

## ⚙️ Configuration Reference

All settings can be configured via environment variables in `.env` or passed as CLI arguments:

| Variable | CLI Argument | Default | Description |
|---|---|---|---|
| `LIBRARY_DIR` | `--dest` | `./data/media` | Path to organized media library collection |
| `INCOMING_DIR` | `--source` | `./data/incoming` | Path to incoming/unsorted video files |
| `DATA_DIR` | N/A | `./data` | Base directory for runtime data and storage |
| `DB_PATH` | N/A | `${DATA_DIR}/library.db` | Custom SQLite database file location |
| `THUMBNAIL_DIR` | N/A | `${DATA_DIR}/thumbnails` | Directory for cached video posters |
| `TRANSCODE_DIR` | N/A | `${DATA_DIR}/transcodes` | Temporary directory for transcoded clips |
| `QUICK_NAV_DIRS` | N/A | Auto-detected | Comma-separated shortcuts for folder browser (e.g. `/media,/mnt,~`) |
| `HOST` | `--host` | `0.0.0.0` | Host interface to bind |
| `PORT` | `--port` | `8888` | Port to listen on |
| `APP_PASSWORD` | N/A | *(empty)* | Optional shared password protecting UI and API endpoints |
| `WEBP_AUTO_OPTIMIZE` | N/A | `true` | Automatically convert loose images to WebP in background |
| `WEBP_WORKER_CONCURRENCY` | N/A | `1` | Number of concurrent workers for image conversion |
| `WEBP_QUALITY` | N/A | `85` | Quality factor (1–100) for WebP encoding |
| `WEBP_SKIP_LARGER` | N/A | `false` | Discard WebP conversion if output size exceeds original |
| `ZIP_CACHE_MAX_SIZE_GB` | N/A | `5.0` | Max disk size in GB for extracted ZIP cache before LRU eviction |
| `LIBRARY_BACKUP_AUTO_ENABLED` | N/A | `true` | Enable background automatic snapshot backups when system is idle |
| `LIBRARY_BACKUP_AUTO_INTERVAL_HOURS` | N/A | `6.0` | Minimum interval (in hours) between automatic background snapshots |

---

## ⌨️ Keyboard Shortcuts (Sorter)

| Key | Action |
|---|---|
| <kbd>Enter</kbd> | Move video to highlighted category & advance |
| <kbd>1</kbd> .. <kbd>6</kbd> | One-touch move to **Recent Folder #1 .. #6** |
| <kbd>S</kbd> or <kbd>→</kbd> | Skip to next video |
| <kbd>P</kbd> or <kbd>←</kbd> | Previous video |
| <kbd>U</kbd> or <kbd>Ctrl+Z</kbd> | Smart Undo last move |
| <kbd>R</kbd> | Rename video filename |
| <kbd>D</kbd> | Delete video (with confirmation) |
| <kbd>Space</kbd> | Play / Pause video |
| <kbd>/</kbd> | Focus category search input |
| <kbd>L</kbd> | Toggle video loop |
| <kbd>F</kbd> | Toggle fullscreen mode |
| <kbd>?</kbd> | Show shortcuts help modal |
| <kbd>Esc</kbd> | Close active modal |

---

## 🐧 Running as a Linux System Service (systemd)

To run the media library continuously in the background on Linux:

1. Copy the systemd template:
   ```bash
   cp media-library.service.example media-library.service
   ```
2. Edit `media-library.service` with your user and installation path.
3. Install and enable the service:
   ```bash
   sudo cp media-library.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now media-library
   ```
4. Check status and logs:
   ```bash
   systemctl status media-library
   journalctl -u media-library -f
   ```

---

## ⚠️ Known Limitations

- **Single shared password, single user.** `APP_PASSWORD` gates the whole app for everyone; there are no accounts, roles or per-user permissions, and no rate limiting or lockout on failed logins. The session cookie is `HttpOnly`/`SameSite=Lax` but not `Secure`.
- **No built-in TLS.** The server speaks plain HTTP on your LAN. Put it behind a reverse proxy (Caddy, nginx, Traefik) before exposing it beyond a trusted network.
- **Deletions and moves are immediate and permanent.** There is no trash or recycle bin, and bulk actions cannot be rolled back beyond the move-only undo stack.
- **Snapshots cover the index and thumbnails, not your media.** `library/.media_library_backup/` restores the database and poster cache after a rebuild; it is not a backup of the media files themselves.
- **Heavy encodes are serialised.** x265 re-encodes and pre-transcoding run one job at a time (CPU-bound); a large queue drains slowly by design.
- **The library must be mounted before start.** FUSE/network mounts are actively tolerated (the indexer protects itself against transient `stat` failures), but an unmounted destination at startup looks like an empty library.
- **No container image yet.** Run it with `./start.sh` or the provided `media-library.service` unit; Docker packaging is future work.

---

## 📄 License
MIT License. See [LICENSE](LICENSE) for details.
