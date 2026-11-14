"""Deduplication: content matching and visual (pHash) clustering.

Hashes are stored in the fixed columns, with the documented formats, so the
two apps agree about what has already been hashed:

- ``quick_hash``: ``sha256(first 128 KB + str(size))`` hex (``empty_file`` for
  zero-byte files, ``unhashable`` when a read fails).
- ``thumb_hash``: 16-hex DCT pHash of the cached poster (``blank_thumb`` /
  ``no_thumb`` sentinels).

Content deletion trusts the ``quick_hash`` grouping (same size + same first
128 KB): no byte-for-byte re-comparison runs before a resolve, so deleting a
content duplicate is immediate. ``files_identical`` remains available for the
explicit ``/dedupe/verify`` double-check.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable
from typing import Any

from media_library import activity, writes
from media_library.cache import thumbnail_path
from media_library.db import reader, writer
from media_library.library import shape_file_row
from media_library.paths import is_zip_member, resolve_library_path

_HEAD_BYTES = 128 * 1024
_CHUNK = 1024 * 1024
_PHASH_RE = re.compile(r"^[0-9a-f]{16}$")

# A visual poster match is a hint. When a group is verified we also sample the
# video at five points and compare those perceptual hashes, which rejects two
# clips that merely share a similar opening frame.
_FRAME_FRACTIONS = (0.1, 0.3, 0.5, 0.7, 0.9)
_FRAME_MATCH_DISTANCE = 10
_FRAME_CONFIRM_MATCHES = 3

_FILE_SQL = """
SELECT f.*, CASE WHEN fav.path IS NOT NULL THEN 1 ELSE 0 END AS is_favorite
FROM files f
LEFT JOIN favorites fav ON f.path = fav.path
"""

# A batch of frame checks runs on one daemon thread, like the other queues, so
# navigating away from the Duplicates page does not stop it and progress can be
# read back from /api/dedupe/verify-visual/status.
_verify_lock = threading.RLock()
_verify_queue: deque[list[str]] = deque()
_verify_thread: threading.Thread | None = None
_verify_state: dict[str, Any] = {
    "status": "idle",
    "total_groups": 0,
    "completed_groups": 0,
    "queued_groups": 0,
    "current": [],
    "stage": "",
    "active_files": 0,
    "active_index": 0,
    "active_file": "",
    "active_frame": 0,
    "active_frames": 0,
    "confirmed": 0,
    "rejected": 0,
    "unknown": 0,
    "errors": 0,
    "started_at": None,
    "finished_at": None,
}


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


def _parse_hashes(raw: str | None) -> list[str | None] | None:
    try:
        value = json.loads(raw or "")
    except (TypeError, ValueError):
        return None
    if not isinstance(value, list) or len(value) != len(_FRAME_FRACTIONS):
        return None
    return [item if isinstance(item, str) else None for item in value]


def _frame_phash(path: str, timestamp: float) -> str | None:
    """Perceptual hash of one sampled video frame."""
    from media_library import thumbnails

    try:
        frame = thumbnails.frame(path, timestamp)
    except Exception:
        return None
    value = thumbnail_phash(str(frame))
    return value if _PHASH_RE.match(value or "") else None


def _signature_for(
    path: str, on_frame: Callable[[int, int], None] | None = None
) -> dict[str, Any] | None:
    """Return the five-frame signature for a video, computing it if needed."""
    with reader() as conn:
        row = conn.execute(
            "SELECT type, mtime, size, duration FROM files WHERE path = ?", (path,)
        ).fetchone()
        cached = conn.execute(
            "SELECT mtime, size, duration, hashes FROM visual_frames WHERE path = ?",
            (path,),
        ).fetchone()
    if row is None or row["type"] != "video":
        return None

    mtime = float(row["mtime"] or 0)
    size = int(row["size"] or 0)
    # The signature is kept for as long as the file exists, not per mtime/size:
    # a remote mount that bumps timestamps must not force expensive re-sampling.
    # Deletes/removes clear the row; renames move it with the file.
    if cached is not None:
        hashes = _parse_hashes(cached["hashes"])
        if hashes is not None:
            return {
                "path": path,
                "mtime": mtime,
                "size": size,
                "duration": float(cached["duration"] or 0),
                "hashes": hashes,
            }

    duration = float(row["duration"]) if row["duration"] else 0.0
    if duration <= 0:
        from media_library import thumbnails

        try:
            duration = float(thumbnails.candidates(path, count=5).get("duration") or 0)
        except Exception:
            duration = 0.0
    if duration <= 0:
        return None

    hashes: list[str | None] = []
    for index, fraction in enumerate(_FRAME_FRACTIONS, start=1):
        hashes.append(_frame_phash(path, round(duration * fraction, 3)))
        if on_frame is not None:
            on_frame(index, len(_FRAME_FRACTIONS))
    with writer() as conn, conn:
        conn.execute(
            "INSERT INTO visual_frames "
            "(path, mtime, size, duration, hashes, captured_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(path) DO UPDATE SET mtime = excluded.mtime, "
            "size = excluded.size, duration = excluded.duration, "
            "hashes = excluded.hashes, captured_at = excluded.captured_at",
            (path, mtime, size, duration, json.dumps(hashes), time.time()),
        )
    return {
        "path": path,
        "mtime": mtime,
        "size": size,
        "duration": duration,
        "hashes": hashes,
    }


def _cached_signatures(paths: list[str]) -> dict[str, dict[str, Any]]:
    """Signatures stored for these paths while their file row still exists."""
    if not paths:
        return {}
    result: dict[str, dict[str, Any]] = {}
    # Stay below SQLite's variable limit on a very large library.
    for start in range(0, len(paths), 400):
        chunk = paths[start : start + 400]
        placeholders = ",".join("?" for _ in chunk)
        with reader() as conn:
            rows = conn.execute(
                "SELECT vf.path, vf.mtime, vf.size, vf.duration, vf.hashes "
                "FROM visual_frames vf JOIN files f ON f.path = vf.path "
                f"WHERE vf.path IN ({placeholders})",
                chunk,
            ).fetchall()
        for row in rows:
            hashes = _parse_hashes(row["hashes"])
            if hashes is None:
                continue
            result[row["path"]] = {
                "path": row["path"],
                "mtime": float(row["mtime"] or 0),
                "size": int(row["size"] or 0),
                "duration": float(row["duration"] or 0),
                "hashes": hashes,
            }
    return result


def _compare_signatures(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    """Compare two five-frame signatures, tolerating a one-slot offset."""
    left_hashes = left["hashes"]
    right_hashes = right["hashes"]
    distances: list[int] = []
    for first, second in zip(left_hashes, right_hashes):
        if _PHASH_RE.match(first or "") and _PHASH_RE.match(second or ""):
            distances.append(_hamming(int(first, 16), int(second, 16)))
    compared = len(distances)
    if compared == 0:
        return {
            "compared": 0,
            "matches": 0,
            "distances": [],
            "confirmed": None,
        }

    matches = sum(1 for distance in distances if distance <= _FRAME_MATCH_DISTANCE)
    # A clip that starts a second or two earlier shifts every sampled frame;
    # comparing a small window around each slot recovers those pairs.
    for shift in (-2, -1, 1, 2):
        shifted = 0
        for index, first in enumerate(left_hashes):
            other = index + shift
            if other < 0 or other >= len(right_hashes):
                continue
            second = right_hashes[other]
            if (
                _PHASH_RE.match(first or "")
                and _PHASH_RE.match(second or "")
                and _hamming(int(first, 16), int(second, 16)) <= _FRAME_MATCH_DISTANCE
            ):
                shifted += 1
        matches = max(matches, shifted)
    matches = min(matches, compared)
    needed = max(_FRAME_CONFIRM_MATCHES, (compared + 1) // 2)
    return {
        "compared": compared,
        "matches": matches,
        "distances": distances,
        "confirmed": matches >= needed if compared >= _FRAME_CONFIRM_MATCHES else None,
    }


def _verification_for(
    paths: list[str],
    signatures: dict[str, dict[str, Any]],
    *,
    include_pairs: bool = False,
) -> dict[str, Any] | None:
    usable = [path for path in paths if path in signatures]
    if len(usable) < 2:
        return None
    anchor = usable[0]
    detailed: list[dict[str, Any]] = []
    for other in usable[1:]:
        result = _compare_signatures(signatures[anchor], signatures[other])
        detailed.append(
            {
                "path": other,
                "against": anchor,
                "compared": result["compared"],
                "matches": result["matches"],
                "distances": result["distances"],
                "confirmed": result["confirmed"],
            }
        )
    checked = len(detailed)
    if checked == 0:  # pragma: no cover - usable >= 2 always yields one pair
        return None
    confirmed_pairs = sum(1 for pair in detailed if pair["confirmed"] is True)
    unknown_pairs = sum(1 for pair in detailed if pair["confirmed"] is None)
    if unknown_pairs == checked:
        confirmed: bool | None = None
    else:
        confirmed = confirmed_pairs == checked
    return {
        "available": True,
        "confirmed": confirmed,
        "checked_pairs": checked,
        "confirmed_pairs": confirmed_pairs,
        "frames": len(_FRAME_FRACTIONS),
        "pairs": detailed if include_pairs else [],
    }


def verify_visual_group(
    paths: list[str],
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Double-check a visual group by sampling five frames from each video."""
    resolved: list[str] = []
    seen: set[str] = set()
    for raw in paths:
        path = resolve_library_path(raw)
        if path not in seen:
            seen.add(path)
            resolved.append(path)
    if len(resolved) < 2:
        raise writes.WriteError("A visual group needs at least two files", 400)

    total_files = len(resolved)
    signatures = _cached_signatures(resolved)
    for index, path in enumerate(resolved, start=1):
        if progress is not None:
            progress(
                {
                    "stage": "sampling",
                    "active_files": total_files,
                    "active_index": index,
                    "active_file": path,
                    "active_frame": 0,
                    "active_frames": len(_FRAME_FRACTIONS),
                }
            )
        if path in signatures:
            continue

        def on_frame(
            done: int,
            frames: int,
            *,
            file_index: int = index,
            file_path: str = path,
        ) -> None:
            if progress is not None:
                progress(
                    {
                        "stage": "sampling",
                        "active_files": total_files,
                        "active_index": file_index,
                        "active_file": file_path,
                        "active_frame": done,
                        "active_frames": frames,
                    }
                )

        signature = _signature_for(path, on_frame=on_frame)
        if signature is not None:
            signatures[path] = signature

    if progress is not None:
        progress(
            {
                "stage": "comparing",
                "active_files": total_files,
                "active_index": total_files,
                "active_frame": len(_FRAME_FRACTIONS),
                "active_frames": len(_FRAME_FRACTIONS),
            }
        )
    summary = _verification_for(resolved, signatures, include_pairs=True)
    if summary is None:
        return {
            "available": False,
            "confirmed": None,
            "checked_pairs": 0,
            "confirmed_pairs": 0,
            "frames": len(_FRAME_FRACTIONS),
            "pairs": [],
        }
    return summary


