"""Metadata probing and poster sweep tests for v2's worker runtime."""

from __future__ import annotations

import os
import sqlite3
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from media_library_v2 import cache
from media_library_v2 import config as v2_config
from media_library_v2.probe import probe_batch
from media_library_v2.thumbnail_worker import ThumbnailSweep


@pytest.fixture
def workers_env(tmp_path, monkeypatch):
    library = tmp_path / "library"
    library.mkdir()
    thumbs = tmp_path / "thumbs"
    (thumbs / "animated").mkdir(parents=True)
    monkeypatch.setenv("LIBRARY_DIR", str(library))
    monkeypatch.setenv("V2_LIBRARY_DIR", str(library))
    monkeypatch.setenv("THUMBNAIL_DIR", str(thumbs))
    monkeypatch.setenv("V2_DB_READ_ONLY", "false")
    monkeypatch.setenv("V2_WRITES", "true")
    v2_config.reload_settings()

    video = library / "clip.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=160x120:rate=20",
            "-t",
            "1",
            "-pix_fmt",
            "yuv420p",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-y",
            str(video),
        ],
        check=True,
    )
    image = library / "photo.png"
    Image.new("RGB", (64, 48), (120, 80, 200)).save(image, "PNG")
    bad = library / "bad.jpg"
    bad.write_bytes(b"not an image")

    db_path = Path(os.environ["DB_PATH"])
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO folders (path, name, parent_path, file_count, "
            "subfolder_count, last_scanned, is_zip, total_size) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (str(library), "library", None, 3, 0, 1.0, 0, 0),
        )
        for path, kind, ext in (
            (video, "video", ".mp4"),
            (image, "image", ".png"),
            (bad, "image", ".jpg"),
        ):
            stat = path.stat()
            conn.execute(
                "INSERT INTO files (path, name, folder_path, type, ext, size, "
                "size_formatted, mtime, needs_transcode, has_thumb) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    str(path),
                    path.name,
                    str(path.parent),
                    kind,
                    ext,
                    stat.st_size,
                    f"{stat.st_size} B",
                    stat.st_mtime,
                    0,
                    0,
                ),
            )

    try:
        yield {
            "library": library,
            "db_path": db_path,
            "video": video,
            "image": image,
            "bad": bad,
            "thumbs": thumbs,
        }
    finally:
        v2_config.reload_settings()


def test_probe_batch_fills_dimensions_and_marks_failures(workers_env):
    with sqlite3.connect(workers_env["db_path"]) as conn:
        conn.execute(
            "INSERT INTO files (path, name, folder_path, type, ext, size, "
            "size_formatted, mtime, needs_transcode, has_thumb) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                f"{workers_env['library']}/archive.zip/member.jpg",
                "member.jpg",
                f"{workers_env['library']}/archive.zip",
                "image",
                ".jpg",
                10,
                "10 B",
                1.0,
                0,
                0,
            ),
        )

    result = probe_batch(limit=10)
    assert result["probed"] == 2
    assert result["failed"] == 2
    assert result["remaining"] == 0

    with sqlite3.connect(workers_env["db_path"]) as conn:
        video = conn.execute(
            "SELECT width, height, duration, video_codec, fps FROM files "
            "WHERE path = ?",
            (str(workers_env["video"]),),
        ).fetchone()
        image = conn.execute(
            "SELECT width, height FROM files WHERE path = ?",
            (str(workers_env["image"]),),
        ).fetchone()
        bad = conn.execute(
            "SELECT width, height FROM files WHERE path = ?",
            (str(workers_env["bad"]),),
        ).fetchone()
        zip_member = conn.execute(
            "SELECT width, height FROM files WHERE path LIKE ?", ("%archive.zip/%",)
        ).fetchone()
    assert video[0] == 160 and video[1] == 120
    assert video[2] == pytest.approx(1.0, abs=0.2)
    assert video[3] == "h264"
    assert video[4] == pytest.approx(20, abs=1)
    assert image == (64, 48)
    assert bad == (0, 0)
    assert zip_member == (0, 0)


