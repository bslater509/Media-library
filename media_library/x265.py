"""In-place H.265 (HEVC) re-encoding.

A serial queue (one libx265 process at a time), live progress from ffmpeg's
``-progress pipe:1``, per-job cancel, and an atomic commit that rewrites the
index and drops the now-stale poster. Fixed defaults: preset medium at
CRF 18 ("no visible loss"), audio stream-copied when MP4-compatible, AAC 192k
otherwise, ``hvc1`` tag so Safari plays it.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import time
import uuid
from collections import deque
from typing import Any

from media_library import writes
from media_library.cache import animated_path, thumbnail_path
from media_library.config import get_settings
from media_library.db import reader, writer
from media_library.paths import format_size, resolve_library_path

_MP4_AUDIO = {"aac", "mp3", "ac3", "eac3", "alac"}
_HEVC = {"hevc", "h265"}
_HISTORY = 100


def _public(job: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in job.items() if not key.startswith("_")}


def parse_progress_line(line: str) -> dict[str, str]:
    """ffmpeg's progress stream is ``key=value`` lines; ignore anything else."""
    if "=" not in line:
        return {}
    key, _, value = line.partition("=")
    if key not in {
        "out_time_us",
        "out_time_ms",
        "fps",
        "speed",
        "progress",
        "frame",
        "total_size",
    }:
        return {}
    return {key: value.strip()}