def _status_locked() -> dict[str, Any]:
    state = dict(_verify_state)
    state["current"] = list(state["current"])
    state["queued_groups"] = len(_verify_queue)
    state["running"] = state["status"] in ("queued", "running")
    return state


def visual_check_status() -> dict[str, Any]:
    """Progress of the current (or last) five-frame batch."""
    with _verify_lock:
        return _status_locked()


def _ensure_verify_thread() -> None:
    global _verify_thread
    if _verify_thread is not None and _verify_thread.is_alive():
        return
    _verify_thread = threading.Thread(
        target=_verify_loop, name="visual-frame-checks", daemon=True
    )
    _verify_thread.start()


def start_visual_checks(groups: list[list[str]]) -> dict[str, Any]:
    """Queue frame checks for one or more visual groups and return immediately."""
    cleaned: list[tuple[tuple[str, ...], list[str]]] = []
    seen: set[tuple[str, ...]] = set()
    for group in groups:
        paths = [path for path in group if isinstance(path, str) and path]
        key = tuple(sorted(set(paths)))
        if len(key) < 2 or key in seen:
            continue
        seen.add(key)
        cleaned.append((key, paths))
    if not cleaned:
        raise writes.WriteError("Choose at least one visual group", 400)

    with _verify_lock:
        existing = {tuple(sorted(set(group))) for group in _verify_queue}
        if _verify_state["current"]:
            existing.add(tuple(sorted(set(_verify_state["current"]))))
        cleaned = [entry for entry in cleaned if entry[0] not in existing]

        starting = not _verify_queue and not (
            _verify_thread is not None and _verify_thread.is_alive()
        )
        if starting:
            _verify_state.update(
                status="queued",
                total_groups=0,
                completed_groups=0,
                current=[],
                stage="",
                active_files=0,
                active_index=0,
                active_file="",
                active_frame=0,
                active_frames=0,
                confirmed=0,
                rejected=0,
                unknown=0,
                errors=0,
                started_at=time.time(),
                finished_at=None,
            )
        if not cleaned:
            return _status_locked()
        _verify_queue.extend(paths for _key, paths in cleaned)
        _verify_state["total_groups"] += len(cleaned)
        _verify_state["queued_groups"] = len(_verify_queue)
        _verify_state["finished_at"] = None
        if _verify_state["status"] in ("idle", "done"):
            _verify_state["status"] = "queued"
        _ensure_verify_thread()
        return _status_locked()


