"""FFmpeg multimedia processing, poster generation, and WebP image optimization."""

import asyncio
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

thumbnail_generation_sem = asyncio.Semaphore(4)


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


def build_live_transcode_command(input_path: str, ss: float = 0.0) -> list[str]:
    """argv for on-the-fly fragmented-MP4 transcoding to pipe:1."""
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
    ]
    if ss and ss > 0:
        cmd.extend(["-ss", str(ss)])
    cmd.extend(
        [
            "-i",
            input_path,
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-tune",
            "zerolatency",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-ac",
            "2",
            "-movflags",
            "frag_keyframe+empty_moov+default_base_moof",
            "-f",
            "mp4",
            "pipe:1",
        ]
    )
    return cmd


async def relay_process_stdout(
    process: asyncio.subprocess.Process,
) -> AsyncIterator[bytes]:
    """Yield stdout in 64 KiB chunks and always terminate the process."""
    try:
        while True:
            chunk = await process.stdout.read(64 * 1024)
            if not chunk:
                break
            yield chunk
    finally:
        try:
            process.terminate()
            await process.wait()
        except (ProcessLookupError, OSError):
            # Process already exited (or pipe gone) on stream end — cleanup
            # is best-effort and must never mask the streamed response.
            pass


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


def get_thumbnail_path_fast(
    file_path: str, mtime: float | None = None, size: int | None = None
) -> Path:
    """Computes thumbnail cache path using stat cache or direct file stat."""
    return get_thumbnail_cache_path(file_path, mtime, size)