class X265Manager:
    def __init__(self) -> None:
        self.queued: list[dict[str, Any]] = []
        self.active: dict[str, Any] | None = None
        self.history: deque[dict[str, Any]] = deque(maxlen=_HISTORY)
        self._task: asyncio.Task | None = None
        self._wake = asyncio.Event()
        self._process: asyncio.subprocess.Process | None = None

    # -- queue ---------------------------------------------------------

    async def enqueue(self, path: str) -> dict[str, Any]:
        target = resolve_library_path(path)
        if not os.path.isfile(target):
            raise writes.WriteError("Video not found", 404)
        settings = get_settings()
        ext = os.path.splitext(target)[1].lower()
        if ext not in settings.video_exts:
            raise writes.WriteError("Not a video file", 400)
        with reader() as conn:
            row = conn.execute(
                "SELECT video_codec, audio_codec, duration, mtime, size "
                "FROM files WHERE path = ?",
                (target,),
            ).fetchone()
        if row is None:
            raise writes.WriteError("Not indexed", 404)
        if (row["video_codec"] or "").lower() in _HEVC:
            raise writes.WriteError("Already H.265/HEVC — nothing to do", 409)
        if self.active and self.active["source_path"] == target:
            raise writes.WriteError("This file is already converting", 409)
        if any(job["source_path"] == target for job in self.queued):
            raise writes.WriteError("This file is already queued", 409)

        duration = float(row["duration"] or 0)
        if duration <= 0:
            duration = await self._probe_duration(target)

        job = {
            "id": uuid.uuid4().hex[:12],
            "source_path": target,
            "output_path": None,
            "status": "queued",
            "percent": 0.0,
            "fps": "",
            "speed": "",
            "eta": "",
            "error": "",
            "duration": duration,
            "audio_mode": "",
            "created_at": time.time(),
            "started_at": None,
            "finished_at": None,
            "_cancel": False,
            "_size_before": int(row["size"] or 0),
            "_mtime_before": float(row["mtime"] or 0),
        }
        self._gc_temp()
        self.queued.append(job)
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())
        return _public(job)

    async def _probe_duration(self, path: str) -> float:
        """One ffprobe call when the index has not probed this file yet."""
        try:
            process = await asyncio.create_subprocess_exec(
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "json",
                path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await process.communicate()
            import json

            payload = json.loads(stdout.decode("utf-8", "replace") or "{}")
            return float((payload.get("format") or {}).get("duration") or 0)
        except Exception:
            return 0.0

    async def _loop(self) -> None:
        while self.queued:
            job = self.queued.pop(0)
            self.active = job
            try:
                await self._execute(job)
            except Exception as exc:  # never let the loop die
                job["status"] = "failed"
                job["error"] = str(exc) or "Conversion failed"
            finally:
                self.active = None
                job["finished_at"] = job.get("finished_at") or time.time()
                job["_cancel"] = False
                self.history.append(job)

    # -- execution -----------------------------------------------------

    def _command(self, source: str, temp: str, copy_audio: bool) -> list[str]:
        settings = get_settings()
        command = [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            source,
            "-map",
            "0:v:0",
            "-map",
            "0:a?",
            "-c:v",
            "libx265",
            "-preset",
            settings.x265_preset,
            "-crf",
            str(settings.x265_crf),
            "-pix_fmt",
            "yuv420p",
            "-tag:v",
            "hvc1",
        ]
        if copy_audio:
            command += ["-c:a", "copy"]
        else:
            command += ["-c:a", "aac", "-b:a", "192k", "-ac", "2"]
        command += ["-movflags", "+faststart", "-progress", "pipe:1", "-f", "mp4", temp]
        return command

    async def run(
        self, job: dict[str, Any], temp: str, command: list[str]
    ) -> tuple[int, list[str]]:
        """Run ffmpeg; overridable in tests."""
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        self._process = process
        lines: list[str] = []
        try:
            assert process.stdout is not None
            while True:
                raw = await process.stdout.readline()
                if not raw:
                    break
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                lines.append(line)
                if len(lines) > 200:
                    del lines[:100]
                self._apply_progress(job, line)
                if job.get("_cancel"):
                    process.kill()
                    break
        finally:
            self._process = None
        code = await process.wait()
        return code, lines

    def _apply_progress(self, job: dict[str, Any], line: str) -> None:
        values = parse_progress_line(line)
        if not values:
            return
        # ffmpeg's out_time_ms is a legacy name for microseconds (as is
        # out_time_us), so both divide by 1e6 — the current app does the same.
        raw_time = values.get("out_time_us") or values.get("out_time_ms")
        if raw_time:
            try:
                seconds = int(raw_time) / 1_000_000
            except ValueError:
                seconds = 0.0
            if job["duration"] > 0:
                job["percent"] = round(
                    min(99.0, max(0.0, seconds / job["duration"] * 100)), 1
                )
        if values.get("fps"):
            job["fps"] = values["fps"]
        if values.get("speed"):
            job["speed"] = values["speed"]
        if job["duration"] > 0 and job.get("speed") and job["speed"].endswith("x"):
            try:
                factor = float(job["speed"][:-1])
                remaining = max(
                    0.0, (job["duration"] * (1 - job["percent"] / 100)) / factor
                )
                minutes, seconds = divmod(int(remaining), 60)
                job["eta"] = f"{minutes}:{seconds:02d}"
            except (ValueError, ZeroDivisionError):
                pass

    async def _execute(self, job: dict[str, Any]) -> None:
        job["status"] = "running"
        job["started_at"] = time.time()
        settings = get_settings()
        source = job["source_path"]
        with reader() as conn:
            row = conn.execute(
                "SELECT audio_codec FROM files WHERE path = ?", (source,)
            ).fetchone()
        audio = (row["audio_codec"] or "").lower() if row else ""
        copy_audio = audio in _MP4_AUDIO
        job["audio_mode"] = "copy" if copy_audio else "aac"

        settings.transcode_dir.mkdir(parents=True, exist_ok=True)
        temp = settings.transcode_dir / f"h265_{job['id']}.mp4"
        try:
            code, lines = await self.run(
                job, str(temp), self._command(source, str(temp), copy_audio)
            )
            if job.get("_cancel"):
                job["status"] = "cancelled"
                job["error"] = "Cancelled"
                return
            if code != 0 or not temp.is_file() or temp.stat().st_size == 0:
                tail = "\n".join(line for line in lines[-5:] if line).strip()
                job["status"] = "failed"
                job["error"] = tail or f"ffmpeg exited with {code}"
                return

            target, size, mtime = await asyncio.to_thread(
                _commit_output, source, str(temp)
            )
            # Off the event loop: this transaction waits for SQLite's single
            # write lock and would otherwise stall every request (Rule 3).
            await asyncio.to_thread(
                _update_index, job, source, target, size, mtime, audio
            )
            job["output_path"] = target
            job["percent"] = 100.0
            job["status"] = "completed"
        finally:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass

    # -- control -------------------------------------------------------

    def status(self, path: str | None = None) -> dict[str, Any]:
        job = None
        if path:
            if self.active and self.active["source_path"] == path:
                job = _public(self.active)
            elif any(entry["source_path"] == path for entry in self.queued):
                job = _public(
                    next(entry for entry in self.queued if entry["source_path"] == path)
                )
            else:
                for entry in reversed(self.history):
                    if entry["source_path"] == path:
                        job = _public(entry)
                        break
        history = [_public(entry) for entry in reversed(self.history)][:20]
        return {
            "active": _public(self.active) if self.active else None,
            "queued": [_public(entry) for entry in self.queued],
            "job": job,
            "active_count": 1 if self.active else 0,
            "queued_count": len(self.queued),
            "history": history,
        }

    def cancel(self, job_id: str) -> dict[str, Any]:
        if self.active and self.active["id"] == job_id:
            self.active["_cancel"] = True
            process = self._process
            if process is not None:
                try:
                    process.kill()
                except (ProcessLookupError, OSError):
                    pass
            return {"cancelled": job_id}
        for job in list(self.queued):
            if job["id"] == job_id:
                self.queued.remove(job)
                job["status"] = "cancelled"
                job["error"] = "Cancelled"
                job["finished_at"] = time.time()
                self.history.append(job)
                return {"cancelled": job_id}
        raise writes.WriteError("Conversion job not found", 404)

    def _gc_temp(self) -> None:
        root = get_settings().transcode_dir
        if not root.is_dir():
            return
        cutoff = time.time() - 24 * 3600
        for entry in root.glob("h265_*.mp4"):
            try:
                if entry.stat().st_mtime < cutoff:
                    entry.unlink(missing_ok=True)
            except OSError:
                continue


def _commit(temp: str, target: str) -> None:
    target_dir = os.path.dirname(target)
    commit_tmp = os.path.join(target_dir, f".tmp_h265_{uuid.uuid4().hex[:8]}.mp4")
    try:
        try:
            os.replace(temp, target)
        except OSError:
            shutil.copy2(temp, commit_tmp)
            if not os.path.exists(commit_tmp) or os.path.getsize(
                commit_tmp
            ) != os.path.getsize(temp):
                raise OSError("Cross-device x265 copy verification failed")
            os.replace(commit_tmp, target)
            try:
                os.remove(temp)
            except OSError:
                pass
    finally:
        if os.path.exists(commit_tmp):
            try:
                os.remove(commit_tmp)
            except OSError:
                pass


def _commit_output(source: str, temp: str) -> tuple[str, int, int]:
    """Move the encoded file into the library and return ``(path, size, mtime)``.

    Runs in a worker thread: committing across filesystems copies the whole
    clip over the (possibly remote) library mount, which must never block the
    event loop.
    """
    target = source
    if os.path.splitext(source)[1].lower() != ".mp4":
        candidate = os.path.splitext(source)[0] + ".mp4"
        if os.path.exists(candidate):
            candidate = writes.unique_target(
                os.path.dirname(source), os.path.basename(candidate), set()
            )
        target = candidate
    _commit(temp, target)
    if target != source:
        try:
            os.remove(source)
        except OSError:
            pass
    stat = os.stat(target)
    return target, stat.st_size, stat.st_mtime


def _update_index(
    job: dict[str, Any],
    source: str,
    target: str,
    size: int,
    mtime: float,
    audio_codec: str,
) -> None:
    old_thumb = thumbnail_path(source, job["_mtime_before"], job["_size_before"])
    old_anim = animated_path(source, job["_mtime_before"], job["_size_before"])
    with writer() as conn, conn:
        if target != source:
            conn.execute(
                "UPDATE files SET path = ?, name = ?, folder_path = ?, ext = '.mp4' "
                "WHERE path = ?",
                (target, os.path.basename(target), os.path.dirname(target), source),
            )
            conn.execute(
                "UPDATE favorites SET path = ? WHERE path = ?", (target, source)
            )
        conn.execute(
            "UPDATE files SET video_codec = 'hevc', audio_codec = ?, "
            "needs_transcode = 0, has_thumb = 0, has_animated = 0, "
            "quick_hash = NULL, thumb_hash = NULL, size = ?, size_formatted = ?, "
            "mtime = ? WHERE path = ?",
            (
                audio_codec or "aac",
                size,
                format_size(size),
                mtime,
                target,
            ),
        )
        writes.recompute_folder(conn, os.path.dirname(source))
        if target != source and os.path.dirname(target) != os.path.dirname(source):
            writes.recompute_folder(conn, os.path.dirname(target))
    for stale in (old_thumb, old_anim):
        try:
            stale.unlink(missing_ok=True)
        except OSError:
            pass
    if target != source:
        try:
            thumbnail_path(target, mtime, size).unlink(missing_ok=True)
        except OSError:
            pass
