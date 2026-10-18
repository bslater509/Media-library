"""Housekeeping for the candidate-frame cache (thumbnail chooser / face scans).

Candidate frames are the 480px JPEG stills that the video thumbnail chooser, the
animated preview compiler and the face worker extract from a video. They live in
``THUMBNAIL_CACHE_DIR / "candidates"`` as::

    candidates/v<sha256(video_path)[:24]>/cand_<seconds>.jpg

Binning the frames under a per-video directory is what makes the cache
sweepable: the directory name is the video's path hashed, so a frame belongs to
a video the index can be asked about by name. The frame's own hash (path +
timestamp) is not invertible, and arbitrary scrubber positions make the set of
timestamps a video *can* have unbounded, so the directory is the only reliable
unit to sweep.

Two guards, matching the animated-preview cache:

* **Orphan collection** (``clean_orphaned_candidate_frames``) — every per-video
  directory whose video no longer has an indexed row is dropped, along with the
  flat ``cand_<24 hex>.jpg`` files the earlier layout wrote (nothing looks those
  up any more, so they are orphans by definition).
* **An LRU ceiling** (``manage_candidate_cache_lru``) — the tree is trimmed back
  under ``CANDIDATE_CACHE_MAX_SIZE_GB``, least recently written frame first, and
  directories left empty are removed. Frames are regenerable, so an evicted one
  simply comes back the next time that second of that video is needed.

Both sweeps are index-only reads plus ``os.scandir`` over a *local* cache
directory (never the FUSE mount), and the generated-name patterns are matched
strictly so the temp file a generator is still writing
(``cand_<ts>.<uuid>.tmp.jpg``) can never be mistaken for a finished frame.

Configuration is read from ``media_library.core.config`` at call time (not bound
at import), so the test suite's sandboxed ``THUMBNAIL_CACHE_DIR`` is honoured and
no test can reach a real cache directory.
"""

import hashlib
import logging
import os
import re
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from media_library.core import config
from media_library.core.state import format_size

logger = logging.getLogger(__name__)

_GIB = 1024 * 1024 * 1024

# One directory per video: `v<24 hex>`. A staging directory or a stray file is
# never mistaken for one.
_VIDEO_DIR_RE = re.compile(r"^v[0-9a-f]{24}$")
# Finished frames inside such a directory: `cand_<seconds with 3 decimals>.jpg`.
_FINAL_FRAME_RE = re.compile(r"^cand_\d+\.\d{3}\.jpg$")
# Frames written by the pre-directory layout (keyed on path + timestamp only,
# flat in the cache directory). Unreachable now, so always an orphan.
_LEGACY_FRAME_RE = re.compile(r"^cand_[0-9a-f]{24}\.jpg$")

# Usage memo, keyed by directory: a status poll every couple of seconds must not
# walk the whole tree each time, but a different cache dir (tests, or a
# restarted process) must never inherit another one's numbers.
_USAGE_CACHE: dict[str, Any] = {"key": "", "at": 0.0, "files": 0, "bytes": 0}
_USAGE_TTL_S = 60.0

# LRU throttling: the ceiling is re-checked at most this often per directory.
_LRU_STATE: dict[str, Any] = {"key": "", "at": 0.0}
_LRU_MIN_INTERVAL_S = 60.0

# Free this much extra headroom once the ceiling is crossed, so a single run
# evicts a batch instead of one file per generation.
_LRU_TARGET_RATIO = 0.9


def get_candidate_cache_dir() -> Path:
    """Return (creating if needed) the candidate frame cache directory."""
    cand_dir = Path(config.THUMBNAIL_CACHE_DIR) / "candidates"
    cand_dir.mkdir(parents=True, exist_ok=True)
    return cand_dir


def candidate_dir_name(file_path: str) -> str:
    """Name of the cache subdirectory holding one video's frames."""
    digest = hashlib.sha256(str(file_path).encode("utf-8")).hexdigest()[:24]
    return f"v{digest}"


def candidate_cache_max_bytes() -> int:
    try:
        gb = float(getattr(config, "CANDIDATE_CACHE_MAX_SIZE_GB", 5.0))
    except (TypeError, ValueError):
        gb = 5.0
    return max(0, int(gb * _GIB))


def valid_candidate_dirs(conn: Any) -> set[str]:
    """Every per-video directory name the current index could be serving."""
    names: set[str] = set()
    cur = conn.execute("SELECT path FROM files WHERE type = 'video'")
    for r in cur.fetchall():
        names.add(candidate_dir_name(r["path"]))
    return names


def _iter_frame_files() -> Iterator[tuple[str, os.stat_result]]:
    """Yield ``(path, stat)`` for every finished frame, oldest bin first."""
    cand_dir = get_candidate_cache_dir()
    try:
        with os.scandir(cand_dir) as entries:
            for entry in entries:
                if not _VIDEO_DIR_RE.match(entry.name):
                    continue
                try:
                    if not entry.is_dir():
                        continue
                    with os.scandir(entry.path) as frames:
                        for frame in frames:
                            if not _FINAL_FRAME_RE.match(frame.name):
                                continue
                            try:
                                if frame.is_file():
                                    yield frame.path, frame.stat()
                            except OSError:
                                continue
                except OSError:
                    continue
    except OSError:
        return


