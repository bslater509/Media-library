"""Index-only library analytics rollups for the ``/analytics`` dashboard.

Everything here is computed from the SQLite index alone (AGENTS.md Rule 2): no
``os.walk``, no remote ``os.stat``, no media decode. The expensive piece is the
recursive folder rollup, which runs over the ``folders``/``files`` rows in
memory, and the whole payload is memoized for ``INDEX_COUNT_CACHE_TTL_S`` keyed
on ``indexer_state["revision"]`` — the same invalidation key the unified status
API's count cache uses — so a crawl or a forced prune can never serve a stale
snapshot. The endpoint's ``refresh=1`` flag drops the cache for an explicit
manual reload (library ops such as moves/deletes do not bump ``revision``).
"""

import logging
import os
import threading
import time
from collections import defaultdict
from typing import Any

from media_library.core.config import DEFAULT_DESTINATION_DIR
from media_library.core.constants import INDEX_COUNT_CACHE_TTL_S
from media_library.core.state import format_size, state
from media_library.db.connection import db_conn
from media_library.db.indexer import indexer_state
from media_library.services.library_query import read_largest_files
from media_library.services.zip import find_batch_zip_candidate_folders

logger = logging.getLogger(__name__)

DEFAULT_FOLDER_LIMIT = 15
DEFAULT_FILE_LIMIT = 25
MAX_LIMIT = 100
TOP_EXTENSIONS = 12
_TREEMAP_CHILDREN = 12
_TREEMAP_TOP = 40

#: Ordered resolution buckets: ``(key, label, minimum largest dimension)``.
#: Portrait clips are classified by their longest edge, so a 2160x3840 phone
#: video reads as 4K rather than SD.
_RESOLUTION_BUCKETS = (
    ("4k", "4K", 3840),
    ("1440p", "1440p", 2560),
    ("1080p", "1080p", 1920),
    ("720p", "720p", 1280),
)
_SD_KEY = "sd"
_UNKNOWN_KEY = "unknown"
_BUCKET_ORDER = [k for k, _l, _m in _RESOLUTION_BUCKETS] + [_SD_KEY, _UNKNOWN_KEY]
_BUCKET_LABELS = {k: label for k, label, _m in _RESOLUTION_BUCKETS}
_BUCKET_LABELS[_SD_KEY] = "SD"
_BUCKET_LABELS[_UNKNOWN_KEY] = "n/a"

_HIGH_FPS_THRESHOLD = 50

#: Folders never worth ranking: the app's own snapshot backups.
_EXCLUDED_MARKERS = (".media_library_backup",)

_analytics_cache: dict[str, Any] = {
    "payload": None,
    "expires_at": 0.0,
    "revision": -1,
    "key": None,
}
_analytics_lock = threading.Lock()


def _is_within(path: str, root: str) -> bool:
    """True when ``path`` is ``root`` or sits beneath it (offline string check)."""
    if not path or not root:
        return False
    if path == root:
        return True
    return path.startswith(root + "/")


def _is_excluded(path: str) -> bool:
    return any(marker in path for marker in _EXCLUDED_MARKERS)


def _format_duration_long(seconds: float) -> str:
    """Render a runtime total as ``12d 7h`` / ``3h 12m`` / ``41m``."""
    try:
        total = int(float(seconds or 0))
    except (TypeError, ValueError):
        return "0h"
    if total <= 0:
        return "0h"
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def _percent(part: float, whole: float) -> float:
    if not whole:
        return 0.0
    return round(float(part) / float(whole) * 100.0, 1)


# --------------------------------------------------------------- totals


