"""URL / playlist downloads.

yt-dlp runs as an isolated subprocess (never imported in-process), with an
automatic gallery-dl fallback for sites yt-dlp cannot handle. Finished files are
imported into the library through the same atomic, indexed path as uploads.

Jobs live in memory: active/queued plus a 500-entry terminal history. A server
restart drops the history and stops running downloads.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import math
import os
import re
import shutil
import sys
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from media_library import writes
from media_library.config import get_settings
from media_library.paths import resolve_library_path

_URL_RE = re.compile(r"^https?://", re.IGNORECASE)
_NA_FIX = re.compile(r'"NA"')
_UNSUPPORTED_HINTS = (
    "Unsupported URL",
    "No video formats found",
    "primarily used for piracy",
    "This website is no longer supported",
)
_GALLERY_DL_HOSTS = ("gofile.io",)
# Longest stretch without a fresh output line before a downloader is killed.
# Progress lines arrive every few seconds while anything is moving, so this
# is an inactivity timeout, not a total download budget.
_OUTPUT_TIMEOUT = 300.0
_AUDIO_EXTS = {".mp3", ".m4a", ".aac", ".opus", ".ogg", ".wav", ".flac"}
_SKIPPED_EXTS = {
    ".json",
    ".nfo",
    ".vtt",
    ".srt",
    ".ass",
    ".ssa",
    ".lrc",
    ".description",
    ".txt",
    ".part",
    ".ytdl",
    ".tmp",
}
_HISTORY_MAX = 500


def _resolve_destination(destination: str) -> str:
    """Validate the folder away from the event loop (a remote-mount stat)."""
    dest = resolve_library_path(destination)
    if not os.path.isdir(dest):
        raise writes.WriteError("Destination folder not found", 404)
    return dest


def _retry_destination(destination: str) -> str:
    """Same validation for a retry, with the original 409 wording."""
    dest = resolve_library_path(destination)
    if not os.path.isdir(dest):
        raise writes.WriteError("The original destination no longer exists", 409)
    return dest


def tools_available() -> dict[str, bool]:
    return {
        "ytdlp": importlib.util.find_spec("yt_dlp") is not None,
        "gallery_dl": importlib.util.find_spec("gallery_dl") is not None,
    }


def parse_progress_line(line: str) -> dict[str, Any] | None:
    """Parse one yt-dlp ``--progress-template`` JSON line."""
    if not line.startswith("{"):
        return None
    try:
        data = json.loads(_NA_FIX.sub("0", line))
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def looks_unsupported(lines: list[str]) -> bool:
    joined = "\n".join(lines[-40:])
    return any(hint in joined for hint in _UNSUPPORTED_HINTS)


def prefers_gallery_dl(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return False
    return any(host == item or host.endswith("." + item) for item in _GALLERY_DL_HOSTS)


def is_media_file(path: Path) -> bool:
    settings = get_settings()
    ext = path.suffix.lower()
    if ext in _SKIPPED_EXTS or path.name.startswith("."):
        return False
    return (
        ext in settings.video_exts or ext in settings.image_exts or ext in _AUDIO_EXTS
    )


def _coerce(value: Any, fallback: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    return fallback if math.isnan(number) else number


def _tail(lines: list[str], count: int = 6) -> str:
    return "\n".join(line for line in lines[-count:] if line).strip()


class DownloadManager:
    """Queue, run and track download jobs on the server's event loop."""

    def __init__(self) -> None:
        self.jobs: dict[str, dict[str, Any]] = {}
        self.history: deque[dict[str, Any]] = deque(maxlen=_HISTORY_MAX)
        self._sem: asyncio.Semaphore | None = None
        self._tasks: set[asyncio.Task] = set()

    # -- queue ---------------------------------------------------------

    def _semaphore(self) -> asyncio.Semaphore:
        if self._sem is None:
            self._sem = asyncio.Semaphore(get_settings().download_concurrency)
        return self._sem

    def _new_job(self, url: str, destination: str) -> dict[str, Any]:
        return {
            "id": uuid.uuid4().hex[:12],
            "url": url,
            "destination": destination,
            "status": "queued",
            "percent": 0.0,
            "downloaded_bytes": 0,
            "total_bytes": 0,
            "speed": "",
            "eta": "",
            "title": "",
            "downloader": "",
            "error": "",
            "files": [],
            "file_progress": [],
            "playlist_index": None,
            "playlist_count": None,
            "created_at": time.time(),
            "started_at": None,
            "finished_at": None,
        }

    async def enqueue(self, urls: list[str], destination: str) -> dict[str, Any]:
        tools = tools_available()
        if not tools["ytdlp"] and not tools["gallery_dl"]:
            raise writes.WriteError(
                "No downloader is installed (yt-dlp or gallery-dl)", 503
            )
        dest = await asyncio.to_thread(_resolve_destination, destination)

        queued: list[dict[str, Any]] = []
        rejected: list[dict[str, str]] = []
        seen: set[str] = set()
        for raw in urls:
            url = (raw or "").strip()
            if not url:
                continue
            if url in seen:
                rejected.append({"url": url, "error": "Duplicate URL"})
                continue
            seen.add(url)
            if not _URL_RE.match(url):
                rejected.append({"url": url, "error": "Not an http(s) URL"})
                continue
            job = self._new_job(url, dest)
            self.jobs[job["id"]] = job
            queued.append(job)

        if not queued:
            raise writes.WriteError("No valid URL to download", 400)

        # The GC sweeps and rmtree's the staging tree: keep it off the loop.
        await asyncio.to_thread(self._gc_staging)
        for job in queued:
            self._spawn(job["id"])
        return {"jobs": [self._public(job) for job in queued], "rejected": rejected}

    def _spawn(self, job_id: str) -> None:
        task = asyncio.create_task(self._execute_job(job_id))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _execute_job(self, job_id: str) -> None:
        job = self.jobs.get(job_id)
        if not job:
            return
        async with self._semaphore():
            if job.get("status") == "cancelled":
                self._finish(job)
                return
            job["status"] = "downloading"
            job["started_at"] = time.time()
            staging = get_settings().download_staging_dir / job["id"]
            staging.mkdir(parents=True, exist_ok=True)
            try:
                await self._download(job, staging)
            except asyncio.CancelledError:
                job["status"] = "cancelled"
                job["error"] = "Cancelled"
                raise
            except Exception as exc:
                job["status"] = "failed"
                job["error"] = str(exc) or "Download failed"
            finally:
                shutil.rmtree(staging, ignore_errors=True)
                self._finish(job)

    async def _download(self, job: dict[str, Any], staging: Path) -> None:
        tools = tools_available()
        downloader = (
            "gallery-dl"
            if prefers_gallery_dl(job["url"]) or not tools["ytdlp"]
            else "yt-dlp"
        )
        if downloader == "gallery-dl" and not tools["gallery_dl"]:
            raise writes.WriteError(
                "This site needs gallery-dl, which is not installed", 503
            )

        job["downloader"] = downloader
        code, lines = await self.run(job, downloader, staging)
        if job.get("_cancel"):
            raise asyncio.CancelledError

        files = list(staging.rglob("*"))
        has_media = any(path.is_file() and is_media_file(path) for path in files)
        if (
            downloader == "yt-dlp"
            and (code != 0 or not has_media)
            and tools["gallery_dl"]
            and (looks_unsupported(lines) or not has_media)
        ):
            job["downloader"] = "gallery-dl"
            job["percent"] = 0.0
            job["title"] = ""
            shutil.rmtree(staging, ignore_errors=True)
            staging.mkdir(parents=True, exist_ok=True)
            code, lines = await self.run(job, "gallery-dl", staging)
            if job.get("_cancel"):
                raise asyncio.CancelledError

        imported = await self.import_staged(staging, job["destination"])
        if not imported:
            detail = _tail(lines) or "The downloader produced no importable media"
            raise RuntimeError(detail)
        job["files"] = imported
        for row in job["file_progress"]:
            row["done"] = True
        job["status"] = "completed"
        job["percent"] = 100.0
        job["finished_at"] = time.time()

    async def run(
        self, job: dict[str, Any], downloader: str, staging: Path
    ) -> tuple[int, list[str]]:
        """Run one downloader, folding its output into the job. Overridable in tests."""
        command = (
            self._ytdlp_command(job["url"], staging)
            if downloader == "yt-dlp"
            else self._gallery_dl_command(job["url"], staging)
        )
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        job["_process"] = process
        lines: list[str] = []
        assert process.stdout is not None
        try:
            while True:
                try:
                    raw = await asyncio.wait_for(
                        process.stdout.readline(), timeout=_OUTPUT_TIMEOUT
                    )
                except asyncio.TimeoutError:
                    # Without this a wedged network fetch holds one of the
                    # DOWNLOAD_WORKER_CONCURRENCY slots forever and the job
                    # sits "downloading" with no recovery but a manual cancel.
                    process.kill()
                    await process.wait()
                    raise RuntimeError(
                        f"{downloader} produced no output for "
                        f"{_OUTPUT_TIMEOUT:g}s: timed out"
                    ) from None
                if not raw:
                    break
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                lines.append(line)
                if len(lines) > 400:
                    del lines[:200]
                self._apply_line(job, line, staging)
                if job.get("_cancel"):
                    process.kill()
                    break
        finally:
            job.pop("_process", None)
        code = await process.wait()
        return code, lines

    async def import_staged(self, staging: Path, destination: str) -> list[str]:
        """Import every media file from the job's staging dir into the library."""
        imported: list[str] = []
        for path in sorted(staging.rglob("*")):
            if not path.is_file() or not is_media_file(path):
                continue
            result = await asyncio.to_thread(
                writes.import_file, str(path), destination, "auto_rename"
            )
            if result.get("target"):
                imported.append(result["target"])
        return imported

    # -- progress ------------------------------------------------------

    def _register_row(
        self,
        job: dict[str, Any],
        name: str,
        percent: float | None,
        downloaded: int | None,
        total: int | None,
        *,
        conclude_others: bool = True,
    ) -> dict[str, Any]:
        """Add or refresh one per-file row; a new name closes the previous file.

        Downloaders work sequentially, so the first sighting of a new
        filename means every earlier file finished. Rows are public state
        (``file_progress``) and survive cancellation, which is what lets the
        client show "stopped after N files" for albums.
        """
        rows = job.setdefault("file_progress", [])
        row = next((entry for entry in rows if entry["name"] == name), None)
        if row is None:
            if conclude_others:
                for other in rows:
                    other["done"] = True
            row = {
                "name": name,
                "done": False,
                "percent": None,
                "downloaded_bytes": None,
                "total_bytes": None,
            }
            rows.append(row)
        if percent is not None:
            row["percent"] = round(percent, 1)
            if percent >= 99.9:
                row["done"] = True
        if downloaded is not None:
            row["downloaded_bytes"] = int(downloaded)
        if total is not None:
            row["total_bytes"] = int(total)
        return row

    def _apply_line(self, job: dict[str, Any], line: str, staging: Path) -> None:
        data = parse_progress_line(line)
        if data is not None:
            if data.get("title"):
                job["title"] = str(data["title"])
            percent: float | None = None
            if data.get("percent") not in (None, "NA"):
                percent = _coerce(data["percent"], 0.0)
                job["percent"] = round(percent, 1)
            downloaded = None
            for key in ("downloaded_bytes", "total_bytes", "total_bytes_estimate"):
                if data.get(key) not in (None, "NA"):
                    value = int(_coerce(data[key], 0))
                    if key == "total_bytes_estimate":
                        if not job["total_bytes"]:
                            job["total_bytes"] = value
                    else:
                        job[key] = value
                    if key == "downloaded_bytes":
                        downloaded = value
            if data.get("speed") not in (None, "NA"):
                job["speed"] = str(data["speed"])
            if data.get("eta") not in (None, "NA"):
                job["eta"] = str(data["eta"])
            if data.get("playlist_index") not in (None, "NA"):
                job["playlist_index"] = data["playlist_index"]
            if data.get("playlist_count") not in (None, "NA"):
                job["playlist_count"] = data["playlist_count"]

            # Album / chunk downloads: one JSON line per file with its own
            # name, percent and sizes. Track rows and restate the job-level
            # numbers so a multi-item job does not bounce 0→100 between items.
            filename = data.get("filename")
            if isinstance(filename, str) and filename:
                total = None
                if data.get("total_bytes") not in (None, "NA"):
                    total = int(_coerce(data["total_bytes"], 0))
                elif data.get("total_bytes_estimate") not in (None, "NA"):
                    total = int(_coerce(data["total_bytes_estimate"], 0))
                self._register_row(
                    job,
                    os.path.basename(filename),
                    percent,
                    downloaded,
                    total,
                )
                known = [
                    entry for entry in job["file_progress"] if entry["total_bytes"]
                ]
                if known:
                    total_known = sum(entry["total_bytes"] for entry in known)
                    done_bytes = sum(
                        entry["total_bytes"]
                        if entry["done"]
                        else (entry["downloaded_bytes"] or 0)
                        for entry in known
                    )
                    job["downloaded_bytes"] = done_bytes
                    job["total_bytes"] = total_known
                    job["percent"] = round(
                        min(100.0, 100.0 * done_bytes / total_known), 1
                    )
            return

        if job["downloader"] == "gallery-dl" and str(staging) in line:
            name = Path(line).name
            job["title"] = name
            seen = job.setdefault("_gallery_seen", set())
            if name in seen:
                # The same path logged twice: the file was started again, i.e.
                # it completed once between the two lines.
                row = next(
                    (entry for entry in job["file_progress"] if entry["name"] == name),
                    None,
                )
                if row is not None:
                    row["done"] = True
            else:
                seen.add(name)
                self._register_row(job, name, None, None, None)
            job["percent"] = min(95.0, round(job["percent"] + 5.0, 1))

    # -- commands ------------------------------------------------------

    def _ytdlp_command(self, url: str, staging: Path) -> list[str]:
        progress = (
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
        command = [
            sys.executable,
            "-m",
            "yt_dlp",
            "--newline",
            "--no-colors",
            "--ignore-errors",
            "--match-filter",
            "!is_live & !is_upcoming",
            "-f",
            "bv*+ba/b",
            "--merge-output-format",
            "mp4",
            "-o",
            str(staging / "%(title).120B [%(id)s].%(ext)s"),
            "--progress-template",
            progress,
        ]
        cookies = get_settings().download_cookies_file
        if cookies and os.path.isfile(cookies):
            command += ["--cookies", cookies]
        command.append(url)
        return command

    def _gallery_dl_command(self, url: str, staging: Path) -> list[str]:
        command = [
            sys.executable,
            "-m",
            "gallery_dl",
            "--dest",
            str(staging),
            "-o",
            "directory=[]",
        ]
        cookies = get_settings().download_cookies_file
        if cookies and os.path.isfile(cookies):
            command += ["--cookies", cookies]
        command.append(url)
        return command

    # -- job control ---------------------------------------------------

    def _finish(self, job: dict[str, Any]) -> None:
        if job.get("_finished"):
            return
        job["_finished"] = True
        job.pop("_cancel", None)
        job.pop("_process", None)
        job["finished_at"] = job.get("finished_at") or time.time()
        if job.get("status") == "downloading":
            job["status"] = "failed"
            job["error"] = job.get("error") or "Download ended unexpectedly"
        self.jobs.pop(job["id"], None)
        self.history.append(job)

    def cancel(self, job_id: str) -> dict[str, Any]:
        job = self.jobs.get(job_id)
        if not job:
            raise writes.WriteError("Download job not found", 404)
        job["_cancel"] = True
        process = job.get("_process")
        if process is not None:
            try:
                process.kill()
            except (ProcessLookupError, OSError):
                pass
        if job["status"] == "queued":
            job["status"] = "cancelled"
            job["error"] = "Cancelled"
            self._finish(job)
        return {"cancelled": job_id}

    async def retry(self, job_id: str) -> dict[str, Any]:
        source = next((job for job in self.history if job["id"] == job_id), None)
        if source is None:
            raise writes.WriteError("Download job not found", 404)
        if source["status"] not in ("failed", "cancelled"):
            raise writes.WriteError("Only failed or cancelled jobs can be retried", 409)
        destination = await asyncio.to_thread(_retry_destination, source["destination"])
        job = self._new_job(source["url"], destination)
        self.jobs[job["id"]] = job
        self._spawn(job["id"])
        return {"job": self._public(job)}

    def clear_history(self) -> dict[str, Any]:
        cleared = len(self.history)
        self.history.clear()
        return {"cleared": cleared}

    def history_page(self, limit: int = 50, offset: int = 0) -> dict[str, Any]:
        items = list(self.history)
        items.reverse()
        total = len(items)
        page = items[offset : offset + limit]
        return {
            "items": [self._public(job) for job in page],
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    def status(self, history_limit: int = 20) -> dict[str, Any]:
        active = [
            self._public(job)
            for job in self.jobs.values()
            if job["status"] == "downloading"
        ]
        queued = [
            self._public(job) for job in self.jobs.values() if job["status"] == "queued"
        ]
        recent = list(self.history)[-history_limit:]
        recent.reverse()
        return {
            "status": "active" if active or queued else "idle",
            "concurrency": get_settings().download_concurrency,
            "tools": tools_available(),
            "active": active,
            "queued": queued,
            "active_count": len(active),
            "queued_count": len(queued),
            "history": [self._public(job) for job in recent],
        }

    async def wait_idle(self) -> None:
        """Await every in-flight job (tests and clean shutdown)."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)
            self._tasks = {task for task in self._tasks if not task.done()}

    def _gc_staging(self) -> None:
        root = get_settings().download_staging_dir
        if not root.is_dir():
            return
        cutoff = time.time() - 24 * 3600
        for entry in root.iterdir():
            try:
                if entry.is_dir() and entry.stat().st_mtime < cutoff:
                    shutil.rmtree(entry, ignore_errors=True)
            except OSError:
                continue

    @staticmethod
    def _public(job: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in job.items() if not key.startswith("_")}
