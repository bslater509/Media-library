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
from pathlib import Path
from typing import Any

from media_library import activity, writes
from media_library.cache import animated_path, thumbnail_path
from media_library.db import DatabaseReadOnlyError, reader, writer, writes_available
from media_library.paths import format_size, is_zip_member, resolve_library_path
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
        }
        self._preview: dict[str, Any] | None = None
        self._start_lock = asyncio.Lock()

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
            }
            self._task = asyncio.create_task(self._batch(preview["paths"]))
            return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        return {**self.state, "errors": list(self.state["errors"])}

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

    async def shutdown(self) -> None:
        self.cancel_batch()
        if self._task:
            await self._task

    async def _batch(self, paths: list[str]) -> None:
        try:
            for path in paths:
                if not await activity.wait_turn_async(
                    "web-mp4", lambda: self.state["cancel_requested"]
                ):
                    break
                if self.state["cancel_requested"]:
                    break
                self.state.update(current=path, phase="probing", percent=0.0)
                try:
                    result = await self._convert(path)
                    self.state[result] += 1
                except Exception as exc:
                    if self.state["cancel_requested"]:
                        break
                    self.state["failed"] += 1
                    self.state["errors"] = (
                        self.state["errors"] + [{"path": path, "error": str(exc)}]
                    )[-100:]
                finally:
                    self.active = None
                self.state["processed"] += 1
        finally:
            self.state.update(
                status="cancelled" if self.state["cancel_requested"] else "completed",
                current=None,
                phase="",
                percent=0.0,
            )

    async def _convert(self, path: str) -> str:
        source = await asyncio.to_thread(resolve_library_path, path)
        if is_zip_member(source):
            raise writes.WriteError("Unpack archive members first")
        if not writes_available():
            raise DatabaseReadOnlyError("Writes are disabled")
        before = await asyncio.to_thread(_fingerprint, source)
        info = await asyncio.to_thread(probe, source)
        if self.state["cancel_requested"]:
            raise writes.WriteError("Cancelled")
        target = str(Path(source).with_suffix(".mp4"))
        if (
            source.lower().endswith(".mp4")
            and compatible(info)
            and "mp4" in info["format"]
        ):
            return "skipped"
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
            self.state["phase"] = "verifying"
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
            return "converted"
        finally:
            await asyncio.to_thread(Path(temp).unlink, missing_ok=True)

    def _apply_progress(self, job: dict[str, Any], line: str) -> None:
        super()._apply_progress(job, line)
        self.state["percent"] = job["percent"]
