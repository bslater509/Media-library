"""WebP optimisation for loose images.

ZIP members are deliberately excluded: archives are browsed as virtual
folders, and optimizing a member would mean rewriting the archive, while loose
images convert one file
at a time. Conversion is atomic, updates the index and moves the cached poster
to the new key, and is safe to batch from the UI.
"""

from __future__ import annotations

import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from media_library import writes
from media_library.cache import animated_path, thumbnail_path
from media_library.config import get_settings
from media_library.db import reader, writer
from media_library.paths import format_size, is_zip_member

_CONVERTIBLE = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff"}
_lock = threading.Lock()


def pending_count(folder: str | None = None) -> int:
    sql = (
        f"SELECT COUNT(*) AS n FROM files WHERE type = 'image' "
        f"AND ext IN ({','.join('?' for _ in _CONVERTIBLE)}) "
        "AND COALESCE(webp_optimized, 0) = 0"
    )
    params: list[Any] = sorted(_CONVERTIBLE)
    if folder:
        sql += " AND folder_path = ?"
        params.append(folder)
    with reader() as conn:
        row = conn.execute(sql, params).fetchone()
    return int(row["n"] or 0)


def _pending_rows(
    limit: int, folder: str | None = None, paths: list[str] | None = None
) -> list[Any]:
    sql = (
        f"SELECT path, name, folder_path, ext, size, mtime FROM files "
        f"WHERE type = 'image' AND ext IN ({','.join('?' for _ in _CONVERTIBLE)}) "
        "AND COALESCE(webp_optimized, 0) = 0"
    )
    params: list[Any] = sorted(_CONVERTIBLE)
    if paths:
        placeholders = ",".join("?" for _ in paths)
        sql += f" AND path IN ({placeholders})"
        params.extend(paths)
    if folder:
        sql += " AND folder_path = ?"
        params.append(folder)
    sql += " ORDER BY size DESC LIMIT ?"
    params.append(limit)
    with reader() as conn:
        return conn.execute(sql, params).fetchall()


def _unique_target(target: Path) -> Path:
    if not target.exists():
        return target
    stem, ext = target.stem, target.suffix
    counter = 1
    while True:
        candidate = target.with_name(f"{stem} ({counter}){ext}")
        if not candidate.exists():
            return candidate
        counter += 1


def _stats(conn) -> tuple[int, int, int]:
    row = conn.execute(
        "SELECT saved_bytes, converted_count, skipped_count FROM webp_stats WHERE id = 1"
    ).fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO webp_stats (id, saved_bytes, converted_count, skipped_count) "
            "VALUES (1, 0, 0, 0)"
        )
        return 0, 0, 0
    return (
        int(row["saved_bytes"] or 0),
        int(row["converted_count"] or 0),
        int(row["skipped_count"] or 0),
    )


# Decoding an image into RGB costs 3 bytes per pixel (4 with alpha). Refuse
# conversions that would allocate more than this in one go: a degraded
# skip is better than an OOM inside a worker thread (~60 MP at RGBA).
_MAX_CONVERT_PIXELS = 60_000_000
# Pillow itself refuses anything beyond ~2*89.5 MP on open; the axis cap adds
# a defence against absurd aspect ratios that pixel-guards alone allow.
_MAX_CONVERT_AXIS = 30_000


