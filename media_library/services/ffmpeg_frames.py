"""Candidate frame extraction and animated WebP preview generation."""

import asyncio
import logging
import os
import uuid
from pathlib import Path

from PIL import Image

logger = logging.getLogger(__name__)

from media_library.services.ffmpeg_posters import (
    generate_thumbnail,
    get_thumbnail_path_fast,
)
from media_library.services.ffmpeg_probe import probe_video_meta

candidate_generation_sem = asyncio.Semaphore(4)


def _mark_animated_in_index(file_path: str) -> None:
    """Flag the preview in the index, off the event loop.

    Imported lazily: ``media_library.db`` must not pull services in at import
    time (AGENTS.md Rule 3), and this module is imported by services.
    """
    from media_library.db.connection import db_conn

    with db_conn() as conn, conn:
        conn.execute("UPDATE files SET has_animated = 1 WHERE path = ?", (file_path,))


def get_candidate_cache_path(file_path: str, timestamp: float) -> Path:
    """Returns the persistent disk path for a cached candidate frame.

    Frames are binned per video (``candidates/v<hash>/cand_<ts>.jpg``) so the
    housekeeping in ``services.candidate_cache`` can tell which video a frame
    belongs to by name and drop a whole bin once that video leaves the index.
    """
    import math

    from media_library.core.config import THUMBNAIL_CACHE_DIR
    from media_library.services.candidate_cache import candidate_dir_name

    cand_dir = Path(THUMBNAIL_CACHE_DIR) / "candidates" / candidate_dir_name(file_path)
    cand_dir.mkdir(parents=True, exist_ok=True)
    try:
        ts = float(timestamp)
    except (TypeError, ValueError):
        ts = 0.0
    if not math.isfinite(ts) or ts < 0:
        ts = 0.0
    return cand_dir / f"cand_{round(ts, 3):.3f}.jpg"


def _manage_candidate_cache() -> None:
    """Throttled LRU trim of the frame cache; never fatal to a generation."""
    try:
        from media_library.services.candidate_cache import (
            manage_candidate_cache_lru,
        )

        manage_candidate_cache_lru()
    except Exception as e:
        logger.debug(f"Could not trim the candidate frame cache: {e}")


#: One decode pass at a time unless the caller brings its own slots: a pass is
#: CPU-heavy and reads a whole file, so the sweep hands a semaphore sized to the
#: core count it was given instead of letting every caller start a stream. On
#: this project's library a *second* stream is what doubles decode throughput
#: (FFmpeg tops out around three cores per stream at 1080p), a third does not —
#: see ``workers.face_worker.scan_concurrency``.
video_scan_sem = asyncio.Semaphore(1)

#: JPEG start-of-image / end-of-image markers. Entropy-coded scan data escapes
#: 0xFF as 0xFF00, so an EOI marker can only be a real frame boundary.
_SOI = b"\xff\xd8\xff"
_EOI = b"\xff\xd9"
_READ_CHUNK = 1 << 16


def _split_jpegs(buffer: bytearray) -> list[bytes]:
    """Pull every complete JPEG out of ``buffer``, leaving the tail behind."""
    frames: list[bytes] = []
    while True:
        start = buffer.find(_SOI)
        if start < 0:
            # Nothing usable: keep at most a marker's worth in case it straddles
            # a chunk boundary.
            if len(buffer) > 3:
                del buffer[:-3]
            return frames
        end = buffer.find(_EOI, start + len(_SOI))
        if end < 0:
            if start:
                del buffer[:start]
            return frames
        end += len(_EOI)
        frames.append(bytes(buffer[start:end]))
        del buffer[:end]


