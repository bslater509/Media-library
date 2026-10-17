"""Animated WebP preview cache housekeeping: orphan sweep, LRU ceiling, status.

Previews are regenerable but large, and are keyed on ``path_mtime_size`` — so a
delete, rename or x265 re-encode orphans the old file. These tests pin the two
guards (orphan collection and the LRU ceiling) plus the status the tasks modal
renders from.
"""

import hashlib
import os
import time

from media_library.db.connection import db_conn
from media_library.db.indexer import prune_stale_index_entries
from media_library.services.anim_cache import (
    anim_cache_payload,
    anim_cache_usage,
    clean_orphaned_animated_previews,
    get_anim_cache_dir,
    manage_animated_cache_lru,
    unlink_animated_previews,
)


def _anim_name(key: str) -> str:
    """The on-disk naming contract: ``anim_<sha256(key)[:24]>.webp``."""
    return f"anim_{hashlib.sha256(key.encode('utf-8')).hexdigest()[:24]}.webp"


def _insert_video(media_dir, name, mtime=100.0, size=2048, has_animated=1):
    path = os.path.join(str(media_dir), name)
    with db_conn() as conn, conn:
        conn.execute(
            """INSERT OR REPLACE INTO files
               (path, name, folder_path, type, ext, size, size_formatted, mtime, has_animated)
               VALUES (?, ?, ?, 'video', '.mp4', ?, '2.0 KB', ?, ?)""",
            (path, name, str(media_dir), size, mtime, has_animated),
        )
    return path


def _write_anim(name, payload_size=1000, mtime=None):
    path = get_anim_cache_dir() / name
    path.write_bytes(b"R" * payload_size)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def test_cache_dir_defaults_under_the_thumbnail_cache(sandbox_env):
    # Read the constant at call time: the sandbox patches config's value, and
    # (unlike an import-time binding) the module honours it.
    from media_library.core import config

    assert get_anim_cache_dir() == config.THUMBNAIL_CACHE_DIR / "animated"
    assert get_anim_cache_dir() == sandbox_env["thumb_dir"] / "animated"
    assert get_anim_cache_dir().is_dir()


def test_orphan_sweep_keeps_live_previews_and_temp_writes(sandbox_env):
    path = _insert_video(sandbox_env["media_dir"], "clip.mp4", mtime=10.0, size=4096)
    live = _write_anim(_anim_name(f"{path}_10.0_4096"))
    orphan = _write_anim("anim_" + "a" * 24 + ".webp")
    # A generator's in-flight temp file must never be swept as a finished one.
    in_flight = _write_anim("anim_" + "b" * 24 + ".deadbeef.tmp.webp")

    with db_conn() as conn:
        pruned = clean_orphaned_animated_previews(conn)

    assert pruned == 1
    assert live.exists()
    assert in_flight.exists()
    assert not orphan.exists()


def test_prune_sweeps_orphaned_previews_and_reports_them(sandbox_env):
    orphan = _write_anim("anim_" + "c" * 24 + ".webp")

    result = prune_stale_index_entries(clean_orphaned_thumbnails=True, force=True)

    assert result["pruned_orphaned_anims"] == 1
    assert not orphan.exists()


def test_removing_a_stale_folder_drops_its_previews(sandbox_env):
    """A folder pruned from the index must not leave its previews behind."""
    path = _insert_video(sandbox_env["media_dir"], "gone.mp4", mtime=5.0, size=512)
    preview = _write_anim(_anim_name(f"{path}_5.0_512"))

    rows = [{"path": path, "mtime": 5.0, "size": 512}]
    removed = unlink_animated_previews(rows)

    assert removed == 1
    assert not preview.exists()


def test_lru_evicts_oldest_previews_and_clears_has_animated(sandbox_env):
    media = sandbox_env["media_dir"]
    now = time.time()
    # Two indexed videos (oldest previews) plus two orphaned previews.
    rows = []
    for i, (name, mtime) in enumerate([("a.mp4", 1.0), ("b.mp4", 2.0)]):
        path = _insert_video(media, name, mtime=mtime, size=2048)
        rows.append((path, mtime, 2048, now - (400 - i * 10)))
    for i, name in enumerate(["c.mp4", "d.mp4"]):
        rows.append((os.path.join(str(media), name), 0.0, 0, now - (200 - i * 10)))

    for path, mtime, size, file_mtime in rows:
        key = f"{path}_{mtime}_{size}" if size else path
        _write_anim(_anim_name(key), payload_size=1000, mtime=file_mtime)

    report = manage_animated_cache_lru(max_bytes=2500)

    assert report["evicted"] == 2
    assert report["bytes"] <= int(2500 * 0.9)
    # Both indexed videos lost their preview, so their flags must reset and be
    # regenerated on the next view rather than reported as cached forever.
    assert report["cleared"] == 2
    with db_conn() as conn:
        flags = [
            r[0]
            for r in conn.execute(
                "SELECT has_animated FROM files WHERE type = 'video'"
            ).fetchall()
        ]
    assert flags == [0, 0]
    assert anim_cache_usage(max_age_s=0)[0] == 2


def test_lru_is_a_noop_below_the_ceiling(sandbox_env):
    _write_anim("anim_" + "d" * 24 + ".webp", payload_size=500)
    report = manage_animated_cache_lru(max_bytes=10 * 1024 * 1024)
    assert report["evicted"] == 0
    assert report["bytes"] == 500


def test_cache_payload_reports_usage_and_cap(sandbox_env):
    _write_anim("anim_" + "e" * 24 + ".webp", payload_size=2048)
    payload = anim_cache_payload()
    assert payload["cache_files"] == 1
    assert payload["cache_bytes"] == 2048
    assert payload["cache_size_formatted"]
    assert payload["cache_max_bytes"] > 0
    assert payload["cache_max_size_formatted"]


def test_animated_worker_status_exposes_the_cache(sandbox_env):
    from media_library.workers import animated_thumb_worker_instance

    status = animated_thumb_worker_instance.get_status()
    assert "cache_bytes" in status
    assert "cache_max_bytes" in status
    assert "cache_size_formatted" in status
