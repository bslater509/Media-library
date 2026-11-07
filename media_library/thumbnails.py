"""On-demand poster generation for images whose cache entry is missing.

The poster sweep covers most files, but ZIP members are deliberately skipped by its
thumbnail worker (they are WebP-converted on demand), and a handful of loose
images never got a poster. Generating one here — from the extracted member for
archives — keeps tiles cheap without loading full-resolution originals.
"""

from __future__ import annotations

import os
import time
import uuid
from pathlib import Path
from urllib.parse import quote

from media_library import writes
from media_library.cache import thumbnail_path
from media_library.config import get_settings
from media_library.db import writer
from media_library.paths import is_zip_member
from media_library.zipfs import extract_member, split_zip_path


def mark_has_thumb(path: str, mtime: float, size: int, value: int) -> bool:
    """Set ``has_thumb`` only if the row still matches the poster's key.

    The file can change (or be re-indexed) while the poster is being
    generated; a flag written for the old mtime/size would mark a poster
    that will never exist, so the update silently skips instead. Returns
    whether the row was actually marked.
    """
    with writer() as conn, conn:
        row = conn.execute(
            "SELECT mtime, size FROM files WHERE path = ?", (path,)
        ).fetchone()
        if row is None:
            return False
        if (
            abs(float(row["mtime"] or 0) - mtime) < 1e-9
            and int(row["size"] or 0) == size
        ):
            conn.execute("UPDATE files SET has_thumb = ? WHERE path = ?", (value, path))
            return True
        return False


def generate_thumbnail(path: str, file_type: str, mtime: float, size: int) -> Path:
    """Build a poster for an image or video (ZIP members included)."""
    target = thumbnail_path(path, mtime, size)
    # Marking happens below on both paths, so a poster that exists while the
    # row still says 0 (a crashed run between replace and commit) self-heals
    # instead of leaving the sweep spinning on this row forever.
    if not target.is_file():
        source = path
        if is_zip_member(path):
            split = split_zip_path(path)
            assert split is not None
            archive, inner = split
            if not os.path.isfile(archive):
                raise writes.WriteError("ZIP archive not found", 404)
            source = extract_member(archive, inner)

        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_name(f".tmp_thumb_{uuid.uuid4().hex}.jpg")
        try:
            if file_type == "video":
                _video_poster(source, temp)
            else:
                _image_poster(source, temp)
            if not temp.is_file() or temp.stat().st_size == 0:
                raise writes.WriteError("Could not build a thumbnail", 500)
            os.replace(temp, target)
        finally:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass

    # Only mark 1 when the row still matches the key we built the poster for;
    # a re-indexed file resets the row itself and is retried with fresh values.
    mark_has_thumb(path, mtime, size, 1)
    return target


def _image_poster(source: str, temp: Path) -> None:
    from PIL import Image, ImageOps

    from media_library.pillow_init import register_heif

    register_heif()
    try:
        with Image.open(source) as image:
            image = ImageOps.exif_transpose(image)
            image.thumbnail((360, 360))
            if image.mode not in ("RGB", "L"):
                image = image.convert("RGB")
            image.save(temp, "JPEG", quality=82)
    except OSError as exc:
        raise writes.WriteError(f"Could not build a thumbnail: {exc}", 500) from exc