async def iter_video_scan_frames(
    file_path: str,
    interval: float,
    width: int,
    max_frames: int = 0,
    timeout_s: float = 0.0,
    slots: asyncio.Semaphore | None = None,
):
    """Yield ``(index, timestamp, jpeg_bytes)`` from one pass over a video.

    FFmpeg decodes the file **once** and samples the timeline itself
    (``fps=1/interval``), which is what makes a dense face sweep affordable: the
    mount is read sequentially once instead of re-seeking for every frame, and
    nothing is written to disk — the caller keeps only the face crops it finds,
    so the shared candidate-frame cache is never touched (or evicted) by the
    sweep. Frames are scaled to ``width`` but never upscaled, so a small source
    is not blown up into blurry pixels the recogniser cannot use.
    """
    interval = max(0.05, float(interval))
    width = max(160, int(width))
    fps = 1.0 / interval
    scale = f"scale='min({width},iw)':-2:flags=fast_bilinear"
    cmd = [
        "nice",
        "-n",
        "15",
        "ffmpeg",
        "-nostdin",
        "-loglevel",
        "error",
        "-i",
        file_path,
        "-an",
        "-sn",
        "-dn",
        "-vf",
        f"fps={fps:.6f},{scale}",
    ]
    if max_frames and int(max_frames) > 0:
        cmd += ["-frames:v", str(int(max_frames))]
    cmd += ["-f", "image2pipe", "-c:v", "mjpeg", "-q:v", "3", "-"]

    async with slots or video_scan_sem:
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except Exception as e:
            # FFmpeg missing, no file handle left, mount hiccup: nothing to read,
            # and the file simply stays pending for the next sweep.
            logger.warning(
                f"[Faces] Could not start the frame pass for {file_path}: {e}"
            )
            return
        buffer = bytearray()
        index = 0
        deadline = None
        if timeout_s and timeout_s > 0:
            deadline = asyncio.get_running_loop().time() + float(timeout_s)
        try:
            while True:
                if deadline is not None:
                    remaining = deadline - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        logger.warning(
                            f"[Faces] Frame pass timed out for {file_path} after "
                            f"{index} frames"
                        )
                        break
                    chunk = await asyncio.wait_for(
                        proc.stdout.read(_READ_CHUNK), remaining
                    )
                else:
                    chunk = await proc.stdout.read(_READ_CHUNK)
                if not chunk:
                    break
                buffer.extend(chunk)
                for jpeg in _split_jpegs(buffer):
                    yield index, round(index * interval, 3), jpeg
                    index += 1
                    if max_frames and index >= int(max_frames):
                        break
                if max_frames and index >= int(max_frames):
                    break
            for jpeg in _split_jpegs(buffer):
                yield index, round(index * interval, 3), jpeg
                index += 1
        except asyncio.TimeoutError:
            logger.warning(f"[Faces] Frame pass timed out for {file_path}")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"[Faces] Frame pass failed for {file_path}: {e}")
        finally:
            try:
                if proc.returncode is None:
                    proc.kill()
            except ProcessLookupError:
                pass
            try:
                await proc.wait()
            except Exception:
                pass
            try:
                if proc.stdout is not None:
                    proc.stdout.feed_eof()
            except Exception:
                pass


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
            time_str = (
                f"{int(ts // 3600):02d}:{int((ts % 3600) // 60):02d}:{ts % 60:06.3f}"
            )
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
                            rgb_img = rgb_img.resize(
                                (480, new_h), Image.Resampling.BILINEAR
                            )
                        rgb_img.save(temp_thumb, "JPEG", quality=85)
                except Exception:
                    pass

            if temp_thumb.exists() and temp_thumb.stat().st_size > 0:
                os.replace(temp_thumb, cand_path)
                # Growth only ever happens here, and the trim is throttled to
                # once a minute, so this keeps the cache under its ceiling
                # without any worker having to know about it.
                await asyncio.to_thread(_manage_candidate_cache)
                return cand_path
        except Exception as e:
            logger.error(
                f"Error extracting candidate frame for {file_path} at {timestamp}: {e}"
            )
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
    import hashlib

    from media_library.core.config import THUMBNAIL_CACHE_DIR

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
                timestamps = [
                    round(min(i * step, max_safe), 3) for i in range(sub_count)
                ]
            else:
                interval = 30.0
                if (duration / 30.0) > max_frames:
                    interval = duration / max_frames

                timestamps = []
                curr = 0.0
                while curr < duration and len(timestamps) < max_frames:
                    timestamps.append(round(min(curr, max_safe), 3))
                    curr += interval

                if (duration - timestamps[-1]) >= max(5.0, interval * 0.33) and len(
                    timestamps
                ) < max_frames:
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
                    custom_thumb = await asyncio.to_thread(
                        get_thumbnail_path_fast, file_path
                    )
                    if (
                        custom_thumb
                        and custom_thumb.exists()
                        and custom_thumb.stat().st_size > 0
                    ):
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

                    pil_frames.append(
                        ImageEnhance.Brightness(pil_frames[0]).enhance(0.92)
                    )
                except Exception:
                    pil_frames.append(pil_frames[0].copy())
            else:
                try:
                    first_bytes = pil_frames[0].tobytes()
                    if all(f.tobytes() == first_bytes for f in pil_frames[1:]):
                        from PIL import ImageEnhance

                        for idx in range(1, len(pil_frames)):
                            factor = 0.92 if idx % 2 == 1 else 1.0
                            pil_frames[idx] = ImageEnhance.Brightness(
                                pil_frames[idx]
                            ).enhance(factor)
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
                    await asyncio.to_thread(_mark_animated_in_index, file_path)
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
