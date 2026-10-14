"""Video poster frame extraction and thumbnail generation."""

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


thumbnail_generation_sem = asyncio.Semaphore(4)

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
                        if not resolved and cache_dir:
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

            if not is_video and (
                not temp_thumb.exists() or temp_thumb.stat().st_size == 0
            ):

                def _pil_fallback():
                    try:
                        from PIL import Image

                        with Image.open(input_path) as im:
                            im = im.convert("RGB")
                            im.thumbnail((360, 360))
                            im.save(str(temp_thumb), "JPEG", quality=85)
                    except Exception as err:
                        logger.debug(
                            f"PIL thumbnail fallback failed for {input_path}: {err}"
                        )

                await asyncio.to_thread(_pil_fallback)

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

async def extract_frame_poster(
    file_path: str,
    timestamp: float = 0.0,
    image_base64: str | None = None,
) -> Path | None:
    """Extracts a specific video frame as a thumbnail or saves provided image bytes."""
    thumb_path = await asyncio.to_thread(get_thumbnail_path_fast, file_path)
    thumb_path.parent.mkdir(parents=True, exist_ok=True)
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
                    if not resolved and cache_dir:
                        resolved = str(cache_dir / inner_p)
                if resolved and os.path.exists(resolved):
                    input_path = resolved
        except Exception:
            pass

    try:
        if image_base64:
            raw_b64 = image_base64
            if "," in raw_b64:
                raw_b64 = raw_b64.split(",", 1)[1]
            img_bytes = base64.b64decode(raw_b64)
            with Image.open(io.BytesIO(img_bytes)) as pil_img:
                rgb_img = pil_img.convert("RGB")
                w, h = rgb_img.size
                if w > 360:
                    new_h = max(1, int(h * 360 / w))
                    rgb_img = rgb_img.resize((360, new_h), Image.Resampling.BILINEAR)
                rgb_img.save(temp_thumb, "JPEG", quality=85)
        else:
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
                    await asyncio.wait_for(proc.communicate(), timeout=12.0)
                except asyncio.TimeoutError:
                    try:
                        proc.kill()
                        await proc.communicate()
                    except Exception:
                        pass

        if temp_thumb.exists() and temp_thumb.stat().st_size > 0:
            os.replace(temp_thumb, thumb_path)

            def _update_db():
                thumb_hash_val = None
                try:
                    import imagehash
                    with Image.open(thumb_path) as img:
                        thumb_hash_val = str(imagehash.phash(img))
                except Exception:
                    thumb_hash_val = None

                now_ts = time.time()
                try:
                    with db_conn() as conn, conn:
                        try:
                            if thumb_hash_val:
                                conn.execute(
                                    "UPDATE files SET has_thumb = 1, thumb_hash = ?, thumb_mtime = ? WHERE path = ?",
                                    (thumb_hash_val, now_ts, file_path),
                                )
                            else:
                                conn.execute(
                                    "UPDATE files SET has_thumb = 1, thumb_mtime = ? WHERE path = ?",
                                    (now_ts, file_path),
                                )
                        except Exception:
                            if thumb_hash_val:
                                conn.execute(
                                    "UPDATE files SET has_thumb = 1, thumb_hash = ? WHERE path = ?",
                                    (thumb_hash_val, file_path),
                                )
                            else:
                                conn.execute(
                                    "UPDATE files SET has_thumb = 1 WHERE path = ?",
                                    (file_path,),
                                )
                except Exception:
                    pass

            await asyncio.to_thread(_update_db)
            return thumb_path
    except Exception as e:
        logger.error(f"Error extracting poster frame for {file_path}: {e}")
    finally:
        if temp_thumb.exists():
            try:
                temp_thumb.unlink()
            except OSError:
                pass
    return None
