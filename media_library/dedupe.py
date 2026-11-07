"""Deduplication: content matching and visual (pHash) clustering.

Hashes are stored in the fixed columns, with the documented formats, so the
two apps agree about what has already been hashed:

- ``quick_hash``: ``sha256(first 128 KB + str(size))`` hex (``empty_file`` for
  zero-byte files, ``unhashable`` when a read fails).
- ``thumb_hash``: 16-hex DCT pHash of the cached poster (``blank_thumb`` /
  ``no_thumb`` sentinels).

Content deletion is always preceded by a full byte-for-byte comparison; the
128 KB head hash only narrows the candidates.
"""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import time
from collections import defaultdict
from typing import Any

from media_library import writes
from media_library.cache import thumbnail_path
from media_library.db import reader, writer
from media_library.library import shape_file_row
from media_library.paths import is_zip_member, resolve_library_path

_HEAD_BYTES = 128 * 1024
_CHUNK = 1024 * 1024
_PHASH_RE = re.compile(r"^[0-9a-f]{16}$")

_FILE_SQL = """
SELECT f.*, CASE WHEN fav.path IS NOT NULL THEN 1 ELSE 0 END AS is_favorite
FROM files f
LEFT JOIN favorites fav ON f.path = fav.path
"""


def quick_hash(path: str) -> str | None:
    """Fast content hash: 128 KB head plus the exact byte size (format is fixed)."""
    try:
        size = os.path.getsize(path)
        if size == 0:
            return "empty_file"
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            digest.update(handle.read(_HEAD_BYTES))
        digest.update(str(size).encode("utf-8"))
        return digest.hexdigest()
    except OSError:
        return None


def files_identical(a: str, b: str) -> bool:
    """Full byte-for-byte comparison, streamed so huge files stay bounded."""
    try:
        if os.path.getsize(a) != os.path.getsize(b):
            return False
        with open(a, "rb") as left, open(b, "rb") as right:
            while True:
                chunk_a = left.read(_CHUNK)
                chunk_b = right.read(_CHUNK)
                if chunk_a != chunk_b:
                    return False
                if not chunk_a:
                    return True
    except OSError:
        return False


def thumbnail_phash(thumb: str) -> str | None:
    """pHash of a cached poster, with a blank-image guard."""
    try:
        import imagehash
        from PIL import Image, ImageStat

        with Image.open(thumb) as image:
            stats = ImageStat.Stat(image)
            average_std = sum(stats.stddev) / len(stats.stddev)
            average_mean = sum(stats.mean) / len(stats.mean)
            if average_std < 8.0 or average_mean < 8.0:
                return "blank_thumb"
            return str(imagehash.phash(image))
    except Exception:
        return "no_thumb"


def _ignored_pairs(conn) -> set[frozenset[str]]:
    try:
        rows = conn.execute("SELECT path1, path2 FROM ignored_duplicates").fetchall()
    except sqlite3.Error:
        return set()
    return {frozenset((row["path1"], row["path2"])) for row in rows}


def _pair_ignored(ignored: set[frozenset[str]], a: str, b: str) -> bool:
    return frozenset((a, b)) in ignored


def _group_fully_ignored(paths: list[str], ignored: set[frozenset[str]]) -> bool:
    if len(paths) < 2:
        return True
    for index, path in enumerate(paths):
        for other in paths[index + 1 :]:
            if not _pair_ignored(ignored, path, other):
                return False
    return True


def _candidate_sizes(conn) -> list[int]:
    rows = conn.execute(
        "SELECT size FROM files WHERE size > 0 GROUP BY size "
        "HAVING COUNT(*) > 1 ORDER BY size DESC"
    ).fetchall()
    return [int(row["size"]) for row in rows]


def _candidate_rows(conn, sizes: list[int]) -> list[sqlite3.Row]:
    if not sizes:
        return []
    placeholders = ",".join("?" for _ in sizes)
    return conn.execute(
        f"{_FILE_SQL} WHERE f.size IN ({placeholders}) ORDER BY f.size DESC, f.path",
        sizes,
    ).fetchall()


