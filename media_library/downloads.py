"""URL / playlist downloads.

yt-dlp runs as an isolated subprocess (never imported in-process), with an
automatic gallery-dl fallback for sites yt-dlp cannot handle. Finished files are
imported into the library through the same atomic, indexed path as uploads.

Jobs live in memory: active/queued plus a 500-entry terminal history. The queue
itself is persisted and restored at startup, so a restart resumes it; the
history is still memory-only.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import logging
import math
import os
import re
import shutil
import subprocess
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

logger = logging.getLogger("media_library.downloads")

_URL_RE = re.compile(r"^https?://", re.IGNORECASE)
_NA_FIX = re.compile(r'"NA"')
_UNSUPPORTED_HINTS = (
    "Unsupported URL",
    "No video formats found",
    "primarily used for piracy",
    "This website is no longer supported",
)
_GALLERY_DL_HOSTS = ("gofile.io",)
# Longest stretch without a fresh output line *or* staging-directory growth
# before a downloader is killed. yt-dlp prints progress every few seconds, but
# gallery-dl in pipe mode prints only when a file starts (and that line only
# reaches the pipe when it eagerly flushes), so on a slow CDN a single large
# file can stream for far longer than this without a line. Growing bytes in
# staging count as activity, which keeps that a true inactivity timeout rather
# than a per-file download budget.
_OUTPUT_TIMEOUT = 300.0
# How often the staging directory is weighed while a downloader is silent.
_PROGRESS_POLL = 5.0
# Queued jobs are written to disk so a restart keeps the queue. Progress lines
# arrive faster than that; the coalescing writer bounds the file churn.
_SAVE_INTERVAL = 10.0
# How long a job's download archive is kept after its last write. It is only a
# resume aid, so an unused one can go.
_ARCHIVE_TTL_DAYS = 30
# Largest member list a job will show. A pathological playlist must not grow
# the in-memory status payload without bound.
_MAX_QUEUE_ROWS = 2000
# How long the member listing may take before it is abandoned. It only feeds
# the GUI; the download itself is unaffected.
_LIST_TIMEOUT = 1800.0
# Downloaded containers: kept in <destination>/archives instead of imported.
_ARCHIVE_EXTS = {".rar", ".7z", ".zip"}
_EXTRACTABLE_EXTS = {".rar", ".7z"}
_PART_VOLUME_RE = re.compile(r"\.part0*(\d+)\.rar$", re.IGNORECASE)
_EXTRACT_TIMEOUT = 6 * 3600
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


def _name_too_long(lines: list[str]) -> bool:
    """The downloader hit the filesystem's 255-byte filename cap."""
    return any("File name too long" in line or "Errno 36" in line for line in lines)


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


def is_archive_file(path: Path) -> bool:
    return path.suffix.lower() in _ARCHIVE_EXTS


def is_downloadable_file(path: Path) -> bool:
    return is_media_file(path) or is_archive_file(path)


