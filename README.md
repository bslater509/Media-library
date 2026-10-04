# 📁 Media Library & Video Sorter

A high-performance, web-based media manager and video sorter built with **FastAPI**, **SQLite**, and modern web standards. Designed for organizing large video collections with sub-millisecond search, proactive poster caching, seamless playback, and a rapid 1-touch keyboard sorter.

---

## ⚡ Key Features

### 📁 Media Library & Gallery
- **Instant SQLite Indexing**: Sub-millisecond search and filtering across tens of thousands of media files and nested category folders.
- **Proactive Thumbnail & Poster Caching**: Automatically extracts video poster frames in the background using FFmpeg and caches them locally for lag-free scrolling.
- **Fullscreen Lightbox Player**: Native browser playback with byte-range streaming, scrubbing, loop toggle, and inline rename/delete.
- **Multi-Word Search**: Filter by multi-word queries with category and folder badge navigation.

### 🎬 Incoming Video Sorter
- **Rapid Keyboard Workflow (<50ms per move)**: Review incoming downloads with autoplay and instant pre-buffering.
- **1-Touch Destination Sorting**:
  - Keys `1`–`6` for one-touch moves into your most frequent folders.
  - Press `/` to fuzzy-filter categories and hit `Enter` to move.
  - Create new subfolders on the fly with `+ Create & Move`.
- **Non-Blocking Background Transfers**: Moves and conversions are queued sequentially without blocking UI responsiveness.
- **Instant Undo**: Cancel or revert moves with `U` or `Ctrl+Z`.
- **Automatic MP4 Transcoding**: Converts incompatible formats (`.mkv`, `.avi`, `.wmv`, `.flv`, `.mov`) into web-native MP4 using stream copying or H.264 fallback.

---

## 🚀 Quickstart Guide

### 1. Prerequisites
- **Python 3.9+**
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

## 📄 License
MIT License. See [LICENSE](LICENSE) for details.
