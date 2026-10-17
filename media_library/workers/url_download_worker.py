"""Background worker for URL/playlist video downloads via yt-dlp + gallery-dl.

Jobs are queued from the Library page ("Download from URL"). Up to
``DOWNLOAD_WORKER_CONCURRENCY`` (default 2) downloads run in parallel; further
jobs wait in the queue. Downloaders run as isolated subprocesses (``python -m
yt_dlp`` / ``python -m gallery_dl``) so a site-breakage crash terminates a single
job, never the worker, and upgrades/cookies stay decoupled from the app's
Python environment.

Pipeline per job:
1. **yt-dlp first**. If it reports an unsupported URL (or finds nothing
   importable), and gallery-dl is installed, the job **automatically falls back
   to gallery-dl** for sites yt-dlp cannot handle (image boards / file hosts
   such as bunkr album links). yt-dlp is still preferred for streaming sites
   where it gives richer format selection and progress reporting.
2. The downloader writes into a per-job staging directory under
   ``UPLOAD_STAGING_DIR`` (local disk; never touches the remote library).
3. Completed files are moved one by one into the user-chosen library folder
   with auto-rename collision handling and an atomic ``.tmp_*`` ->
   ``os.replace`` pattern (FUSE-safe), then the target folder is re-indexed.

Queue semantics: playlist items that fail are skipped and the job continues
(yt-dlp ``--ignore-errors``); live streams and upcoming premieres are filtered
out for yt-dlp (``--match-filter "!is_live & !is_upcoming"``). Jobs are
individually cancellable.
"""

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from media_library.core.config import (
    DOWNLOAD_COOKIES_FILE,
    DOWNLOAD_WORKER_CONCURRENCY,
    UPLOAD_STAGING_DIR,
)
from media_library.core.constants import IMAGE_EXTENSIONS, VIDEO_EXTENSIONS
from media_library.db.indexer import index_folder
from media_library.workers.base import BaseWorker

logger = logging.getLogger(__name__)

# Audio extensions produced by yt-dlp for video+audio-only downloads; they are
# accepted into the library (indexed as type 'other'), though "best quality"
# video downloads merge to .mp4/.webm/.mkv by default.
AUDIO_EXTENSIONS = {".mp3", ".m4a", ".aac", ".opus", ".ogg", ".wav", ".flac"}

# Sidecar stems/suffixes yt-dlp may emit next to media (subtitles, metadata,
# description text, thumbnails). Fallback guard on top of the allowed-extension
# set, kept for clarity and for future allow-listed extensions.
SKIPPED_EXTENSIONS = {
    ".json",
    ".nfo",
    ".vtt",
    ".srt",
    ".ass",
    ".ssa",
    ".lrc",
    ".description",
    ".txt",
    ".thumbnail",
}

_URL_RE = re.compile(r"^https?://", re.IGNORECASE)

# Replace "NA" (yt-dlp's missing-value marker) with 0 so the JSON line stays parseable.
_NA_FIX = re.compile(r'"NA"')

# Media extensions importable from a downloader staging directory.
_MEDIA_EXTENSIONS = VIDEO_EXTENSIONS | IMAGE_EXTENSIONS | AUDIO_EXTENSIONS

# Substrings in yt-dlp output that mean "this extractor does not handle the URL";
# these trigger the gallery-dl fallback instead of a hard failure.
_UNSUPPORTED_HINTS = ("Unsupported URL", "No video formats found")


class DownloadCancelled(Exception):
    pass


def _build_ytdlp_command(url: str, out_dir: Path) -> list[str]:
    """Assemble the ``python -m yt_dlp`` subprocess command for one job."""
    progress_tpl = (
        'download:{"type":"progress",'
        '"title":%(title)j,'
        '"filename":%(filename)j,'
        '"percent":%(progress.percent)j,'
        '"downloaded_bytes":%(progress.downloaded_bytes)j,'
        '"total_bytes":%(progress.total_bytes)j,'
        '"total_bytes_estimate":%(progress.total_bytes_estimate)j,'
        '"speed":%(progress.speed)j,'
        '"eta":%(progress.eta)j,'
        '"playlist_index":%(playlist_index)j,'
        '"playlist_count":%(playlist_count)j}'
    )
    cmd = [
        sys.executable,
        "-m",
        "yt_dlp",
        "--newline",
        "--no-colors",
        # Skip and continue when an individual playlist item fails.
        "--ignore-errors",
        # Skip live streams and upcoming premieres entirely.
        "--match-filter",
        "!is_live & !is_upcoming",
        # Best quality: best video + best audio merged (ffmpeg), fallback best.
        "-f",
        "bv*+ba/b",
        "--merge-output-format",
        "mp4",
        "-o",
        str(out_dir / "%(title).120B [%(id)s].%(ext)s"),
        "--progress-template",
        progress_tpl,
    ]
    if DOWNLOAD_COOKIES_FILE and os.path.isfile(DOWNLOAD_COOKIES_FILE):
        cmd += ["--cookies", DOWNLOAD_COOKIES_FILE]
    cmd.append(url)
    return cmd