def _read_totals(conn) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT
            COUNT(*) AS files,
            COALESCE(SUM(size), 0) AS bytes,
            COUNT(CASE WHEN type = 'video' THEN 1 END) AS videos,
            COUNT(CASE WHEN type = 'image' THEN 1 END) AS images,
            COUNT(CASE WHEN type NOT IN ('video', 'image') THEN 1 END) AS other,
            COALESCE(SUM(CASE WHEN type = 'video' THEN size ELSE 0 END), 0) AS video_bytes,
            COALESCE(SUM(CASE WHEN type = 'image' THEN size ELSE 0 END), 0) AS image_bytes
        FROM files
        """
    ).fetchone()
    folder_row = conn.execute(
        """
        SELECT
            COUNT(*) AS folders,
            COUNT(CASE WHEN is_zip = 1 THEN 1 END) AS zip_folders
        FROM folders
        """
    ).fetchone()

    bytes_total = int(row["bytes"] or 0)
    return {
        "bytes": bytes_total,
        "bytes_formatted": format_size(bytes_total),
        "files": int(row["files"] or 0),
        "videos": int(row["videos"] or 0),
        "images": int(row["images"] or 0),
        "other": int(row["other"] or 0),
        "video_bytes": int(row["video_bytes"] or 0),
        "video_bytes_formatted": format_size(row["video_bytes"] or 0),
        "image_bytes": int(row["image_bytes"] or 0),
        "image_bytes_formatted": format_size(row["image_bytes"] or 0),
        "folders": int(folder_row["folders"] or 0),
        "zip_folders": int(folder_row["zip_folders"] or 0),
    }


def _read_by_type(conn, total_bytes: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT type, COUNT(*) AS count, COALESCE(SUM(size), 0) AS bytes
        FROM files
        GROUP BY type
        ORDER BY bytes DESC, count DESC
        """
    ).fetchall()
    items = []
    for r in rows:
        size = int(r["bytes"] or 0)
        items.append(
            {
                "type": r["type"] or "other",
                "count": int(r["count"] or 0),
                "bytes": size,
                "bytes_formatted": format_size(size),
                "percent_bytes": _percent(size, total_bytes),
            }
        )
    return items


def _read_by_extension(conn, total_bytes: int, total_files: int) -> dict[str, Any]:
    """Top extensions by bytes, plus a rolled-up remainder so shares sum to 100%."""
    rows = conn.execute(
        """
        SELECT ext, COUNT(*) AS count, COALESCE(SUM(size), 0) AS bytes
        FROM files
        GROUP BY ext
        ORDER BY bytes DESC, count DESC
        LIMIT ?
        """,
        (TOP_EXTENSIONS,),
    ).fetchall()

    items = []
    listed_bytes = 0
    listed_count = 0
    for r in rows:
        size = int(r["bytes"] or 0)
        listed_bytes += size
        listed_count += int(r["count"] or 0)
        items.append(
            {
                "ext": r["ext"] or "(none)",
                "count": int(r["count"] or 0),
                "bytes": size,
                "bytes_formatted": format_size(size),
                "percent_bytes": _percent(size, total_bytes),
            }
        )

    other_bytes = max(0, int(total_bytes) - listed_bytes)
    return {
        "items": items,
        "count": int(total_files),
        "bytes": int(total_bytes),
        "other_count": max(0, int(total_files) - listed_count),
        "other_bytes": other_bytes,
        "other_bytes_formatted": format_size(other_bytes),
        "other_percent_bytes": _percent(other_bytes, total_bytes),
    }


# ------------------------------------------------------- folder rollup


def _derive_library_root(meta: dict[str, dict]) -> str:
    """Pick the rollup root: the configured library dir when it is indexed,
    otherwise the shallowest indexed folder (the crawler's own root)."""
    configured = str(state.get("destination_dir") or DEFAULT_DESTINATION_DIR or "").rstrip("/")
    if configured and configured in meta:
        return configured
    candidates = [p for p in meta if p and not _is_excluded(p)]
    if not candidates:
        return configured
    return min(candidates, key=lambda p: (p.count("/"), len(p), p))


