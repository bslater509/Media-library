"""Candidate frame extraction and animated WebP preview generation."""

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

from media_library.services.ffmpeg_posters import (  # noqa: E402
    generate_thumbnail,
    get_thumbnail_path_fast,
)
from media_library.services.ffmpeg_probe import probe_video_meta  # noqa: E402


candidate_generation_sem = asyncio.Semaphore(4)

def get_candidate_cache_path(file_path: str, timestamp: float) -> Path:
    """Returns the persistent disk path for a cached candidate frame."""
    from media_library.core.config import THUMBNAIL_CACHE_DIR
    import hashlib

    cand_dir = Path(THUMBNAIL_CACHE_DIR) / "candidates"
    cand_dir.mkdir(parents=True, exist_ok=True)
    ts = max(0.0, round(float(timestamp), 3))
    key = f"{file_path}_{ts:.3f}"
    h = hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
    return cand_dir / f"cand_{h}.jpg"

async def extract_candidate_frame(
    file_path: str,
    timestamp: float = 0.0,
) -> Path | None:
    """Extracts a candidate frame thumbnail at the given timestamp with caching."""
    cand_path = await asyncio.to_thread(get_candidate_cache_path, file_path, timestamp)
    if cand_path.exists() and cand_path.stat().st_size > 0:
        return cand_path

    async with candidate_generation_sem:
        if cand_path.exists() and cand_path.stat().st_size > 0:
            return cand_path

        temp_thumb = cand_path.with_suffix(f".{uuid.uuid4().hex[:8]}.tmp.jpg")
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
                        if not resolved and cache_dir:
                            resolved = str(cache_dir / inner_p)
                    if resolved and os.path.exists(resolved):
                        input_path = resolved
            except Exception:
                pass

        try:
            ts = max(0.0, float(timestamp))
            time_str = f"{int(ts // 3600):02d}:{int((ts % 3600) // 60):02d}:{ts % 60:06.3f}"
            cmd = [
                "nice",
                "-n",
                "15",
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-ss",
                time_str,
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
                "scale=480:-2:flags=fast_bilinear",
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
                await asyncio.wait_for(proc.communicate(), timeout=12.0)
            except asyncio.TimeoutError:
                try:
                    proc.kill()
                    await proc.communicate()
                except Exception:
                    pass

            if (
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
                    "-i",
                    input_path,
                    "-ss",
                    time_str,
                    "-an",
                    "-sn",
                    "-dn",
                    "-frames:v",
                    "1",
                    "-update",
                    "1",
                    "-vf",
                    "scale=480:-2:flags=fast_bilinear",
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
                    await asyncio.wait_for(proc.communicate(), timeout=12.0)
                except asyncio.TimeoutError:
                    try:
                        proc.kill()
                        await proc.communicate()
                    except Exception:
                        pass

            # Fallback for PIL-compatible mock media in test fixtures
            if not temp_thumb.exists() or temp_thumb.stat().st_size == 0:
                try:
                    with Image.open(input_path) as pil_img:
                        rgb_img = pil_img.convert("RGB")
                        w, h = rgb_img.size
                        if w > 480:
                            new_h = max(1, int(h * 480 / w))
                            rgb_img = rgb_img.resize((480, new_h), Image.Resampling.BILINEAR)
                        rgb_img.save(temp_thumb, "JPEG", quality=85)
                except Exception:
                    pass

            if temp_thumb.exists() and temp_thumb.stat().st_size > 0:
                os.replace(temp_thumb, cand_path)
                return cand_path
        except Exception as e:
            logger.error(f"Error extracting candidate frame for {file_path} at {timestamp}: {e}")
        finally:
            if temp_thumb.exists():
                try:
                    temp_thumb.unlink()
                except OSError:
                    pass
        return None

animated_generation_sem = asyncio.Semaphore(2)

def get_animated_preview_path(
    file_path: str | Path, mtime: float | None = None, size: int | None = None
) -> Path:
    """Returns the persistent disk path for an animated WebP preview."""
    from media_library.core.config import THUMBNAIL_CACHE_DIR
    import hashlib

    anim_dir = Path(THUMBNAIL_CACHE_DIR) / "animated"
    anim_dir.mkdir(parents=True, exist_ok=True)

    str_path = str(file_path)
    key = str_path
    if mtime is not None and size is not None:
        key = f"{str_path}_{mtime}_{size}"
    else:
        try:
            from media_library.db.connection import db_conn

            with db_conn() as conn:
                row = conn.execute(
                    "SELECT mtime, size FROM files WHERE path = ? LIMIT 1",
                    (str_path,),
                ).fetchone()
                if row and row["mtime"] is not None and row["size"] is not None:
                    key = f"{str_path}_{row['mtime']}_{row['size']}"
        except Exception:
            pass

    h = hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
    return anim_dir / f"anim_{h}.webp"

async def generate_animated_preview(
    file_path: str | Path,
    max_frames: int = 24,
) -> Path | None:
    """Generates a looping animated WebP preview (480px, 500ms/frame) for a video file."""
    file_path = str(file_path)
    anim_path = await asyncio.to_thread(get_animated_preview_path, file_path)
    if anim_path.exists() and anim_path.stat().st_size > 0:
        return anim_path

    async with animated_generation_sem:
        if anim_path.exists() and anim_path.stat().st_size > 0:
            return anim_path

        temp_anim = anim_path.with_suffix(f".{uuid.uuid4().hex[:8]}.tmp.webp")

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
                        if not resolved and cache_dir:
                            resolved = str(cache_dir / inner_p)
                    if resolved and os.path.exists(resolved):
                        input_path = resolved
            except Exception:
                pass

        try:
            duration = 0.0
            try:
                from media_library.db.connection import db_conn

                with db_conn() as conn:
                    row = conn.execute(
                        "SELECT duration FROM files WHERE path = ? LIMIT 1",
                        (file_path,),
                    ).fetchone()
                    if row and row["duration"]:
                        duration = float(row["duration"])
            except Exception:
                pass

            if duration <= 0.0:
                meta = await probe_video_meta(input_path)
                duration = float(meta.get("duration") or 0.0)

            if duration <= 0.0:
                duration = 1.0

            max_safe = max(0.0, duration - min(0.5, duration * 0.02))

            # Determine sample timestamps (spaced by 30s or up to max_frames)
            if duration <= 30.0:
                sub_count = max(2, min(6, int(duration // 2) + 1))
                step = duration / max(1, sub_count - 1)
                timestamps = [round(min(i * step, max_safe), 3) for i in range(sub_count)]
            else:
                interval = 30.0
                if (duration / 30.0) > max_frames:
                    interval = duration / max_frames

                timestamps = []
                curr = 0.0
                while curr < duration and len(timestamps) < max_frames:
                    timestamps.append(round(min(curr, max_safe), 3))
                    curr += interval

                if (duration - timestamps[-1]) >= max(5.0, interval * 0.33) and len(timestamps) < max_frames:
                    end_ts = round(max_safe, 3)
                    if end_ts > timestamps[-1]:
                        timestamps.append(end_ts)

            # Gather frames
            frame_paths = []
            for ts in timestamps:
                fp = await extract_candidate_frame(file_path, timestamp=ts)
                if fp and fp.exists() and fp.stat().st_size > 0:
                    frame_paths.append(fp)

            pil_frames = []
            for fp in frame_paths:
                try:
                    with Image.open(fp) as img:
                        rgb = img.convert("RGB")
                        if rgb.width != 480:
                            h = max(1, int(rgb.height * 480 / rgb.width))
                            rgb = rgb.resize((480, h), Image.Resampling.BILINEAR)
                        pil_frames.append(rgb)
                except Exception:
                    pass

            # Fallback if no candidate frames could be extracted
            if not pil_frames:
                static_thumb = await generate_thumbnail(file_path, is_video=True)
                if static_thumb and static_thumb.exists():
                    try:
                        with Image.open(static_thumb) as img:
                            rgb = img.convert("RGB")
                            if rgb.width != 480:
                                h = max(1, int(rgb.height * 480 / rgb.width))
                                rgb = rgb.resize((480, h), Image.Resampling.BILINEAR)
                            pil_frames.append(rgb)
                    except Exception:
                        pass
            else:
                # If a custom/static thumbnail already exists for this video, ensure it leads the animation
                try:
                    custom_thumb = await asyncio.to_thread(get_thumbnail_path_fast, file_path)
                    if custom_thumb and custom_thumb.exists() and custom_thumb.stat().st_size > 0:
                        with Image.open(custom_thumb) as c_img:
                            c_rgb = c_img.convert("RGB")
                            pil_frames.insert(0, c_rgb)
                            if len(pil_frames) > max_frames:
                                pil_frames.pop()
                except Exception:
                    pass

            if not pil_frames:
                return None

            # CRITICAL: Normalize ALL frames to the exact same (480, target_h) dimensions.
            # In animated WebP, Pillow/libwebp strictly rejects frames with differing dimensions.
            heights = [f.height for f in pil_frames]
            target_h = max(set(heights), key=heights.count) if heights else 270
            target_w = 480

            for idx in range(len(pil_frames)):
                if pil_frames[idx].size != (target_w, target_h):
                    pil_frames[idx] = pil_frames[idx].resize(
                        (target_w, target_h), Image.Resampling.BILINEAR
                    )

            # Animated WebP requires at least 2 distinct frames for animation
            if len(pil_frames) == 1:
                try:
                    from PIL import ImageEnhance
                    pil_frames.append(ImageEnhance.Brightness(pil_frames[0]).enhance(0.92))
                except Exception:
                    pil_frames.append(pil_frames[0].copy())
            else:
                try:
                    first_bytes = pil_frames[0].tobytes()
                    if all(f.tobytes() == first_bytes for f in pil_frames[1:]):
                        from PIL import ImageEnhance
                        for idx in range(1, len(pil_frames)):
                            factor = 0.92 if idx % 2 == 1 else 1.0
                            pil_frames[idx] = ImageEnhance.Brightness(pil_frames[idx]).enhance(factor)
                except Exception:
                    pass

            # Save animated WebP: 500ms per frame, loop forever
            pil_frames[0].save(
                temp_anim,
                format="WEBP",
                save_all=True,
                append_images=pil_frames[1:],
                duration=500,
                loop=0,
                quality=80,
                method=4,
            )

            if temp_anim.exists() and temp_anim.stat().st_size > 0:
                os.replace(temp_anim, anim_path)
                try:
                    from media_library.db.connection import db_conn
                    with db_conn() as conn, conn:
                        conn.execute(
                            "UPDATE files SET has_animated = 1 WHERE path = ?",
                            (file_path,),
                        )
                except Exception:
                    pass
                return anim_path
        except Exception as e:
            logger.error(f"Error generating animated preview for {file_path}: {e}")
        finally:
            if temp_anim.exists():
                try:
                    temp_anim.unlink()
                except OSError:
                    pass
        return None
