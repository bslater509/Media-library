"""Poster chooser frames and the animated preview queue."""

from __future__ import annotations

import os
import sqlite3
import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from media_library_v2 import animated
from media_library_v2 import config as v2_config
from media_library_v2.mount import build_v2_app


def _make_video(target: Path, seconds: int = 4) -> None:
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
            str(seconds),
            "-pix_fmt",
            "yuv420p",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-y",
            str(target),
        ],
        check=True,
    )


@pytest.fixture
def media_env(tmp_path, monkeypatch):
    library = tmp_path / "library"
    thumbs = tmp_path / "thumbs"
    (thumbs / "animated").mkdir(parents=True)
    library.mkdir()
    monkeypatch.setenv("LIBRARY_DIR", str(library))
    monkeypatch.setenv("V2_LIBRARY_DIR", str(library))
    monkeypatch.setenv("THUMBNAIL_DIR", str(thumbs))
    monkeypatch.setenv("V2_DB_READ_ONLY", "false")
    monkeypatch.setenv("V2_WRITES", "true")
    v2_config.reload_settings()

    video = library / "clip.mp4"
    _make_video(video)
    image = library / "photo.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\nfake")

    db_path = Path(os.environ["DB_PATH"])
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO folders (path, name, parent_path, file_count, "
            "subfolder_count, total_size, is_zip) VALUES (?,?,?,?,?,?,?)",
            (str(library), "library", str(library.parent), 2, 0, 0, 0),
        )
        stat = video.stat()
        conn.execute(
            "INSERT INTO files (path, name, folder_path, type, ext, size, "
            "size_formatted, mtime, needs_transcode, has_thumb, duration) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                str(video),
                video.name,
                str(library),
                "video",
                ".mp4",
                stat.st_size,
                "1 KB",
                stat.st_mtime,
                0,
                0,
                4.0,
            ),
        )
        conn.execute(
            "INSERT INTO files (path, name, folder_path, type, ext, size, "
            "size_formatted, mtime, needs_transcode, has_thumb) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                str(image),
                image.name,
                str(library),
                "image",
                ".png",
                8,
                "8 B",
                image.stat().st_mtime,
                0,
                0,
            ),
        )

    try:
        yield {
            "client": TestClient(build_v2_app()),
            "library": library,
            "video": video,
            "image": image,
            "thumbs": thumbs,
            "db_path": db_path,
        }
    finally:
        animated.cancel()
        v2_config.reload_settings()


def _wait_ready(client: TestClient, video: Path, timeout: float = 60.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get("/api/v2/thumbnails/animated/status").json()
        if body["ready"] >= 1 or body["failed_total"] >= 1:
            return body
        time.sleep(0.2)
    raise AssertionError("animated preview never finished")


def test_candidates_offer_enough_frames(media_env):
    response = media_env["client"].get(
        "/api/v2/thumbnails/candidates",
        params={"path": str(media_env["video"])},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["duration"] == pytest.approx(4.0, abs=0.5)
    assert len(body["frames"]) >= 8
    assert body["frames"][0]["timestamp"] == 0
    assert body["frames"][-1]["timestamp"] < body["duration"]


def test_candidate_frames_reject_images_and_missing_files(media_env):
    client = media_env["client"]
    image = client.get(
        "/api/v2/thumbnails/candidates",
        params={"path": str(media_env["image"])},
    )
    assert image.status_code == 400
    missing = client.get(
        "/api/v2/thumbnails/candidates",
        params={"path": str(media_env["library"] / "nope.mp4")},
    )
    assert missing.status_code == 404


def test_frame_endpoint_returns_a_cached_jpeg(media_env):
    client = media_env["client"]
    response = client.get(
        "/api/v2/thumbnails/frame",
        params={"path": str(media_env["video"]), "timestamp": 2.0},
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.content[:2] == b"\xff\xd8"


def test_set_poster_updates_the_row_and_cache(media_env):
    client = media_env["client"]
    response = client.post(
        "/api/v2/thumbnails/poster",
        json={"path": str(media_env["video"]), "timestamp": 2.5},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["thumb_mtime"] > 0

    with sqlite3.connect(media_env["db_path"]) as conn:
        row = conn.execute(
            "SELECT has_thumb, thumb_mtime FROM files WHERE path = ?",
            (str(media_env["video"]),),
        ).fetchone()
    assert row[0] == 1
    assert row[1] == pytest.approx(body["thumb_mtime"])
    poster = media_env["thumbs"] / f"thumb_{_key(media_env['video'])}.jpg"
    assert poster.is_file()


def _key(path: Path) -> str:
    from media_library_v2.cache import cache_key

    stat = path.stat()
    return cache_key(str(path), stat.st_mtime, stat.st_size)


def test_animated_queue_generates_and_marks_the_row(media_env):
    client = media_env["client"]
    response = client.post(
        "/api/v2/thumbnails/animated", json={"path": str(media_env["video"])}
    )
    assert response.status_code == 200
    status = _wait_ready(client, media_env["video"])
    assert status["ready"] == 1
    assert status["pending"] == 0

    with sqlite3.connect(media_env["db_path"]) as conn:
        value = conn.execute(
            "SELECT has_animated FROM files WHERE path = ?",
            (str(media_env["video"]),),
        ).fetchone()[0]
    assert value == 1
    preview = media_env["thumbs"] / "animated" / f"anim_{_key(media_env['video'])}.webp"
    assert preview.is_file() and preview.stat().st_size > 0


def test_batch_requires_a_scope(media_env):
    client = media_env["client"]
    assert client.post("/api/v2/thumbnails/animated/batch", json={}).status_code == 400


def test_batch_queues_missing_videos(media_env):
    client = media_env["client"]
    response = client.post(
        "/api/v2/thumbnails/animated/batch",
        json={"all_videos": True},
    )
    assert response.status_code == 200
    _wait_ready(client, media_env["video"])
    status = client.get("/api/v2/thumbnails/animated/status").json()
    assert status["ready"] == 1


def test_cancel_empties_the_queue(media_env):
    client = media_env["client"]
    response = client.post("/api/v2/thumbnails/animated/cancel")
    assert response.status_code == 200
    assert response.json()["queued"] == 0


def test_artwork_writes_are_refused_read_only(media_env, monkeypatch):
    monkeypatch.setenv("V2_DB_READ_ONLY", "true")
    v2_config.reload_settings()
    client = TestClient(build_v2_app())
    assert (
        client.post(
            "/api/v2/thumbnails/poster",
            json={"path": str(media_env["video"]), "timestamp": 1.0},
        ).status_code
        == 503
    )
    assert (
        client.post(
            "/api/v2/thumbnails/animated", json={"path": str(media_env["video"])}
        ).status_code
        == 503
    )