def _rollup_folders(conn, library_root: str) -> dict[str, Any]:
    """Aggregate every folder's recursive subtree size and file count.

    Returns ``{"root", "size", "count", "meta", "parent_of", "children"}`` with
    one entry per indexed folder beneath the library root.
    """
    meta: dict[str, dict] = {}
    for r in conn.execute("SELECT path, parent_path, name, is_zip FROM folders"):
        p = r["path"]
        if not p or p in meta:
            continue
        meta[p] = {
            "name": r["name"] or os.path.basename(p) or p,
            "parent": r["parent_path"],
            "is_zip": bool(r["is_zip"]),
        }
    if not meta:
        return {"root": library_root, "size": {}, "count": {}, "meta": {}, "parent_of": {}, "children": {}}

    root = _derive_library_root(meta) or library_root
    meta.setdefault(root, {"name": os.path.basename(root) or root, "parent": None, "is_zip": False})

    # Link every folder to its nearest ancestor that is itself included, so a
    # gap in the index (an unindexed intermediate directory) can never orphan a
    # subtree. Folders outside the derived root are dropped from the rollup.
    parent_of: dict[str, str | None] = {root: None}
    dropped = 0
    for path in meta:
        if path == root or _is_excluded(path):
            continue
        if not _is_within(path, root):
            dropped += 1
            continue
        cur = meta[path]["parent"] or os.path.dirname(path)
        seen: set[str] = set()
        resolved: str | None = None
        while cur and cur not in seen:
            seen.add(cur)
            if cur in meta and _is_within(cur, root):
                resolved = cur
                break
            cur = meta.get(cur, {}).get("parent") or os.path.dirname(cur)
        parent_of[path] = resolved or root
    if dropped:
        logger.debug("Analytics rollup skipped %d folder(s) outside %s", dropped, root)

    children: dict[str, list[str]] = defaultdict(list)
    for path, parent in parent_of.items():
        if parent:
            children[parent].append(path)

    def node_for(folder_path: str) -> str:
        if folder_path in parent_of:
            return folder_path
        cur = folder_path
        seen: set[str] = set()
        while cur and cur not in seen:
            seen.add(cur)
            parent = meta.get(cur, {}).get("parent")
            cur = parent or os.path.dirname(cur)
            if cur in parent_of:
                return cur
        return root

    own_size: dict[str, int] = defaultdict(int)
    own_count: dict[str, int] = defaultdict(int)
    for r in conn.execute(
        """
        SELECT folder_path, COUNT(*) AS c, COALESCE(SUM(size), 0) AS s
        FROM files
        GROUP BY folder_path
        """
    ):
        folder_path = r["folder_path"]
        if not folder_path:
            continue
        node = node_for(folder_path)
        own_size[node] += int(r["s"] or 0)
        own_count[node] += int(r["c"] or 0)

    depth: dict[str, int] = {}
    for path in parent_of:
        d = 0
        cur = parent_of.get(path)
        seen = set()
        while cur and cur not in seen:
            seen.add(cur)
            d += 1
            cur = parent_of.get(cur)
        depth[path] = d

    size = dict(own_size)
    count = dict(own_count)
    # Deepest first: each node pushes its finished subtree totals into its
    # parent, so every node ends up holding its own recursive totals.
    for path in sorted(parent_of, key=lambda p: depth[p], reverse=True):
        parent = parent_of.get(path)
        if parent:
            size[parent] = size.get(parent, 0) + size.get(path, 0)
            count[parent] = count.get(parent, 0) + count.get(path, 0)

    return {
        "root": root,
        "size": size,
        "count": count,
        "meta": meta,
        "parent_of": parent_of,
        "children": children,
    }


def _folder_entry(
    path: str,
    rollup: dict[str, Any],
    total_bytes: int,
    include_children: bool = False,
) -> dict[str, Any]:
    root = rollup["root"]
    size = int(rollup["size"].get(path, 0) or 0)
    entry: dict[str, Any] = {
        "path": path,
        "name": rollup["meta"].get(path, {}).get("name") or os.path.basename(path) or path,
        "rel_path": os.path.relpath(path, root) if path != root else os.path.basename(root),
        "bytes": size,
        "bytes_formatted": format_size(size),
        "files": int(rollup["count"].get(path, 0) or 0),
        "percent_bytes": _percent(size, total_bytes),
        "is_zip": bool(rollup["meta"].get(path, {}).get("is_zip")),
    }
    if include_children:
        kids = []
        for child in sorted(
            rollup["children"].get(path, []),
            key=lambda c: rollup["size"].get(c, 0),
            reverse=True,
        ):
            if _is_excluded(child):
                continue
            child_size = int(rollup["size"].get(child, 0) or 0)
            if child_size <= 0:
                continue
            kids.append(
                {
                    "path": child,
                    "name": rollup["meta"].get(child, {}).get("name") or os.path.basename(child),
                    "rel_path": os.path.relpath(child, root),
                    "bytes": child_size,
                    "bytes_formatted": format_size(child_size),
                    "files": int(rollup["count"].get(child, 0) or 0),
                    "is_zip": bool(rollup["meta"].get(child, {}).get("is_zip")),
                }
            )
            if len(kids) >= _TREEMAP_CHILDREN:
                break
        entry["children"] = kids
    return entry


def _prune_nested(
    candidates: list[tuple[str, int]], rollup: dict[str, Any], limit: int
) -> list[str]:
    """Pick the ``limit`` largest subtrees that do not contain one another, so
    the ranking reads as a partition instead of double-counting a parent and
    its own child."""
    picked: list[str] = []
    picked_set: set[str] = set()
    for path, _size in candidates:
        cur = rollup["parent_of"].get(path)
        nested = False
        seen: set[str] = set()
        while cur and cur not in seen:
            if cur in picked_set:
                nested = True
                break
            seen.add(cur)
            cur = rollup["parent_of"].get(cur)
        if nested:
            continue
        picked.append(path)
        picked_set.add(path)
        if len(picked) >= limit:
            break
    return picked