def _build_gallerydl_command(url: str, out_dir: Path) -> list[str]:
    """Assemble the ``python -m gallery_dl`` subprocess command for one job.

    ``directory=[]`` forces a flat layout (gallery-dl otherwise nests files under
    a per-extractor subfolder) so finalization sees every downloaded file in one
    place. gallery-dl prints the completed path of each file on its own line,
    which is parsed for lightweight progress.
    """
    cmd = [
        sys.executable,
        "-m",
        "gallery_dl",
        "--dest",
        str(out_dir),
        "-o",
        "directory=[]",
    ]
    if DOWNLOAD_COOKIES_FILE and os.path.isfile(DOWNLOAD_COOKIES_FILE):
        cmd += ["--cookies", DOWNLOAD_COOKIES_FILE]
    cmd.append(url)
    return cmd


def _coerce_num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _apply_progress(item: dict[str, Any], data: dict[str, Any]) -> None:
    """Fold one parsed progress line into the job's status dict."""
    if "percent" in data:
        item["percent"] = _coerce_num(data.get("percent"), item["percent"])
    for key in ("downloaded_bytes", "total_bytes", "total_bytes_estimate"):
        if key in data:
            item[key] = int(_coerce_num(data.get(key), item.get(key, 0)))
    if data.get("speed"):
        item["speed"] = str(data["speed"])
    if data.get("eta"):
        item["eta"] = str(data["eta"])
    title = data.get("title") or data.get("filename")
    if title:
        item["current_title"] = str(title)
    pi = int(_coerce_num(data.get("playlist_index")))
    pc = int(_coerce_num(data.get("playlist_count")))
    if pi:
        item["playlist_index"] = pi
    if pc:
        item["playlist_count"] = pc


def _finalize_downloaded_file(
    src_file: Path, target_dir: Path, job_id: str
) -> str | None:
    """Move one downloaded file into the library with auto-rename + atomic replace.

    Returns the destination path, or None when the extension is not importable
    (subtitles, sidecars, etc. are dropped). FUSE-safe: no traversal, atomic
    ``os.replace`` after a same-directory temporary copy.
    """
    ext = src_file.suffix.lower()
    if (
        ext not in VIDEO_EXTENSIONS
        and ext not in IMAGE_EXTENSIONS
        and ext not in AUDIO_EXTENSIONS
    ):
        return None
    base = src_file.stem
    # Drop sidecars yt-dlp may emit (subtitles, metadata, description text, ...).
    if ext in SKIPPED_EXTENSIONS or base.endswith(".thumbnail"):
        return None

    target_path = target_dir / src_file.name
    if target_path.exists():
        target_path = _unique_filename(target_dir, src_file.name)

    tmp_path = target_dir / f".tmp_{job_id}_{target_path.name}"
    shutil.move(str(src_file), str(tmp_path))
    os.replace(str(tmp_path), str(target_path))
    return str(target_path)


def _unique_filename(target_dir: Path, filename: str) -> Path:
    """Auto-rename like uploads: ``file (1).ext``, ``file (2).ext``, ..."""
    target_path = target_dir / filename
    if not target_path.exists():
        return target_path
    stem = target_path.stem
    ext = target_path.suffix
    counter = 1
    while True:
        candidate = target_dir / f"{stem} ({counter}){ext}"
        if not candidate.exists():
            return candidate
        counter += 1