def _coerce(value: Any, fallback: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    return fallback if math.isnan(number) else number


def _staged_bytes(staging: Path) -> int:
    """Bytes currently written into a job's staging dir.

    Runs on a worker thread while a downloader is silent; a change between two
    samples is proof the subprocess is still moving bytes.
    """
    total = 0
    for root, _dirs, names in os.walk(staging):
        for name in names:
            try:
                total += os.stat(os.path.join(root, name)).st_size
            except OSError:
                # A file can be renamed between listing and stat.
                continue
    return total


def _downloadable_extensions() -> set[str]:
    settings = get_settings()
    return {
        ext.lstrip(".").lower()
        for ext in settings.video_exts | settings.image_exts | _AUDIO_EXTS
    } | {ext.lstrip(".") for ext in _ARCHIVE_EXTS}


def _extension_filter() -> str:
    """gallery-dl ``--filter`` that keeps only files the importer accepts.

    Without it an album like a game rip pulls tens of gigabytes of RAR volumes
    that ``import_staged`` then discards as non-media. Items whose extension is
    unknown stay in the run: gallery-dl resolves those from the response.
    """
    quoted = ", ".join(f"'{ext}'" for ext in sorted(_downloadable_extensions()))
    return f"extension in ({quoted}) or not extension"


def _tail(lines: list[str], count: int = 6) -> str:
    return "\n".join(line for line in lines[-count:] if line).strip()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    """Write a small state file atomically (same directory, then replace)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f"{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(temp, path)


def _read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    return data if isinstance(data, dict) else {}


def _directory_names(path: str) -> set[str]:
    try:
        return set(os.listdir(path))
    except OSError:
        return set()


def _archive_path(url: str, destination: str, downloader: str) -> Path:
    """Resume ledger for one (URL, destination) pair and downloader.

    The key carries the destination because an archive only records that a
    member was fetched, not where it landed: the same album queued into two
    folders must download twice. yt-dlp writes a plain-text list, gallery-dl a
    SQLite database, so they never share a file.
    """
    digest = hashlib.sha256(f"{url}\n{destination}".encode()).hexdigest()[:24]
    suffix = ".db" if downloader == "gallery-dl" else ".txt"
    return get_settings().download_archive_dir / f"{digest}-{downloader}{suffix}"


def _extracted_path(destination: str) -> Path:
    digest = hashlib.sha256(destination.encode()).hexdigest()[:24]
    return get_settings().download_archive_dir / f"extracted-{digest}.json"


def _load_extracted(path: Path) -> set[str]:
    try:
        data = _read_json(path)
    except (OSError, ValueError):
        return set()
    return {entry for entry in data.get("done") or () if isinstance(entry, str)}


def _set_first_part(archives: Path, name: str) -> Path | None:
    """The volume to hand 7z for a set, or None while it has not arrived."""
    match = _PART_VOLUME_RE.search(name)
    if match:
        base = name[: match.start()]
        for digits in ("01", "1", "001"):
            candidate = archives / f"{base}.part{digits}.rar"
            if candidate.exists():
                return candidate
        return None
    if name.lower().endswith((".rar", ".7z")):
        return archives / name
    return None


def _is_first_volume(name: str) -> bool:
    match = _PART_VOLUME_RE.search(name)
    if match:
        return int(match.group(1)) == 1
    return name.lower().endswith((".rar", ".7z"))


def _set_key(name: str) -> str:
    """A volume set's identity: volumes named part1/part01 share one key."""
    match = _PART_VOLUME_RE.search(name)
    if match:
        return name[: match.start()]
    return name


def _store_archive(source: str, destination: str) -> str | None:
    """Move a downloaded container to ``<destination>/archives``.

    A name already there is left alone and the staged copy is dropped, so a
    resumed album does not duplicate or overwrite a finished volume.
    """
    archives = Path(destination) / "archives"
    archives.mkdir(parents=True, exist_ok=True)
    name = Path(source).name
    target = archives / name
    if target.exists():
        Path(source).unlink(missing_ok=True)
        return None
    temp = archives / f".mllibtmp-{uuid.uuid4().hex[:8]}-{name}"
    try:
        shutil.move(source, temp)
        os.replace(temp, target)
    except Exception:
        temp.unlink(missing_ok=True)
        raise
    return str(target)


def _extract_volume(first: Path, destination: str) -> bool:
    """Decompress one complete RAR/7z set into the destination's archives.

    Extracts into a hidden work directory and only moves the results out on
    success, so a set with a missing volume cannot leave half a film in the
    library. The first volume's name marks the set as done; 7z locates the
    sibling volumes itself.
    """
    tool = shutil.which("7z") or shutil.which("7zz") or shutil.which("7za")
    if tool is None:
        logger.warning("[downloads] 7z is not installed: %s stays archived", first)
        return False
    archives = Path(destination) / "archives"
    marker = _extracted_path(destination)
    done = _load_extracted(marker)
    if first.name in done:
        return False
    temp = archives / ".media-library-extracting"
    shutil.rmtree(temp, ignore_errors=True)
    temp.mkdir(parents=True, exist_ok=True)
    try:
        try:
            result = subprocess.run(
                [tool, "x", "-y", "-aoa", f"-o{temp}", str(first)],
                capture_output=True,
                text=True,
                timeout=_EXTRACT_TIMEOUT,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        if result.returncode != 0:
            logger.info(
                "[downloads] %s is not complete yet: %s",
                first.name,
                (result.stderr or result.stdout).strip()[-200:],
            )
            return False
        for entry in sorted(temp.iterdir()):
            target = archives / entry.name
            if entry.is_dir() and target.is_dir():
                shutil.copytree(entry, target, dirs_exist_ok=True)
            else:
                if target.is_dir():
                    shutil.rmtree(target, ignore_errors=True)
                elif target.exists():
                    target.unlink()
                shutil.move(str(entry), str(target))
    finally:
        shutil.rmtree(temp, ignore_errors=True)
    done.add(first.name)
    _write_json(marker, {"done": sorted(done)})
    return True


def _extract_pending(destination: str) -> int:
    """Try every set already sitting in the archives folder (a resumed job)."""
    archives = Path(destination) / "archives"
    if not archives.is_dir():
        return 0
    extracted = 0
    for name in sorted(_directory_names(str(archives))):
        if not _is_first_volume(name):
            continue
        if _extract_volume(archives / name, destination):
            extracted += 1
    return extracted


class _Interrupted(RuntimeError):
    """The downloader was stopped by a signal: keep the job for a resume."""


class DownloadManager:
    """Queue, run and track download jobs on the server's event loop."""

    def __init__(self) -> None:
        self.jobs: dict[str, dict[str, Any]] = {}
        self.history: deque[dict[str, Any]] = deque(maxlen=_HISTORY_MAX)
        self._sem: asyncio.Semaphore | None = None
        self._tasks: set[asyncio.Task] = set()
        self._restored = False
        self._dirty = False
        self._save_task: asyncio.Task | None = None
        self._closing = False

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
            "_imported": set(),
            "_skipped": 0,
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
        self._save_soon()
        return {"jobs": [self._public(job) for job in queued], "rejected": rejected}

    # -- persistence ---------------------------------------------------

    @staticmethod
    def _snapshot(job: dict[str, Any]) -> dict[str, Any]:
        data = DownloadManager._public(job)
        data["imported"] = sorted(job.get("_imported") or ())
        return data

    async def _save(self) -> None:
        payload = {"jobs": [self._snapshot(job) for job in self.jobs.values()]}
        await asyncio.to_thread(
            _write_json, get_settings().download_state_path, payload
        )

    async def _save_loop(self) -> None:
        """Write the queue at most once per interval while it keeps changing."""
        while self._dirty:
            self._dirty = False
            try:
                await self._save()
            except Exception:
                logger.exception("[downloads] Could not write the queue state")
                return
            if self._dirty:
                await asyncio.sleep(_SAVE_INTERVAL)

    def _save_soon(self) -> None:
        if self._closing:
            return
        self._dirty = True
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            # No running loop (a direct call from a test): the next save wins.
            return
        if self._save_task is None or self._save_task.done():
            self._save_task = asyncio.create_task(self._save_loop())

    async def persist(self) -> None:
        """Flush the queue now (shutdown, or a deliberate checkpoint)."""
        if self._save_task is not None and not self._save_task.done():
            self._save_task.cancel()
        self._dirty = False
        try:
            await self._save()
        except Exception:
            logger.exception("[downloads] Could not write the queue state")

    async def shutdown(self) -> None:
        """Stop the downloaders but keep the queue for the next process.

        The queue file is written first, then the subprocesses die with the
        server. Staging is deliberately left in place: a restored job reuses
        its id, so completed members it never imported are picked up on the
        next run (and yt-dlp continues a partial file).
        """
        await self.persist()
        self._closing = True
        for job in list(self.jobs.values()):
            job["_cancel"] = True
            for key in ("_process", "_list_process"):
                process = job.get(key)
                if process is None:
                    continue
                try:
                    process.kill()
                except (ProcessLookupError, OSError):
                    pass
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    async def restore(self) -> int:
        """Reload the queued jobs left by the previous process and start them.

        Only the queue is persisted (it is what survives a restart); finished
        history stays in memory. Members already fetched are skipped by the
        per-job download archive, and the destination's existing names show as
        done in the queue listing, so a restored album resumes where it stopped.
        """
        if self._restored:
            return 0
        self._restored = True
        try:
            payload = await asyncio.to_thread(
                _read_json, get_settings().download_state_path
            )
        except FileNotFoundError:
            return 0
        except (OSError, ValueError):
            logger.exception("[downloads] Ignoring an unreadable queue state")
            return 0

        restored = 0
        for raw in payload.get("jobs") or []:
            if not isinstance(raw, dict):
                continue
            url = raw.get("url")
            destination = raw.get("destination")
            if not isinstance(url, str) or not isinstance(destination, str):
                continue
            job = self._new_job(url, destination)
            if isinstance(raw.get("id"), str) and raw["id"]:
                job["id"] = raw["id"]
            for key in (
                "percent",
                "downloaded_bytes",
                "total_bytes",
                "title",
                "downloader",
                "playlist_index",
                "playlist_count",
                "created_at",
            ):
                if key in raw:
                    job[key] = raw[key]
            job["file_progress"] = [
                row
                for row in raw.get("file_progress") or []
                if isinstance(row, dict) and isinstance(row.get("name"), str)
            ]
            job["status"] = "queued"
            self.jobs[job["id"]] = job
            self._spawn(job["id"])
            restored += 1
        if restored:
            logger.info("[downloads] Restored %d queued job(s)", restored)
        return restored

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
            self._save_soon()
            staging = get_settings().download_staging_dir / job["id"]
            staging.mkdir(parents=True, exist_ok=True)
            try:
                await self._download(job, staging)
            except asyncio.CancelledError:
                job["status"] = "cancelled"
                job["error"] = "Cancelled"
                if not self._closing:
                    await self._salvage(job, staging)
                raise
            except _Interrupted as exc:
                # A signal cut the downloader off (a stop, an OOM kill, an
                # admin kill). There is nothing wrong with the job itself, so
                # it stays queued and keeps its staging for the next run.
                job["status"] = "queued"
                job["error"] = ""
                job["_keep_queued"] = True
                logger.warning("[downloads] Job %s interrupted: %s", job["id"], exc)
            except Exception as exc:
                job["status"] = "failed"
                job["error"] = str(exc) or "Download failed"
                await self._salvage(job, staging)
            finally:
                if job.pop("_keep_queued", False):
                    self._save_soon()
                    self._resume_later(job["id"])
                else:
                    if not self._closing:
                        shutil.rmtree(staging, ignore_errors=True)
                    self._finish(job)

    def _resume_later(self, job_id: str) -> None:
        """Restart an interrupted job after a grace period (unless shutting down)."""
        try:
            asyncio.get_running_loop().call_later(10.0, self._respawn, job_id)
        except RuntimeError:
            pass

    def _respawn(self, job_id: str) -> None:
        job = self.jobs.get(job_id)
        if job is None or job.get("status") != "queued" or self._closing:
            return
        self._spawn(job_id)

    async def _salvage(self, job: dict[str, Any], staging: Path) -> None:
        """Import the whole files a stopped job already fetched.

        A partial download keeps its ``.part`` suffix and is not media, so it
        stays out. Everything complete lands in the library, which keeps a
        retry's cost to the unfinished member.
        """
        try:
            imported, _skipped = await self.import_staged(
                staging, job["destination"], skip=job["_imported"]
            )
        except Exception:
            logger.warning("[downloads] Could not salvage staged files", exc_info=True)
            return
        job["files"] = list(dict.fromkeys(job["files"] + imported))

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
        await asyncio.to_thread(
            _archive_path(job["url"], job["destination"], downloader).parent.mkdir,
            parents=True,
            exist_ok=True,
        )
        code, lines = await self._run_and_list(job, downloader, staging)
        if job.get("_cancel"):
            raise asyncio.CancelledError
        if code < 0:
            raise _Interrupted(f"{downloader} was terminated (signal {-code})")

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
            await asyncio.to_thread(
                _archive_path(
                    job["url"], job["destination"], "gallery-dl"
                ).parent.mkdir,
                parents=True,
                exist_ok=True,
            )
            code, lines = await self._run_and_list(job, "gallery-dl", staging)
            if job.get("_cancel"):
                raise asyncio.CancelledError
            if code < 0:
                raise _Interrupted(f"gallery-dl was terminated (signal {-code})")

        if (
            code != 0
            and job["downloader"] == "gallery-dl"
            and not job.get("_name_retry")
            and _name_too_long(lines)
        ):
            # gallery-dl died because a member's own title is too long for
            # the filesystem (ENAMETOOLONG). Retry the album once with a
            # byte-truncated name template; the download archive keeps the
            # members that landed, so only the rejected ones are refetched.
            job["_name_retry"] = True
            job["percent"] = 0.0
            code, lines = await self._run_and_list(
                job, job["downloader"], staging, name_limit=True
            )
            if job.get("_cancel"):
                raise asyncio.CancelledError
            if code < 0:
                raise _Interrupted(f"gallery-dl was terminated (signal {-code})")

        imported, skipped = await self.import_staged(
            staging, job["destination"], skip=job["_imported"]
        )
        job["files"] = list(dict.fromkeys(job["files"] + imported))
        if job.get("_queue_complete"):
            await self._extract_ready(job)
        else:
            await asyncio.to_thread(_extract_pending, job["destination"])
        if code != 0:
            detail = _tail(lines) or "no output"
            raise RuntimeError(
                f"Download stopped (exit {code}) after "
                f"{len(job['files'])} file(s): {detail}"
            )
        if (
            not job["files"]
            and not imported
            and not skipped
            and not job["_imported"]
            and not job.get("_skipped")
        ):
            detail = _tail(lines) or "The downloader produced no importable media"
            raise RuntimeError(detail)
        # ``skipped`` counts members the destination already contained, so a
        # re-run that lands everything existing is a success with 0 new files
        landed = {Path(path).name for path in imported} | set(job["_imported"])
        for row in job["file_progress"]:
            if row["name"] in landed:
                row["done"] = True
        job["status"] = "completed"
        job["percent"] = 100.0
        job["finished_at"] = time.time()

    async def _run_and_list(
        self,
        job: dict[str, Any],
        downloader: str,
        staging: Path,
        *,
        name_limit: bool = False,
    ) -> tuple[int, list[str]]:
        """Run the downloader, with the album listing running alongside it."""
        listing = (
            asyncio.create_task(self._prelist(job, staging))
            # A name-truncating retry lists nothing: the first pass already
            # filled the queue and truncated names would duplicate the rows.
            if downloader == "gallery-dl" and not name_limit
            else None
        )
        try:
            if name_limit:
                # Only the retry passes this keyword, so test doubles with
                # the plain three-argument signature keep working.
                return await self.run(job, downloader, staging, name_limit=True)
            return await self.run(job, downloader, staging)
        finally:
            if listing is not None:
                listing.cancel()
                await asyncio.gather(listing, return_exceptions=True)

    async def _prelist(self, job: dict[str, Any], staging: Path) -> None:
        """List every album member so the client can show the whole queue.

        ``gallery-dl --simulate`` prints each member's target name as a skip
        line (``# <name>``, bare when the path was never built) without
        downloading anything. Rows are added as they arrive, and names the
        destination already contains are marked done, so a resumed album shows
        what is already in the library. A failure or timeout only costs the
        pending list.
        """
        command = self._gallery_dl_command(job, staging, simulate=True)
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except OSError:
            return
        job["_list_process"] = process
        loop = asyncio.get_running_loop()
        deadline = loop.time() + _LIST_TIMEOUT
        added = 0
        complete = False
        assert process.stdout is not None
        try:
            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    break
                try:
                    raw = await asyncio.wait_for(
                        process.stdout.readline(), timeout=remaining
                    )
                except asyncio.TimeoutError:
                    break
                if not raw:
                    complete = True
                    break
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("# "):
                    continue
                name = Path(line[2:].strip()).name
                if not name or not is_downloadable_file(Path(name)):
                    continue
                if any(row["name"] == name for row in job["file_progress"]):
                    continue
                if any(
                    Path(row["name"]).stem == Path(name).stem
                    for row in job["file_progress"]
                ):
                    # already learned under the name the downloader settled on
                    continue
                if len(job["file_progress"]) >= _MAX_QUEUE_ROWS:
                    continue
                self._register_row(job, name, None, None, None, conclude_others=False)
                added += 1
        finally:
            job.pop("_list_process", None)
            if process.returncode is None:
                process.kill()
            try:
                await process.wait()
            except Exception:
                pass

        if added:
            present = await asyncio.to_thread(_directory_names, job["destination"])
            present |= await asyncio.to_thread(
                _directory_names, str(Path(job["destination"]) / "archives")
            )
            if present:
                stems = {Path(entry).stem for entry in present}
                for row in job["file_progress"]:
                    if row["name"] in present or Path(row["name"]).stem in stems:
                        row["done"] = True
        if complete and process.returncode == 0:
            job["_queue_complete"] = True
            self._refresh_percent(job)
        self._save_soon()

    def _refresh_percent(self, job: dict[str, Any]) -> None:
        """Percent from the known queue once the listing is complete."""
        if not job.get("_queue_complete"):
            return
        rows = job.get("file_progress") or []
        if not rows:
            return
        done = sum(1 for row in rows if row.get("done"))
        job["percent"] = round(min(99.0, 100.0 * done / len(rows)), 1)

    async def run(
        self,
        job: dict[str, Any],
        downloader: str,
        staging: Path,
        *,
        name_limit: bool = False,
    ) -> tuple[int, list[str]]:
        """Run one downloader, folding its output into the job. Overridable in tests."""
        command = (
            self._ytdlp_command(job, staging)
            if downloader == "yt-dlp"
            else self._gallery_dl_command(job, staging, name_limit=name_limit)
        )
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        job["_process"] = process
        lines: list[str] = []
        assert process.stdout is not None
        loop = asyncio.get_running_loop()
        last_activity = loop.time()
        seen_bytes = _staged_bytes(staging)
        try:
            while True:
                try:
                    raw = await asyncio.wait_for(
                        process.stdout.readline(), timeout=_PROGRESS_POLL
                    )
                except asyncio.TimeoutError:
                    written = await asyncio.to_thread(_staged_bytes, staging)
                    now = loop.time()
                    if written != seen_bytes:
                        # Bytes are landing even though nothing was printed.
                        seen_bytes = written
                        last_activity = now
                        continue
                    if now - last_activity < _OUTPUT_TIMEOUT:
                        continue
                    # Without this a wedged network fetch holds one of the
                    # DOWNLOAD_WORKER_CONCURRENCY slots forever and the job
                    # sits "downloading" with no recovery but a manual cancel.
                    process.kill()
                    await process.wait()
                    raise RuntimeError(
                        f"{downloader} produced no output or file growth for "
                        f"{_OUTPUT_TIMEOUT:g}s: timed out"
                    ) from None
                if not raw:
                    break
                last_activity = loop.time()
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                lines.append(line)
                if len(lines) > 400:
                    del lines[:200]
                self._apply_line(job, line, staging)
                if job["downloader"] == "gallery-dl":
                    # Hand finished album members to the library while the
                    # rest is still downloading (yt-dlp stays batch-imported:
                    # its files go through post-processing after 100%).
                    await self._evacuate_done_files(job, staging)
                if job.get("_cancel"):
                    process.kill()
                    break
        except BaseException:
            # The fold itself blew up (or the task was cancelled): never leave
            # the downloader running detached, writing into an orphaned
            # staging directory nobody is reading.
            process.kill()
            await process.wait()
            raise
        finally:
            job.pop("_process", None)
        code = await process.wait()
        logger.info("[downloads] %s exited with code %s", downloader, code)
        return code, lines

    async def _evacuate_done_files(self, job: dict[str, Any], staging: Path) -> None:
        """Import or archive every concluded gallery-dl file in staging.

        Album members arrive one at a time; the downloader prints a member's
        path when it is finished, so the file is closed and safe to move out.
        Media travels to the destination immediately (``writes.import_file``
        moves and indexes it), containers go to ``<destination>/archives`` and
        are extracted when their set is complete, which leaves everything
        visible in the library mid-download. A failure is swallowed and the
        file stays in staging; the end-of-job import retries it.
        """
        done = [
            row["name"]
            for row in job["file_progress"]
            if row["done"] and row["name"] not in job["_imported"]
        ]
        for name in done:
            path = staging / name
            if not path.is_file():
                # A row can be done without a file here: the archive skipped it,
                # or the listing already found it in the destination.
                continue
            if is_archive_file(path):
                try:
                    target = await asyncio.to_thread(
                        _store_archive, str(path), job["destination"]
                    )
                except Exception:
                    return
                job["_imported"].add(name)
                if target:
                    job["files"].append(target)
                await self._extract_ready(job)
                continue
            if not is_media_file(path):
                continue
            source = str(path)
            try:
                result = await asyncio.to_thread(
                    writes.import_file, source, job["destination"], "skip"
                )
            except Exception:
                return
            # missing-but-listed names and files already in the destination
            # both count as seen; only genuinely new members change the count
            job["_imported"].add(name)
            if result.get("target") and not result.get("skipped"):
                job["files"].append(result["target"])
        self._save_soon()

    async def import_staged(
        self, staging: Path, destination: str, skip: set[str] | None = None
    ) -> tuple[list[str], int]:
        """Import media and archive containers from the job's staging dir.

        ``skip`` lists source basenames already handed over by the incremental
        evacuation during the download itself. A media name that already exists
        in the destination is skipped in place, so result[1] counts files the
        destination already contained. Containers move to ``archives``; the
        job's ``_extract_ready`` unpacks complete sets once the listing says no
        more volumes are coming.
        """
        imported: list[str] = []
        skipped = 0
        for path in sorted(staging.rglob("*")):
            if not path.is_file():
                continue
            if skip and path.name in skip:
                continue
            if is_archive_file(path):
                target = await asyncio.to_thread(_store_archive, str(path), destination)
                if target:
                    imported.append(target)
                continue
            if not is_media_file(path):
                continue
            result = await asyncio.to_thread(
                writes.import_file, str(path), destination, "skip"
            )
            if result.get("skipped"):
                skipped += 1
            elif result.get("target"):
                imported.append(result["target"])
        return imported, skipped

    async def _extract_ready(self, job: dict[str, Any]) -> int:
        """Unpack every archive set whose members the queue says are all in.

        A set is only handed to 7z once every volume the listing knows about
        sits in ``archives``. Extracting earlier would decompress the parts
        already present and then fail on the missing one, repeating the whole
        run for each volume that arrives.
        """
        if not job.get("_queue_complete"):
            return 0
        destination = job["destination"]
        archives = Path(destination) / "archives"
        if not archives.is_dir():
            return 0
        present = await asyncio.to_thread(_directory_names, str(archives))
        groups: dict[str, set[str]] = {}
        for row in job["file_progress"]:
            name = row["name"]
            if is_archive_file(Path(name)):
                groups.setdefault(_set_key(name), set()).add(name)
        extracted = 0
        for expected in groups.values():
            if not expected <= present:
                continue
            for name in sorted(expected):
                if not _is_first_volume(name):
                    continue
                first = _set_first_part(archives, name)
                if first is None:
                    continue
                if await asyncio.to_thread(_extract_volume, first, destination):
                    extracted += 1
        return extracted

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
        match_stem: bool = False,
    ) -> dict[str, Any]:
        """Add or refresh one per-file row; a new name closes the previous file.

        Downloaders work sequentially, so the first sighting of a new
        filename means every earlier file finished. Rows are public state
        (``file_progress``) and survive cancellation, which is what lets the
        client show "stopped after N files" for albums.

        ``match_stem`` reconciles a listed member with the name the downloader
        finally chose: gallery-dl fixes an extension from the response
        (``.mp4`` served as ``video/m4v`` lands as ``.m4v``), so the listing
        and the download disagree on the suffix while the stem stays put.
        """
        rows = job.setdefault("file_progress", [])
        row = next((entry for entry in rows if entry["name"] == name), None)
        if row is None and match_stem:
            stem = Path(name).stem
            row = next(
                (entry for entry in rows if Path(entry["name"]).stem == stem),
                None,
            )
            if row is not None:
                row["name"] = name
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
            self._save_soon()
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
            skipped = line.startswith("# ")
            name = Path(line[2:].strip() if skipped else line.strip()).name
            job["title"] = name
            row = self._register_row(
                job, name, None, None, None, conclude_others=False, match_stem=True
            )
            # Pipe output prints a member once it is done (success) or skipped
            # by the archive (# path). A path that is neither is a line from a
            # start message, so it must not be handed to the library yet.
            # gallery-dl also echoes its own failures here — a member whose
            # name exceeds the filesystem's cap makes a plain is_file() raise
            # ENAMETOOLONG on the impossible name, so stat defensively: the
            # member simply never landed.
            try:
                landed = (staging / name).is_file()
            except OSError:
                landed = False
            if skipped or landed:
                row["done"] = True
            if skipped:
                job["_skipped"] = job.get("_skipped", 0) + 1
            if job.get("_queue_complete"):
                self._refresh_percent(job)
            else:
                job["percent"] = min(95.0, round(job["percent"] + 5.0, 1))

    # -- commands ------------------------------------------------------

    def _ytdlp_command(self, job: dict[str, Any], staging: Path) -> list[str]:
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
            "--download-archive",
            str(_archive_path(job["url"], job["destination"], "yt-dlp")),
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
        command.append(job["url"])
        return command

    def _gallery_dl_command(
        self,
        job: dict[str, Any],
        staging: Path,
        *,
        simulate: bool = False,
        name_limit: bool = False,
    ) -> list[str]:
        command = [sys.executable, "-m", "gallery_dl"]
        if simulate:
            command.append("--simulate")
        else:
            command += [
                "--download-archive",
                str(_archive_path(job["url"], job["destination"], "gallery-dl")),
            ]
        command += [
            "--filter",
            _extension_filter(),
            "--dest",
            str(staging),
            "-o",
            "directory=[]",
        ]
        if name_limit:
            # gallery-dl names each member after its own (possibly enormous)
            # title and the filesystem refuses names past 255 bytes. The
            # formatter's byte-bounded slice keeps the name under the cap and
            # passes short names through unchanged; only the ENAMETOOLONG
            # retry passes run with this.
            command += ["-o", "filename={filename[b:180]}.{extension}"]
        cookies = get_settings().download_cookies_file
        if cookies and os.path.isfile(cookies):
            command += ["--cookies", cookies]
        command.append(job["url"])
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
        logger.info(
            "[downloads] Job %s on %s: %s (%s file%s)",
            job["id"],
            job.get("url", "")[:80],
            job.get("status"),
            len(job.get("files") or ()),
            "" if len(job.get("files") or ()) == 1 else "s",
        )
        self._save_soon()

    def cancel(self, job_id: str) -> dict[str, Any]:
        job = self.jobs.get(job_id)
        if not job:
            raise writes.WriteError("Download job not found", 404)
        job["_cancel"] = True
        for process in (job.get("_process"), job.get("_list_process")):
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
        self._save_soon()
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
        if root.is_dir():
            cutoff = time.time() - 24 * 3600
            for entry in root.iterdir():
                try:
                    if entry.is_dir() and entry.stat().st_mtime < cutoff:
                        shutil.rmtree(entry, ignore_errors=True)
                except OSError:
                    continue
        archives = get_settings().download_archive_dir
        if archives.is_dir():
            cutoff = time.time() - _ARCHIVE_TTL_DAYS * 86400
            for entry in archives.iterdir():
                try:
                    if entry.is_file() and entry.stat().st_mtime < cutoff:
                        entry.unlink()
                except OSError:  # pragma: no cover - the entry vanished mid-sweep
                    continue

    @staticmethod
    def _public(job: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in job.items() if not key.startswith("_")}
