import os
import shutil
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any

from media_library.core.config import (
    WEBP_SKIP_LARGER,
    ZIP_STAGING_DIR,
)
from media_library.core.constants import IMAGE_EXTENSIONS
from media_library.db.connection import db_conn

from .core import (
    _get_archive_lock,
    get_zip_cache_dir,
    index_zip_archive,
    is_zip_extracted,
    logger,
    manage_lru_cache,
    resolve_zip_file_path,
    zip_lock,
    zip_tasks_state,
)


def optimize_zip_images_to_webp(
    zip_path: str, quality: int | None = None
) -> dict[str, Any]:
    """
    Scans a ZIP archive and converts all non-WebP image members to .webp format.
    Rebuilds archive atomically, verifies CRC, purges stale extraction cache,
    re-indexes in SQLite, and updates webp_stats.

    Serialized per archive via ``_get_archive_lock`` so concurrent worker items
    for the same ZIP cannot race on the remote file.
    """
    if not os.path.exists(zip_path) or not zip_path.lower().endswith(".zip"):
        return {
            "status": "error",
            "error": f"Invalid zip path: {zip_path}",
            "converted": 0,
        }

    with _get_archive_lock(zip_path):
        return _optimize_zip_images_to_webp_impl(zip_path, quality=quality)


def _read_zip_member_bytes(zip_path: str, zf_in, info, cache_available: bool) -> bytes:
    """Reads a member's bytes from the local extraction cache when available.

    Falls back to reading from the (possibly local) archive handle otherwise.
    """
    if cache_available:
        resolved = resolve_zip_file_path(os.path.join(zip_path, info.filename))
        if resolved and os.path.isfile(resolved):
            with open(resolved, "rb") as fh:
                return fh.read()
    return zf_in.read(info.filename)


