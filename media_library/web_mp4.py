"""Explicit, serial library-wide H.264/AAC MP4 replacement.

Discovery reads only SQLite. Remote probes, verification and filesystem commits
run off-loop. Originals survive every failed conversion or index transaction.
"""

from __future__ import annotations

import asyncio
import ctypes
import json
import os
import subprocess
import time
import uuid
from collections import Counter
from pathlib import Path
from typing import Any

from media_library import activity, writes
from media_library.cache import animated_path, thumbnail_path
from media_library.config import get_settings
from media_library.db import DatabaseReadOnlyError, reader, writer, writes_available
from media_library.paths import format_size, is_zip_member, resolve_library_path
from media_library.schemas import WebMP4Status
from media_library.x265 import X265Manager


def probe(path: str) -> dict[str, Any]:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            path,
        ],
        capture_output=True,
        timeout=60,
        check=True,
    )
    payload = json.loads(result.stdout)
    videos = [
        stream
        for stream in payload.get("streams", [])
        if stream.get("codec_type") == "video"
        and not stream.get("disposition", {}).get("attached_pic")
    ]
    if len(videos) != 1:
        raise writes.WriteError("Expected exactly one video stream; original preserved")
    video = videos[0]
    duration = float(
        payload.get("format", {}).get("duration") or video.get("duration") or 0
    )
    if duration <= 0:
        raise writes.WriteError("Cannot verify video duration; original preserved")
    return {
        "video": video,
        "audio": [
            stream
            for stream in payload.get("streams", [])
            if stream.get("codec_type") == "audio"
        ],
        "duration": duration,
        "format": payload.get("format", {}).get("format_name", ""),
    }


def compatible(info: dict[str, Any]) -> bool:
    video = info["video"]
    return (
        video.get("codec_name") == "h264"
        and video.get("pix_fmt") == "yuv420p"
        and all(
            audio.get("codec_name") == "aac" and 0 < audio.get("channels", 0) <= 2
            for audio in info["audio"]
        )
    )


def command(source: str, temp: str, info: dict[str, Any]) -> list[str]:
    video = info["video"]
    copy_video = video.get("codec_name") == "h264" and video.get("pix_fmt") == "yuv420p"
    copy_audio = all(
        a.get("codec_name") == "aac" and 0 < a.get("channels", 0) <= 2
        for a in info["audio"]
    )
    args = [
        "ffmpeg",
        "-n",
        "-hide_banner",
        "-loglevel",
        "error",
        "-xerror",
        "-i",
        source,
        "-map",
        f"0:{video['index']}",
        "-map",
        "0:a?",
        "-map_metadata",
        "0",
        "-c:v",
    ]
    args += (
        ["copy"]
        if copy_video
        else [
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-vf",
            "scale=ceil(iw/2)*2:ceil(ih/2)*2",
        ]
    )
    args += (
        ["-c:a", "copy"] if copy_audio else ["-c:a", "aac", "-b:a", "192k", "-ac", "2"]
    )
    return args + ["-movflags", "+faststart", "-progress", "pipe:1", "-f", "mp4", temp]


def _fingerprint(path: str) -> tuple[int, int]:
    stat = os.stat(path)
    return stat.st_size, stat.st_mtime_ns


def _install(temp: str, target: str) -> None:
    """Atomic no-clobber rename; fail closed on filesystems lacking support."""
    libc = ctypes.CDLL(None, use_errno=True)
    rename = getattr(libc, "renameat2", None)
    if rename is None:
        # Portable no-clobber fallback, only for filesystems with hard links.
        os.link(temp, target)
        os.unlink(temp)
    elif rename(-100, os.fsencode(temp), -100, os.fsencode(target), 1) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), target)


