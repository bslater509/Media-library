"""FFmpeg multimedia processing, poster generation, and WebP image optimization."""

import os
import uuid
import json
import io
import shutil
import asyncio
from typing import Optional, Dict, Any, Union
from pathlib import Path
from PIL import Image

from media_library.core.config import (
    WEBP_QUALITY,
    WEBP_SKIP_LARGER,
    WEBP_STAGING_DIR,
)
from media_library.core.state import (
    format_size,
    get_thumbnail_cache_path,
)
from media_library.db.connection import get_db

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

async def probe_video_meta(file_path: str) -> Dict[str, Any]:
    """Probes video duration and codecs using ffprobe."""
    try:
        cmd = [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration:stream=codec_name,codec_type",
            "-of", "json",
            file_path
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL
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
        print(f"Probe error on {file_path}: {e}")
    return {"duration": 0.0, "video_codec": "", "audio_codec": ""}

def get_thumbnail_path_fast(file_path: str, mtime: Optional[float] = None, size: Optional[int] = None) -> Path:
    """Computes thumbnail cache path using stat cache or direct file stat."""
    return get_thumbnail_cache_path(file_path, mtime, size)

async def generate_thumbnail(
    file_path: str,
    is_video: bool,
    update_db: bool = False,
    mtime: Optional[float] = None,
    size: Optional[int] = None
) -> Optional[Path]:
    """Generates a poster frame thumbnail for video or image using FFmpeg."""
    thumb_path = await asyncio.to_thread(get_thumbnail_path_fast, file_path, mtime, size)
    if thumb_path.exists() and thumb_path.stat().st_size > 0:
        return thumb_path

    async with thumbnail_generation_sem:
        if thumb_path.exists() and thumb_path.stat().st_size > 0:
            return thumb_path

        temp_thumb = thumb_path.with_suffix(f".{uuid.uuid4().hex[:8]}.tmp.jpg")
        input_path = file_path
        if not os.path.exists(input_path):
            try:
                from media_library.services.zip_service import resolve_zip_file_path, split_zip_path, extract_zip_to_cache
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
                    "nice", "-n", "15",
                    "ffmpeg", "-y", "-loglevel", "error",
                    "-ss", "00:00:01",
                    "-i", input_path,
                    "-an", "-sn", "-dn",
                    "-frames:v", "1",
                    "-update", "1",
                    "-vf", "scale=360:-2:flags=fast_bilinear",
                    "-q:v", "3",
                    str(temp_thumb)
                ]
            else:
                cmd = [
                    "nice", "-n", "15",
                    "ffmpeg", "-y", "-loglevel", "error",
                    "-i", input_path,
                    "-frames:v", "1",
                    "-update", "1",
                    "-vf", "scale=360:-2:flags=fast_bilinear",
                    "-q:v", "3",
                    str(temp_thumb)
                ]

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL
            )
            try:
                await asyncio.wait_for(proc.communicate(), timeout=8.0)
            except asyncio.TimeoutError:
                try:
                    proc.kill()
                    await proc.communicate()
                except Exception:
                    pass

            if is_video and (proc.returncode != 0 or not temp_thumb.exists() or temp_thumb.stat().st_size == 0):
                retry_cmd = [
                    "nice", "-n", "15",
                    "ffmpeg", "-y", "-loglevel", "error",
                    "-ss", "00:00:00",
                    "-i", file_path,
                    "-an", "-sn", "-dn",
                    "-frames:v", "1",
                    "-update", "1",
                    "-vf", "scale=360:-2:flags=fast_bilinear",
                    "-q:v", "3",
                    str(temp_thumb)
                ]
                proc = await asyncio.create_subprocess_exec(
                    *retry_cmd,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL
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
                            conn = get_db()
                            with conn:
                                conn.execute("UPDATE files SET has_thumb = 1 WHERE path = ?", (file_path,))
                            conn.close()
                        except Exception:
                            pass
                    await asyncio.to_thread(_update_thumb_status)
                return thumb_path
        except Exception as e:
            print(f"Thumbnail generation error for {file_path}: {e}")
        finally:
            if temp_thumb.exists():
                try:
                    temp_thumb.unlink()
                except OSError:
                    pass
    return None

def convert_image_to_webp_bytes(source: Union[str, Path, bytes], quality: Optional[int] = None) -> bytes:
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

        save_kwargs = {
            "format": "WEBP",
            "quality": quality,
            "method": 6,
            "lossless": False,
        }
        if icc_profile:
            save_kwargs["icc_profile"] = icc_profile
        if len(exif) > 0:
            save_kwargs["exif"] = exif

        if orig_mode == "CMYK":
            work_img = img.convert("RGB")
        elif orig_mode not in ("RGB", "RGBA", "L", "LA"):
            work_img = img.convert("RGBA" if "transparency" in img.info or orig_mode in ("P", "PA") else "RGB")
        else:
            work_img = img

        out_io = io.BytesIO()
        work_img.save(out_io, **save_kwargs)
        return out_io.getvalue()

def convert_image_file_to_webp(file_path: str, quality: Optional[int] = None) -> dict:
    """Converts a local image file to modern WebP format with size and safety checks."""
    fpath = Path(file_path).resolve()
    try:
        if not fpath.exists():
            conn = get_db()
            try:
                with conn:
                    conn.execute("UPDATE files SET webp_optimized = -3 WHERE path = ?", (str(fpath),))
            except Exception:
                pass
            finally:
                conn.close()
            return {"status": "error", "error": "File not found", "path": str(fpath)}
    except OSError as e:
        conn = get_db()
        try:
            with conn:
                conn.execute("UPDATE files SET webp_optimized = -3 WHERE path = ?", (str(fpath),))
        except Exception:
            pass
        finally:
            conn.close()
        return {"status": "error", "error": str(e), "path": str(fpath)}

    if quality is None:
        quality = WEBP_QUALITY

    ext = fpath.suffix.lower()
    if ext == ".webp":
        conn = get_db()
        try:
            with conn:
                conn.execute("UPDATE files SET webp_optimized = 1 WHERE path = ?", (str(fpath),))
        except Exception:
            pass
        finally:
            conn.close()
        return {"status": "already_webp", "path": str(fpath)}
    if ext not in (".jpg", ".jpeg", ".png", ".bmp", ".tiff"):
        conn = get_db()
        try:
            with conn:
                conn.execute("UPDATE files SET webp_optimized = -2 WHERE path = ?", (str(fpath),))
        except Exception:
            pass
        finally:
            conn.close()
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
                work_img = img.convert("RGBA" if "transparency" in img.info or orig_mode in ("P", "PA") else "RGB")
            else:
                work_img = img

            work_img.save(temp_path, **save_kwargs)

        with Image.open(temp_path) as v_img:
            v_img.verify()
        with Image.open(temp_path) as v_img:
            v_img.load()
            v_w, v_h = v_img.size
            if v_w != orig_width or v_h != orig_height:
                raise ValueError(f"Dimension mismatch: expected {orig_width}x{orig_height}, got {v_w}x{v_h}")
    except Exception as e:
        temp_path.unlink(missing_ok=True)
        conn = get_db()
        try:
            with conn:
                conn.execute("UPDATE files SET webp_optimized = -2 WHERE path = ?", (str(fpath),))
        except Exception:
            pass
        finally:
            conn.close()
        return {"status": "error", "error": str(e), "path": str(fpath)}

    webp_size = temp_path.stat().st_size

    if WEBP_SKIP_LARGER and webp_size >= orig_size:
        temp_path.unlink(missing_ok=True)
        conn = get_db()
        try:
            with conn:
                conn.execute("UPDATE files SET webp_optimized = -1 WHERE path = ?", (str(fpath),))
                conn.execute("UPDATE webp_stats SET skipped_count = skipped_count + 1 WHERE id = 1")
        except Exception as e:
            print(f"Error updating SQLite index for skipped WebP: {e}")
        finally:
            conn.close()

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

    shutil.copy2(temp_path, target_path)
    temp_path.unlink(missing_ok=True)

    if not target_path.exists() or target_path.stat().st_size != webp_size:
        raise IOError("Target WebP verification failed after copy")

    try:
        fpath.unlink()
    except Exception as e:
        print(f"Warning: could not unlink original {fpath}: {e}")

    saved_bytes = orig_size - webp_size
    new_mtime = target_path.stat().st_mtime
    new_size_fmt = format_size(webp_size)

    conn = get_db()
    try:
        with conn:
            conn.execute("""
                UPDATE files
                SET path = ?, name = ?, ext = '.webp', size = ?, size_formatted = ?, mtime = ?, webp_optimized = 1, has_thumb = 1
                WHERE path = ?
            """, (str(target_path), target_path.name, webp_size, new_size_fmt, new_mtime, str(fpath)))
            conn.execute("UPDATE webp_stats SET saved_bytes = saved_bytes + ?, converted_count = converted_count + 1 WHERE id = 1", (saved_bytes,))
    except Exception as e:
        print(f"Error updating SQLite index for WebP: {e}")
    finally:
        conn.close()

    try:
        new_thumb = get_thumbnail_cache_path(str(target_path), new_mtime, webp_size)
        if not new_thumb.exists() or new_thumb.stat().st_size == 0:
            with Image.open(target_path) as t_img:
                t_img.thumbnail((360, 360), Image.Resampling.BILINEAR)
                if t_img.mode not in ("RGB", "L"):
                    t_img = t_img.convert("RGB")
                t_img.save(new_thumb, format="JPEG", quality=80)
    except Exception as e:
        print(f"Thumbnail generation error for {target_path}: {e}")

    try:
        old_thumb = get_thumbnail_cache_path(str(fpath))
        if old_thumb.exists() and old_thumb != get_thumbnail_cache_path(str(target_path), new_mtime, webp_size):
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
