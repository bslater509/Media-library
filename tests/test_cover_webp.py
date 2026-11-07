"""Line-coverage tests for the WebP pipeline, prober and WebP router.

Fills the branches the neighbouring suites leave open: folder-scoped
sweeps, target-name collisions, alpha conversion, skip-larger and
too-large skips, failed cleanup and poster-move paths, probe parsing
corners, probe worker loop states and the WebP HTTP endpoints.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import types
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageOps

from media_library import cache, pillow_init, probe, webp
from media_library import config as v2_config
from media_library.app import create_app


@pytest.fixture
def webp_env(tmp_path, monkeypatch):
    """Two library folders, WebP tuning and thumbnails on a scratch dir."""
    library = tmp_path / "library"
    pics = library / "Pics"
    other = library / "Other"
    pics.mkdir(parents=True)
    other.mkdir(parents=True)
    thumbs = tmp_path / "thumbs"
    (thumbs / "animated").mkdir(parents=True)
    monkeypatch.setenv("LIBRARY_DIR", str(library))
    monkeypatch.setenv("THUMBNAIL_DIR", str(thumbs))
    monkeypatch.setenv("WRITES", "true")
    monkeypatch.setenv("DB_READ_ONLY", "false")
    monkeypatch.setenv("WEBP_QUALITY", "80")
    monkeypatch.setenv("WEBP_SKIP_LARGER", "false")
    v2_config.reload_settings()

    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        for folder in (pics, other):
            conn.execute(
                "INSERT OR IGNORE INTO folders (path, name, parent_path, "
                "last_scanned, is_zip) VALUES (?,?,?,?,0)",
                (str(folder), folder.name, str(library), 1.0),
            )
    return {
        "library": library,
        "pics": pics,
        "other": other,
        "thumbs": thumbs,
        "db_path": db_path,
    }


def _add_image(env, folder, name, *, mode="RGB", color=(5, 200, 90)):
    """Create a small image file plus its pending index row."""
    path = Path(folder) / name
    Image.new(mode, (8, 8), color).save(path, "PNG")
    stat = path.stat()
    _insert_file_row(env["db_path"], path, "image", stat.st_size, stat.st_mtime)
    return path


def _insert_file_row(db_path, path, kind, size, mtime, optimized=0):
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO folders (path, name, parent_path, "
            "last_scanned, is_zip) VALUES (?,?,?,?,0)",
            (str(path.parent), path.parent.name, str(path.parent.parent), 1.0),
        )
        conn.execute(
            "INSERT INTO files (path, name, folder_path, type, ext, size, "
            "size_formatted, mtime, webp_optimized) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                str(path),
                path.name,
                str(path.parent),
                kind,
                path.suffix,
                size,
                "1 B",
                mtime,
                optimized,
            ),
        )


def _db_value(db_path, sql, params=()):
    with sqlite3.connect(db_path) as conn:
        return conn.execute(sql, params).fetchone()[0]


# --- media_library.webp -----------------------------------------------------


def test_optimize_batch_scopes_to_one_folder(webp_env):
    alpha = _add_image(webp_env, webp_env["pics"], "alpha.png")
    beta = _add_image(webp_env, webp_env["other"], "beta.png")

    assert webp.pending_count() == 2
    result = webp.optimize_batch(limit=5, folder=str(webp_env["pics"]))

    assert result["converted"] == 1
    assert alpha.with_suffix(".webp").is_file()
    assert not alpha.exists()
    assert beta.is_file()
    assert result["remaining"] == 0
    assert webp.pending_count() == 1
    assert (
        _db_value(
            webp_env["db_path"],
            "SELECT webp_optimized FROM files WHERE path = ?",
            (str(beta),),
        )
        == 0
    )
    assert webp.pending_count(str(webp_env["pics"])) == 0


def test_unique_target_skips_taken_names(webp_env):
    target = webp_env["pics"] / "alpha.webp"
    target.write_bytes(b"taken")
    (webp_env["pics"] / "alpha (1).webp").write_bytes(b"taken too")

    chosen = webp._unique_target(target)

    assert chosen.name == "alpha (2).webp"


def test_conversion_keeps_alpha_channels(webp_env):
    alpha = _add_image(
        webp_env,
        webp_env["pics"],
        "alpha.png",
        mode="RGBA",
        color=(255, 0, 0, 128),
    )

    result = webp.optimize_batch(limit=5)

    assert result["converted"] == 1
    assert alpha.with_suffix(".webp").is_file()
    assert webp.status()["converted"] == 1


def test_optimize_marks_a_row_when_conversion_says_already(webp_env, monkeypatch):
    native = webp_env["pics"] / "native.webp"
    Image.new("RGB", (4, 4)).save(native, "WEBP")
    # ext stays ".png" so the pre-pass does not claim the row first.
    _insert_file_row(
        webp_env["db_path"], native.with_name("native.png"), "image", 99, 1.0
    )
    with sqlite3.connect(webp_env["db_path"]) as conn:
        conn.execute(
            "UPDATE files SET path = ? WHERE path = ?",
            (str(native), str(native.with_name("native.png"))),
        )

    monkeypatch.setattr(
        webp, "_pending_rows", lambda *args, **kwargs: [{"path": str(native)}]
    )
    result = webp.optimize_batch(limit=5)

    assert result["converted"] == 0
    assert result["errors"] == []
    assert (
        _db_value(
            webp_env["db_path"],
            "SELECT webp_optimized FROM files WHERE path = ?",
            (str(native),),
        )
        == 1
    )


def _shrinking_path():
    """A Path class whose ``.png`` stat reports one-byte files."""

    class ShrinkingPath(type(Path())):
        def stat(self, **kwargs):
            result = super().stat(**kwargs)
            if self.suffix == ".png":
                return os.stat_result((0o100644, 0, 0, 1, 0, 0, 1, 100.0, 100.0, 100.0))
            return result

    return ShrinkingPath


def test_optimize_batch_converts_an_explicit_selection(webp_env):
    alpha = _add_image(webp_env, webp_env["pics"], "alpha.png")
    beta = _add_image(webp_env, webp_env["other"], "beta.png")

    result = webp.optimize_batch(paths=[str(alpha), str(beta)])

    assert result["converted"] == 2
    assert alpha.with_suffix(".webp").is_file()
    assert beta.with_suffix(".webp").is_file()
    assert result["remaining"] == 0


def test_optimize_reports_a_missing_file_without_crashing(webp_env):
    ghost = webp_env["pics"] / "ghost.png"
    _insert_file_row(webp_env["db_path"], ghost, "image", 10, 1.0)

    result = webp.optimize_batch(limit=5)

    assert result["failed"] == 1
    assert result["errors"] == [{"path": str(ghost), "error": "missing"}]
    assert webp.pending_count(str(webp_env["pics"])) == 0


def test_optimize_skips_when_the_webp_is_not_smaller(webp_env, monkeypatch):
    photo = _add_image(webp_env, webp_env["pics"], "skipper.png")
    monkeypatch.setenv("WEBP_SKIP_LARGER", "true")
    v2_config.reload_settings()
    monkeypatch.setattr(webp, "Path", _shrinking_path())

    result = webp.optimize_batch(limit=5)

    assert result["converted"] == 0
    assert result["skipped"] == 1
    assert photo.is_file()
    assert not list(webp_env["pics"].glob(".tmp_webp_*"))
    assert (
        _db_value(
            webp_env["db_path"],
            "SELECT webp_optimized FROM files WHERE path = ?",
            (str(photo),),
        )
        == -1
    )
    assert _db_value(webp_env["db_path"], "SELECT skipped_count FROM webp_stats") == 1


def test_optimize_seeds_stats_when_the_row_is_missing(webp_env, monkeypatch):
    photo = _add_image(webp_env, webp_env["pics"], "starter.png")
    with sqlite3.connect(webp_env["db_path"]) as conn:
        conn.execute("DELETE FROM webp_stats")
    monkeypatch.setattr(webp, "_MAX_CONVERT_PIXELS", 0)

    result = webp.optimize_batch(limit=5)
    status = webp.status()

    assert result["skipped"] == 1
    assert status["skipped"] == 1
    assert status["converted"] == 0
    assert photo.is_file()
    with sqlite3.connect(webp_env["db_path"]) as conn:
        row = conn.execute(
            "SELECT saved_bytes, converted_count, skipped_count "
            "FROM webp_stats WHERE id = 1"
        ).fetchone()
    assert row == (0, 0, 1)


def test_optimize_survives_a_failing_temp_cleanup(webp_env, monkeypatch):
    broken = webp_env["pics"] / "broken.png"
    broken.write_bytes(b"definitely not an image")
    _insert_file_row(
        webp_env["db_path"],
        broken,
        "image",
        broken.stat().st_size,
        broken.stat().st_mtime,
    )
    real_unlink = os.unlink

    def noisy_unlink(path, *args, **kwargs):
        if ".tmp_webp_" in os.path.basename(str(path)):
            raise OSError("cannot remove")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(os, "unlink", noisy_unlink)

    result = webp.optimize_batch(limit=5)

    assert result["failed"] == 1
    assert result["converted"] == 0
    assert "cannot identify image file" in result["errors"][0]["error"]
    assert (
        _db_value(
            webp_env["db_path"],
            "SELECT webp_optimized FROM files WHERE path = ?",
            (str(broken),),
        )
        == -1
    )


def test_optimize_survives_a_failing_source_removal(webp_env, monkeypatch):
    photo = _add_image(webp_env, webp_env["pics"], "stubborn.png")
    real_unlink = os.unlink

    def noisy_unlink(path, *args, **kwargs):
        if ".tmp_webp_" in os.path.basename(str(path)):
            return real_unlink(path, *args, **kwargs)
        raise OSError("cannot remove")

    monkeypatch.setattr(os, "unlink", noisy_unlink)

    result = webp.optimize_batch(limit=5)

    assert result["converted"] == 1
    assert photo.is_file()
    assert photo.with_suffix(".webp").is_file()
    assert (
        _db_value(
            webp_env["db_path"],
            "SELECT webp_optimized FROM files WHERE path = ?",
            (str(photo.with_suffix(".webp")),),
        )
        == 1
    )


def test_optimize_tolerates_failing_poster_and_preview_moves(webp_env, monkeypatch):
    photo = _add_image(webp_env, webp_env["pics"], "postered.png")
    stat = photo.stat()
    thumb = cache.thumbnail_path(str(photo), stat.st_mtime, stat.st_size)
    Image.new("RGB", (8, 8)).save(thumb, "JPEG")
    anim = cache.animated_path(str(photo), stat.st_mtime, stat.st_size)
    anim.write_bytes(b"RIFFfake-preview")
    real_replace = os.replace

    def failing_replace(source, target):
        if str(target).startswith(str(webp_env["thumbs"])):
            raise OSError("cannot move poster")
        return real_replace(source, target)

    monkeypatch.setattr(os, "replace", failing_replace)

    result = webp.optimize_batch(limit=5)

    assert result["converted"] == 1
    assert thumb.is_file()
    assert anim.is_file()


# --- media_library.probe ----------------------------------------------------


def _mock_run(monkeypatch, payload_or_result):
    """Replace ``ffprobe`` with a canned return value."""

    def fake_run(*args, **kwargs):
        if isinstance(payload_or_result, Exception):
            raise payload_or_result
        stdout = json.dumps(payload_or_result).encode("utf-8")
        return types.SimpleNamespace(returncode=0, stdout=stdout)

    monkeypatch.setattr(probe.subprocess, "run", fake_run)


def test_probe_video_failure_modes(monkeypatch):
    calls = {"count": 0}

    def fake_run(*args, **kwargs):
        step = calls["count"]
        calls["count"] += 1
        if step == 0:
            raise OSError("ffprobe launch failed")
        if step == 1:
            return types.SimpleNamespace(returncode=1, stdout=b"")
        if step == 2:
            raise subprocess.TimeoutExpired(cmd=["ffprobe"], timeout=25)
        if step == 3:
            return types.SimpleNamespace(returncode=0, stdout=b"not JSON")
        return types.SimpleNamespace(returncode=0, stdout=b'{"streams": []}')

    monkeypatch.setattr(probe.subprocess, "run", fake_run)

    assert probe._probe_video("clip.mp4") is probe.TRANSIENT  # OSError
    assert probe._probe_video("clip.mp4") is None  # non-zero exit
    assert probe._probe_video("clip.mp4") is probe.TRANSIENT  # timeout
    assert probe._probe_video("clip.mp4") is None  # unparseable payload
    assert probe._probe_video("clip.mp4") is None  # no video stream


def test_probe_video_rotation_swap_and_capture(monkeypatch):
    _mock_run(
        monkeypatch,
        {
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "h264",
                    "width": 1920,
                    "height": 1080,
                    "avg_frame_rate": "30000/1001",
                    "side_data_list": [
                        {"rotation": "junk"},
                        {"rotation": 90},
                    ],
                },
                {"codec_type": "audio", "codec_name": "aac"},
            ],
            "format": {
                "duration": "12.5",
                "tags": {"creation_time": "2021-03-04T05:06:07Z"},
            },
        },
    )

    result = probe._probe_video("rotated.mp4")

    assert result["width"] == 1080
    assert result["height"] == 1920
    assert result["duration"] == 12.5
    assert result["video_codec"] == "h264"
    assert result["audio_codec"] == "aac"
    assert result["fps"] == pytest.approx(29.97002997002997)
    assert result["capture_time"] == pytest.approx(
        datetime(2021, 3, 4, 5, 6, 7, tzinfo=timezone.utc).timestamp()
    )


def test_probe_video_tolerates_a_bad_duration(monkeypatch):
    _mock_run(
        monkeypatch,
        {
            "streams": [{"codec_type": "video", "width": 320, "height": 240}],
            "format": {"duration": "junk"},
        },
    )

    result = probe._probe_video("clip.mp4")

    assert result["duration"] is None
    assert result["width"] == 320
    assert result["height"] == 240
    assert result["fps"] is None
    assert result["capture_time"] is None


def test_fraction_and_iso_epoch_parsing():
    assert probe._fraction(None) is None
    assert probe._fraction("0/0") is None
    assert probe._fraction("N/A") is None
    assert probe._fraction("1/0") is None
    assert probe._fraction("24.5") == 24.5
    assert probe._fraction("30000/1001") == pytest.approx(29.97002997002997)
    assert probe._fraction("junk") is None

    assert probe._iso_epoch(None) is None
    assert probe._iso_epoch("") is None
    assert probe._iso_epoch("2021-03-04T05:06:07Z") == pytest.approx(
        datetime(2021, 3, 4, 5, 6, 7, tzinfo=timezone.utc).timestamp()
    )
    assert probe._iso_epoch("junk-date") is None


def test_probe_image_reads_capture_time(tmp_path):
    good = tmp_path / "good.jpg"
    image = Image.new("RGB", (4, 4), (10, 200, 30))
    exif = image.getexif()
    exif[36867] = "2020:01:02 03:04:05"
    image.save(good, "JPEG", exif=exif)

    bad = tmp_path / "bad.jpg"
    other = Image.new("RGB", (4, 4))
    broken = other.getexif()
    broken[36867] = "not-a-date"
    other.save(bad, "JPEG", exif=broken)

    good_result = probe._probe_image(str(good))
    bad_result = probe._probe_image(str(bad))

    assert (good_result["width"], good_result["height"]) == (4, 4)
    assert good_result["capture_time"] == pytest.approx(
        datetime(2020, 1, 2, 3, 4, 5, tzinfo=timezone.utc).timestamp()
    )
    assert bad_result["capture_time"] is None
    assert bad_result["duration"] is None


def test_probe_image_transient_and_generic_failures(tmp_path, monkeypatch):
    ghost = tmp_path / "ghost.jpg"  # never created: OSError make trans
    real = tmp_path / "real.png"
    Image.new("RGB", (4, 4)).save(real, "PNG")

    def explode(image):
        raise RuntimeError("transpose blew up")

    monkeypatch.setattr(ImageOps, "exif_transpose", explode)

    assert probe._probe_image(str(ghost)) is probe.TRANSIENT
    assert probe._probe_image(str(real)) is None


def test_probe_media_routes_by_extension(tmp_path):
    assert probe.probe_media(str(tmp_path / "notes.txt")) is None


def test_probe_batch_resolves_success_failure_and_zip_members(tmp_path, monkeypatch):
    db_path = os.environ["DB_PATH"]
    video = tmp_path / "video_ok.mp4"
    video.write_bytes(b"\x00\x00\x00\x18ftypmp42")
    image = tmp_path / "image_ok.png"
    Image.new("RGB", (4, 4)).save(image, "PNG")
    broken = tmp_path / "broken.png"
    broken.write_bytes(b"\x00\x01\x02\x03")
    ghost_video = tmp_path / "transient.mp4"
    ghost_video.write_bytes(b"\x00\x00\x00\x18ftypmp42")
    zip_member = tmp_path / "arch.zip/inner.mp4"
    for path in (video, image, broken, ghost_video):
        _insert_file_row(
            db_path,
            path,
            "video" if path.suffix == ".mp4" else "image",
            32,
            1.0,
        )
    _insert_file_row(db_path, zip_member, "video", 32, 1.0)
    # Drop the plain folders row so the ".zip/" string contract applies.
    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM folders WHERE path = ?", (str(zip_member.parent),))

    def fake_run(args, **kwargs):
        if str(args[-1]) == str(ghost_video):
            raise OSError("mount hiccup")
        payload = {
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "h264",
                    "width": 640,
                    "height": 480,
                }
            ],
            "format": {"duration": "3"},
        }
        return types.SimpleNamespace(
            returncode=0, stdout=json.dumps(payload).encode("utf-8")
        )

    monkeypatch.setattr(probe.subprocess, "run", fake_run)

    result = probe.probe_batch(limit=10)

    assert result == {
        "probed": 2,
        "failed": 2,
        "transient": 1,
        "remaining": 1,
    }
    assert (
        _db_value(db_path, "SELECT width FROM files WHERE path = ?", (str(video),))
        == 640
    )
    assert (
        _db_value(db_path, "SELECT width FROM files WHERE path = ?", (str(image),)) == 4
    )
    assert (
        _db_value(db_path, "SELECT width FROM files WHERE path = ?", (str(broken),))
        == 0
    )
    assert (
        _db_value(db_path, "SELECT width FROM files WHERE path = ?", (str(zip_member),))
        == 0
    )
    assert (
        _db_value(
            db_path, "SELECT width FROM files WHERE path = ?", (str(ghost_video),)
        )
        is None
    )


def test_metadata_worker_start_refuses_a_second_thread():
    worker = probe.MetadataWorker(batch=1)
    worker.start()
    try:
        worker.start()  # second call must be a no-op while it runs
        assert worker._thread is not None
        assert worker._thread.is_alive()
    finally:
        worker.stop()
    assert not worker._thread.is_alive()


def test_metadata_worker_loop_survives_a_probe_crash(monkeypatch):
    worker = probe.MetadataWorker(batch=1)

    def exploding(limit):
        worker._stop.set()
        raise RuntimeError("probe blew up")

    monkeypatch.setattr(probe, "probe_batch", exploding)

    worker._loop()

    assert worker._stop.is_set()


def test_metadata_worker_loop_pauses_until_work_is_done(monkeypatch):
    worker = probe.MetadataWorker(batch=1)

    def busy(limit):
        worker._stop.set()
        return {"remaining": 4}

    monkeypatch.setattr(probe, "probe_batch", busy)

    worker._loop()

    assert worker._stop.is_set()


# --- media_library.pillow_init ----------------------------------------------


def test_register_heif_tolerates_a_missing_dependency(monkeypatch):
    monkeypatch.setattr(pillow_init, "_registered", False)
    monkeypatch.setitem(sys.modules, "pillow_heif", None)

    pillow_init.register_heif()

    # Even after the failed registration the helper is done trying.
    assert pillow_init._registered is True


# --- media_library.routers.webp ----------------------------------------------


def test_webp_status_endpoint_exposes_the_queue_state(webp_env):
    client = TestClient(create_app())

    response = client.get("/api/webp/status")

    assert response.status_code == 200
    body = response.json()
    assert body["quality"] == 80
    assert body["skip_larger"] is False
    assert body["pending"] == 0
    assert body["converted"] == 0
    assert body["skipped"] == 0


def test_webp_optimize_endpoint_wraps_failures(webp_env, monkeypatch):
    def explode(limit, folder, paths):
        raise RuntimeError("boom")

    monkeypatch.setattr(webp, "optimize_batch", explode)
    client = TestClient(create_app())

    response = client.post("/api/webp/optimize", json={"limit": 2})

    assert response.status_code == 500
    assert response.json()["detail"] == "Internal error"
