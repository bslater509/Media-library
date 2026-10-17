"""Housekeeping for the on-disk animated WebP preview cache.

Animated previews live in ``THUMBNAIL_CACHE_DIR / "animated" / anim_<hash>.webp``
where ``<hash>`` is derived from ``path_mtime_size`` (a renamed, deleted or
re-encoded video therefore orphans its old file). The files are large — up to 24
frames at 480px — and purely regenerable, so they need the same two guards the
extracted-ZIP cache already has:

* **Orphan collection** — previews whose video no longer exists in the index are
  removed (``clean_orphaned_animated_previews``), and
* **An LRU ceiling** — the directory is trimmed back under
  ``ANIMATED_CACHE_MAX_SIZE_GB`` by deleting the least recently written previews
  and clearing their ``has_animated`` flag so a later view regenerates them
  (``manage_animated_cache_lru``).

Both sweeps are index-only reads plus one ``os.scandir`` over a *local* cache
directory (never the FUSE mount), and the generated-name pattern is matched
strictly so a preview another thread is still writing
(``anim_<hash>.<uuid>.tmp.webp``) can never be mistaken for a finished one.

Configuration is read from ``media_library.core.config`` at call time (not bound
at import), so the test suite's sandboxed ``THUMBNAIL_CACHE_DIR`` is honoured and
no test can reach a real cache directory.
"""

import hashlib
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

from media_library.core import config
from media_library.core.state import format_size
from media_library.db.connection import db_conn

logger = logging.getLogger(__name__)

_GIB = 1024 * 1024 * 1024

# Only finished previews are candidates: `anim_<24 hex>.webp`. The temp file a
# generator writes next to it (`anim_<hash>.<uuid>.tmp.webp`) carries an extra
# segment and is skipped.
_FINAL_ANIM_RE = re.compile(r"^anim_[0-9a-f]{24}\.webp$")

# Usage memo, keyed by directory: a status poll every couple of seconds must not
# scandir the whole directory each time, but a different cache dir (tests, or a
# restarted process) must never inherit another one's numbers.
_USAGE_CACHE: dict[str, Any] = {"key": "", "at": 0.0, "files": 0, "bytes": 0}
_USAGE_TTL_S = 60.0

# LRU throttling: the ceiling is re-checked at most this often per directory.
_LRU_STATE: dict[str, Any] = {"key": "", "at": 0.0}
_LRU_MIN_INTERVAL_S = 60.0

# Free this much extra headroom once the ceiling is crossed, so a single run
# evicts a batch instead of one file per generation.
_LRU_TARGET_RATIO = 0.9


def get_anim_cache_dir() -> Path:
    """Return (creating if needed) the animated preview cache directory."""
    anim_dir = Path(config.THUMBNAIL_CACHE_DIR) / "animated"
    anim_dir.mkdir(parents=True, exist_ok=True)
    return anim_dir


def anim_cache_max_bytes() -> int:
    try:
        gb = float(getattr(config, "ANIMATED_CACHE_MAX_SIZE_GB", 5.0))
    except (TypeError, ValueError):
        gb = 5.0
    return max(0, int(gb * _GIB))


def _anim_name_for_key(key: str) -> str:
    return f"anim_{hashlib.sha256(key.encode('utf-8')).hexdigest()[:24]}.webp"


def _names_for_row(path: str, mtime: Any, size: Any) -> list[str]:
    """Every preview filename ``get_animated_preview_path`` could have produced."""
    names = [_anim_name_for_key(path)]
    if mtime is not None and size is not None:
        names.append(_anim_name_for_key(f"{path}_{mtime}_{size}"))
    if size is not None:
        names.append(_anim_name_for_key(f"{path}_{size}"))
    return names


def valid_anim_names(conn: Any) -> set[str]:
    """Return every preview filename the current index could be serving."""
    names: set[str] = set()
    cur = conn.execute("SELECT path, mtime, size FROM files")
    for r in cur.fetchall():
        names.update(_names_for_row(r["path"], r["mtime"], r["size"]))
    return names


def anim_cache_usage(max_age_s: float = _USAGE_TTL_S) -> tuple[int, int]:
    """Return ``(file_count, total_bytes)`` for the preview cache, memoized."""
    anim_dir = get_anim_cache_dir()
    key = str(anim_dir)
    now = time.time()
    if _USAGE_CACHE["key"] == key and (now - float(_USAGE_CACHE["at"])) < max_age_s:
        return int(_USAGE_CACHE["files"]), int(_USAGE_CACHE["bytes"])

    files = 0
    total = 0
    try:
        with os.scandir(anim_dir) as entries:
            for entry in entries:
                if not _FINAL_ANIM_RE.match(entry.name):
                    continue
                try:
                    if entry.is_file():
                        files += 1
                        total += entry.stat().st_size
                except OSError:
                    continue
    except OSError:
        pass

    _USAGE_CACHE.update(key=key, at=now, files=files, bytes=total)
    return files, total


