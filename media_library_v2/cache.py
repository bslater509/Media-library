"""Deterministic cache keys for poster frames and animated previews.

One implementation shared by streaming, writes and dedupe so a move/rename and
the dedupe worker can never disagree about where a poster lives. The key format
must stay identical to v1's: ``sha256(f"{path}_{mtime}_{size}")[:24]``.

The owned runtime also sweeps artwork no indexed row can reference (see
``sweep_orphaned_artwork``); a strict 24-hex key check keeps an in-flight
generator's temp file safe.
"""

from __future__ import annotations

import hashlib
import re
import shutil
from pathlib import Path

from media_library_v2.config import get_settings
from media_library_v2.db import reader

_KEY_RE = re.compile(r"^[0-9a-f]{24}$")


def cache_key(path: str, mtime: float, size: int) -> str:
    payload = f"{path}_{mtime}_{size}"
    return hashlib.sha256(payload.encode("utf-8", "surrogatepass")).hexdigest()[:24]


def thumbnail_path(path: str, mtime: float, size: int) -> Path:
    return get_settings().thumbnail_dir / f"thumb_{cache_key(path, mtime, size)}.jpg"


def animated_path(path: str, mtime: float, size: int) -> Path:
    return (
        get_settings().thumbnail_dir
        / "animated"
        / f"anim_{cache_key(path, mtime, size)}.webp"
    )


def sweep_orphaned_artwork() -> dict[str, int]:
    """Delete posters, previews and chooser frames no indexed file can use.

    A delete, rename or archive rewrite performed outside the app leaves its
    cached artwork behind; v1's prune sweeps those keys, and v2's owned runtime
    needs the same housekeeping. Keys are recomputed from the index rows, so a
    preview generated for a file whose mtime changed is also swept. An empty
    index is never swept: that would wipe a good cache when a crawl could not
    read the mount.
    """
    with reader() as conn:
        rows = conn.execute("SELECT path, mtime, size FROM files").fetchall()
    if not rows:
        return {"thumbnails": 0, "animated": 0, "frames": 0, "skipped": 1}

    keys = {
        cache_key(row["path"], float(row["mtime"] or 0), int(row["size"] or 0))
        for row in rows
    }
    root = get_settings().thumbnail_dir
    if not root.is_dir():
        return {"thumbnails": 0, "animated": 0, "frames": 0, "skipped": 1}

    removed = {"thumbnails": 0, "animated": 0, "frames": 0, "skipped": 0}
    for entry in root.glob("thumb_*.jpg"):
        key = entry.stem.removeprefix("thumb_")
        if _KEY_RE.match(key) and key not in keys:
            try:
                entry.unlink()
                removed["thumbnails"] += 1
            except OSError:
                continue

    animated_dir = root / "animated"
    if animated_dir.is_dir():
        for entry in animated_dir.glob("anim_*.webp"):
            key = entry.stem.removeprefix("anim_")
            if _KEY_RE.match(key) and key not in keys:
                try:
                    entry.unlink()
                    removed["animated"] += 1
                except OSError:
                    continue

    frames_dir = root / "frames"
    if frames_dir.is_dir():
        for child in frames_dir.iterdir():
            if child.is_dir() and _KEY_RE.match(child.name) and child.name not in keys:
                try:
                    shutil.rmtree(child)
                    removed["frames"] += 1
                except OSError:
                    continue
    return removed