def _finalize_job_files(item: dict[str, Any], job_dir: Path) -> int:
    """Move every downloaded file from the local staging dir into the library.

    Returns the number of files imported. The staging directory is local disk,
    so walking it is safe (AGENTS.md Rule 2 only forbids remote walks).
    """
    target_dir = Path(item["destination_dir"])
    if not target_dir.is_dir():
        raise RuntimeError(f"Destination folder no longer exists: {target_dir}")
    imported = 0
    # Recurse: gallery-dl can nest files under subfolders (mitigated by
    # ``directory=[]`` but not guaranteed for every extractor).
    for src_file in sorted(p for p in job_dir.rglob("*") if p.is_file()):
        try:
            dest = _finalize_downloaded_file(src_file, target_dir, item["id"])
            if dest:
                imported += 1
        except Exception as e:
            logger.warning(f"Failed to import downloaded file {src_file}: {e}")
    index_folder(str(target_dir))
    return imported


def _reset_job_dir(job_dir: Path) -> None:
    """Clear a staging dir before a fallback downloader reuses it."""
    shutil.rmtree(job_dir, ignore_errors=True)
    job_dir.mkdir(parents=True, exist_ok=True)


def _apply_ytdlp_line(item: dict[str, Any], line: str) -> None:
    """Fold one yt-dlp ``--progress-template`` JSON line into job status."""
    if not line.startswith("{"):
        return
    try:
        data = json.loads(_NA_FIX.sub("0", line))
    except (ValueError, TypeError):
        return
    if isinstance(data, dict):
        _apply_progress(item, data)


def _apply_gallerydl_line(item: dict[str, Any], line: str, out_dir: Path) -> None:
    """Track gallery-dl progress from the per-file path it prints on completion."""
    path = Path(line)
    if path.suffix.lower() not in _MEDIA_EXTENSIONS:
        return
    if not path.is_absolute():
        return
    if out_dir != path.parent and out_dir not in path.parents:
        return
    item["current_title"] = path.name
    try:
        item["playlist_index"] = int(item.get("playlist_index", 0)) + 1
    except (TypeError, ValueError):
        item["playlist_index"] = 1


def _stream_process(
    item: dict[str, Any], cmd: list[str], on_line=None
) -> tuple[int, list[str]]:
    """Run a downloader subprocess, streaming stdout lines through ``on_line``.

    Returns ``(returncode, lines)``. Raises ``DownloadCancelled`` when the job's
    abort event is set (killing the process first).
    """
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    item["proc"] = proc
    assert proc.stdout is not None
    lines: list[str] = []
    try:
        for raw_line in proc.stdout:
            if item.get("abort_event", threading.Event()).is_set():
                proc.kill()
                proc.wait()
                raise DownloadCancelled()
            line = raw_line.strip()
            if not line:
                continue
            lines.append(line)
            if on_line is not None:
                on_line(line)
        proc.wait()
    finally:
        item["proc"] = None

    if item.get("abort_event", threading.Event()).is_set():
        raise DownloadCancelled()
    return proc.returncode, lines


def _looks_unsupported(lines: list[str]) -> bool:
    """True when yt-dlp output indicates its extractors do not handle the URL."""
    joined = "\n".join(lines)
    return any(hint in joined for hint in _UNSUPPORTED_HINTS)


def _attempt_downloads(
    item: dict[str, Any], job_dir: Path
) -> tuple[int, Exception | None]:
    """Try yt-dlp, then fall back to gallery-dl for sites yt-dlp cannot handle.

    Returns ``(imported_count, last_error)``; ``last_error`` is None on success.
    ``DownloadCancelled`` propagates to the caller.
    """
    last_error: Exception | None = None

    if is_ytdlp_available():
        item["downloader"] = "yt-dlp"
        try:
            code, lines = _stream_process(
                item,
                _build_ytdlp_command(item["url"], job_dir),
                lambda line: _apply_ytdlp_line(item, line),
            )
            if code == 0:
                imported = _finalize_job_files(item, job_dir)
                if imported:
                    return imported, None
                last_error = RuntimeError(
                    "No downloadable media found in URL (live streams / premieres skipped?)"
                )
            else:
                last_error = RuntimeError(f"yt-dlp exited with code {code}")
                if not _looks_unsupported(lines):
                    return 0, last_error
        except DownloadCancelled:
            raise
        except Exception as e:
            last_error = e
            return 0, last_error

    if is_gallery_dl_available():
        item["downloader"] = "gallery-dl"
        _reset_job_dir(job_dir)
        try:
            code, _ = _stream_process(
                item,
                _build_gallerydl_command(item["url"], job_dir),
                lambda line: _apply_gallerydl_line(item, line, job_dir),
            )
            if code != 0:
                return 0, RuntimeError(f"gallery-dl exited with code {code}")
            imported = _finalize_job_files(item, job_dir)
            if imported:
                return imported, None
            return 0, last_error or RuntimeError("No downloadable media found in URL")
        except DownloadCancelled:
            raise
        except Exception as e:
            return 0, e

    return 0, last_error


