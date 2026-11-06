"""Deterministic cache keys for poster frames and animated previews.

One implementation shared by streaming, writes and dedupe so a move/rename and
the dedupe worker can never disagree about where a poster lives. The key format
must stay identical to v1's: ``sha256(f"{path}_{mtime}_{size}")[:24]``.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from media_library_v2.config import get_settings


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
