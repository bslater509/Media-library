# 📁 Media Library & Video Sorter

A self-hosted media library and keyboard-driven video sorter for large video and
image collections — often tens of thousands of files on a slow remote mount
(pCloud, NFS). One FastAPI process serves a JSON API at `/api` and a React
client at `/`, backed by a single SQLite index.

**Current release: v3.3.0** · [Changelog](CHANGELOG.md) · [MIT License](LICENSE)

---

## ⚡ What it does

- **Library** — indexed browsing of the folder tree, multi-word search over
  filenames *and* library-relative folder paths, justified natural-ratio tiles,
  a fullscreen viewer with byte-range streaming, favorites, and multi-select
  bulk move / delete / rename / star / WebP. On desktop the sidebar stays
  pinned, with an independently scrolling folder list and always-visible
  navigation and Settings.
- **Watch history** — a server-side record of what you actually watched: a
  Continue-watching rail on the Library page, resume-at-position playback, and
  a View history page split into Continue / Recently viewed / Never watched.
- **Sorter** — review an incoming folder with autoplay and pre-buffering, then
  move with `1`–`6`, a folder search (`/`) and `Enter`, with a duplicate
  warning, undo, rename, delete and live transcoding for MKV/AVI/WMV.
- **Artwork** — background posters and looping animated WebP previews, plus a
  poster chooser that samples frames across a clip and can set any of them.
  New video posters use a frame 20% into the clip to skip opening titles.
  The library's “Regenerate video thumbnails” toolbar action replaces posters
  for the current folder; selection mode offers the same action for selected
  videos. Confirmation includes manually chosen posters. Progress runs in the
  background, and failures leave existing thumbnails intact.
  Cached artwork is served locally without checking originals on a remote
  drive; missing posters are generated on demand.
- **Processing** — metadata probing (dimensions, duration, codecs, fps, capture
  time), in-place x265 (HEVC) conversion with live progress, WebP optimisation,
  content and visual dedupe with side-by-side playback, an optional images
  filter and a cached five-frame visual check, ZIP virtual browsing and
  unpacking (single or batched), empty-folder cleanup, and a verification
  queue that flags likely damaged files (zero-byte, failed probe, failed
  poster) for review.
- **Downloads** — queue one or many URLs into a folder: yt-dlp with a
  gallery-dl fallback, live progress, cancel, retry, paginated history. The
  queue survives a restart, a failed album resumes where it stopped, and every
  album member shows up in the queue (done or pending) before it downloads.
  RAR/7z containers are kept in an `archives` subfolder and unpacked there.
- **Web MP4 tool** — Tools → Convert to web MP4 previews the indexed library,
  then checks actual codecs and replaces incompatible loose videos with
  H.264/AAC MP4. Compatible streams are remuxed, compatible MP4s skipped.
  New files pass a full decode verification before originals are removed;
  conflicts/failures preserve originals. Progress, recent failures and cancel
  are on Tools, with a summary in Activity. Archives need unpacking first.
  Re-encoding is lossy and subtitles/attachments are dropped: back up original
  media separately (library snapshots do not contain videos). Output needs
  free space beside the source. Restart stops the batch; rerun to continue.
  A crash may leave a hidden `.mllibtmp-*.original` backup; recover it manually
  with the service stopped rather than deleting it.
- **Analytics** — an index-only dashboard: storage treemap, largest folders and
  files, media mix, reclaimable space, quality histogram, health pills, and a
  daily growth history recorded automatically (chart library size over time).
  Likely damaged files are flagged by the verification queue.
- **Snapshots** — hot SQLite snapshots plus a poster archive with automatic
  startup restore, and snapshots are never taken over a running import.
- **Activity** — a global header panel for downloads, indexing, metadata,
  artwork, conversions, duplicate checks, ZIP batches, snapshots and tool
  updates, with progress, errors and supported cancel/pause/resume/retry
  controls. **Prioritize browsing** defers automatic heavy work and queued
  jobs after current items finish; playback and explicit actions remain
  available. The toggle is shared across devices and resets on server restart.
  Polling uses local memory only, not remote-drive checks.
