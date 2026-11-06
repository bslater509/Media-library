"""On-demand poster generation for images whose cache entry is missing.

v1's worker covers most files, but ZIP members are deliberately skipped by its
thumbnail worker (they are WebP-converted on demand), and a handful of loose
images never got a poster. Generating one here — from the extracted member for
archives — keeps tiles cheap without loading full-resolution originals.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

from media_library_v2 import writes
from media_library_v2.cache import thumbnail_path
from media_library_v2.db import writer
from media_library_v2.paths import is_zip_member
from media_library_v2.zipfs import extract_member, split_zip_path


def generate_image_thumbnail(path: str, mtime: float, size: int) -> Path:
    from PIL import Image, ImageOps

    target = thumbnail_path(path, mtime, size)
    if target.is_file():
        return target

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
        with Image.open(source) as image:
            image = ImageOps.exif_transpose(image)
            image.thumbnail((360, 360))
            if image.mode not in ("RGB", "L"):
                image = image.convert("RGB")
            image.save(temp, "JPEG", quality=82)
        os.replace(temp, target)
    except OSError as exc:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
        raise writes.WriteError(f"Could not build a thumbnail: {exc}", 500) from exc
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass

    with writer() as conn, conn:
        conn.execute("UPDATE files SET has_thumb = 1 WHERE path = ?", (path,))
    return target