def _verify_loop() -> None:
    global _verify_thread
    while True:
        activity.wait_turn("duplicate-checks", lambda: not _verify_queue)
        with _verify_lock:
            if not _verify_queue:
                _verify_state.update(
                    status="done",
                    current=[],
                    queued_groups=0,
                    stage="",
                    active_files=0,
                    active_index=0,
                    active_file="",
                    active_frame=0,
                    active_frames=0,
                    finished_at=time.time(),
                )
                _verify_thread = None
                return
            group = _verify_queue.popleft()
            _verify_state.update(
                status="running",
                current=list(group),
                queued_groups=len(_verify_queue),
                stage="sampling",
                active_files=len(group),
                active_index=0,
                active_file="",
                active_frame=0,
                active_frames=len(_FRAME_FRACTIONS),
            )

        def report(update: dict[str, Any]) -> None:
            with _verify_lock:
                _verify_state.update(update)

        confirmed: bool | None = None
        failed = False
        try:
            result = verify_visual_group(group, progress=report)
            confirmed = result.get("confirmed")
        except Exception:
            failed = True

        with _verify_lock:
            if failed:
                _verify_state["errors"] += 1
            elif confirmed is True:
                _verify_state["confirmed"] += 1
            elif confirmed is False:
                _verify_state["rejected"] += 1
            else:
                _verify_state["unknown"] += 1
            _verify_state.update(
                completed_groups=_verify_state["completed_groups"] + 1,
                current=[],
                queued_groups=len(_verify_queue),
                stage="",
                active_files=0,
                active_index=0,
                active_file="",
                active_frame=0,
                active_frames=0,
            )
            if not _verify_queue:
                _verify_state.update(status="done", finished_at=time.time())