def _optimize_zip_images_to_webp_impl(
    zip_path: str, quality: int | None = None
) -> dict[str, Any]:
    manage_lru_cache()
    orig_zip_size = os.path.getsize(zip_path)
    parent_dir = os.path.dirname(os.path.normpath(zip_path))
    local_temp_zip = str(
        ZIP_STAGING_DIR / f".tmp_webp_opt_{int(time.time())}_{uuid.uuid4().hex[:6]}.zip"
    )
    remote_temp_zip = os.path.join(
        parent_dir, f".tmp_transfer_webp_{Path(zip_path).stem}_{int(time.time())}.zip"
    )
    cache_available = False
    download_zip = None

    with zip_lock:
        task = zip_tasks_state["active_tasks"].get(zip_path)
        if not task:
            zip_tasks_state["active_tasks"][zip_path] = {
                "type": "webp_optimize",
                "status": "running",
                "percent": 0.0,
                "current": 0,
                "total": 0,
                "message": "Scanning ZIP for non-WebP images...",
                "started_at": time.time(),
            }

    try:
        from media_library.services.ffmpeg_service import convert_image_to_webp_bytes

        # Resolve a local archive source. Prefer the already-extracted cache (zero
        # remote reads); otherwise download the archive to fast local staging with a
        # single sequential copy so conversion never performs random-access FUSE reads.
        cache_available = is_zip_extracted(zip_path)
        if cache_available:
            zf_read_path = zip_path  # central directory + non-image members; image bytes come from cache
        else:
            download_zip = str(
                ZIP_STAGING_DIR / f".tmp_webp_dl_{uuid.uuid4().hex[:8]}.zip"
            )
            shutil.copyfile(zip_path, download_zip)
            zf_read_path = download_zip

        with zipfile.ZipFile(zf_read_path, "r") as zf_in:
            infolist = zf_in.infolist()
            convert_targets = []
            for info in infolist:
                if info.is_dir():
                    continue
                ext = os.path.splitext(info.filename)[1].lower()
                if ext in IMAGE_EXTENSIONS and ext != ".webp":
                    convert_targets.append(info.filename)

            if not convert_targets:
                with zip_lock:
                    t = zip_tasks_state["active_tasks"].get(zip_path)
                    if t:
                        t["status"] = "completed"
                        t["percent"] = 100.0
                        t["message"] = "All images in ZIP are already WebP"
                return {
                    "status": "already_webp",
                    "zip_path": zip_path,
                    "converted": 0,
                    "message": "All images in ZIP are already WebP",
                }

            total_items = len(infolist)
            converted_count = 0
            skipped_count = 0
            skipped_members = []

            with zipfile.ZipFile(
                local_temp_zip, "w", compression=zipfile.ZIP_DEFLATED
            ) as zf_out:
                for idx, info in enumerate(infolist):
                    if info.is_dir():
                        zf_out.writestr(info, "")
                        continue

                    ext = os.path.splitext(info.filename)[1].lower()
                    is_convertible_image = ext in IMAGE_EXTENSIONS and ext != ".webp"

                    if is_convertible_image:
                        raw_data = _read_zip_member_bytes(
                            zip_path, zf_in, info, cache_available
                        )
                        try:
                            webp_bytes = convert_image_to_webp_bytes(
                                raw_data, quality=quality
                            )
                        except Exception as conv_err:
                            logger.warning(
                                f"Warning: could not convert {info.filename} in {zip_path}: {conv_err}"
                            )
                            zf_out.writestr(info, raw_data)
                        else:
                            if WEBP_SKIP_LARGER and len(webp_bytes) >= len(raw_data):
                                # Keep the original member when WebP would not save space.
                                zf_out.writestr(info, raw_data)
                                skipped_count += 1
                                skipped_members.append(info.filename)
                            else:
                                new_name = os.path.splitext(info.filename)[0] + ".webp"
                                new_info = zipfile.ZipInfo(
                                    new_name, date_time=info.date_time
                                )
                                new_info.compress_type = zipfile.ZIP_DEFLATED
                                zf_out.writestr(new_info, webp_bytes)
                                converted_count += 1
                    else:
                        # Stream non-image members to bound peak memory (e.g. large videos).
                        with zf_in.open(info) as src, zf_out.open(info, "w") as dst:
                            shutil.copyfileobj(src, dst)

                    pct = round(((idx + 1) / max(total_items, 1)) * 100, 1)
                    with zip_lock:
                        t = zip_tasks_state["active_tasks"].get(zip_path)
                        if t:
                            t["percent"] = pct
                            t["current"] = idx + 1
                            t["message"] = (
                                f"Converting images in ZIP to WebP ({pct}%)..."
                            )

        if download_zip and os.path.exists(download_zip):
            try:
                os.remove(download_zip)
            except OSError:
                pass

        # Verify integrity on local disk
        with zipfile.ZipFile(local_temp_zip, "r") as zf_test:
            corrupt = zf_test.testzip()
            if corrupt:
                raise RuntimeError(
                    f"Optimized zip verification failed on member: {corrupt}"
                )

        # Transfer verified archive to destination and atomically replace
        shutil.copyfile(local_temp_zip, remote_temp_zip)
        os.replace(remote_temp_zip, zip_path)

        if os.path.exists(local_temp_zip):
            try:
                os.remove(local_temp_zip)
            except OSError:
                pass

        new_zip_size = os.path.getsize(zip_path)
        saved_bytes = max(0, orig_zip_size - new_zip_size)

        # Invalidate extraction cache if exists
        cache_dir = get_zip_cache_dir(zip_path)
        if cache_dir.exists():
            shutil.rmtree(cache_dir, ignore_errors=True)

        # Re-index ZIP in SQLite
        index_zip_archive(zip_path, parent_path=parent_dir)

        # Update webp_stats and mark skip-larger members so they are not retried.
        try:
            with db_conn() as conn, conn:
                conn.execute(
                    """
                    UPDATE webp_stats 
                    SET saved_bytes = saved_bytes + ?, converted_count = converted_count + ?, skipped_count = skipped_count + ?
                    WHERE id = 1
                """,
                    (saved_bytes, converted_count, skipped_count),
                )
                for name in skipped_members:
                    conn.execute(
                        "UPDATE files SET webp_optimized = -1 WHERE path = ?",
                        (os.path.join(zip_path, name),),
                    )
        except Exception as se:
            logger.error(f"Error updating webp_stats for zip: {se}")

        with zip_lock:
            t = zip_tasks_state["active_tasks"].get(zip_path)
            if t:
                t["status"] = "completed"
                t["percent"] = 100.0
                t["message"] = f"Converted {converted_count} images to WebP"

        return {
            "status": "converted",
            "zip_path": zip_path,
            "converted_count": converted_count,
            "skipped_count": skipped_count,
            "orig_size": orig_zip_size,
            "new_size": new_zip_size,
            "saved_bytes": saved_bytes,
        }

    except Exception as e:
        for p in (local_temp_zip, remote_temp_zip, download_zip):
            if p and os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass
        with zip_lock:
            t = zip_tasks_state["active_tasks"].get(zip_path)
            if t:
                t["status"] = "failed"
                t["error"] = str(e)
                t["message"] = f"WebP optimization of ZIP failed: {e}"
        raise
