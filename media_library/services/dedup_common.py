"""Deduplication primitives: hashing, pHash, pair normalisation and scoring."""

import hashlib
import logging
import os
import re
from pathlib import Path
from typing import Any

import imagehash
from PIL import Image, ImageStat

from media_library.db.connection import db_conn

logger = logging.getLogger(__name__)


visual_duplicates_cache: dict[str, Any] = {
    "timestamp": 0,
    "total_groups": 0,
    "total_reclaimable": 0,
    "groups": [],
    "db_hash_count": 0,
    "ignored_count": 0,
}


def invalidate_visual_cache():
    """Invalidates the in-memory visual duplicates cache."""
    visual_duplicates_cache["timestamp"] = 0


def compute_quick_hash(filepath: str, chunk_size: int = 131072) -> str | None:
    """Computes a fast 128KB head SHA-256 hash incorporating exact byte size."""
    try:
        if not os.path.exists(filepath) or not os.path.isfile(filepath):
            return None
        size = os.path.getsize(filepath)
        if size == 0:
            return "empty_file"
        hasher = hashlib.sha256()
        with open(filepath, "rb") as f:
            hasher.update(f.read(chunk_size))
        hasher.update(str(size).encode("utf-8"))
        return hasher.hexdigest()
    except Exception as e:
        logger.error(f"Error computing quick_hash for {filepath}: {e}")
        return None


def compute_thumbnail_phash(thumb_path: Path) -> str | None:
    """Computes a 64-bit DCT perceptual hash (pHash) from a cached JPEG thumbnail."""
    try:
        if not thumb_path.exists() or thumb_path.stat().st_size == 0:
            return None
        with Image.open(thumb_path) as img:
            stat = ImageStat.Stat(img)
            avg_std = sum(stat.stddev) / len(stat.stddev)
            avg_mean = sum(stat.mean) / len(stat.mean)
            if avg_std < 8.0 or avg_mean < 8.0:
                return "blank_thumb"
            return str(imagehash.phash(img))
    except Exception:
        return None


compute_thumbnail_dhash = compute_thumbnail_phash


def _files_have_identical_content(a: str, b: str) -> bool:
    """Full-content byte comparison.

    ``quick_hash`` only reads a 128 KB head chunk, so two same-size files can
    share a quick_hash while differing in the remainder. Before auto-deleting
    anything we compare the full contents to guarantee genuine duplicates.
    """
    try:
        if os.path.getsize(a) != os.path.getsize(b):
            return False
        chunk = 1024 * 1024
        with open(a, "rb") as fa, open(b, "rb") as fb:
            while True:
                ba = fa.read(chunk)
                bb = fb.read(chunk)
                if ba != bb:
                    return False
                if not ba:
                    return True
    except OSError:
        return False
    return False


def normalize_pair(p1: str, p2: str) -> tuple[str, str]:
    return (p1, p2) if p1 < p2 else (p2, p1)


def get_ignored_pairs_set(match_type: str | None = None) -> set[tuple[str, str]]:
    with db_conn() as conn:
        if match_type:
            rows = conn.execute(
                "SELECT path1, path2 FROM ignored_duplicates WHERE match_type = ?",
                (match_type,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT path1, path2 FROM ignored_duplicates"
            ).fetchall()
        return {(r["path1"], r["path2"]) for r in rows}


def score_duplicate_file(
    name: str, folder_path: str, mtime: float, size: int = 0
) -> float:
    score = 0.0
    if size > 0:
        score += size / (1024 * 1024 * 10)  # +1 point per 10MB
    if re.search(r"(_\d+|\(\d+\)|\bcopy\b|\.mp4\.mp4)", name, re.IGNORECASE):
        score -= 50.0
    if re.search(r"^[0-9a-z]{15,}_source", name, re.IGNORECASE):
        score -= 20.0
    if re.search(r"\d{4}-\d{2}-\d{2}", name):
        score += 15.0
    if " " in name or "-" in name:
        score += 5.0
    depth = len(Path(folder_path).parts)
    score += depth * 2.0
    score += mtime / 1e10
    return score