def reset_duplicates() -> dict[str, int]:
    """Forget every ignored pair and cached frame check."""
    with writer() as conn, conn:
        ignored = conn.execute("DELETE FROM ignored_duplicates").rowcount
        signatures = conn.execute("DELETE FROM visual_frames").rowcount
    with _verify_lock:
        _verify_queue.clear()
        if not (_verify_thread is not None and _verify_thread.is_alive()):
            _verify_state.update(
                status="idle",
                total_groups=0,
                completed_groups=0,
                queued_groups=0,
                current=[],
                stage="",
                active_files=0,
                active_index=0,
                active_file="",
                active_frame=0,
                active_frames=0,
                confirmed=0,
                rejected=0,
                unknown=0,
                errors=0,
                started_at=None,
                finished_at=None,
            )
    invalidate_merged_cache()
    return {"ignored_cleared": ignored or 0, "signatures_cleared": signatures or 0}


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


def _candidate_sizes(conn, include_images: bool = True) -> list[int]:
    image_filter = "" if include_images else " AND type != 'image'"
    rows = conn.execute(
        "SELECT size FROM files WHERE size > 0"
        f"{image_filter} GROUP BY size "
        "HAVING COUNT(*) > 1 ORDER BY size DESC"
    ).fetchall()
    return [int(row["size"]) for row in rows]


def _candidate_rows(
    conn, sizes: list[int], include_images: bool = True
) -> list[sqlite3.Row]:
    if not sizes:
        return []
    image_filter = "" if include_images else " AND f.type != 'image'"
    rows: list[sqlite3.Row] = []
    # Chunked like _cached_signatures, so a library with more distinct
    # duplicated sizes than SQLite's variable limit still queries.
    for start in range(0, len(sizes), 400):
        chunk = sizes[start : start + 400]
        placeholders = ",".join("?" for _ in chunk)
        rows.extend(
            conn.execute(
                f"{_FILE_SQL} WHERE f.size IN ({placeholders}){image_filter} "
                "ORDER BY f.size DESC, f.path",
                chunk,
            ).fetchall()
        )
    rows.sort(key=lambda row: (-int(row["size"] or 0), row["path"]))
    return rows


