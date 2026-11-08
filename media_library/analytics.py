"""Index-only analytics for the library.

Every figure comes from SQLite: no ``os.walk``, no ``os.scandir``, no media
probe, no ``os.stat`` on a library file. That is what keeps the dashboard
instant over a FUSE mount, and ``tests/test_v2_analytics.py`` enforces it by
poisoning the filesystem functions during the request.
"""

from __future__ import annotations

import os
import threading
import time
from collections import defaultdict
from typing import Any

from media_library import library
from media_library.config import get_settings
from media_library.db import reader

_CACHE_TTL = 30.0
_cache_lock = threading.Lock()
_cache: dict[str, Any] = {"key": None, "at": 0.0, "payload": None}

_VIDEO_EXTS = {".mp4", ".m4v", ".mkv", ".webm", ".mov", ".avi", ".wmv", ".flv"}


def _pct(part: float, whole: float) -> float:
    return round((part / whole) * 100, 2) if whole else 0.0


def _format_duration(seconds: float) -> str:
    total = int(seconds or 0)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def _totals(conn) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT COUNT(*) AS files,
               COALESCE(SUM(size), 0) AS bytes,
               COALESCE(SUM(type = 'video'), 0) AS videos,
               COALESCE(SUM(type = 'image'), 0) AS images,
               COALESCE(SUM(CASE WHEN type = 'video' THEN duration END), 0) AS runtime
        FROM files
        """
    ).fetchone()
    folders = conn.execute("SELECT COUNT(*) AS n FROM folders").fetchone()["n"]
    zips = conn.execute(
        "SELECT COUNT(*) AS n FROM folders WHERE is_zip = 1"
    ).fetchone()["n"]
    return {
        "files": row["files"] or 0,
        "folders": folders or 0,
        "zips": zips or 0,
        "videos": row["videos"] or 0,
        "images": row["images"] or 0,
        "bytes": row["bytes"] or 0,
        "runtime": row["runtime"] or 0.0,
        "runtime_formatted": _format_duration(row["runtime"] or 0.0),
        "size_formatted": library.format_size(row["bytes"] or 0),
    }


def _by_type(conn, total_bytes: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT type, COUNT(*) AS files, COALESCE(SUM(size), 0) AS bytes
        FROM files GROUP BY type ORDER BY bytes DESC
        """
    ).fetchall()
    return [
        {
            "type": row["type"] or "other",
            "files": row["files"],
            "bytes": row["bytes"],
            "share": _pct(row["bytes"], total_bytes),
        }
        for row in rows
    ]