# ---------------------------------------------------------- quality


def _read_quality(conn) -> dict[str, Any]:
    bucket_rows = conn.execute(
        """
        SELECT
            CASE
                WHEN COALESCE(width, 0) <= 0 OR COALESCE(height, 0) <= 0 THEN ?
                WHEN MAX(COALESCE(width, 0), COALESCE(height, 0)) >= 3840 THEN ?
                WHEN MAX(COALESCE(width, 0), COALESCE(height, 0)) >= 2560 THEN ?
                WHEN MAX(COALESCE(width, 0), COALESCE(height, 0)) >= 1920 THEN ?
                WHEN MAX(COALESCE(width, 0), COALESCE(height, 0)) >= 1280 THEN ?
                ELSE ?
            END AS bucket,
            COUNT(*) AS count,
            COALESCE(SUM(size), 0) AS bytes,
            COALESCE(SUM(duration), 0) AS duration
        FROM files
        WHERE type = 'video'
        GROUP BY bucket
        """,
        (
            _UNKNOWN_KEY,
            _RESOLUTION_BUCKETS[0][0],
            _RESOLUTION_BUCKETS[1][0],
            _RESOLUTION_BUCKETS[2][0],
            _RESOLUTION_BUCKETS[3][0],
            _SD_KEY,
        ),
    ).fetchall()

    by_key = {r["bucket"]: r for r in bucket_rows}
    buckets = []
    for key in _BUCKET_ORDER:
        r = by_key.get(key)
        size = int(r["bytes"] or 0) if r else 0
        buckets.append(
            {
                "key": key,
                "label": _BUCKET_LABELS[key],
                "count": int(r["count"] or 0) if r else 0,
                "bytes": size,
                "bytes_formatted": format_size(size),
            }
        )

    agg = conn.execute(
        """
        SELECT
            COUNT(*) AS videos,
            COALESCE(SUM(size), 0) AS bytes,
            COALESCE(SUM(duration), 0) AS duration,
            COUNT(CASE WHEN fps >= ? THEN 1 END) AS high_fps,
            COUNT(CASE WHEN width IS NULL THEN 1 END) AS unprobed
        FROM files
        WHERE type = 'video'
        """,
        (_HIGH_FPS_THRESHOLD,),
    ).fetchone()

    duration = float(agg["duration"] or 0)
    return {
        "buckets": buckets,
        "videos": int(agg["videos"] or 0),
        "video_bytes": int(agg["bytes"] or 0),
        "video_bytes_formatted": format_size(agg["bytes"] or 0),
        "probed_videos": int(agg["videos"] or 0) - int(agg["unprobed"] or 0),
        "unprobed_videos": int(agg["unprobed"] or 0),
        "high_fps_count": int(agg["high_fps"] or 0),
        "fps_threshold": _HIGH_FPS_THRESHOLD,
        "total_duration": round(duration, 1),
        "total_duration_formatted": _format_duration_long(duration),
    }


# ------------------------------------------------------- reclaimable


