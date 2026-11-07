"""Metadata probing: dimensions, duration, codecs, fps, capture time.

One FFprobe pass per video and one Pillow open per image. The worker processes
rows whose ``width`` is still NULL, marks failures as ``0 x 0`` so they are not
retried until the file changes, and leaves ZIP members alone (their dimensions
are backfilled from cached posters elsewhere).
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
from datetime import datetime, timezone
from typing import Any

from media_library.config import get_settings
from media_library.db import reader, writer
from media_library.paths import is_zip_member


def _iso_epoch(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return None


def _fraction(value: str | None) -> float | None:
    if not value or value in ("0/0", "N/A"):
        return None
    try:
        if "/" in value:
            numerator, denominator = value.split("/", 1)
            return float(numerator) / float(denominator) if float(denominator) else None
        return float(value)
    except (ValueError, ZeroDivisionError):
        return None


def _probe_video(path: str) -> dict[str, Any] | None:
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-print_format",
                "json",
                "-show_streams",
                "-show_format",
                path,
            ],
            capture_output=True,
            timeout=25,
            check=False,
        )
        if result.returncode != 0:
            return None
        payload = json.loads(result.stdout.decode("utf-8", "replace") or "{}")
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None

    streams = payload.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if video is None:
        return None
    width = int(video.get("width") or 0)
    height = int(video.get("height") or 0)
    rotation = 0
    for side in video.get("side_data_list") or []:
        if "rotation" in side:
            try:
                rotation = int(float(side["rotation"]))
            except (ValueError, TypeError):
                rotation = 0
    if rotation % 180 != 0:
        width, height = height, width
    duration = payload.get("format", {}).get("duration") or video.get("duration")
    try:
        duration = float(duration) if duration is not None else None
    except (ValueError, TypeError):
        duration = None
    capture = _iso_epoch(payload.get("format", {}).get("tags", {}).get("creation_time"))
    return {
        "width": width,
        "height": height,
        "duration": duration,
        "video_codec": video.get("codec_name"),
        "audio_codec": audio.get("codec_name") if audio else None,
        "fps": _fraction(video.get("avg_frame_rate") or video.get("r_frame_rate")),
        "capture_time": capture,
    }


def _probe_image(path: str) -> dict[str, Any] | None:
    try:
        from PIL import Image, ImageOps

        from media_library.pillow_init import register_heif

        register_heif()
        with Image.open(path) as image:
            image = ImageOps.exif_transpose(image)
            width, height = image.size
            capture = None
            try:
                raw = image.getexif().get(36867)  # DateTimeOriginal
                if raw:
                    capture = (
                        datetime.strptime(str(raw), "%Y:%m:%d %H:%M:%S")
                        .replace(tzinfo=timezone.utc)
                        .timestamp()
                    )
            except Exception:
                capture = None
        return {
            "width": int(width),
            "height": int(height),
            "duration": None,
            "video_codec": None,
            "audio_codec": None,
            "fps": None,
            "capture_time": capture,
        }
    except Exception:
        return None


def probe_media(path: str) -> dict[str, Any] | None:
    settings = get_settings()
    ext = os.path.splitext(path)[1].lower()
    if ext in settings.video_exts:
        return _probe_video(path)
    if ext in settings.image_exts:
        return _probe_image(path)
    return None


def probe_batch(limit: int = 20) -> dict[str, Any]:
    with reader() as conn:
        rows = conn.execute(
            "SELECT path, type FROM files WHERE width IS NULL "
            "AND type IN ('video', 'image') ORDER BY path LIMIT ?",
            (limit,),
        ).fetchall()
    probed = 0
    failed = 0
    for row in rows:
        path = row["path"]
        if is_zip_member(path):
            with writer() as conn, conn:
                conn.execute(
                    "UPDATE files SET width = 0, height = 0 WHERE path = ?", (path,)
                )
            failed += 1
            continue
        result = probe_media(path)
        with writer() as conn, conn:
            if result is None:
                conn.execute(
                    "UPDATE files SET width = 0, height = 0 WHERE path = ?", (path,)
                )
                failed += 1
            else:
                conn.execute(
                    "UPDATE files SET width = ?, height = ?, duration = ?, "
                    "video_codec = ?, audio_codec = ?, fps = ?, capture_time = ? "
                    "WHERE path = ?",
                    (
                        result["width"],
                        result["height"],
                        result["duration"],
                        result["video_codec"],
                        result["audio_codec"],
                        result["fps"],
                        result["capture_time"],
                        path,
                    ),
                )
                probed += 1
    with reader() as conn:
        remaining = conn.execute(
            "SELECT COUNT(*) AS n FROM files WHERE width IS NULL "
            "AND type IN ('video', 'image')"
        ).fetchone()["n"]
    return {"probed": probed, "failed": failed, "remaining": remaining or 0}


class MetadataWorker:
    def __init__(self, batch: int = 20) -> None:
        self.batch = batch
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="metadata-probe", daemon=True
        )
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                result = probe_batch(self.batch)
            except Exception:
                result = {"remaining": 0}
            if not result.get("remaining"):
                if self._stop.wait(5):
                    break
            elif self._stop.wait(0.2):
                break

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