def _by_extension(
    conn, total_bytes: int, total_files: int, limit: int = 12
) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT ext, COUNT(*) AS files, COALESCE(SUM(size), 0) AS bytes
        FROM files GROUP BY ext ORDER BY bytes DESC
        """
    ).fetchall()
    top = [
        {
            "ext": row["ext"] or "",
            "files": row["files"],
            "bytes": row["bytes"],
            "share": _pct(row["bytes"], total_bytes),
        }
        for row in rows[:limit]
    ]
    rest = rows[limit:]
    other = {
        "ext": "other",
        "files": sum(row["files"] for row in rest),
        "bytes": sum(row["bytes"] for row in rest),
        "share": _pct(sum(row["bytes"] for row in rest), total_bytes),
    }
    return {
        "items": top + ([other] if other["files"] else []),
        "total_files": total_files,
    }


def _derive_root(meta: dict[str, dict]) -> str:
    configured = str(get_settings().library_dir)
    if configured in meta:
        return configured
    if not meta:
        return configured
    return min(meta, key=lambda path: (path.count(os.sep), len(path)))


def _rollup(conn, total_bytes: int) -> dict[str, Any]:
    meta: dict[str, dict] = {}
    for row in conn.execute("SELECT path, parent_path, name, is_zip FROM folders"):
        path = row["path"]
        if not path:
            continue
        meta[path] = {
            "name": row["name"] or os.path.basename(path) or path,
            "parent": row["parent_path"],
            "is_zip": bool(row["is_zip"]),
        }
    root = _derive_root(meta)
    meta.setdefault(root, {"name": os.path.basename(root) or root, "parent": None})

    parent_of: dict[str, str | None] = {root: None}
    for path in meta:
        if path == root:
            continue
        if not path.startswith(root + os.sep):
            continue
        cursor = meta[path]["parent"] or os.path.dirname(path)
        seen: set[str] = set()
        while cursor and cursor not in seen and cursor != root:
            seen.add(cursor)
            if cursor in meta and cursor.startswith(root + os.sep):
                break
            cursor = meta.get(cursor, {}).get("parent") or os.path.dirname(cursor)
        parent_of[path] = cursor if cursor in meta else root

    children: dict[str, list[str]] = defaultdict(list)
    for path, parent in parent_of.items():
        if parent:
            children[parent].append(path)

    sizes: dict[str, int] = defaultdict(int)
    counts: dict[str, int] = defaultdict(int)
    for row in conn.execute(
        "SELECT folder_path, COUNT(*) AS c, COALESCE(SUM(size), 0) AS s "
        "FROM files GROUP BY folder_path"
    ):
        folder = row["folder_path"]
        if not folder:
            continue
        node = folder if folder in parent_of else None
        if node is None:
            cursor = folder
            seen: set[str] = set()
            while cursor and cursor not in seen:
                seen.add(cursor)
                if cursor in parent_of:
                    node = cursor
                    break
                cursor = os.path.dirname(cursor)
        node = node or root
        sizes[node] += int(row["s"] or 0)
        counts[node] += int(row["c"] or 0)

    depth: dict[str, int] = {}
    for path in parent_of:
        steps = 0
        cursor = parent_of.get(path)
        seen = set()
        while cursor and cursor not in seen:
            seen.add(cursor)
            steps += 1
            cursor = parent_of.get(cursor)
        depth[path] = steps
    for path in sorted(parent_of, key=lambda value: depth[value], reverse=True):
        parent = parent_of.get(path)
        if parent:
            sizes[parent] += sizes.get(path, 0)
            counts[parent] += counts.get(path, 0)

    return {
        "root": root,
        "sizes": sizes,
        "counts": counts,
        "meta": meta,
        "parent_of": parent_of,
        "children": children,
        "total_bytes": total_bytes,
    }


def _entry(path: str, rollup: dict[str, Any], include_children: bool = False) -> dict:
    size = int(rollup["sizes"].get(path, 0))
    entry = {
        "path": path,
        "name": rollup["meta"].get(path, {}).get("name") or os.path.basename(path),
        "bytes": size,
        "files": int(rollup["counts"].get(path, 0)),
        "share": _pct(size, rollup["total_bytes"]),
        "is_group": False,
    }
    if include_children:
        kids = sorted(
            rollup["children"].get(path, []),
            key=lambda child: rollup["sizes"].get(child, 0),
            reverse=True,
        )[:12]
        entry["children"] = [
            {
                "name": rollup["meta"].get(child, {}).get("name")
                or os.path.basename(child),
                "path": child,
                "bytes": int(rollup["sizes"].get(child, 0)),
                "files": int(rollup["counts"].get(child, 0)),
            }
            for child in kids
            if rollup["sizes"].get(child, 0) > 0
        ]
    return entry


def _folders(conn, total_bytes: int, folder_limit: int) -> dict[str, Any]:
    rollup = _rollup(conn, total_bytes)
    root = rollup["root"]
    ranked = sorted(
        (path for path in rollup["parent_of"] if path != root),
        key=lambda path: rollup["sizes"].get(path, 0),
        reverse=True,
    )

    largest: list[dict] = []
    chosen: set[str] = set()
    for path in ranked:
        cursor = rollup["parent_of"].get(path)
        covered = False
        while cursor:
            if cursor in chosen:
                covered = True
                break
            cursor = rollup["parent_of"].get(cursor)
        if covered:
            continue
        chosen.add(path)
        largest.append(_entry(path, rollup))
        if len(largest) >= folder_limit:
            break

    top_children = sorted(
        rollup["children"].get(root, []),
        key=lambda child: rollup["sizes"].get(child, 0),
        reverse=True,
    )
    included = top_children[:folder_limit]
    remainder = top_children[folder_limit:]
    top = [_entry(path, rollup, include_children=True) for path in included]
    if remainder:
        top.append(
            {
                "name": f"Smaller folders ({len(remainder)})",
                "path": "",
                "bytes": sum(rollup["sizes"].get(path, 0) for path in remainder),
                "files": sum(rollup["counts"].get(path, 0) for path in remainder),
                "share": _pct(
                    sum(rollup["sizes"].get(path, 0) for path in remainder),
                    total_bytes,
                ),
                "is_group": True,
            }
        )

    return {
        "root": root,
        "root_bytes": int(rollup["sizes"].get(root, 0)),
        "root_files": int(rollup["counts"].get(root, 0)),
        "top": top,
        "largest": largest,
        "folder_count": len(rollup["parent_of"]),
    }


def _quality(conn) -> dict[str, Any]:
    buckets = {
        "4K": 0,
        "1440p": 0,
        "1080p": 0,
        "720p": 0,
        "SD": 0,
        "unprobed": 0,
    }
    probed = 0
    high_fps = 0
    runtime = 0.0
    for row in conn.execute(
        "SELECT width, height, fps, duration FROM files WHERE type = 'video'"
    ):
        runtime += float(row["duration"] or 0)
        width = row["width"] or 0
        height = row["height"] or 0
        if not width or not height:
            buckets["unprobed"] += 1
            continue
        probed += 1
        if row["fps"] and float(row["fps"]) >= 50:
            high_fps += 1
        longest = max(width, height)
        if longest >= 3800:
            buckets["4K"] += 1
        elif longest >= 2500:
            buckets["1440p"] += 1
        elif longest >= 1900:
            buckets["1080p"] += 1
        elif longest >= 1200:
            buckets["720p"] += 1
        else:
            buckets["SD"] += 1
    return {
        "buckets": [
            {"label": label, "count": count} for label, count in buckets.items()
        ],
        "probed": probed,
        "high_fps": high_fps,
        "runtime": runtime,
        "runtime_formatted": _format_duration(runtime),
    }


def _reclaimable(conn) -> dict[str, Any]:
    duplicates = conn.execute(
        "SELECT COUNT(*) AS groups, COALESCE(SUM(n), 0) AS files, "
        "COALESCE(SUM(size * (n - 1)), 0) AS bytes FROM ("
        "  SELECT size, COUNT(*) AS n FROM files WHERE size > 0 "
        "  GROUP BY size HAVING n > 1"
        ")"
    ).fetchone()
    stats = conn.execute(
        "SELECT saved_bytes, converted_count, skipped_count FROM webp_stats WHERE id = 1"
    ).fetchone()
    pending_webp = conn.execute(
        "SELECT COUNT(*) AS n FROM files WHERE type = 'image' AND ext != '.webp' "
        "AND COALESCE(webp_optimized, 0) = 0"
    ).fetchone()["n"]
    empty = conn.execute(
        """
        SELECT COUNT(*) AS n FROM folders f
        WHERE NOT EXISTS (SELECT 1 FROM files x WHERE x.folder_path = f.path)
          AND NOT EXISTS (SELECT 1 FROM folders c WHERE c.parent_path = f.path)
        """
    ).fetchone()["n"]
    return {
        "duplicates": {
            "groups": duplicates["groups"] or 0,
            "files": duplicates["files"] or 0,
            "bytes": duplicates["bytes"] or 0,
            "size_formatted": library.format_size(duplicates["bytes"] or 0),
        },
        "webp": {
            "saved_bytes": (stats["saved_bytes"] if stats else 0) or 0,
            "saved_formatted": library.format_size(
                (stats["saved_bytes"] if stats else 0) or 0
            ),
            "converted": (stats["converted_count"] if stats else 0) or 0,
            "pending": pending_webp or 0,
        },
        "empty_folders": empty or 0,
    }


def _health(conn) -> dict[str, Any]:
    def scalar(sql: str) -> int:
        return int(conn.execute(sql).fetchone()[0] or 0)

    return {
        "missing_thumbnails": scalar(
            "SELECT COUNT(*) FROM files WHERE has_thumb = 0 "
            "AND type IN ('video', 'image')"
        ),
        "unprobed": scalar("SELECT COUNT(*) FROM files WHERE width IS NULL"),
        "missing_visual_hashes": scalar(
            "SELECT COUNT(*) FROM files WHERE thumb_hash IS NULL"
        ),
        "missing_animated": scalar(
            "SELECT COUNT(*) FROM files WHERE type = 'video' AND has_animated = 0"
        ),
        "needs_transcode": scalar(
            "SELECT COUNT(*) FROM files WHERE needs_transcode = 1"
        ),
        "ignored_pairs": scalar("SELECT COUNT(*) FROM ignored_duplicates"),
    }


def _largest_files(conn, limit: int) -> list[dict]:
    rows = conn.execute(
        """
        SELECT f.*, CASE WHEN fav.path IS NOT NULL THEN 1 ELSE 0 END AS is_favorite
        FROM files f
        LEFT JOIN favorites fav ON f.path = fav.path
        WHERE f.size > 0
        ORDER BY f.size DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [library.shape_file_row(row) for row in rows]


def overview(
    folder_limit: int = 15, file_limit: int = 25, refresh: bool = False
) -> dict[str, Any]:
    key = f"{folder_limit}:{file_limit}"
    now = time.monotonic()
    with _cache_lock:
        if (
            not refresh
            and _cache["key"] == key
            and _cache["payload"] is not None
            and now - _cache["at"] < _CACHE_TTL
        ):
            return _cache["payload"]

    with reader() as conn:
        totals = _totals(conn)
        payload = {
            "generated_at": time.time(),
            "library_root": str(get_settings().library_dir),
            "limits": {"folders": folder_limit, "files": file_limit},
            "totals": totals,
            "by_type": _by_type(conn, totals["bytes"]),
            "by_extension": _by_extension(conn, totals["bytes"], totals["files"]),
            "folders": _folders(conn, totals["bytes"], folder_limit),
            "largest_files": _largest_files(conn, file_limit),
            "quality": _quality(conn),
            "reclaimable": _reclaimable(conn),
            "health": _health(conn),
        }

    with _cache_lock:
        _cache.update({"key": key, "at": now, "payload": payload})
    return payload


def invalidate() -> None:
    with _cache_lock:
        _cache.update({"key": None, "at": 0.0, "payload": None})
