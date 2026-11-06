"""Rewrite the images inside a ZIP archive to WebP.

Images in archives are deliberately excluded from the loose-image optimizer:
rewriting an archive is an archive-level operation. This module streams every
member into a new archive on local staging (converting JPEG/PNG/BMP/TIFF to
WebP, optionally skipping when the result would be larger), verifies it, swaps
it in atomically, purges the member cache and re-indexes the archive.
"""

from __future__ import annotations

import io
import os
import shutil
import threading
import uuid
import zipfile
from pathlib import Path
from typing import Any

from media_library_v2 import writes, zip_ops
from media_library_v2.config import get_settings
from media_library_v2.db import writer
from media_library_v2.paths import format_size, resolve_library_path
from media_library_v2.zipfs import purge_cache

_CONVERTIBLE = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff"}
_SKIP = {".DS_Store", "Thumbs.db", "thumbs.db", "desktop.ini"}
_lock = threading.Lock()


def _webp_bytes(data: bytes, quality: int) -> bytes:
    from PIL import Image, ImageOps

    with Image.open(io.BytesIO(data)) as image:
        source_format = (image.format or "").upper()
        image = ImageOps.exif_transpose(image)
        if image.mode in ("RGBA", "LA") or (
            image.mode == "P" and "transparency" in image.info
        ):
            image = image.convert("RGBA")
        else:
            image = image.convert("RGB")
        output = io.BytesIO()
        if source_format == "PNG":
            image.save(output, "WEBP", lossless=True, method=6)
        else:
            image.save(output, "WEBP", quality=quality, method=6)
        return output.getvalue()


def _unique_member(name: str, taken: set[str]) -> str:
    if name not in taken:
        return name
    stem, ext = os.path.splitext(name)
    counter = 1
    while True:
        candidate = f"{stem} ({counter}){ext}"
        if candidate not in taken:
            return candidate
        counter += 1


def optimize_archive(
    archive_path: str,
    quality: int | None = None,
    skip_larger: bool | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    archive = Path(resolve_library_path(archive_path))
    if not archive.is_file() or archive.suffix.lower() != ".zip":
        raise writes.WriteError("ZIP archive not found", 404)
    q = quality if quality is not None else settings.webp_quality
    skip = settings.webp_skip_larger if skip_larger is None else skip_larger

    with _lock:
        staging = settings.zip_staging_dir
        staging.mkdir(parents=True, exist_ok=True)
        staged = staging / f"webp-{uuid.uuid4().hex}.zip"
        converted = 0
        skipped = 0
        saved = 0
        mappings: list[tuple[str, str]] = []
        try:
            with zipfile.ZipFile(archive) as source_zip:
                taken: set[str] = set()
                with zipfile.ZipFile(
                    staged, "w", zipfile.ZIP_DEFLATED, compresslevel=6
                ) as target_zip:
                    for info in source_zip.infolist():
                        name = info.filename
                        if info.is_dir():
                            target_zip.writestr(info, b"")
                            taken.add(name)
                            continue
                        if os.path.basename(name) in _SKIP:
                            target_zip.writestr(info, source_zip.read(info))
                            taken.add(name)
                            continue
                        ext = os.path.splitext(name)[1].lower()
                        if ext not in _CONVERTIBLE:
                            target_zip.writestr(info, source_zip.read(info))
                            taken.add(name)
                            continue
                        data = source_zip.read(info)
                        try:
                            webp = _webp_bytes(data, q)
                        except Exception:
                            target_zip.writestr(info, data)
                            taken.add(name)
                            continue
                        if skip and len(webp) >= len(data):
                            target_zip.writestr(info, data)
                            taken.add(name)
                            skipped += 1
                            continue
                        new_name = _unique_member(
                            os.path.splitext(name)[0] + ".webp", taken
                        )
                        new_info = zipfile.ZipInfo(new_name, date_time=info.date_time)
                        new_info.compress_type = zipfile.ZIP_DEFLATED
                        new_info.external_attr = info.external_attr
                        target_zip.writestr(new_info, webp)
                        taken.add(new_name)
                        converted += 1
                        saved += max(0, len(data) - len(webp))
                        mappings.append((name, new_name))

            with zipfile.ZipFile(staged) as check:
                bad = check.testzip()
                if bad:
                    raise writes.WriteError(
                        f"Rewritten archive failed verification at {bad}", 500
                    )
                infos = check.infolist()

            if converted == 0:
                return {
                    "archive": str(archive),
                    "changed": False,
                    "converted": 0,
                    "skipped": skipped,
                    "saved_bytes": 0,
                    "saved_formatted": "0 B",
                    "members": len(infos),
                }

            target_tmp = archive.with_name(f".tmp-{uuid.uuid4().hex}-{archive.name}")
            shutil.copyfile(staged, target_tmp)
            if target_tmp.stat().st_size != staged.stat().st_size:
                target_tmp.unlink(missing_ok=True)
                raise writes.WriteError("Archive copy was truncated", 500)
            os.replace(target_tmp, archive)

            with writer() as conn, conn:
                for old, new in mappings:
                    conn.execute(
                        "UPDATE favorites SET path = ? WHERE path = ?",
                        (f"{archive}/{new}", f"{archive}/{old}"),
                    )
                zip_ops.clear_archive_members(conn, str(archive))
                zip_ops.index_archive(conn, str(archive), infos)
                row = conn.execute(
                    "SELECT saved_bytes, converted_count, skipped_count "
                    "FROM webp_stats WHERE id = 1"
                ).fetchone()
                base_saved = int(row["saved_bytes"] or 0) if row else 0
                base_converted = int(row["converted_count"] or 0) if row else 0
                base_skipped = int(row["skipped_count"] or 0) if row else 0
                conn.execute(
                    "UPDATE webp_stats SET saved_bytes = ?, converted_count = ?, "
                    "skipped_count = ? WHERE id = 1",
                    (
                        base_saved + saved,
                        base_converted + converted,
                        base_skipped + skipped,
                    ),
                )
            purge_cache(str(archive))
            return {
                "archive": str(archive),
                "changed": True,
                "converted": converted,
                "skipped": skipped,
                "saved_bytes": saved,
                "saved_formatted": format_size(saved),
                "members": len(infos),
                "size_formatted": format_size(archive.stat().st_size),
            }
        finally:
            try:
                staged.unlink(missing_ok=True)
            except OSError:
                pass