def _convert_one(path: str, quality: int, skip_larger: bool) -> dict[str, Any]:
    """Convert one image; returns outcome used to update the index."""
    from PIL import Image, ImageOps

    from media_library.pillow_init import register_heif

    register_heif()
    source = Path(path)
    if is_zip_member(path) or not source.is_file():
        return {"status": "missing", "path": path}
    if source.suffix.lower() == ".webp":
        return {"status": "already", "path": path}

    old_stat = source.stat()
    target = _unique_target(source.with_suffix(".webp"))
    temp = source.with_name(f".tmp_webp_{uuid.uuid4().hex}{target.suffix}")
    try:
        with Image.open(source) as image:
            width, height = image.size
            if (
                width * height > _MAX_CONVERT_PIXELS
                or max(width, height) > _MAX_CONVERT_AXIS
            ):
                # Too large to decode in-process: keep the original untouched
                # rather than risking a multi-GB allocation per sweep item.
                return {"status": "skipped", "path": path}
            image = ImageOps.exif_transpose(image)
            if image.mode in ("RGBA", "LA") or (
                image.mode == "P" and "transparency" in image.info
            ):
                converted = image.convert("RGBA")
            else:
                converted = image.convert("RGB")
            converted.save(temp, "WEBP", quality=quality, method=6)
        new_size = temp.stat().st_size
        if skip_larger and new_size >= old_stat.st_size:
            temp.unlink(missing_ok=True)
            return {"status": "skipped", "path": path}
        os.replace(temp, target)
    except Exception as exc:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
        return {"status": "failed", "path": path, "error": str(exc)}

    if target != source:
        try:
            source.unlink()
        except OSError:
            pass

    new_stat = target.stat()
    saved = max(0, old_stat.st_size - new_stat.st_size)
    old_thumb = thumbnail_path(path, old_stat.st_mtime, old_stat.st_size)
    new_thumb = thumbnail_path(str(target), new_stat.st_mtime, new_stat.st_size)
    if old_thumb.is_file():
        try:
            os.replace(old_thumb, new_thumb)
        except OSError:
            pass
    old_anim = animated_path(path, old_stat.st_mtime, old_stat.st_size)
    if old_anim.is_file():
        try:
            os.replace(
                old_anim,
                animated_path(str(target), new_stat.st_mtime, new_stat.st_size),
            )
        except OSError:
            pass

    with writer() as conn, conn:
        conn.execute(
            """
            UPDATE files SET path = ?, name = ?, ext = '.webp', size = ?,
                size_formatted = ?, mtime = ?, webp_optimized = 1,
                quick_hash = NULL, thumb_hash = NULL
            WHERE path = ?
            """,
            (
                str(target),
                target.name,
                new_stat.st_size,
                format_size(new_stat.st_size),
                new_stat.st_mtime,
                path,
            ),
        )
        conn.execute(
            "UPDATE favorites SET path = ? WHERE path = ?", (str(target), path)
        )
        writes.recompute_folder(conn, str(source.parent))
        saved_total, converted_total, skipped_total = _stats(conn)
        conn.execute(
            "UPDATE webp_stats SET saved_bytes = ?, converted_count = ?, "
            "skipped_count = ? WHERE id = 1",
            (saved_total + saved, converted_total + 1, skipped_total),
        )
    return {
        "status": "converted",
        "path": path,
        "target": str(target),
        "saved": saved,
        "original": old_stat.st_size,
        "size": new_stat.st_size,
    }


def optimize_batch(
    limit: int = 20,
    folder: str | None = None,
    paths: list[str] | None = None,
) -> dict[str, Any]:
    """Convert up to ``limit`` pending images; safe to call repeatedly."""
    settings = get_settings()
    # Native WebP rows need no conversion; mark them so they leave the queue.
    with writer() as conn, conn:
        conn.execute(
            "UPDATE files SET webp_optimized = 1 WHERE ext = '.webp' "
            "AND COALESCE(webp_optimized, 0) = 0"
        )
    # An explicit selection is converted whole (bounded by the request cap);
    # the worker's batch limit only governs the folder/library sweep.
    effective_limit = min(500, max(1, len(paths))) if paths else limit
    rows = _pending_rows(effective_limit, folder, paths)
    converted = 0
    skipped = 0
    failed = 0
    saved = 0
    errors: list[dict[str, str]] = []

    with _lock:
        for row in rows:
            outcome = _convert_one(
                row["path"], settings.webp_quality, settings.webp_skip_larger
            )
            status = outcome["status"]
            if status == "converted":
                converted += 1
                saved += int(outcome.get("saved") or 0)
            elif status == "already":
                with writer() as conn, conn:
                    conn.execute(
                        "UPDATE files SET webp_optimized = 1 WHERE path = ?",
                        (row["path"],),
                    )
            elif status == "skipped":
                skipped += 1
                with writer() as conn, conn:
                    conn.execute(
                        "UPDATE files SET webp_optimized = -1 WHERE path = ?",
                        (row["path"],),
                    )
                    _saved, _converted, skipped_total = _stats(conn)
                    conn.execute(
                        "UPDATE webp_stats SET skipped_count = ? WHERE id = 1",
                        (skipped_total + 1,),
                    )
            else:
                failed += 1
                errors.append(
                    {"path": row["path"], "error": outcome.get("error", "missing")}
                )
                with writer() as conn, conn:
                    conn.execute(
                        "UPDATE files SET webp_optimized = -1 WHERE path = ?",
                        (row["path"],),
                    )

    return {
        "converted": converted,
        "skipped": skipped,
        "failed": failed,
        "saved_bytes": saved,
        "saved_formatted": format_size(saved),
        "remaining": pending_count(folder),
        "errors": errors[:10],
        "quality": settings.webp_quality,
        "skip_larger": settings.webp_skip_larger,
    }


def status() -> dict[str, Any]:
    settings = get_settings()
    with reader() as conn:
        saved, converted, skipped = _stats(conn)
    return {
        "pending": pending_count(),
        "saved_bytes": saved,
        "saved_formatted": format_size(saved),
        "converted": converted,
        "skipped": skipped,
        "quality": settings.webp_quality,
        "skip_larger": settings.webp_skip_larger,
        "scope": "loose images; ZIP members stay with the current app",
        "updated_at": time.time(),
    }
