"""FFmpeg multimedia processing, poster generation, and WebP image optimization.

The FFprobe/poster/animated-preview helpers live in the sibling modules
``ffmpeg_probe``, ``ffmpeg_transcode``, ``ffmpeg_posters`` and ``ffmpeg_frames``;
they are re-exported here so existing imports keep working.
"""

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

from media_library.services.ffmpeg_frames import (  # noqa: F401
    animated_generation_sem,
    candidate_generation_sem,
    extract_candidate_frame,
    generate_animated_preview,
    get_animated_preview_path,
    get_candidate_cache_path,
)
from media_library.services.ffmpeg_posters import (  # noqa: F401
    extract_frame_poster,
    generate_thumbnail,
    get_thumbnail_path_fast,
    thumbnail_generation_sem,
)
from media_library.services.ffmpeg_probe import (  # noqa: F401
    _parse_exif_capture_time,
    _parse_fraction,
    _parse_iso_time,
    format_eta_str,
    probe_media_metadata,
    probe_video_meta,
)
from media_library.services.ffmpeg_transcode import (  # noqa: F401
    build_live_transcode_command,
    relay_process_stdout,
)

logger = logging.getLogger(__name__)

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
            conn.execute(
                "UPDATE folders SET total_size = MAX(0, total_size - ?) WHERE path = ?",
                (saved_bytes, str(fpath.parent)),
            )
            conn.execute(
                "UPDATE ignored_duplicates SET path1 = ? WHERE path1 = ?",
                (str(target_path), str(fpath)),
            )
            conn.execute(
                "UPDATE ignored_duplicates SET path2 = ? WHERE path2 = ?",
                (str(target_path), str(fpath)),
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