def _run_ytdlp_job(item: dict[str, Any]) -> None:
    """Synchronous (thread) execution of one download job (yt-dlp -> gallery-dl)."""
    job_dir = UPLOAD_STAGING_DIR / f"dl_{item['id']}"
    job_dir.mkdir(parents=True, exist_ok=True)
    try:
        imported, error = _attempt_downloads(item, job_dir)
        if error is not None:
            raise error
        item["imported_count"] = imported
        if not imported:
            raise RuntimeError(
                "No downloadable media found in URL (live streams / premieres skipped?)"
            )
        item["status"] = "completed"
        item["percent"] = 100.0
    except DownloadCancelled:
        item["status"] = "cancelled"
        item["error"] = "Cancelled by user"
    except Exception as e:
        item["status"] = "failed"
        item["error"] = str(e)
        logger.error(f"URL download job {item['id']} failed ({item['url']}): {e}")
    finally:
        item["proc"] = None
        item["completed_at"] = time.time()
        shutil.rmtree(job_dir, ignore_errors=True)


class URLDownloadWorker(BaseWorker):
    """Queued yt-dlp/gallery-dl downloader with up to ``DOWNLOAD_WORKER_CONCURRENCY`` slots."""

    def __init__(self):
        super().__init__("url_download_worker")
        self.jobs_queue: asyncio.Queue = asyncio.Queue()
        self.jobs_map: dict[str, dict[str, Any]] = {}
        self.active_jobs: dict[str, dict[str, Any]] = {}
        self.history: list[dict[str, Any]] = []

    def enqueue(self, url: str, destination_dir: str) -> str:
        """Queue one URL/playlist download job. Returns its job id."""
        job_id = "dl_" + uuid.uuid4().hex[:12]
        item: dict[str, Any] = {
            "id": job_id,
            "url": url,
            "destination_dir": destination_dir,
            "status": "queued",
            "created_at": time.time(),
            "started_at": None,
            "completed_at": None,
            "abort_event": threading.Event(),
            "proc": None,
            "percent": 0.0,
            "downloaded_bytes": 0,
            "total_bytes": 0,
            "total_bytes_estimate": 0,
            "speed": "",
            "eta": "",
            "current_title": "",
            "playlist_index": 0,
            "playlist_count": 0,
            "imported_count": 0,
            "downloader": None,
            "error": None,
        }
        self.jobs_map[job_id] = item
        self.enqueue_nowait(self.jobs_queue, item)
        return job_id

    def cancel(self, job_id: str) -> bool:
        item = self.jobs_map.get(job_id)
        if not item:
            return False
        item["status"] = "cancelled"
        item["abort_event"].set()
        proc = item.get("proc")
        if proc is not None:
            try:
                proc.kill()
            except Exception:
                pass
        return True

    def get_status(self) -> dict[str, Any]:
        active = []
        for item in self.active_jobs.values():
            active.append(self._entry(item))
        queued = []
        for item in self.jobs_map.values():
            if item.get("status") == "queued":
                queued.append(self._entry(item))
        queued.sort(key=lambda x: x.get("created_at", 0))
        return {
            "status": self._status,
            "concurrency": DOWNLOAD_WORKER_CONCURRENCY,
            "active": active,
            "active_count": len(active),
            "queued": queued,
            "queued_count": len(queued),
            "history": [self._entry(h) for h in self.history[-20:]],
            # Downloader availability rides along so every surface (library
            # tasks modal, mobile tab, /downloads page) can show whether yt-dlp
            # and gallery-dl are installed. Checks are cached for 60s.
            "tools": {
                "ytdlp": is_ytdlp_available(),
                "gallery_dl": is_gallery_dl_available(),
            },
        }

    def find_finished(self, job_id: str) -> dict[str, Any] | None:
        """Return the public entry for a terminal job, or None if unknown."""
        for item in self.history:
            if item.get("id") == job_id:
                return self._entry(item)
        return None

    def retry(self, job_id: str) -> str | None:
        """Clone a failed or cancelled job as a new queue entry.

        Returns the new job id, or None when the original job is unknown or not
        retryable (completed jobs are never re-downloaded).
        """
        entry = self.find_finished(job_id)
        if not entry:
            return None
        if entry.get("status") not in ("failed", "cancelled"):
            return None
        url = entry.get("url")
        destination = entry.get("destination_dir")
        if not url or not destination:
            return None
        return self.enqueue(url, destination)

    def clear_history(self) -> int:
        """Remove all finished jobs from the retained history.

        Active and queued jobs are never stored in ``self.history`` (they live
        in ``active_jobs`` / ``jobs_map``), so this only touches terminal rows.
        Returns the number of entries cleared.
        """
        terminal = {"completed", "failed", "cancelled"}
        before = len(self.history)
        self.history = [h for h in self.history if h.get("status") not in terminal]
        return before - len(self.history)

    def get_history(self, limit: int = 50, offset: int = 0) -> dict[str, Any]:
        """Paginated finished-history listing, newest first.

        ``self.history`` is retained oldest -> newest (capped at 500), so the
        newest-first window is ``reversed(history)[offset:offset+limit]``.
        """
        limit = 50 if limit is None else int(limit)
        limit = max(1, min(limit, 500))
        offset = max(0, int(offset or 0))
        newest = list(reversed(self.history))
        page = [self._entry(h) for h in newest[offset : offset + limit]]
        return {
            "history": page,
            "total": len(self.history),
            "limit": limit,
            "offset": offset,
        }

    @staticmethod
    def _entry(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": item.get("id"),
            "url": item.get("url"),
            "destination_dir": item.get("destination_dir"),
            "status": item.get("status"),
            "percent": round(float(item.get("percent") or 0.0), 1),
            "downloaded_bytes": item.get("downloaded_bytes", 0),
            "total_bytes": item.get("total_bytes", 0)
            or item.get("total_bytes_estimate", 0),
            "speed": item.get("speed", ""),
            "eta": item.get("eta", ""),
            "current_title": item.get("current_title", ""),
            "playlist_index": item.get("playlist_index", 0),
            "playlist_count": item.get("playlist_count", 0),
            "imported_count": item.get("imported_count", 0),
            "downloader": item.get("downloader"),
            "error": item.get("error"),
            "created_at": item.get("created_at"),
            "started_at": item.get("started_at"),
            "completed_at": item.get("completed_at"),
        }

    async def _run_loop(self):
        await asyncio.sleep(1.0)
        while True:
            try:
                if not self._pause_event.is_set():
                    self._status = "paused"
                    await self._pause_event.wait()
                    self._status = "running"

                while len(self.active_jobs) < DOWNLOAD_WORKER_CONCURRENCY:
                    try:
                        item = self.jobs_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                    if item.get("status") == "cancelled":
                        item["completed_at"] = time.time()
                        self.history.append(item)
                        if len(self.history) > 500:
                            self.history = self.history[-500:]
                        self.jobs_queue.task_done()
                        continue
                    item["status"] = "downloading"
                    item["started_at"] = time.time()
                    self.active_jobs[item["id"]] = item
                    self._status = "running"
                    asyncio.create_task(self._run_job(item))

                if not self.active_jobs:
                    self._status = "idle"

                await asyncio.sleep(1.0)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in url_download_worker loop: {e}")
                await asyncio.sleep(5.0)

    async def _run_job(self, item: dict[str, Any]):
        try:
            await asyncio.to_thread(_run_ytdlp_job, item)
        except asyncio.CancelledError:
            item["status"] = "cancelled"
            item["error"] = "Worker stopped"
        finally:
            self.active_jobs.pop(item["id"], None)
            self.history.append(item)
            if len(self.history) > 500:
                self.history = self.history[-500:]
            self.jobs_queue.task_done()


def is_ytdlp_available() -> bool:
    """True when ``python -m yt_dlp`` runs on this host (cached 60s)."""
    cached = getattr(is_ytdlp_available, "_cached", None)
    if cached and time.time() - cached[1] < 60:
        return cached[0]
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "yt_dlp", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        ok = proc.returncode == 0
    except Exception:
        ok = False
    is_ytdlp_available._cached = (ok, time.time())
    return ok


def is_gallery_dl_available() -> bool:
    """True when ``python -m gallery_dl`` runs on this host (cached 60s)."""
    cached = getattr(is_gallery_dl_available, "_cached", None)
    if cached and time.time() - cached[1] < 60:
        return cached[0]
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "gallery_dl", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        ok = proc.returncode == 0
    except Exception:
        ok = False
    is_gallery_dl_available._cached = (ok, time.time())
    return ok


url_download_worker_instance = URLDownloadWorker()