- **Access control** — optional shared password (`APP_PASSWORD`) with a
  stateless signed cookie; no accounts, no session store.

---

## 🚀 Quickstart

### 1. Prerequisites

- **Python 3.10+** (PEP 604 unions are evaluated at runtime, so 3.9 cannot
  import the package)
- **FFmpeg** and **FFprobe**
  ```bash
  sudo apt update && sudo apt install -y ffmpeg     # Debian / Ubuntu
  sudo dnf install -y ffmpeg                        # Fedora / RHEL
  brew install ffmpeg                               # macOS
  ```
- **7z** (optional, only to unpack downloaded RAR/7z sets)
  ```bash
  sudo apt install -y 7zip                          # Debian / Ubuntu
  sudo dnf install -y p7zip                         # Fedora / RHEL
  brew install sevenzip                             # macOS
  ```
- **Node.js 22+** and npm (client build)

### 2. Configure

```bash
git clone https://github.com/your-username/media-library.git
cd media-library
cp .env.example .env
```

```bash
# Your organized library
LIBRARY_DIR=/path/to/media/library

# Where new/unsorted videos arrive
INCOMING_DIR=/path/to/incoming/downloads

HOST=0.0.0.0
PORT=8888
```

### 3. Build the client and run

```bash
npm ci
npm run build          # typecheck + vite build into static/dist/

./start.sh             # creates the venv, installs deps, starts the server
```

Then open:

- **Library** — `http://localhost:8888/`
- **Sorter** — `http://localhost:8888/sorter`
- **Downloads** — `http://localhost:8888/downloads`
- **Analytics** — `http://localhost:8888/analytics`
- **API docs** — `http://localhost:8888/api/docs`

For development, `npm run dev` serves the client on `:5173` and proxies `/api`
and `/static` to the running server on `:8888`.

---

## ⚙️ Configuration

Environment variables first, `.env` second. The old `V2_` names are still read
as a fallback, so a pre-upgrade file keeps working.

| Variable | Default | Description |
|---|---|---|
| `LIBRARY_DIR` | `./data/media` | Your organized media library |
| `INCOMING_DIR` | `./data/incoming` | Incoming queue the sorter reviews |
| `SOURCE_DIR` | `INCOMING_DIR` | Explicit sorter folder |
| `DATA_DIR` | `./data` | Runtime root: index, caches, staging |
| `DB_PATH` | `${DATA_DIR}/library.db` | SQLite index (a root `backup_index.db` wins) |
| `DB_READ_ONLY` | `false` | Open the index read-only; mutations answer 503 |
| `WRITES` | `true` | Master switch for write endpoints |
| `THUMBNAIL_DIR` | `${DATA_DIR}/thumbnails` | Poster and preview cache |
| `TRANSCODE_DIR` | `${DATA_DIR}/transcodes` | x265 output while encoding |
| `LIBRARY_BACKUP_DIR` | `${LIBRARY_DIR}/.media_library_backup` | Snapshot location |
| `ALLOW_LIVE_RESTORE` | `false` | Allow in-app restore over the live index |
| `OWN_WORKERS` | `true` | Run the crawler, prober, sweeps and snapshots |
| `CRAWL_INTERVAL_MINUTES` | `30` | Crawl cadence |
| `BACKUP_INTERVAL_HOURS` | `6.0` | Automatic snapshot cadence |
| `WEBP_QUALITY` / `WEBP_SKIP_LARGER` | `85` / `false` | WebP conversion |
| `X265_PRESET` / `X265_CRF` | `medium` / `18` | HEVC encode settings |
| `UPLOAD_STAGING_DIR` | `${DATA_DIR}/upload_staging` | Resumable upload chunks |
| `DOWNLOAD_STAGING_DIR` | `${DATA_DIR}/download_staging` | Downloader working dirs |
| `DOWNLOAD_STATE` / `DOWNLOAD_ARCHIVE_DIR` | `${DATA_DIR}/download_queue.json` / `${DATA_DIR}/download_archives` | Persisted queue and per-URL resume ledgers |
| `DOWNLOAD_WORKER_CONCURRENCY` | `2` | Parallel URL downloads |
| `DOWNLOAD_COOKIES_FILE` | *(empty)* | Cookies for gated sites |
| `ZIP_CACHE_MAX_SIZE_GB` | `5.0` | LRU ceiling for extracted archives |
| `BROWSE_LIMIT` / `SEARCH_LIMIT` | `120` / `1000` | Default page sizes |
| `HOST` / `PORT` | `0.0.0.0` / `8888` | Bind address |
| `APP_PASSWORD` | *(empty — auth off)* | Shared password for the whole app |

