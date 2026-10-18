"""Candidate frame cache housekeeping: binning, orphan sweep, LRU ceiling.

Candidate frames (the 480px stills the thumbnail chooser, the animated preview
compiler and the face worker pull out of a video) are regenerable, and the face
sweep now asks for up to ``FACE_VIDEO_MAX_FRAMES`` of them per video — so the
cache needs the same two guards the animated preview cache has. These tests pin
the on-disk layout (one directory per video), the orphan sweep, the LRU ceiling
and the status the faces payload reports.
"""

import math
import os
import time

from media_library.db.connection import db_conn
from media_library.db.indexer import prune_stale_index_entries
from media_library.services.candidate_cache import (
    candidate_cache_max_bytes,
    candidate_cache_usage,
    candidate_dir_name,
    clean_orphaned_candidate_frames,
    frame_cache_payload,
    get_candidate_cache_dir,
    manage_candidate_cache_lru,
    unlink_candidate_frames,
)
from media_library.services.ffmpeg_frames import get_candidate_cache_path


def _insert_video(media_dir, name, mtime=100.0, size=2048, duration=600.0):
    path = os.path.join(str(media_dir), name)
    with db_conn() as conn, conn:
        conn.execute(
            """INSERT OR REPLACE INTO files
               (path, name, folder_path, type, ext, size, size_formatted, mtime, duration)
               VALUES (?, ?, ?, 'video', '.mp4', ?, '2.0 KB', ?, ?)""",
            (path, name, str(media_dir), size, mtime, duration),
        )
    return path


def _write_frame(video_path, timestamp, payload_size=1000, mtime=None):
    frame = get_candidate_cache_path(video_path, timestamp)
    frame.write_bytes(b"J" * payload_size)
    if mtime is not None:
        os.utime(frame, (mtime, mtime))
    return frame


def test_cache_dir_defaults_under_the_thumbnail_cache(sandbox_env):
    # Read the constant at call time: the sandbox patches config's value, and
    # (unlike an import-time binding) the module honours it.
    from media_library.core import config

    assert get_candidate_cache_dir() == config.THUMBNAIL_CACHE_DIR / "candidates"
    assert get_candidate_cache_dir() == sandbox_env["thumb_dir"] / "candidates"
    assert get_candidate_cache_dir().is_dir()
    assert candidate_cache_max_bytes() > 0


def test_frames_are_binned_per_video(sandbox_env):
    one = get_candidate_cache_path("/media/one.mp4", 95.5124)
    two = get_candidate_cache_path("/media/two.mp4", 95.5124)

    assert one.parent.name == candidate_dir_name("/media/one.mp4")
    assert one.parent.parent == get_candidate_cache_dir()
    assert two.parent != one.parent
    # The frame is named after its second, so a whole video's bin is readable.
    assert one.name == "cand_95.512.jpg"
    # A missing or absurd timestamp must still produce a sweepable name.
    assert get_candidate_cache_path("/media/one.mp4", -3.0).name == "cand_0.000.jpg"
    assert get_candidate_cache_path("/media/one.mp4", math.inf).name == "cand_0.000.jpg"
    assert get_candidate_cache_path("/media/one.mp4", math.nan).name == "cand_0.000.jpg"


def test_orphan_sweep_keeps_live_frames_and_temp_writes(sandbox_env):
    media = sandbox_env["media_dir"]
    live_video = _insert_video(media, "clip.mp4", duration=3600.0)
    live = _write_frame(live_video, 30.0)
    live_second = _write_frame(live_video, 60.0)

    # A video that is no longer in the index keeps nothing.
    ghost_dir = get_candidate_cache_dir() / candidate_dir_name(
        os.path.join(str(media), "deleted.mp4")
    )
    ghost_dir.mkdir(parents=True, exist_ok=True)
    ghost = ghost_dir / "cand_0.000.jpg"
    ghost.write_bytes(b"J" * 100)

    # A generator's in-flight temp file must never be swept, even in a live bin.
    in_flight = live.parent / "cand_120.000.deadbeef.tmp.jpg"
    in_flight.write_bytes(b"J" * 100)

    # Frames from the pre-directory layout are unreachable now.
    legacy = get_candidate_cache_dir() / ("cand_" + "a" * 24 + ".jpg")
    legacy.write_bytes(b"J" * 100)

    with db_conn() as conn:
        pruned = clean_orphaned_candidate_frames(conn)

    assert pruned == 2  # the ghost bin's frame + the legacy file
    assert live.exists()
    assert live_second.exists()
    assert in_flight.exists()
    assert not ghost.exists()
    assert not ghost_dir.exists()
    assert not legacy.exists()