async def generate_thumbnail(
    file_path: str,
    is_video: bool,
    update_db: bool = False,
    mtime: float | None = None,
    size: int | None = None,
) -> Path | None:
    """Generates a poster frame thumbnail for video or image using FFmpeg."""
    thumb_path = await asyncio.to_thread(
        get_thumbnail_path_fast, file_path, mtime, size
    )
    if thumb_path.exists() and thumb_path.stat().st_size > 0:
        return thumb_path

    async with thumbnail_generation_sem:
        if thumb_path.exists() and thumb_path.stat().st_size > 0:
            return thumb_path

        temp_thumb = thumb_path.with_suffix(f".{uuid.uuid4().hex[:8]}.tmp.jpg")
        input_path = file_path
        if not os.path.exists(input_path):
            try:
                from media_library.services.zip import (
                    extract_zip_to_cache,
                    resolve_zip_file_path,
                    split_zip_path,
                )

                zip_p, inner_p = split_zip_path(file_path)
                if zip_p and inner_p:
                    resolved = resolve_zip_file_path(file_path)
                    if not resolved:
                        cache_dir = await asyncio.to_thread(extract_zip_to_cache, zip_p)
                        resolved = resolve_zip_file_path(file_path)
                        if not resolved:
                            resolved = str(cache_dir / inner_p)
                    if resolved and os.path.exists(resolved):
                        input_path = resolved
            except Exception:
                pass

        try:
            if is_video:
                cmd = [
                    "nice",
                    "-n",
                    "15",
                    "ffmpeg",
                    "-y",
                    "-loglevel",
                    "error",
                    "-ss",
                    "00:00:01",
                    "-i",
                    input_path,
                    "-an",
                    "-sn",
                    "-dn",
                    "-frames:v",
                    "1",
                    "-update",
                    "1",
                    "-vf",
                    "scale=360:-2:flags=fast_bilinear",
                    "-q:v",
                    "3",
                    str(temp_thumb),
                ]
            else:
                cmd = [
                    "nice",
                    "-n",
                    "15",
                    "ffmpeg",
                    "-y",
                    "-loglevel",
                    "error",
                    "-i",
                    input_path,
                    "-frames:v",
                    "1",
                    "-update",
                    "1",
                    "-vf",
                    "scale=360:-2:flags=fast_bilinear",
                    "-q:v",
                    "3",
                    str(temp_thumb),
                ]

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                await asyncio.wait_for(proc.communicate(), timeout=8.0)
            except asyncio.TimeoutError:
                try:
                    proc.kill()
                    await proc.communicate()
                except Exception:
                    pass

            if is_video and (
                proc.returncode != 0
                or not temp_thumb.exists()
                or temp_thumb.stat().st_size == 0
            ):
                retry_cmd = [
                    "nice",
                    "-n",
                    "15",
                    "ffmpeg",
                    "-y",
                    "-loglevel",
                    "error",
                    "-ss",
                    "00:00:00",
                    "-i",
                    file_path,
                    "-an",
                    "-sn",
                    "-dn",
                    "-frames:v",
                    "1",
                    "-update",
                    "1",
                    "-vf",
                    "scale=360:-2:flags=fast_bilinear",
                    "-q:v",
                    "3",
                    str(temp_thumb),
                ]
                proc = await asyncio.create_subprocess_exec(
                    *retry_cmd,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                try:
                    await asyncio.wait_for(proc.communicate(), timeout=8.0)
                except asyncio.TimeoutError:
                    try:
                        proc.kill()
                        await proc.communicate()
                    except Exception:
                        pass

            if temp_thumb.exists() and temp_thumb.stat().st_size > 0:
                temp_thumb.rename(thumb_path)
                if update_db:

                    def _update_thumb_status():
                        try:
                            with db_conn() as conn, conn:
                                conn.execute(
                                    "UPDATE files SET has_thumb = 1 WHERE path = ?",
                                    (file_path,),
                                )
                        except Exception:
                            pass

                    await asyncio.to_thread(_update_thumb_status)
                return thumb_path
        except Exception as e:
            logger.error(f"Thumbnail generation error for {file_path}: {e}")
        finally:
            if temp_thumb.exists():
                try:
                    temp_thumb.unlink()
                except OSError:
                    pass
    return None


def convert_image_to_webp_bytes(
    source: str | Path | bytes, quality: int | None = None
) -> bytes:
    """Converts an image file path or raw image bytes to WebP bytes."""
    if quality is None:
        quality = WEBP_QUALITY
    if isinstance(source, (str, Path)):
        img = Image.open(str(source))
    else:
        img = Image.open(io.BytesIO(source))

    with img:
        orig_mode = img.mode
        icc_profile = img.info.get("icc_profile")
        exif = img.getexif()

        is_png = getattr(img, "format", None) == "PNG"
        save_kwargs = {
            "format": "WEBP",
            "method": 6,
        }
        if is_png:
            save_kwargs["lossless"] = True
        else:
            save_kwargs["quality"] = quality
            save_kwargs["lossless"] = False
        if icc_profile:
            save_kwargs["icc_profile"] = icc_profile
        if len(exif) > 0:
            save_kwargs["exif"] = exif

        if orig_mode == "CMYK":
            work_img = img.convert("RGB")
        elif orig_mode not in ("RGB", "RGBA", "L", "LA"):
            work_img = img.convert(
                "RGBA"
                if "transparency" in img.info or orig_mode in ("P", "PA")
                else "RGB"
            )
        else:
            work_img = img

        out_io = io.BytesIO()
        work_img.save(out_io, **save_kwargs)
        return out_io.getvalue()


def convert_image_file_to_webp(file_path: str, quality: int | None = None) -> dict:
    """Converts a local image file to modern WebP format with size and safety checks."""
    fpath = Path(file_path).resolve()
    try:
        if not fpath.exists():
            try:
                with db_conn() as conn, conn:
                    conn.execute(
                        "UPDATE files SET webp_optimized = -3 WHERE path = ?",
                        (str(fpath),),
                    )
            except Exception:
                pass
            return {"status": "error", "error": "File not found", "path": str(fpath)}
    except OSError as e:
        try:
            with db_conn() as conn, conn:
                conn.execute(
                    "UPDATE files SET webp_optimized = -3 WHERE path = ?", (str(fpath),)
                )
        except Exception:
            pass
        return {"status": "error", "error": str(e), "path": str(fpath)}

    if quality is None:
        quality = WEBP_QUALITY

    ext = fpath.suffix.lower()
    if ext == ".webp":
        try:
            with db_conn() as conn, conn:
                conn.execute(
                    "UPDATE files SET webp_optimized = 1 WHERE path = ?", (str(fpath),)
                )
        except Exception:
            pass
        return {"status": "already_webp", "path": str(fpath)}
    if ext not in (".jpg", ".jpeg", ".png", ".bmp", ".tiff"):
        try:
            with db_conn() as conn, conn:
                conn.execute(
                    "UPDATE files SET webp_optimized = -2 WHERE path = ?", (str(fpath),)
                )
        except Exception:
            pass
        return {"status": "unsupported", "path": str(fpath)}

    orig_size = fpath.stat().st_size
    temp_name = f"temp_{uuid.uuid4().hex}.webp"
    temp_path = WEBP_STAGING_DIR / temp_name

    try:
        with Image.open(fpath) as img:
            orig_width, orig_height = img.size
            orig_mode = img.mode
            icc_profile = img.info.get("icc_profile")
            exif = img.getexif()

            is_png = ext == ".png"
            save_kwargs = {
                "format": "WEBP",
                "method": 6,
            }
            if is_png:
                save_kwargs["lossless"] = True
            else:
                save_kwargs["quality"] = quality
                save_kwargs["lossless"] = False

            if icc_profile:
                save_kwargs["icc_profile"] = icc_profile
            if len(exif) > 0:
                save_kwargs["exif"] = exif

            if orig_mode == "CMYK":
                work_img = img.convert("RGB")
            elif orig_mode not in ("RGB", "RGBA", "L", "LA"):
                work_img = img.convert(
                    "RGBA"
                    if "transparency" in img.info or orig_mode in ("P", "PA")
                    else "RGB"
                )
            else:
                work_img = img

            work_img.save(temp_path, **save_kwargs)

        with Image.open(temp_path) as v_img:
            v_img.verify()
        with Image.open(temp_path) as v_img:
            v_img.load()
            v_w, v_h = v_img.size
            if v_w != orig_width or v_h != orig_height:
                raise ValueError(
                    f"Dimension mismatch: expected {orig_width}x{orig_height}, got {v_w}x{v_h}"
                )
    except Exception as e:
        temp_path.unlink(missing_ok=True)
        try:
            with db_conn() as conn, conn:
                conn.execute(
                    "UPDATE files SET webp_optimized = -2 WHERE path = ?", (str(fpath),)
                )
        except Exception:
            pass
        return {"status": "error", "error": str(e), "path": str(fpath)}

    webp_size = temp_path.stat().st_size

    if WEBP_SKIP_LARGER and webp_size >= orig_size:
        temp_path.unlink(missing_ok=True)
        try:
            with db_conn() as conn, conn:
                conn.execute(
                    "UPDATE files SET webp_optimized = -1 WHERE path = ?", (str(fpath),)
                )
                conn.execute(
                    "UPDATE webp_stats SET skipped_count = skipped_count + 1 WHERE id = 1"
                )
        except Exception as e:
            logger.error(f"Error updating SQLite index for skipped WebP: {e}")

        return {
            "status": "skipped",
            "reason": "webp_larger_or_equal",
            "path": str(fpath),
            "orig_size": orig_size,
            "webp_size": webp_size,
            "difference": webp_size - orig_size,
        }

    target_path = fpath.with_suffix(".webp")
    if target_path.exists() and target_path != fpath:
        counter = 1
        while (fpath.parent / f"{fpath.stem}_opt{counter}.webp").exists():
            counter += 1
        target_path = fpath.parent / f"{fpath.stem}_opt{counter}.webp"

    # Atomically commit the WebP to its final path: write to a temp file in the
    # same directory first, then os.replace() so an interrupted transfer cannot
    # leave a truncated .webp at the final path (FUSE-safe).
    commit_tmp = target_path.parent / f".tmp_webp_{uuid.uuid4().hex}.webp"
    try:
        shutil.copy2(temp_path, commit_tmp)
        if not commit_tmp.exists() or commit_tmp.stat().st_size != webp_size:
            raise OSError("Target WebP verification failed after copy")
        os.replace(commit_tmp, target_path)
    finally:
        commit_tmp.unlink(missing_ok=True)
        temp_path.unlink(missing_ok=True)

    try:
        fpath.unlink()
    except Exception as e:
        # Roll back the freshly written WebP so we don't leave an orphaned
        # on-disk duplicate while the index still points at the old path.
        target_path.unlink(missing_ok=True)
        logger.error(f"Error: could not unlink original {fpath}: {e}")
        return {
            "status": "error",
            "error": f"Could not remove original after conversion: {e}",
            "path": str(fpath),
        }

    saved_bytes = orig_size - webp_size
    new_mtime = target_path.stat().st_mtime
    new_size_fmt = format_size(webp_size)

    try:
        with db_conn() as conn, conn:
            conn.execute(
                """
                UPDATE files
                SET path = ?, name = ?, ext = '.webp', size = ?, size_formatted = ?, mtime = ?, webp_optimized = 1, has_thumb = 1, quick_hash = NULL, thumb_hash = NULL
                WHERE path = ?
            """,
                (
                    str(target_path),
                    target_path.name,
                    webp_size,
                    new_size_fmt,
                    new_mtime,
                    str(fpath),
                ),
            )
            conn.execute(
                "UPDATE webp_stats SET saved_bytes = saved_bytes + ?, converted_count = converted_count + 1 WHERE id = 1",
                (saved_bytes,),
            )
    except Exception as e:
        logger.error(f"Error updating SQLite index for WebP: {e}")

    try:
        new_thumb = get_thumbnail_cache_path(str(target_path), new_mtime, webp_size)
        if not new_thumb.exists() or new_thumb.stat().st_size == 0:
            with Image.open(target_path) as t_img:
                t_img.thumbnail((360, 360), Image.Resampling.BILINEAR)
                if t_img.mode not in ("RGB", "L"):
                    t_img = t_img.convert("RGB")
                t_img.save(new_thumb, format="JPEG", quality=80)
    except Exception as e:
        logger.error(f"Thumbnail generation error for {target_path}: {e}")

    try:
        old_thumb = get_thumbnail_cache_path(str(fpath))
        if old_thumb.exists() and old_thumb != get_thumbnail_cache_path(
            str(target_path), new_mtime, webp_size
        ):
            old_thumb.unlink(missing_ok=True)
    except Exception:
        pass

    return {
        "status": "converted",
        "orig_path": str(fpath),
        "new_path": str(target_path),
        "orig_size": orig_size,
        "webp_size": webp_size,
        "saved_bytes": saved_bytes,
        "savings_pct": round(saved_bytes / orig_size * 100, 2),
    }