def anim_cache_payload() -> dict[str, Any]:
    """Status-ready snapshot of the preview cache for the tasks modal."""
    files, total = anim_cache_usage()
    cap = anim_cache_max_bytes()
    return {
        "cache_files": files,
        "cache_bytes": total,
        "cache_size_formatted": format_size(total),
        "cache_max_bytes": cap,
        "cache_max_size_formatted": format_size(cap),
    }


def unlink_animated_previews(rows: Any) -> int:
    """Remove the cached previews for already-known DB rows (path/mtime/size)."""
    removed = 0
    anim_dir = get_anim_cache_dir()
    for r in rows:
        for name in _names_for_row(r["path"], r["mtime"], r["size"]):
            try:
                target = anim_dir / name
                if target.exists():
                    target.unlink()
                    removed += 1
            except OSError:
                pass
    if removed:
        _USAGE_CACHE["at"] = 0.0
    return removed


def clean_orphaned_animated_previews(conn: Any) -> int:
    """Delete cached previews no indexed file can reference. Returns the count."""
    anim_dir = get_anim_cache_dir()
    try:
        valid = valid_anim_names(conn)
    except Exception as e:  # a broken index read must never delete anything
        logger.error(f"[AnimCache] Could not read index for orphan sweep: {e}")
        return 0

    pruned = 0
    try:
        with os.scandir(anim_dir) as entries:
            for entry in entries:
                if not _FINAL_ANIM_RE.match(entry.name) or entry.name in valid:
                    continue
                try:
                    if entry.is_file():
                        Path(entry.path).unlink(missing_ok=True)
                        pruned += 1
                except OSError:
                    continue
    except OSError:
        pass

    if pruned:
        _USAGE_CACHE["at"] = 0.0
    return pruned


def _clear_missing_animated_flags() -> int:
    """Reset ``has_animated`` for videos whose cached preview is gone."""
    cleared = 0
    anim_dir = get_anim_cache_dir()
    try:
        with db_conn() as conn:
            rows = conn.execute(
                "SELECT path, mtime, size FROM files "
                "WHERE type = 'video' AND has_animated = 1"
            ).fetchall()
            stale = [
                (r["path"],)
                for r in rows
                if not any(
                    (anim_dir / n).exists()
                    for n in _names_for_row(r["path"], r["mtime"], r["size"])
                )
            ]
            if stale:
                with conn:
                    conn.executemany(
                        "UPDATE files SET has_animated = 0 WHERE path = ?", stale
                    )
                cleared = len(stale)
    except Exception as e:
        logger.error(f"[AnimCache] Could not clear stale has_animated flags: {e}")
    return cleared


def manage_animated_cache_lru(
    max_bytes: int | None = None, min_interval_s: float = _LRU_MIN_INTERVAL_S
) -> dict[str, Any]:
    """Trim the preview cache back under its ceiling, oldest previews first.

    Returns a small report (``evicted``, ``cleared``, ``bytes``, ``cap_bytes``).
    The check is throttled per cache directory unless an explicit ``max_bytes``
    is passed, so calling it after every generation is cheap.
    """
    cap = anim_cache_max_bytes() if max_bytes is None else max(0, int(max_bytes))
    anim_dir = get_anim_cache_dir()
    key = f"{anim_dir}|{cap}"
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
        "cleared": 0,
        "bytes": 0,
        "cap_bytes": cap,
        "skipped": throttled,
    }

    try:
        entries = []
        total = 0
        with os.scandir(anim_dir) as it:
            for e in it:
                if not _FINAL_ANIM_RE.match(e.name):
                    continue
                try:
                    if not e.is_file():
                        continue
                    st = e.stat()
                except OSError:
                    continue
                entries.append((e.path, st.st_mtime, st.st_size))
                total += st.st_size
    except OSError:
        return report

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
        report["cleared"] = _clear_missing_animated_flags()
        logger.info(
            f"[AnimCache] Evicted {evicted} animated previews "
            f"({format_size(report['bytes'])} of {format_size(cap)} left); "
            f"cleared {report['cleared']} has_animated flags for regeneration."
        )
    return report