def _video_poster(source: str, temp: Path) -> None:
    import subprocess

    for seek in ("00:00:01", "00:00:00"):
        command = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            seek,
            "-i",
            source,
            "-vf",
            "scale=360:-2:flags=fast_bilinear",
            "-frames:v",
            "1",
            "-q:v",
            "3",
            "-y",
            str(temp),
        ]
        try:
            subprocess.run(
                command,
                timeout=20,
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except (subprocess.TimeoutExpired, OSError):
            continue
        if temp.is_file() and temp.stat().st_size > 0:
            return
    raise writes.WriteError("Could not extract a poster frame", 500)


# --- poster chooser ---------------------------------------------------------

_FRAME_WIDTH = 480


def _video_row(path: str) -> tuple[float, int, float | None]:
    """Return ``(mtime, size, duration)`` for an indexed video."""
    from media_library.db import reader
    from media_library.paths import resolve_library_path

    # Clamp like every media endpoint: a stale index row from another
    # library root must never turn the frame extractor into an
    # arbitrary-file reader.
    path = resolve_library_path(path)

    with reader() as conn:
        row = conn.execute(
            "SELECT mtime, size, type, duration FROM files WHERE path = ?", (path,)
        ).fetchone()
    if row is None:
        raise writes.WriteError("Not indexed", 404)
    if row["type"] != "video":
        raise writes.WriteError("Poster frames are for videos", 400)
    return (
        float(row["mtime"] or 0),
        int(row["size"] or 0),
        float(row["duration"]) if row["duration"] else None,
    )


def _probe_duration(path: str) -> float | None:
    import subprocess

    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=nw=1:nk=1",
                path,
            ],
            capture_output=True,
            timeout=20,
            check=False,
        )
        return float(result.stdout.decode().strip())
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def _extract_frame(source: str, timestamp: float, target: Path, width: int) -> None:
    import subprocess

    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f".tmp_frame_{uuid.uuid4().hex}.jpg")
    try:
        result = subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-ss",
                f"{max(0.0, timestamp):.3f}",
                "-i",
                source,
                "-vf",
                f"scale={width}:-2:flags=fast_bilinear",
                "-frames:v",
                "1",
                "-q:v",
                "3",
                "-y",
                str(temp),
            ],
            capture_output=True,
            timeout=60,
            check=False,
        )
        if result.returncode != 0 or not temp.is_file() or temp.stat().st_size == 0:
            raise writes.WriteError("Could not extract that frame", 500)
        os.replace(temp, target)
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass


def _video_source(path: str) -> str:
    """A real file ffmpeg can read: ZIP members are extracted to the cache first."""
    if not is_zip_member(path):
        return path
    split = split_zip_path(path)
    assert split is not None
    archive, inner = split
    if not os.path.isfile(archive):
        raise writes.WriteError("ZIP archive not found", 404)
    return extract_member(archive, inner)


def candidates(path: str, interval: float = 30.0, count: int | None = None) -> dict:
    """Evenly spaced candidate timestamps across the clip."""
    _mtime, _size, duration = _video_row(path)
    if duration is None:
        duration = _probe_duration(_video_source(path))
    if not duration or duration <= 0:
        return {"path": path, "duration": 0.0, "interval": interval, "frames": []}
    interval = max(1.0, min(600.0, float(interval)))
    if count is not None:
        wanted = max(1, min(60, int(count)))
    else:
        # At least 8 frames so a short clip still gives a usable chooser;
        # 30s spacing (the current app's default) is the floor above that.
        wanted = max(8, min(60, int(duration // interval) + 1))
    step = duration / wanted
    frames = []
    for index in range(wanted):
        timestamp = round(index * step, 3)
        frames.append(
            {
                "timestamp": timestamp,
                "label": f"{int(timestamp // 60)}:{int(timestamp % 60):02d}",
                "url": (
                    f"/api/thumbnails/frame?path={quote(path)}&timestamp={timestamp}"
                ),
            }
        )
    return {
        "path": path,
        "duration": duration,
        "interval": interval,
        "frames": frames,
    }


def frame(path: str, timestamp: float) -> Path:
    """Extract (or reuse) one 480px candidate frame."""
    mtime, size, _duration = _video_row(path)
    key = thumbnail_path(path, mtime, size).stem.removeprefix("thumb_")
    safe = f"{int(max(0.0, timestamp) * 1000):012d}"
    target = get_settings().thumbnail_dir / "frames" / key / f"frame_{safe}.jpg"
    if target.is_file():
        return target
    _extract_frame(_video_source(path), timestamp, target, _FRAME_WIDTH)
    return target


def set_poster(path: str, timestamp: float) -> dict:
    """Replace the poster with a frame at ``timestamp``."""
    mtime, size, _duration = _video_row(path)
    target = thumbnail_path(path, mtime, size)
    _extract_frame(_video_source(path), timestamp, target, 360)
    stamp = time.time()
    with writer() as conn, conn:
        conn.execute(
            "UPDATE files SET has_thumb = 1, thumb_mtime = ? WHERE path = ?",
            (stamp, path),
        )
    return {"path": path, "timestamp": timestamp, "thumb_mtime": stamp}