def _read_reclaimable(conn) -> dict[str, Any]:
    dupe = conn.execute(
        """
        SELECT
            COUNT(*) AS groups,
            COALESCE(SUM(cnt), 0) AS files,
            COALESCE(SUM((cnt - 1) * size), 0) AS bytes
        FROM (
            SELECT size, COUNT(*) AS cnt
            FROM files
            WHERE size > 0
            GROUP BY size
            HAVING COUNT(*) > 1
        )
        """
    ).fetchone()

    ignored = conn.execute("SELECT COUNT(*) AS c FROM ignored_duplicates").fetchone()

    webp_row = conn.execute(
        "SELECT saved_bytes, converted_count, skipped_count FROM webp_stats WHERE id = 1"
    ).fetchone()

    webp_pending = conn.execute(
        """
        SELECT COUNT(*) AS c, COALESCE(SUM(f.size), 0) AS s
        FROM files f
        LEFT JOIN folders fo ON fo.path = f.folder_path
        WHERE f.type = 'image'
          AND f.ext != '.webp'
          AND COALESCE(f.webp_optimized, 0) = 0
          AND COALESCE(fo.is_zip, 0) = 0
        """
    ).fetchone()

    try:
        zip_candidates = find_batch_zip_candidate_folders()
    except Exception as e:  # pragma: no cover - defensive: never break the page
        logger.error("Could not compute ZIP candidates for analytics: %s", e)
        zip_candidates = {"total_candidates": 0, "total_size": 0}

    # Index-only empty-leaf count. ``find_empty_folders`` (the Clean Empty
    # Folders tool) verifies each candidate with ``os.scandir``, which must not
    # run inside a request handler on a FUSE mount — this counts the same
    # thing from the index instead, and the tool remains the authority when
    # actually deleting them.
    empty_row = conn.execute(
        """
        SELECT COUNT(*) AS c
        FROM folders f
        WHERE (f.is_zip = 0 OR f.is_zip IS NULL)
          AND COALESCE(f.subfolder_count, 0) = 0
          AND COALESCE(f.file_count, 0) = 0
          AND NOT EXISTS (SELECT 1 FROM files fi WHERE fi.folder_path = f.path)
          AND NOT EXISTS (
              SELECT 1 FROM folders z
              WHERE z.parent_path = f.path AND z.is_zip = 1
          )
        """
    ).fetchone()
    empty_count = int(empty_row["c"] or 0)

    dupe_bytes = int(dupe["bytes"] or 0)
    saved = int((webp_row["saved_bytes"] if webp_row else 0) or 0)
    return {
        "duplicate_groups": int(dupe["groups"] or 0),
        "duplicate_files": int(dupe["files"] or 0),
        "duplicate_bytes": dupe_bytes,
        "duplicate_bytes_formatted": format_size(dupe_bytes),
        "duplicate_ignored_pairs": int(ignored["c"] or 0),
        "webp_saved_bytes": saved,
        "webp_saved_formatted": format_size(saved),
        "webp_converted_count": int((webp_row["converted_count"] if webp_row else 0) or 0),
        "webp_skipped_count": int((webp_row["skipped_count"] if webp_row else 0) or 0),
        "webp_pending_count": int(webp_pending["c"] or 0),
        "webp_pending_bytes": int(webp_pending["s"] or 0),
        "webp_pending_bytes_formatted": format_size(webp_pending["s"] or 0),
        "zip_eligible_folders": int(zip_candidates.get("total_candidates", 0) or 0),
        "zip_eligible_bytes": int(zip_candidates.get("total_size", 0) or 0),
        "zip_eligible_bytes_formatted": format_size(zip_candidates.get("total_size", 0) or 0),
        "empty_folders": empty_count,
    }


# ------------------------------------------------------------ health