def content_groups(limit: int = 25, offset: int = 0) -> dict[str, Any]:
    with reader() as conn:
        rows = _candidate_rows(conn, _candidate_sizes(conn))
        ignored = _ignored_pairs(conn)

    bucket: dict[tuple[int, str], list[sqlite3.Row]] = defaultdict(list)
    unhashed = 0
    for row in rows:
        if is_zip_member(row["path"]):
            continue
        digest = row["quick_hash"]
        if not digest or digest in ("unhashable", "empty_file"):
            unhashed += 1
            continue
        bucket[(int(row["size"] or 0), digest)].append(row)

    groups = []
    for (size, digest), members in bucket.items():
        if len(members) < 2:
            continue
        paths = [row["path"] for row in members]
        if _group_fully_ignored(paths, ignored):
            continue
        groups.append(
            {
                "id": digest[:16],
                "match_type": "content",
                "size": size,
                "size_formatted": members[0]["size_formatted"],
                "reclaimable": size * (len(members) - 1),
                "files": [shape_file_row(row) for row in members],
            }
        )

    groups.sort(key=lambda group: group["reclaimable"], reverse=True)
    total = len(groups)
    page = groups[offset : offset + limit]
    return {
        "mode": "content",
        "total_groups": total,
        "total_files": sum(len(group["files"]) for group in groups),
        "unhashed": unhashed,
        "groups": page,
    }