def candidate_cache_usage(max_age_s: float = _USAGE_TTL_S) -> tuple[int, int]:
    """Return ``(file_count, total_bytes)`` for the frame cache, memoized."""
    cand_dir = get_candidate_cache_dir()
    key = str(cand_dir)
    now = time.time()
    if _USAGE_CACHE["key"] == key and (now - float(_USAGE_CACHE["at"])) < max_age_s:
        return int(_USAGE_CACHE["files"]), int(_USAGE_CACHE["bytes"])

    files = 0
    total = 0
    for _path, st in _iter_frame_files():
        files += 1
        total += st.st_size

    _USAGE_CACHE.update(key=key, at=now, files=files, bytes=total)
    return files, total


def frame_cache_payload() -> dict[str, Any]:
    """Status-ready snapshot of the frame cache for the status payload."""
    files, total = candidate_cache_usage()
    cap = candidate_cache_max_bytes()
    return {
        "frame_cache_files": files,
        "frame_cache_bytes": total,
        "frame_cache_size_formatted": format_size(total),
        "frame_cache_max_bytes": cap,
        "frame_cache_max_size_formatted": format_size(cap),
    }


def unlink_candidate_frames(rows: Any) -> int:
    """Drop the cached frames of already-known DB rows (path only)."""
    removed = 0
    cand_dir = get_candidate_cache_dir()
    for r in rows:
        target = cand_dir / candidate_dir_name(r["path"])
        try:
            if not target.is_dir():
                continue
            for frame in target.iterdir():
                try:
                    if frame.is_file():
                        frame.unlink(missing_ok=True)
                        removed += 1
                except OSError:
                    continue
            os.rmdir(target)
        except OSError:
            continue
    if removed:
        _USAGE_CACHE["at"] = 0.0
    return removed


def clean_orphaned_candidate_frames(conn: Any) -> int:
    """Delete frames no indexed video can reference. Returns the count removed."""
    cand_dir = get_candidate_cache_dir()
    try:
        indexed = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        valid = valid_candidate_dirs(conn)
    except Exception as e:  # a broken index read must never delete anything
        logger.error(f"[FrameCache] Could not read index for orphan sweep: {e}")
        return 0
    if not indexed:
        # An empty or unreadable index must not be allowed to empty the cache:
        # the frames are still perfectly good once the crawl has run.
        return 0

    pruned = 0
    try:
        with os.scandir(cand_dir) as entries:
            for entry in entries:
                try:
                    if entry.is_file():
                        if _LEGACY_FRAME_RE.match(entry.name):
                            Path(entry.path).unlink(missing_ok=True)
                            pruned += 1
                        continue
                    if not _VIDEO_DIR_RE.match(entry.name) or entry.name in valid:
                        continue
                    with os.scandir(entry.path) as frames:
                        for frame in frames:
                            try:
                                if frame.is_file():
                                    Path(frame.path).unlink(missing_ok=True)
                                    pruned += 1
                            except OSError:
                                continue
                    try:
                        os.rmdir(entry.path)
                    except OSError:
                        # A generator may be writing into it right now.
                        pass
                except OSError:
                    continue
    except OSError:
        pass

    if pruned:
        _USAGE_CACHE["at"] = 0.0
    return pruned


def manage_candidate_cache_lru(
    max_bytes: int | None = None, min_interval_s: float = _LRU_MIN_INTERVAL_S
) -> dict[str, Any]:
    """Trim the frame cache back under its ceiling, oldest frames first.

    Returns a small report (``evicted``, ``bytes``, ``cap_bytes``, ``skipped``).
    The check is throttled per cache directory unless an explicit ``max_bytes``
    is passed, so calling it after every generation is cheap.
    """
    cap = candidate_cache_max_bytes() if max_bytes is None else max(0, int(max_bytes))
    cand_dir = get_candidate_cache_dir()
    key = f"{cand_dir}|{cap}"
    now = time.time()
    throttled = (
        max_bytes is None
        and _LRU_STATE["key"] == key
        and (now - float(_LRU_STATE["at"])) < min_interval_s
    )
    if not throttled:
        _LRU_STATE.update(key=key, at=now)

    report: dict[str, Any] = {
        "evicted": 0,
        "bytes": 0,
        "cap_bytes": cap,
        "skipped": throttled,
    }

    entries = [(path, st.st_mtime, st.st_size) for path, st in _iter_frame_files()]
    total = sum(size for _path, _mtime, size in entries)
    report["bytes"] = total
    if throttled or total <= cap:
        return report

    entries.sort(key=lambda t: t[1])  # oldest written first
    target = int(cap * _LRU_TARGET_RATIO)
    evicted = 0
    for path, _mtime, size in entries:
        if total <= target:
            break
        try:
            Path(path).unlink(missing_ok=True)
            total -= size
            evicted += 1
        except OSError:
            continue

    report["evicted"] = evicted
    report["bytes"] = total
    _USAGE_CACHE["at"] = 0.0
    if evicted:
        _prune_empty_video_dirs()
        logger.info(
            f"[FrameCache] Evicted {evicted} candidate frames "
            f"({format_size(total)} of {format_size(cap)} left); "
            "they regenerate on the next view."
        )
    return report


def _dir_is_empty(path: str) -> bool:
    with os.scandir(path) as it:
        for _entry in it:
            return False
    return True


def _prune_empty_video_dirs() -> int:
    """Remove per-video directories the eviction (or a delete) emptied."""
    cand_dir = get_candidate_cache_dir()
    removed = 0
    try:
        with os.scandir(cand_dir) as entries:
            for entry in entries:
                if not _VIDEO_DIR_RE.match(entry.name):
                    continue
                try:
                    if entry.is_dir() and _dir_is_empty(entry.path):
                        os.rmdir(entry.path)
                        removed += 1
                except OSError:
                    continue
    except OSError:
        pass
    return removed