def _read_health(conn) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT
            COUNT(CASE WHEN has_thumb = 0 THEN 1 END) AS missing_thumbs,
            COUNT(CASE WHEN width IS NULL THEN 1 END) AS unprobed,
            COUNT(CASE WHEN type = 'video' AND has_animated = 0 THEN 1 END) AS missing_animated,
            COUNT(CASE WHEN thumb_hash IS NULL THEN 1 END) AS unhashed_visual,
            COUNT(CASE WHEN type = 'video' AND needs_transcode = 1 THEN 1 END) AS needs_transcode
        FROM files
        """
    ).fetchone()

    stale_folders = int(indexer_state.get("stale_folders", 0) or 0)
    stale_files = int(indexer_state.get("stale_files", 0) or 0)
    is_crawling = indexer_state.get("status") == "crawling"
    return {
        "missing_thumbs": int(row["missing_thumbs"] or 0),
        "unprobed": int(row["unprobed"] or 0),
        "missing_animated": int(row["missing_animated"] or 0),
        "unhashed_visual": int(row["unhashed_visual"] or 0),
        "needs_transcode": int(row["needs_transcode"] or 0),
        "stale_folders": stale_folders,
        "stale_files": stale_files,
        "prune_aborted": bool(indexer_state.get("prune_aborted", False)),
        "prune_reason": indexer_state.get("prune_reason", ""),
        "is_crawling": is_crawling,
        "is_complete": (not is_crawling) and stale_folders == 0,
    }


# ------------------------------------------------------------ build


def build_analytics_overview(
    folder_limit: int = DEFAULT_FOLDER_LIMIT,
    file_limit: int = DEFAULT_FILE_LIMIT,
) -> dict[str, Any]:
    """Compute the whole ``/analytics`` payload from the index (FUSE-safe)."""
    folder_limit = max(1, min(MAX_LIMIT, int(folder_limit)))
    file_limit = max(1, min(MAX_LIMIT, int(file_limit)))

    with db_conn() as conn:
        totals = _read_totals(conn)
        total_bytes = totals["bytes"]

        configured_root = str(
            state.get("destination_dir") or DEFAULT_DESTINATION_DIR or ""
        ).rstrip("/")
        rollup = _rollup_folders(conn, configured_root)
        root = rollup["root"]

        top_children = [
            _folder_entry(child, rollup, total_bytes, include_children=True)
            for child in sorted(
                rollup["children"].get(root, []),
                key=lambda c: rollup["size"].get(c, 0),
                reverse=True,
            )
            if not _is_excluded(child) and int(rollup["size"].get(child, 0) or 0) > 0
        ]
        # Keep the treemap complete in area but bounded in size: everything past
        # the cap is folded into one aggregate node.
        if len(top_children) > _TREEMAP_TOP:
            kept = top_children[:_TREEMAP_TOP]
            rest = top_children[_TREEMAP_TOP:]
            rest_bytes = sum(int(c["bytes"] or 0) for c in rest)
            rest_files = sum(int(c["files"] or 0) for c in rest)
            kept.append(
                {
                    "path": None,
                    "name": f"Smaller folders ({len(rest)})",
                    "rel_path": None,
                    "bytes": rest_bytes,
                    "bytes_formatted": format_size(rest_bytes),
                    "files": rest_files,
                    "percent_bytes": _percent(rest_bytes, total_bytes),
                    "is_zip": False,
                    "is_group": True,
                    "children": [],
                }
            )
            top_children = kept

        ranked = sorted(
            (
                (path, int(rollup["size"].get(path, 0) or 0))
                for path in rollup["parent_of"]
                if path != root and not _is_excluded(path)
            ),
            key=lambda item: item[1],
            reverse=True,
        )
        largest_paths = _prune_nested(ranked, rollup, folder_limit)
        largest_folders = [
            _folder_entry(path, rollup, total_bytes) for path in largest_paths
        ]

        by_extension = _read_by_extension(conn, total_bytes, totals["files"])
        by_type = _read_by_type(conn, total_bytes)
        quality = _read_quality(conn)
        reclaimable = _read_reclaimable(conn)
        health = _read_health(conn)
        largest_files = read_largest_files(conn, file_limit)

    return {
        "generated_at": time.time(),
        "library_root": root,
        "limits": {"folders": folder_limit, "files": file_limit},
        "totals": totals,
        "by_type": by_type,
        "by_extension": by_extension,
        "folders": {
            "root_bytes": int(rollup["size"].get(root, 0) or 0),
            "root_bytes_formatted": format_size(rollup["size"].get(root, 0) or 0),
            "root_files": int(rollup["count"].get(root, 0) or 0),
            "top": top_children,
            "largest": largest_folders,
        },
        "largest_files": largest_files,
        "quality": quality,
        "reclaimable": reclaimable,
        "health": health,
    }


def get_analytics_overview(
    folder_limit: int = DEFAULT_FOLDER_LIMIT,
    file_limit: int = DEFAULT_FILE_LIMIT,
) -> dict[str, Any]:
    """Cached :func:`build_analytics_overview` (30s, revision-keyed)."""
    folder_limit = max(1, min(MAX_LIMIT, int(folder_limit)))
    file_limit = max(1, min(MAX_LIMIT, int(file_limit)))
    cache_key = (folder_limit, file_limit)

    now = time.time()
    revision = indexer_state.get("revision", 0)
    with _analytics_lock:
        if (
            _analytics_cache["payload"] is not None
            and now < _analytics_cache["expires_at"]
            and _analytics_cache["revision"] == revision
            and _analytics_cache["key"] == cache_key
        ):
            return _analytics_cache["payload"]

    payload = build_analytics_overview(folder_limit, file_limit)

    with _analytics_lock:
        _analytics_cache["payload"] = payload
        _analytics_cache["expires_at"] = time.time() + INDEX_COUNT_CACHE_TTL_S
        _analytics_cache["revision"] = revision
        _analytics_cache["key"] = cache_key
    return payload


def invalidate_analytics_cache() -> None:
    """Drop the memoized analytics payload so the next read is fresh."""
    with _analytics_lock:
        _analytics_cache["payload"] = None
        _analytics_cache["expires_at"] = 0.0
        _analytics_cache["revision"] = -1
        _analytics_cache["key"] = None