def _hamming(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def visual_groups(limit: int = 25, offset: int = 0) -> dict[str, Any]:
    with reader() as conn:
        rows = conn.execute(
            f"{_FILE_SQL} WHERE length(f.thumb_hash) = 16 ORDER BY f.path"
        ).fetchall()
        ignored = _ignored_pairs(conn)
        unhashed = conn.execute(
            "SELECT COUNT(*) AS n FROM files WHERE has_thumb = 1 AND thumb_hash IS NULL"
        ).fetchone()["n"]

    items: list[tuple[sqlite3.Row, int]] = []
    for row in rows:
        value = (row["thumb_hash"] or "").lower()
        if not _PHASH_RE.match(value):
            continue
        items.append((row, int(value, 16)))

    # Two 64-bit hashes within distance 2 must share at least two of four
    # 16-bit bands (pigeonhole), so banding finds every close pair without an
    # O(n^2) scan.
    bands: list[dict[int, list[int]]] = [defaultdict(list) for _ in range(4)]
    for index, (_row, value) in enumerate(items):
        for band in range(4):
            bands[band][(value >> (band * 16)) & 0xFFFF].append(index)

    # Complete-linkage growth: every member of a group is within distance 2 of
    # every other member. Plain union-find lets A~B~C chain into a 50-file blob
    # of unrelated scenes; this keeps each group genuinely cohesive. Groups are
    # also capped, because a visual match is a hint, not proof.
    max_group = 12
    clusters: list[list[int]] = []
    cluster_of: dict[int, int] = {}

    for index, (row, value) in enumerate(items):
        candidates: set[int] = set()
        for band in range(4):
            candidates.update(bands[band][(value >> (band * 16)) & 0xFFFF])
        candidate_clusters: list[int] = []
        for other in candidates:
            cluster = cluster_of.get(other)
            if cluster is not None and cluster not in candidate_clusters:
                candidate_clusters.append(cluster)
        joined = False
        for cluster in candidate_clusters:
            members = clusters[cluster]
            if len(members) >= max_group:
                continue
            if not all(_hamming(value, items[member][1]) <= 2 for member in members):
                continue
            if any(
                _pair_ignored(ignored, row["path"], items[member][0]["path"])
                for member in members
            ):
                continue
            members.append(index)
            cluster_of[index] = cluster
            joined = True
            break
        if not joined:
            cluster_of[index] = len(clusters)
            clusters.append([index])

    groups = []
    for members in clusters:
        if len(members) < 2:
            continue
        member_rows = [items[index][0] for index in members]
        sizes = [int(row["size"] or 0) for row in member_rows]
        groups.append(
            {
                "id": "v"
                + hashlib.sha1(
                    "|".join(sorted(row["path"] for row in member_rows)).encode(
                        "utf-8", "surrogatepass"
                    )
                ).hexdigest()[:15],
                "match_type": "visual",
                "size": sum(sizes),
                "size_formatted": member_rows[0]["size_formatted"],
                "reclaimable": max(0, sum(sizes) - max(sizes)),
                "files": [shape_file_row(row) for row in member_rows],
            }
        )

    groups.sort(key=lambda group: group["reclaimable"], reverse=True)
    total = len(groups)
    page = groups[offset : offset + limit]
    return {
        "mode": "visual",
        "total_groups": total,
        "total_files": sum(len(group["files"]) for group in groups),
        "unhashed": unhashed,
        "groups": page,
    }


def status() -> dict[str, Any]:
    with reader() as conn:
        candidate_files = conn.execute(
            "SELECT COUNT(*) AS n FROM files WHERE size > 0 AND size IN ("
            "SELECT size FROM files WHERE size > 0 GROUP BY size HAVING COUNT(*) > 1)"
        ).fetchone()["n"]
        unhashed_content = conn.execute(
            "SELECT COUNT(*) AS n FROM files WHERE size > 0 AND quick_hash IS NULL "
            "AND size IN (SELECT size FROM files WHERE size > 0 "
            "GROUP BY size HAVING COUNT(*) > 1)"
        ).fetchone()["n"]
        visual_ready = conn.execute(
            "SELECT COUNT(*) AS n FROM files WHERE length(thumb_hash) = 16"
        ).fetchone()["n"]
        visual_unhashed = conn.execute(
            "SELECT COUNT(*) AS n FROM files WHERE has_thumb = 1 AND thumb_hash IS NULL"
        ).fetchone()["n"]
        ignored = conn.execute(
            "SELECT COUNT(*) AS n FROM ignored_duplicates"
        ).fetchone()["n"]
    return {
        "content": {
            "candidate_files": candidate_files or 0,
            "unhashed": unhashed_content or 0,
        },
        "visual": {
            "hashed": visual_ready or 0,
            "unhashed": visual_unhashed or 0,
        },
        "ignored": ignored or 0,
    }


def hash_content(limit: int = 50) -> dict[str, Any]:
    """Hash up to ``limit`` candidate files that have no quick hash yet."""
    hashed = 0
    failed = 0
    with reader() as conn:
        rows = conn.execute(
            """
            SELECT f.path FROM files f
            WHERE f.size > 0 AND f.quick_hash IS NULL
              AND f.size IN (SELECT size FROM files WHERE size > 0
                             GROUP BY size HAVING COUNT(*) > 1)
            ORDER BY f.size DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    updates: list[tuple[str, str]] = []
    for row in rows:
        path = row["path"]
        if is_zip_member(path):
            continue
        digest = quick_hash(path)
        if digest is None:
            failed += 1
            digest = "unhashable"
        else:
            hashed += 1
        updates.append((digest, path))
    if updates:
        with writer() as conn, conn:
            conn.executemany("UPDATE files SET quick_hash = ? WHERE path = ?", updates)
    with reader() as conn:
        remaining = conn.execute(
            "SELECT COUNT(*) AS n FROM files WHERE size > 0 AND quick_hash IS NULL "
            "AND size IN (SELECT size FROM files WHERE size > 0 "
            "GROUP BY size HAVING COUNT(*) > 1)"
        ).fetchone()["n"]
    return {"hashed": hashed, "failed": failed, "remaining": remaining or 0}


def hash_visual(limit: int = 50) -> dict[str, Any]:
    """Compute pHashes for up to ``limit`` files that have a cached poster."""
    with reader() as conn:
        rows = conn.execute(
            "SELECT path, mtime, size FROM files WHERE has_thumb = 1 "
            "AND thumb_hash IS NULL ORDER BY path LIMIT ?",
            (limit,),
        ).fetchall()
    updates: list[tuple[str, str]] = []
    hashed = 0
    for row in rows:
        thumb = thumbnail_path(
            row["path"], float(row["mtime"] or 0), int(row["size"] or 0)
        )
        value = thumbnail_phash(str(thumb)) if thumb.is_file() else "no_thumb"
        if _PHASH_RE.match(value or ""):
            hashed += 1
        updates.append((value or "no_thumb", row["path"]))
    if updates:
        with writer() as conn, conn:
            conn.executemany("UPDATE files SET thumb_hash = ? WHERE path = ?", updates)
    with reader() as conn:
        remaining = conn.execute(
            "SELECT COUNT(*) AS n FROM files WHERE has_thumb = 1 AND thumb_hash IS NULL"
        ).fetchone()["n"]
    return {"hashed": hashed, "remaining": remaining or 0}


def verify_pair(keep: str, other: str) -> dict[str, Any]:
    keep_path = resolve_library_path(keep)
    other_path = resolve_library_path(other)
    if not os.path.isfile(keep_path) or not os.path.isfile(other_path):
        raise writes.WriteError("One of the files no longer exists", 404)
    return {
        "keep": keep_path,
        "path": other_path,
        "identical": files_identical(keep_path, other_path),
    }


def resolve(keep: str, deletes: list[str], mode: str = "content") -> dict[str, Any]:
    keep_path = resolve_library_path(keep)
    if not os.path.isfile(keep_path):
        raise writes.WriteError("The file to keep was not found", 404)
    if not deletes:
        raise writes.WriteError("Choose at least one file to delete", 400)
    if mode not in ("content", "visual"):
        raise writes.WriteError("Unknown match type", 400)

    mismatched: list[str] = []
    candidates: list[str] = []
    for raw in deletes:
        path = resolve_library_path(raw)
        if path == keep_path:
            continue
        if not os.path.isfile(path):
            mismatched.append(path)
            continue
        if mode == "content" and not files_identical(keep_path, path):
            mismatched.append(path)
            continue
        candidates.append(path)

    deleted: list[str] = []
    failed: list[dict[str, str]] = []
    if candidates:
        result = writes.delete_files(candidates)
        deleted = result["deleted"]
        failed = result["failed"]

    return {
        "kept": keep_path,
        "deleted": deleted,
        "failed": failed,
        "mismatched": mismatched,
    }


def ignore_pair(path1: str, path2: str, match_type: str = "content") -> dict[str, Any]:
    if match_type not in ("content", "visual"):
        raise writes.WriteError("Unknown match type", 400)
    first = resolve_library_path(path1)
    second = resolve_library_path(path2)
    if first == second:
        raise writes.WriteError("A file cannot be paired with itself", 400)
    low, high = sorted((first, second))
    with writer() as conn, conn:
        conn.execute(
            "INSERT OR IGNORE INTO ignored_duplicates "
            "(path1, path2, match_type, created_at) VALUES (?, ?, ?, ?)",
            (low, high, match_type, time.time()),
        )
    return {"ignored": True, "path1": low, "path2": high, "match_type": match_type}


def unignore_pair(path1: str, path2: str) -> dict[str, Any]:
    first = resolve_library_path(path1)
    second = resolve_library_path(path2)
    low, high = sorted((first, second))
    with writer() as conn, conn:
        cursor = conn.execute(
            "DELETE FROM ignored_duplicates WHERE path1 = ? AND path2 = ?",
            (low, high),
        )
        removed = cursor.rowcount
    return {"unignored": bool(removed), "path1": low, "path2": high}


def ignored_page(limit: int = 50, offset: int = 0) -> dict[str, Any]:
    with reader() as conn:
        total = conn.execute("SELECT COUNT(*) AS n FROM ignored_duplicates").fetchone()[
            "n"
        ]
        rows = conn.execute(
            "SELECT id, path1, path2, match_type, created_at "
            "FROM ignored_duplicates ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
    return {
        "items": [
            {
                "id": row["id"],
                "path1": row["path1"],
                "path2": row["path2"],
                "match_type": row["match_type"],
                "created_at": row["created_at"],
            }
            for row in rows
        ],
        "total": total or 0,
        "limit": limit,
        "offset": offset,
    }