def test_orphan_sweep_never_runs_against_an_empty_index(sandbox_env):
    """An un-crawled (or unreadable) index must not empty the cache."""
    stray_dir = get_candidate_cache_dir() / candidate_dir_name("/media/whatever.mp4")
    stray_dir.mkdir(parents=True, exist_ok=True)
    frame = stray_dir / "cand_0.000.jpg"
    frame.write_bytes(b"J" * 100)

    with db_conn() as conn:
        assert clean_orphaned_candidate_frames(conn) == 0

    assert frame.exists()


def test_prune_sweeps_orphaned_frames_and_reports_them(sandbox_env):
    _insert_video(sandbox_env["media_dir"], "kept.mp4")
    ghost_dir = get_candidate_cache_dir() / candidate_dir_name("/media/gone.mp4")
    ghost_dir.mkdir(parents=True, exist_ok=True)
    ghost = ghost_dir / "cand_0.000.jpg"
    ghost.write_bytes(b"J" * 100)

    result = prune_stale_index_entries(clean_orphaned_thumbnails=True, force=True)

    assert result["pruned_orphaned_frames"] == 1
    assert not ghost.exists()


def test_removing_a_stale_folder_drops_its_frames(sandbox_env):
    """A folder pruned from the index must not leave its frames behind."""
    video = _insert_video(sandbox_env["media_dir"], "gone.mp4")
    _write_frame(video, 0.0)
    _write_frame(video, 30.0)
    kept_video = _insert_video(sandbox_env["media_dir"], "kept.mp4")
    kept = _write_frame(kept_video, 0.0)

    removed = unlink_candidate_frames([{"path": video}])

    assert removed == 2
    assert not (get_candidate_cache_dir() / candidate_dir_name(video)).exists()
    assert kept.exists()


def test_lru_evicts_oldest_frames_and_drops_emptied_bins(sandbox_env):
    media = sandbox_env["media_dir"]
    now = time.time()
    old_video = _insert_video(media, "old.mp4")
    new_video = _insert_video(media, "new.mp4")

    # Two oldest frames share the "old" video's bin, so evicting both must leave
    # no empty directory behind.
    for ts, age in [(0.0, 400), (30.0, 390)]:
        _write_frame(old_video, ts, mtime=now - age)
    for ts, age in [(0.0, 200), (30.0, 190)]:
        _write_frame(new_video, ts, mtime=now - age)

    report = manage_candidate_cache_lru(max_bytes=2500)

    assert report["evicted"] == 2
    assert report["bytes"] == 2000
    assert report["bytes"] <= int(2500 * 0.9)
    assert not (get_candidate_cache_dir() / candidate_dir_name(old_video)).exists()
    assert candidate_cache_usage(max_age_s=0)[0] == 2


def test_lru_is_a_noop_below_the_ceiling_and_throttles(sandbox_env):
    _write_frame(_insert_video(sandbox_env["media_dir"], "clip.mp4"), 0.0)

    first = manage_candidate_cache_lru()
    assert first["evicted"] == 0
    assert first["bytes"] == 1000
    assert first["skipped"] is False

    # Without an explicit ceiling the check is throttled to once a minute, so a
    # generation per frame stays cheap.
    second = manage_candidate_cache_lru()
    assert second["skipped"] is True


def test_cache_payload_reports_usage_and_cap(sandbox_env):
    _write_frame(_insert_video(sandbox_env["media_dir"], "clip.mp4"), 0.0, 2048)

    payload = frame_cache_payload()

    assert payload["frame_cache_files"] == 1
    assert payload["frame_cache_bytes"] == 2048
    assert payload["frame_cache_size_formatted"]
    assert payload["frame_cache_max_bytes"] > 0
    assert payload["frame_cache_max_size_formatted"]


def test_face_worker_status_exposes_the_frame_cache(sandbox_env):
    from media_library.workers import face_worker_instance

    status = face_worker_instance.get_status()

    assert "frame_cache_bytes" in status
    assert "frame_cache_max_bytes" in status
    assert "frame_cache_size_formatted" in status
