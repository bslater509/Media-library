"""FFprobe metadata extraction (dimensions, codecs, duration, capture time)."""

import asyncio
import base64
import io
import json
import logging
import os
import shutil
import time
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from PIL import Image

from media_library.core.config import (
    WEBP_QUALITY,
    WEBP_SKIP_LARGER,
    WEBP_STAGING_DIR,
)
from media_library.core.constants import IMAGE_EXTENSIONS
from media_library.core.state import (
    format_size,
    get_thumbnail_cache_path,
)
from media_library.db.connection import db_conn

logger = logging.getLogger(__name__)


def format_eta_str(seconds: float) -> str:
    """Formats estimated remaining seconds into human-readable duration."""
    if seconds <= 0:
        return "0s"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h}h {m}m"
    if m > 0:
        return f"{m}m {s}s"
    return f"{s}s"

async def probe_video_meta(file_path: str) -> dict[str, Any]:
    """Probes video duration and codecs using ffprobe."""
    try:
        cmd = [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=codec_name,codec_type",
            "-of",
            "json",
            file_path,
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
        )
        stdout, _ = await proc.communicate()
        if proc.returncode == 0:
            data = json.loads(stdout.decode("utf-8", errors="replace"))
            duration_str = data.get("format", {}).get("duration", "0")
            try:
                duration = float(duration_str)
            except (ValueError, TypeError):
                duration = 0.0
            video_codec = ""
            audio_codec = ""
            for s in data.get("streams", []):
                ctype = s.get("codec_type")
                if ctype == "video" and not video_codec:
                    video_codec = s.get("codec_name", "").lower()
                elif ctype == "audio" and not audio_codec:
                    audio_codec = s.get("codec_name", "").lower()
            return {
                "duration": duration,
                "video_codec": video_codec,
                "audio_codec": audio_codec,
            }
    except Exception as e:
        logger.error(f"Probe error on {file_path}: {e}")
    return {"duration": 0.0, "video_codec": "", "audio_codec": ""}

def _parse_fraction(value: Any) -> float:
    """Parses an ffprobe rational (e.g. ``30000/1001``) into a float."""
    try:
        if value is None or value == "":
            return 0.0
        if isinstance(value, str) and "/" in value:
            num, den = value.split("/", 1)
            den = float(den)
            return round(float(num) / den, 3) if den else 0.0
        return round(float(value), 3)
    except (TypeError, ValueError, ZeroDivisionError):
        return 0.0

def _parse_iso_time(value: Any) -> float | None:
    """Parses an ISO-8601 timestamp (as ffprobe emits) into an epoch float."""
    if not value:
        return None
    try:
        text = str(value).strip().replace("Z", "+00:00")
        from datetime import datetime, timezone

        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except (ValueError, TypeError):
        return None

def _parse_exif_capture_time(path: str) -> float | None:
    """Reads an image's EXIF capture timestamp as an epoch float, if present."""
    try:
        with Image.open(path) as img:
            exif = img.getexif()
            if not exif:
                return None
            # DateTimeOriginal, DateTimeDigitized, DateTime
            for tag in (36867, 36868, 306):
                val = exif.get(tag)
                if not val:
                    continue
                try:
                    return time.mktime(time.strptime(str(val), "%Y:%m:%d %H:%M:%S"))
                except (ValueError, TypeError):
                    continue
    except Exception:
        return None
    return None

async def probe_media_metadata(file_path: str) -> dict[str, Any] | None:
    """Probes display dimensions and media metadata for a video or image.

    Returns a dict with ``width``/``height`` (rotation applied so the values match
    the orientation FFmpeg renders into the poster frame), ``duration``,
    ``video_codec``, ``audio_codec``, ``fps`` and ``capture_time`` (epoch float or
    ``None``). Returns ``None`` when the media cannot be probed at all.
    """
    try:
        cmd = [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration,format_tags=creation_time:stream=width,height,codec_name,codec_type,avg_frame_rate:stream_tags=rotate,creation_time:stream_side_data=rotation",
            "-of",
            "json",
            file_path,
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode != 0:
            return None
        data = json.loads(stdout.decode("utf-8", errors="replace"))
        streams = data.get("streams") or []
        if not streams:
            return None

        video_stream = next(
            (s for s in streams if s.get("codec_type") == "video"), None
        )
        audio_stream = next(
            (s for s in streams if s.get("codec_type") == "audio"), None
        )
        if video_stream is None:
            return None

        try:
            width = int(video_stream.get("width") or 0)
            height = int(video_stream.get("height") or 0)
        except (TypeError, ValueError):
            return None
        if width <= 0 or height <= 0:
            return None

        rotation = 0
        tags = video_stream.get("tags") or {}
        if tags.get("rotate") is not None:
            try:
                rotation = int(float(tags["rotate"]))
            except (TypeError, ValueError):
                rotation = 0
        if rotation == 0:
            for side in video_stream.get("side_data_list") or []:
                if side.get("rotation") is not None:
                    try:
                        rotation = int(float(side["rotation"]))
                    except (TypeError, ValueError):
                        rotation = 0
                    break
        if abs(rotation) % 180 == 90:
            width, height = height, width

        fmt = data.get("format") or {}
        duration = 0.0
        try:
            duration = float(fmt.get("duration") or 0.0)
        except (TypeError, ValueError):
            duration = 0.0

        capture_time = _parse_iso_time((fmt.get("tags") or {}).get("creation_time"))
        if capture_time is None:
            capture_time = _parse_iso_time(tags.get("creation_time"))

        # ffprobe rarely surfaces EXIF date tags; fall back to reading them for images.
        if capture_time is None:
            ext = os.path.splitext(file_path)[1].lower()
            if ext in IMAGE_EXTENSIONS:
                capture_time = await asyncio.to_thread(
                    _parse_exif_capture_time, file_path
                )

        return {
            "width": width,
            "height": height,
            "duration": duration,
            "video_codec": (video_stream.get("codec_name") or "").lower(),
            "audio_codec": ((audio_stream or {}).get("codec_name") or "").lower(),
            "fps": _parse_fraction(video_stream.get("avg_frame_rate")),
            "capture_time": capture_time,
        }
    except Exception as e:
        logger.error(f"Metadata probe error on {file_path}: {e}")
    return None