def commit(
    source: str, target: str, temp: str, before: tuple[int, int], info: dict[str, Any]
) -> None:
    """Filesystem first, SQL second, with the original retained for rollback.

    A crash can leave a .mllibtmp-* backup; it is deliberately never GC'd.
    No remote I/O happens inside the SQL transaction.
    """
    if not writes_available():
        raise DatabaseReadOnlyError("Writes are disabled")
    if _fingerprint(source) != before:
        raise writes.WriteError(
            "Source changed during conversion; original preserved", 409
        )
    if source != target and os.path.lexists(target):
        raise writes.WriteError("MP4 filename already exists; original preserved", 409)
    with reader() as conn:
        old = conn.execute("SELECT * FROM files WHERE path = ?", (source,)).fetchone()
        conflict = conn.execute(
            "SELECT 1 FROM files WHERE path = ? AND path != ?", (target, source)
        ).fetchone()
    if old is None or conflict:
        raise writes.WriteError(
            "Index changed or target already indexed; original preserved", 409
        )
    backup = str(Path(source).parent / f".mllibtmp-{uuid.uuid4().hex}.original")
    os.replace(source, backup)
    installed = False
    try:
        # Unlike exists()+replace(), this cannot overwrite a concurrently
        # created destination (including an upload or another converter).
        _install(temp, target)
        installed = True
        stat = os.stat(target)
        video = info["video"]
        with writer() as conn, conn:
            updated = conn.execute(
                "UPDATE files SET path=?, name=?, ext='.mp4', size=?, size_formatted=?, "
                "mtime=?, video_codec='h264', audio_codec=?, duration=?, width=?, height=?, "
                "needs_transcode=0, has_thumb=0, has_animated=0, thumb_mtime=NULL, "
                "quick_hash=NULL, thumb_hash=NULL WHERE path=? AND size=? AND mtime=?",
                (
                    target,
                    os.path.basename(target),
                    stat.st_size,
                    format_size(stat.st_size),
                    stat.st_mtime,
                    "aac" if info["audio"] else None,
                    info["duration"],
                    video["width"],
                    video["height"],
                    source,
                    old["size"],
                    old["mtime"],
                ),
            )
            if updated.rowcount != 1:
                raise writes.WriteError("Index changed during conversion", 409)
            for table in ("favorites", "watch_history", "visual_frames", "file_issues"):
                conn.execute(
                    f"UPDATE {table} SET path=? WHERE path=?", (target, source)
                )
            conn.execute("DELETE FROM file_issues WHERE path=?", (target,))
            conn.execute(
                "DELETE FROM ignored_duplicates WHERE path1=? OR path2=?",
                (source, source),
            )
            writes.recompute_folder(conn, os.path.dirname(source))
    except BaseException:
        if installed:
            os.unlink(target)
        os.replace(backup, source)
        raise
    # An unlink failure must not roll back an already committed database.
    try:
        os.unlink(backup)
    except OSError:
        pass
    for stale in (
        thumbnail_path(source, old["mtime"], old["size"]),
        animated_path(source, old["mtime"], old["size"]),
    ):
        try:
            stale.unlink(missing_ok=True)
        except OSError:
            pass
    from media_library.dedupe import invalidate_merged_cache

    invalidate_merged_cache()


_CHECKERS = 4
_SAVE_INTERVAL = 2.0