The full annotated list is in [`.env.example`](.env.example).

---

## ⌨️ Keyboard shortcuts

**Sorter**

| Key | Action |
|---|---|
| <kbd>Enter</kbd> | Move to the highlighted folder and advance |
| <kbd>1</kbd> … <kbd>6</kbd> | Move to recent folder 1–6 |
| <kbd>/</kbd> | Focus folder search |
| <kbd>S</kbd> / <kbd>→</kbd> | Skip to the next clip |
| <kbd>U</kbd> / <kbd>Ctrl</kbd>+<kbd>Z</kbd> | Undo the last move |
| <kbd>R</kbd> | Rename |
| <kbd>D</kbd> | Delete (confirms) |
| <kbd>Esc</kbd> | Close the open dialog / search |

**Viewer**

| Key | Action |
|---|---|
| <kbd>←</kbd> / <kbd>→</kbd> | Previous / next item |
| <kbd>Space</kbd> | Play / pause |
| <kbd>L</kbd> | Loop |
| <kbd>I</kbd> | Media info |
| <kbd>Esc</kbd> | Close |

---

## 🐧 Run it as a service

```bash
cp media-library.service.example media-library.service
# edit User / WorkingDirectory / paths
sudo cp media-library.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now media-library
systemctl status media-library
journalctl -u media-library -f
```

`systemctl restart media-library` applies any backend or asset change. The unit
runs `app.py` with `Restart=always`.

### Watchdog for a hung process

`Restart=always` only helps when the process exits. A process can hold its port
while answering nothing (a wedged mount read, a long lock wait). The bundled
watchdog polls `/api/health` and restarts the unit after two consecutive
failures:

```bash
sudo cp media-library-watchdog.service.example /etc/systemd/system/media-library-watchdog.service
sudo cp media-library-watchdog.timer.example /etc/systemd/system/media-library-watchdog.timer
sudo sed -i "s#/path/to/media-library#$PWD#" /etc/systemd/system/media-library-watchdog.service
sudo systemctl daemon-reload
sudo systemctl enable --now media-library-watchdog.timer
```

---

## 🧪 Tests

```bash
./venv/bin/pytest                      # backend (parallel by default; -n0 for serial)
npm test                               # client
npm run build                          # typecheck + build
./venv/bin/ruff check . && ./venv/bin/ruff format --check .
```

The backend suite spreads across every CPU with `pytest-xdist` and records
coverage through `sys.monitoring` on Python 3.12+, which cuts a full run to a
few seconds on a multicore machine without changing what is measured.

---

## ⚠️ Known limitations

- **Single shared password.** No accounts, roles or per-user permissions, no
  login rate limiting, and the cookie is `HttpOnly`/`SameSite=Lax` but not
  `Secure`.
- **No built-in TLS.** Plain HTTP on the LAN; put it behind a reverse proxy
  before exposing it.
- **Deletes and moves are immediate.** No trash; the sorter's undo is
  move-only and held in memory, so a restart clears it.
- **Snapshots cover the index and posters, not your media.**
- **One heavy job at a time.** x265 encodes are serialised and long.
- **Moving a folder into a ZIP member is not supported** — archive members are
  read-only in the app.
- **The library must be mounted before start.** An unmounted destination looks
  like an empty library (the crawler guards against transient failures, but a
  genuine empty mount is indistinguishable).
- **In-memory queues.** Sorter undo, download history and ZIP batch state do
  not survive a restart (the download queue itself does).

---

## 📄 License

MIT License. See [LICENSE](LICENSE) for details.