def content_groups(
    limit: int = 25, offset: int = 0, include_images: bool = True
) -> dict[str, Any]:
    with reader() as conn:
        rows = _candidate_rows(
            conn, _candidate_sizes(conn, include_images), include_images
        )
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
                "verification": None,
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


def visual_groups(
    limit: int = 25, offset: int = 0, include_images: bool = True
) -> dict[str, Any]:
    image_filter = "" if include_images else " AND f.type != 'image'"
    with reader() as conn:
        rows = conn.execute(
            f"{_FILE_SQL} WHERE length(f.thumb_hash) = 16{image_filter} ORDER BY f.path"
        ).fetchall()
        ignored = _ignored_pairs(conn)
        unhashed_query = (
            "SELECT COUNT(*) AS n FROM files WHERE has_thumb = 1 AND thumb_hash IS NULL"
        )
        if not include_images:
            unhashed_query += " AND type != 'image'"
        unhashed = conn.execute(unhashed_query).fetchone()["n"]

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
    cached_signatures = _cached_signatures([row["path"] for row, _value in items])
    for members in clusters:
        if len(members) < 2:
            continue
        member_rows = [items[index][0] for index in members]
        sizes = [int(row["size"] or 0) for row in member_rows]
        paths = [row["path"] for row in member_rows]
        groups.append(
            {
                "id": "v"
                + hashlib.sha1(
                    "|".join(sorted(paths)).encode("utf-8", "surrogatepass")
                ).hexdigest()[:15],
                "match_type": "visual",
                "size": sum(sizes),
                "size_formatted": member_rows[0]["size_formatted"],
                "reclaimable": max(0, sum(sizes) - max(sizes)),
                "files": [shape_file_row(row) for row in member_rows],
                "verification": _verification_for(paths, cached_signatures),
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


def all_groups(
    limit: int = 25,
    offset: int = 0,
    include_rejected: bool = False,
    include_images: bool = True,
) -> dict[str, Any]:
    """One combined list: byte-identical groups first, visual extras beside them.

    A visual cluster made entirely of files that already appear in a content
    group adds nothing, so it is dropped. A cluster that also contains a
    genuinely different file keeps one representative of the byte-identical
    group plus that extra, which is the only pair worth reviewing. Visual
    groups whose five-frame check disagreed are counted in ``rejected_groups``
    and hidden unless ``include_rejected`` is set.

    The expensive part — walking every content and visual group and merging
    them — is cached for a few seconds (the same shape as the analytics
    rollup): the merged, sorted universe changes only when the index or the
    ignore list changes, not per page turn. Verification stamps are applied
    per request so they always reflect the latest frame checks.
    """
    merged, _unhashed = _cached_merged_state(include_images)
    visual_paths = [
        file["path"]
        for group in merged
        if group["match_type"] == "visual"
        for file in group["files"]
    ]
    cached = _cached_signatures(visual_paths)
    rejected_groups = 0
    visible: list[dict[str, Any]] = []
    for group in merged:
        if group["match_type"] != "visual":
            visible.append(group)
            continue
        paths = [file["path"] for file in group["files"]]
        verification = _verification_for(paths, cached)
        group["verification"] = verification
        if (
            verification is not None
            and verification["available"]
            and verification["confirmed"] is False
        ):
            rejected_groups += 1
            if not include_rejected:
                continue
        visible.append(group)

    return {
        "mode": "all",
        "total_groups": len(visible),
        "total_files": sum(len(group["files"]) for group in visible),
        "unhashed": _cached_unhashed(include_images),
        "rejected_groups": rejected_groups,
        "groups": visible[offset : offset + limit],
    }


_MERGE_CACHE_TTL = 10.0
_merge_cache_lock = threading.Lock()
_merge_cache: dict[str, Any] = {
    "at": 0.0,
    "merged": None,
    "unhashed": 0,
    "fingerprint": None,
    "include_images": True,
}


def invalidate_merged_cache() -> None:
    """Drop the cached group universe (called after changes that edit it)."""
    with _merge_cache_lock:
        _merge_cache.update(
            {
                "at": 0.0,
                "merged": None,
                "unhashed": 0,
                "fingerprint": None,
                "include_images": True,
            }
        )


def _cached_unhashed(include_images: bool = True) -> int:
    return int(_cached_merged_state(include_images)[1])


def _merge_fingerprint() -> tuple[int, int]:
    """Cheap index shape (file count, ignored pairs) the cache is keyed on.

    A full group rebuild is expensive, so drift in the underlying index —
    rows added or removed by any writer, not just this module's endpoints —
    must invalidate it. Row count is one index scan; exact ignore-state
    changes go through ``invalidate_merged_cache`` in addition.
    """
    try:
        with reader() as conn:
            files = int(
                conn.execute("SELECT COUNT(*) AS n FROM files").fetchone()["n"] or 0
            )
            ignored = int(
                conn.execute("SELECT COUNT(*) AS n FROM ignored_duplicates").fetchone()[
                    "n"
                ]
                or 0
            )
    except Exception:
        return (-1, -1)
    return files, ignored


def _cached_merged_state(
    include_images: bool = True,
) -> tuple[list[dict[str, Any]], int]:
    """The merged+sorted group list and combined unhashed count."""
    now = time.monotonic()
    fingerprint = _merge_fingerprint()
    with _merge_cache_lock:
        if (
            _merge_cache["merged"] is not None
            and _merge_cache["fingerprint"] == fingerprint
            and _merge_cache["include_images"] == include_images
            and now - _merge_cache["at"] < _MERGE_CACHE_TTL
        ):
            return _merge_cache["merged"], _merge_cache["unhashed"]
    content = content_groups(limit=100_000, offset=0, include_images=include_images)
    visual = visual_groups(limit=100_000, offset=0, include_images=include_images)
    owner: dict[str, str] = {}
    for group in content["groups"]:
        for file in group["files"]:
            owner[file["path"]] = group["id"]

    merged: list[dict[str, Any]] = list(content["groups"])
    for group in visual["groups"]:
        extras = [file for file in group["files"] if file["path"] not in owner]
        if not extras:
            continue
        representatives: dict[str, dict[str, Any]] = {}
        files: list[dict[str, Any]] = []
        for file in group["files"]:
            content_id = owner.get(file["path"])
            if content_id is None:
                files.append(file)
            elif content_id not in representatives:
                representatives[content_id] = file
                files.append(file)
        if len(files) < 2:  # pragma: no cover - extras guarantee >= 2 entries
            continue
        sizes = [int(file["size"] or 0) for file in files]
        merged.append(
            {
                **group,
                "files": files,
                "size": sum(sizes),
                "size_formatted": files[0]["size_formatted"],
                "reclaimable": max(0, sum(sizes) - max(sizes)),
            }
        )

    merged.sort(key=lambda group: group["reclaimable"], reverse=True)
    unhashed = int(content["unhashed"]) + int(visual["unhashed"])
    with _merge_cache_lock:
        _merge_cache.update(
            {
                "at": now,
                "merged": merged,
                "unhashed": unhashed,
                "fingerprint": fingerprint,
                "include_images": include_images,
            }
        )
    return merged, unhashed


def auto_verify_groups(groups: list[dict[str, Any]]) -> int:
    """Queue a five-frame check for visual groups that have none yet."""
    candidates: list[list[str]] = []
    for group in groups:
        if group.get("match_type") != "visual":
            continue
        verification = group.get("verification")
        if verification and verification.get("available"):
            continue
        paths = [
            file["path"]
            for file in group.get("files", [])
            if file.get("type") == "video"
        ]
        if len(paths) >= 2:
            candidates.append(paths)
    if not candidates:
        return 0
    try:
        start_visual_checks(candidates)
    except writes.WriteError:
        return 0
    return len(candidates)


def status(include_images: bool = True) -> dict[str, Any]:
    image_filter = "" if include_images else " AND type != 'image'"
    with reader() as conn:
        candidate_files = conn.execute(
            "SELECT COUNT(*) AS n FROM files WHERE size > 0"
            f"{image_filter} AND size IN ("
            "SELECT size FROM files WHERE size > 0"
            f"{image_filter} GROUP BY size HAVING COUNT(*) > 1)"
        ).fetchone()["n"]
        unhashed_content = conn.execute(
            "SELECT COUNT(*) AS n FROM files WHERE size > 0 AND quick_hash IS NULL"
            f"{image_filter} AND size IN (SELECT size FROM files WHERE size > 0"
            f"{image_filter} GROUP BY size HAVING COUNT(*) > 1)"
        ).fetchone()["n"]
        visual_ready = conn.execute(
            "SELECT COUNT(*) AS n FROM files WHERE length(thumb_hash) = 16"
            f"{image_filter}"
        ).fetchone()["n"]
        visual_unhashed = conn.execute(
            "SELECT COUNT(*) AS n FROM files WHERE has_thumb = 1 AND thumb_hash IS NULL"
            f"{image_filter}"
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


@activity.tracked("content-hashes")
def hash_content(limit: int = 50, include_images: bool = True) -> dict[str, Any]:
    """Hash up to ``limit`` candidate files that have no quick hash yet."""
    hashed = 0
    failed = 0
    image_filter = "" if include_images else " AND f.type != 'image'"
    plain_image_filter = "" if include_images else " AND type != 'image'"
    with reader() as conn:
        rows = conn.execute(
            f"""
            SELECT f.path FROM files f
            WHERE f.size > 0 AND f.quick_hash IS NULL
              AND f.size IN (SELECT size FROM files WHERE size > 0
                             GROUP BY size HAVING COUNT(*) > 1){image_filter}
              -- ZIP members can never be hashed by the byte scanner; without
              -- this filter they permanently occupy the batch window (they
              -- share sizes, so they keep ranking first) and the sweep never
              -- converges. Their groups form from dedupe content elsewhere.
              AND f.folder_path NOT IN (
                  SELECT path FROM folders WHERE is_zip = 1
              )
              -- The byte hash of a streaming transcode target/needs-transcode
              -- file is still valid, so no extra type filter is needed.
            ORDER BY f.size DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    updates: list[tuple[str, str]] = []
    for row in rows:
        if not activity.checkpoint("content-hashes", row["path"]):
            break
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
            "SELECT COUNT(*) AS n FROM files WHERE size > 0 AND quick_hash IS NULL"
            f"{plain_image_filter} AND size IN (SELECT size FROM files WHERE size > 0"
            " GROUP BY size HAVING COUNT(*) > 1) "
            "AND folder_path NOT IN (SELECT path FROM folders WHERE is_zip = 1)"
        ).fetchone()["n"]
    if updates:
        invalidate_merged_cache()
    return {"hashed": hashed, "failed": failed, "remaining": remaining or 0}


@activity.tracked("visual-hashes")
def hash_visual(limit: int = 50, include_images: bool = True) -> dict[str, Any]:
    """Compute pHashes for up to ``limit`` files that have a cached poster."""
    image_filter = "" if include_images else " AND type != 'image'"
    with reader() as conn:
        rows = conn.execute(
            "SELECT path, mtime, size FROM files WHERE has_thumb = 1 "
            f"AND thumb_hash IS NULL{image_filter} ORDER BY path LIMIT ?",
            (limit,),
        ).fetchall()
    updates: list[tuple[str, str]] = []
    hashed = 0
    for row in rows:
        if not activity.checkpoint("visual-hashes", row["path"]):
            break
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
            f"{image_filter}"
        ).fetchone()["n"]
    if updates:
        invalidate_merged_cache()
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
        candidates.append(path)

    deleted: list[str] = []
    failed: list[dict[str, str]] = []
    if candidates:
        result = writes.delete_files(candidates)
        deleted = result["deleted"]
        failed = result["failed"]

    if deleted:
        invalidate_merged_cache()
    return {
        "kept": keep_path,
        "deleted": deleted,
        "failed": failed,
        "mismatched": mismatched,
    }


def batch_resolve(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Resolve several duplicate groups; every group is independent.

    A group that fails (its kept file vanished, an invalid path) is recorded
    into ``failed`` and the remaining groups still resolve, so one bad group
    never blocks the rest of a bulk delete.
    """
    resolved = 0
    deleted: list[str] = []
    mismatched: list[str] = []
    failed: list[dict[str, str]] = []
    for item in items:
        keep = str(item.get("keep", ""))
        deletes = [str(path) for path in item.get("delete", [])]
        mode = str(item.get("mode", "content"))
        try:
            result = resolve(keep, deletes, mode)
            resolved += 1
            deleted.extend(result["deleted"])
            mismatched.extend(result["mismatched"])
        except writes.WriteError as exc:
            failed.append({"path": keep, "error": exc.message})
    return {
        "attempted": len(items),
        "resolved": resolved,
        "deleted": deleted,
        "mismatched": mismatched,
        "failed": failed,
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
    invalidate_merged_cache()
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
    invalidate_merged_cache()
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