def save_state(payload: dict[str, Any]) -> None:
    """Only local runtime state: atomic replacement, never the library mount."""
    path = get_settings().data_dir / "web_mp4_state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
    try:
        with temp.open("x", encoding="utf-8") as handle:
            json.dump(payload, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def load_state() -> dict[str, Any] | None:
    path = get_settings().data_dir / "web_mp4_state.json"
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    state = WebMP4Status.model_validate(payload["state"]).model_dump()
    payload["state"] = state
    remaining = payload["remaining"]
    if (
        payload["version"] not in (1, 2)
        or payload["library_root"] != str(get_settings().library_dir)
        or not isinstance(remaining, list)
        or not all(isinstance(path, str) for path in remaining)
        or not isinstance(payload["cache"], dict)
        or any(
            not isinstance(state[key], int) or state[key] < 0
            for key in ("total", "processed", "converted", "skipped", "failed")
        )
        or state["processed"] + len(remaining) != state["total"]
        or state["converted"] + state["skipped"] + state["failed"] != state["processed"]
    ):
        raise ValueError("Invalid MP4 resume state or library root mismatch")
    unchecked = (
        payload["remaining"] if payload["version"] == 1 else payload["unchecked"]
    )
    ready = [] if payload["version"] == 1 else payload["ready"]
    if (
        not isinstance(unchecked, list)
        or not isinstance(ready, list)
        or not all(isinstance(path, str) for path in unchecked + ready)
        or Counter(unchecked + ready) != Counter(remaining)
    ):
        raise ValueError("Invalid MP4 checking/conversion queues")
    payload.update(unchecked=unchecked, ready=ready)
    state.update(
        checked=state["total"] - len(unchecked),
        unchecked=len(unchecked),
        conversion_pending=len(ready),
    )
    return payload


class WebMP4Manager(X265Manager):
    """Reuse only FFmpeg progress/process handling, not the HEVC commit pipeline."""

    def __init__(self) -> None:
        super().__init__()
        self.state: dict[str, Any] = {
            "status": "idle",
            "total": 0,
            "processed": 0,
            "converted": 0,
            "skipped": 0,
            "failed": 0,
            "current": None,
            "percent": 0.0,
            "phase": "",
            "errors": [],
            "cancel_requested": False,
            "checking": [],
            "checker_concurrency": _CHECKERS,
            "cached": 0,
            "remaining": 0,
            "persistence_error": None,
        }
        self._preview: dict[str, Any] | None = None
        self._start_lock = asyncio.Lock()
        self._save_lock = asyncio.Lock()
        self._remaining: list[str] = []
        self._unchecked: list[str] = []
        self._ready: list[str] = []
        self._prepared: dict[str, dict[str, Any]] = {}
        self._cache: dict[str, Any] = {}
        self._last_save = 0.0
        self._shutdown = False

    async def _persist(self, *, force: bool = False) -> None:
        async with self._save_lock:
            await self._persist_locked(force=force)

    async def _persist_locked(self, *, force: bool) -> None:
        if not force and time.monotonic() - self._last_save < _SAVE_INTERVAL:
            return
        payload = {
            "version": 2,
            "library_root": str(get_settings().library_dir),
            "state": self.snapshot(),
            "remaining": list(self._remaining),
            "cache": dict(self._cache),
            "unchecked": list(self._unchecked),
            "ready": list(self._ready),
        }
        payload["state"]["persistence_error"] = None
        try:
            saving = asyncio.create_task(asyncio.to_thread(save_state, payload))
            try:
                await asyncio.shield(saving)
            except asyncio.CancelledError:
                # Keep the save lock until the thread has actually finished:
                # an older checkpoint must never overwrite the final cancel.
                await saving
                raise
        except OSError as exc:
            self.state["persistence_error"] = "Cannot save MP4 resume state"
            raise writes.WriteError(self.state["persistence_error"], 503) from exc
        self._last_save = time.monotonic()
        self.state["persistence_error"] = None

    async def restore(self) -> dict[str, Any]:
        """Auto-resume only previously running/cleanly shutdown batches."""
        async with self._start_lock:
            try:
                payload = await asyncio.to_thread(load_state)
            except (OSError, ValueError, KeyError, TypeError):
                self.state.update(
                    status="error", persistence_error="Cannot read MP4 resume state"
                )
                return self.snapshot()
            if payload is None:
                return self.snapshot()
            self._remaining = payload["remaining"]
            self._unchecked = payload["unchecked"]
            self._ready = payload["ready"]
            self._prepared = {}
            self._cache = payload["cache"]
            self.state.update(payload["state"], current=None, checking=[], percent=0.0)
            if self.state["status"] == "running" and self.state["cancel_requested"]:
                self.state["status"] = "cancelled"
            if self._remaining and self.state["status"] in ("running", "paused"):
                self.state["status"] = "paused"
                if writes_available():
                    await self._launch()
            return self.snapshot()

    async def _launch(self) -> None:
        self._shutdown = False
        self.state.update(
            status="running", cancel_requested=False, current=None, phase="checking"
        )
        # Refuse to start destructive work if we cannot save its confirmed queue.
        try:
            await self._persist(force=True)
        except writes.WriteError:
            self.state["status"] = "error"
            raise
        self._task = asyncio.create_task(self._batch(list(self._remaining)))

    async def resume(self) -> dict[str, Any]:
        async with self._start_lock:
            if not writes_available():
                raise DatabaseReadOnlyError("Writes are disabled")
            if self._task and not self._task.done():
                raise writes.WriteError("An MP4 batch is already running", 409)
            if not self._remaining:
                raise writes.WriteError("No unfinished MP4 batch", 409)
            await self._launch()
            return self.snapshot()

    def preview(self) -> dict[str, Any]:
        with reader() as conn:
            rows = conn.execute(
                "SELECT path, ext, video_codec, audio_codec FROM files "
                "WHERE type='video' ORDER BY path"
            ).fetchall()
        loose = [dict(row) for row in rows if not is_zip_member(row["path"])]
        token = uuid.uuid4().hex
        self._preview = {
            "token": token,
            "paths": [r["path"] for r in loose],
            "time": time.time(),
        }
        return {
            "token": token,
            "total": len(loose),
            "archives": len(rows) - len(loose),
            "sample": loose[:50],
        }

    async def start(self, token: str) -> dict[str, Any]:
        async with self._start_lock:
            if not writes_available():
                raise DatabaseReadOnlyError("Writes are disabled")
            if self._task and not self._task.done():
                raise writes.WriteError("An MP4 batch is already running", 409)
            preview = self._preview
            if (
                not preview
                or preview["token"] != token
                or time.time() - preview["time"] > 900
            ):
                raise writes.WriteError(
                    "Preview expired; preview the library again", 409
                )
            self._preview = None
            self.state = {
                "status": "running",
                "total": len(preview["paths"]),
                "processed": 0,
                "converted": 0,
                "skipped": 0,
                "failed": 0,
                "current": None,
                "percent": 0.0,
                "phase": "queued",
                "errors": [],
                "cancel_requested": False,
                "checking": [],
                "checker_concurrency": _CHECKERS,
                "cached": 0,
                "remaining": len(preview["paths"]),
                "persistence_error": None,
            }
            self._remaining = list(preview["paths"])
            self._unchecked = list(preview["paths"])
            self._ready = []
            self._prepared = {}
            await self._launch()
            return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        return {
            **self.state,
            "errors": list(self.state["errors"]),
            "checking": list(self.state["checking"]),
            "remaining": len(self._remaining),
            "checked": self.state["total"] - len(self._unchecked),
            "unchecked": len(self._unchecked),
            "conversion_pending": len(self._ready),
        }

    def cancel_batch(self) -> dict[str, Any]:
        self.state["cancel_requested"] = True
        if self.active and self.state["phase"] != "committing":
            self.active["_cancel"] = True
            if self._process and self._process.returncode is None:
                try:
                    self._process.kill()
                except ProcessLookupError:
                    pass
        return self.snapshot()

    async def request_cancel(self) -> dict[str, Any]:
        self.cancel_batch()
        await self._persist(force=True)
        return self.snapshot()

    async def shutdown(self) -> None:
        self._shutdown = True
        self.cancel_batch()
        if self._task:
            await self._task

    def _finish(self, path: str, outcome: str, error: Exception | None = None) -> None:
        self.state[outcome] += 1
        self.state["processed"] += 1
        self._remaining.remove(path)
        if error:
            self.state["errors"] = (
                self.state["errors"] + [{"path": path, "error": str(error)}]
            )[-100:]

    @staticmethod
    def _can_skip(prepared: dict[str, Any]) -> bool:
        return (
            prepared["source"].lower().endswith(".mp4")
            and compatible(prepared["info"])
            and "mp4" in prepared["info"]["format"]
        )

    async def _batch(self, paths: list[str]) -> None:
        # Four checkers can finish the entire confirmed library independently
        # of a slow encode. Only paths/metadata accumulate, never extra encoders.
        self._remaining = list(paths)
        if not self._unchecked and not self._ready:
            self._unchecked = list(paths)
        pending = iter(list(self._unchecked))
        changed = asyncio.Event()

        def stopping() -> bool:
            return bool(
                self.state["cancel_requested"] or self.state["persistence_error"]
            )

        async def checker() -> None:
            try:
                for path in pending:
                    if not await activity.wait_turn_async("web-mp4", stopping):
                        break
                    if stopping():
                        break
                    self.state["checking"].append(path)
                    try:
                        prepared = await self._check(path)
                        if stopping():
                            break
                        skip = self._can_skip(prepared)
                        cached = int(prepared["cached"])
                        self._unchecked.remove(path)
                        self.state["cached"] += cached
                        if skip:
                            self._finish(path, "skipped")
                        else:
                            self._prepared[path] = prepared
                            self._ready.append(path)
                    except Exception as exc:
                        if stopping():
                            break
                        self._unchecked.remove(path)
                        self._finish(path, "failed", exc)
                    finally:
                        self.state["checking"].remove(path)
                        changed.set()
                    await self._persist()
            except asyncio.CancelledError:
                return
            finally:
                changed.set()

        async def converter() -> None:
            try:
                while not stopping():
                    if not self._ready:
                        if all(worker.done() for worker in workers):
                            break
                        changed.clear()
                        await changed.wait()
                        continue
                    if not await activity.wait_turn_async("web-mp4", stopping):
                        break
                    if stopping():
                        break
                    path = self._ready[0]  # includes the active file until committed
                    self.state.update(current=path, phase="preparing", percent=0.0)
                    try:
                        outcome = await self._convert(path, self._prepared.get(path))
                        self._finish(path, outcome)
                    except Exception as exc:
                        if stopping():
                            break
                        self._finish(path, "failed", exc)
                    finally:
                        self.active = None
                        self.state.update(current=None, phase="checking", percent=0.0)
                    self._ready.remove(path)
                    self._prepared.pop(path, None)
                    await self._persist()
            finally:
                if stopping():
                    for worker in workers:
                        worker.cancel()

        workers = [asyncio.create_task(checker()) for _ in range(_CHECKERS)]
        conversion = asyncio.create_task(converter())
        try:
            await asyncio.gather(*workers, conversion)
        except writes.WriteError:
            # A failed checkpoint stops both lanes. Let an atomic commit finish;
            # cancel_batch kills FFmpeg only outside that safe boundary.
            self.cancel_batch()
        finally:
            for worker in workers:
                worker.cancel()
            await asyncio.gather(*workers, return_exceptions=True)
            changed.set()
            await asyncio.gather(conversion, return_exceptions=True)
            self.state.update(
                status=(
                    "error"
                    if self.state["persistence_error"]
                    else "paused"
                    if self._shutdown and self._remaining
                    else "cancelled"
                    if self.state["cancel_requested"]
                    else "completed"
                ),
                current=None,
                checking=[],
                phase="",
                percent=0.0,
            )
            try:
                await self._persist(force=True)
            except writes.WriteError:
                self.state["status"] = "error"

    async def _check(self, path: str) -> dict[str, Any]:
        source = await asyncio.to_thread(resolve_library_path, path)
        if await asyncio.to_thread(is_zip_member, source):
            raise writes.WriteError("Unpack archive members first")
        if not writes_available():
            raise DatabaseReadOnlyError("Writes are disabled")
        before = await asyncio.to_thread(_fingerprint, source)
        cached = self._cache.get(source)
        hit = (
            isinstance(cached, dict)
            and cached.get("fingerprint") == list(before)
            and isinstance(cached.get("info"), dict)
            and all(
                key in cached["info"]
                for key in ("video", "audio", "duration", "format")
            )
        )
        info = cached["info"] if hit else await asyncio.to_thread(probe, source)
        self._cache[source] = {"fingerprint": list(before), "info": info}
        return {"source": source, "before": before, "info": info, "cached": hit}

    async def _convert(self, path: str, prepared: dict[str, Any] | None = None) -> str:
        checked = prepared or await self._check(path)
        source, before, info = checked["source"], checked["before"], checked["info"]
        if self.state["cancel_requested"]:
            raise writes.WriteError("Cancelled")
        target = str(Path(source).with_suffix(".mp4"))
        if self._can_skip(checked):
            return "skipped"
        if prepared and await asyncio.to_thread(_fingerprint, source) != before:
            # Recheck only a queued conversion: a compatible skip performs no
            # destructive operation and needs no second remote stat.
            return await self._convert(path)
        if target != source and await asyncio.to_thread(os.path.lexists, target):
            raise writes.WriteError(
                "MP4 filename already exists; original preserved", 409
            )
        temp = str(Path(source).parent / f".mllibtmp-{uuid.uuid4().hex}.mp4")
        job = {
            "duration": info["duration"],
            "percent": 0.0,
            "speed": "",
            "_cancel": False,
        }
        self.active = job
        try:
            self.state["phase"] = "converting"
            code, lines = await self.run(job, temp, command(source, temp, info))
            if self.state["cancel_requested"]:
                raise writes.WriteError("Cancelled")
            if code:
                raise writes.WriteError(
                    "\n".join(lines[-5:]) or "FFmpeg conversion failed"
                )
            job["percent"] = 0.0
            self.state.update(phase="verifying", percent=0.0)
            output = await asyncio.to_thread(probe, temp)
            if (
                not compatible(output)
                or len(output["audio"]) != len(info["audio"])
                or abs(output["duration"] - info["duration"])
                > max(1.0, info["duration"] * 0.01)
            ):
                raise writes.WriteError(
                    "Output verification failed; original preserved"
                )
            # Fully decode the output, not merely its container header.
            code, lines = await self.run(
                job,
                temp,
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-xerror",
                    "-err_detect",
                    "explode",
                    "-i",
                    temp,
                    "-map",
                    "0:v:0",
                    "-map",
                    "0:a?",
                    "-progress",
                    "pipe:1",
                    "-f",
                    "null",
                    "-",
                ],
            )
            if self.state["cancel_requested"]:
                raise writes.WriteError("Cancelled")
            if code:
                raise writes.WriteError(
                    "Output decode verification failed; original preserved"
                )
            self.state["phase"] = "committing"
            await asyncio.to_thread(commit, source, target, temp, before, output)
            self._cache.pop(source, None)
            self._cache[target] = {
                "fingerprint": list(await asyncio.to_thread(_fingerprint, target)),
                "info": output,
            }
            return "converted"
        finally:
            await asyncio.to_thread(Path(temp).unlink, missing_ok=True)

    def _apply_progress(self, job: dict[str, Any], line: str) -> None:
        super()._apply_progress(job, line)
        self.state["percent"] = job["percent"]