def test_thumbnail_sweep_generates_posters_and_marks_failures(workers_env):
    sweep = ThumbnailSweep(batch=10)
    result = sweep.run_once()
    assert result["generated"] == 2
    assert result["failed"] == 1
    assert result["remaining"] == 0

    with sqlite3.connect(workers_env["db_path"]) as conn:
        rows = {
            row[0]: row[1]
            for row in conn.execute("SELECT path, has_thumb FROM files").fetchall()
        }
    assert rows[str(workers_env["video"])] == 1
    assert rows[str(workers_env["image"])] == 1
    assert rows[str(workers_env["bad"])] == -1

    video_stat = workers_env["video"].stat()
    image_stat = workers_env["image"].stat()
    assert cache.thumbnail_path(
        str(workers_env["video"]), video_stat.st_mtime, video_stat.st_size
    ).is_file()
    assert cache.thumbnail_path(
        str(workers_env["image"]), image_stat.st_mtime, image_stat.st_size
    ).is_file()


def test_runtime_stays_off_while_v1_owns_the_workers(workers_env):
    from media_library_v2 import runtime

    assert runtime.start_runtime() is False
    status = runtime.runtime_status()
    assert status["owned"] is False
    assert status["started"] is False
    runtime.stop_runtime()


def test_owned_runtime_starts_every_sweep(workers_env, monkeypatch):
    monkeypatch.setenv("V2_OWN_WORKERS", "true")
    v2_config.reload_settings()
    from media_library_v2 import runtime

    try:
        assert runtime.start_runtime() is True
        status = runtime.runtime_status()
        assert status["started"] is True
        for key in (
            "crawler",
            "metadata",
            "thumbnails",
            "webp",
            "content_hashes",
            "visual_hashes",
            "backups",
        ):
            assert status[key] is True, key
    finally:
        runtime.stop_runtime()
        v2_config.reload_settings()


def test_orphaned_artwork_is_swept(workers_env):
    from media_library_v2.cache import (
        animated_path,
        cache_key,
        sweep_orphaned_artwork,
        thumbnail_path,
    )

    video = workers_env["video"]
    stat = video.stat()
    live_thumb = thumbnail_path(str(video), stat.st_mtime, stat.st_size)
    live_thumb.write_bytes(b"poster")
    live_anim = animated_path(str(video), stat.st_mtime, stat.st_size)
    live_anim.write_bytes(b"anim")

    orphans = ["a" * 24, "b" * 24]
    thumbs = workers_env["thumbs"]
    (thumbs / f"thumb_{orphans[0]}.jpg").write_bytes(b"orphan")
    (thumbs / "animated" / f"anim_{orphans[1]}.webp").write_bytes(b"orphan")
    (thumbs / "frames" / orphans[0]).mkdir(parents=True)
    (thumbs / "frames" / orphans[0] / "frame_000000001000.jpg").write_bytes(b"x")
    live_key = cache_key(str(video), stat.st_mtime, stat.st_size)
    (thumbs / "frames" / live_key).mkdir(parents=True)
    (thumbs / "frames" / live_key / "frame_000000001000.jpg").write_bytes(b"x")

    result = sweep_orphaned_artwork()

    assert result == {"thumbnails": 1, "animated": 1, "frames": 1, "skipped": 0}
    assert live_thumb.is_file()
    assert live_anim.is_file()
    assert (thumbs / "frames" / live_key).is_dir()
    assert not (thumbs / "frames" / orphans[0]).exists()


def test_orphan_sweep_skips_an_empty_index(workers_env):
    from media_library_v2.cache import sweep_orphaned_artwork

    orphan = workers_env["thumbs"] / f"thumb_{'c' * 24}.jpg"
    orphan.write_bytes(b"orphan")
    with sqlite3.connect(workers_env["db_path"]) as conn:
        conn.execute("DELETE FROM files")

    result = sweep_orphaned_artwork()

    assert result["skipped"] == 1
    assert orphan.is_file()
