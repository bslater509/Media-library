"""Poster chooser frames and the animated preview queue."""

from __future__ import annotations

import os
import sqlite3
import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from media_library import animated
from media_library import config as v2_config
from media_library.app import create_app


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
    monkeypatch.setenv("LIBRARY_DIR", str(library))
    monkeypatch.setenv("THUMBNAIL_DIR", str(thumbs))
    monkeypatch.setenv("DB_READ_ONLY", "false")
    monkeypatch.setenv("WRITES", "true")
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
            "client": TestClient(create_app()),
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
        body = client.get("/api/thumbnails/animated/status").json()
        if body["ready"] >= 1 or body["failed_total"] >= 1:
            return body
        time.sleep(0.2)
    raise AssertionError("animated preview never finished")


def test_candidates_offer_enough_frames(media_env):
    response = media_env["client"].get(
        "/api/thumbnails/candidates",
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
        "/api/thumbnails/candidates",
        params={"path": str(media_env["image"])},
    )
    assert image.status_code == 400
    missing = client.get(
        "/api/thumbnails/candidates",
        params={"path": str(media_env["library"] / "nope.mp4")},
    )
    assert missing.status_code == 404


def test_frame_endpoint_returns_a_cached_jpeg(media_env):
    client = media_env["client"]
    response = client.get(
        "/api/thumbnails/frame",
        params={"path": str(media_env["video"]), "timestamp": 2.0},
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.content[:2] == b"\xff\xd8"


def test_set_poster_updates_the_row_and_cache(media_env):
    client = media_env["client"]
    response = client.post(
        "/api/thumbnails/poster",
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
    from media_library.cache import cache_key

    stat = path.stat()
    return cache_key(str(path), stat.st_mtime, stat.st_size)


def test_animated_queue_generates_and_marks_the_row(media_env):
    client = media_env["client"]
    response = client.post(
        "/api/thumbnails/animated", json={"path": str(media_env["video"])}
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
    assert client.post("/api/thumbnails/animated/batch", json={}).status_code == 400


def test_batch_queues_missing_videos(media_env):
    client = media_env["client"]
    response = client.post(
        "/api/thumbnails/animated/batch",
        json={"all_videos": True},
    )
    assert response.status_code == 200
    _wait_ready(client, media_env["video"])
    status = client.get("/api/thumbnails/animated/status").json()
    assert status["ready"] == 1


def test_cancel_empties_the_queue(media_env):
    client = media_env["client"]
    response = client.post("/api/thumbnails/animated/cancel")
    assert response.status_code == 200
    assert response.json()["queued"] == 0


def test_artwork_writes_are_refused_read_only(media_env, monkeypatch):
    monkeypatch.setenv("DB_READ_ONLY", "true")
    v2_config.reload_settings()
    client = TestClient(create_app())
    assert (
        client.post(
            "/api/thumbnails/poster",
            json={"path": str(media_env["video"]), "timestamp": 1.0},
        ).status_code
        == 503
    )
    assert (
        client.post(
            "/api/thumbnails/animated", json={"path": str(media_env["video"])}
        ).status_code
        == 503
    )


def test_worker_thread_is_cleared_when_the_queue_drains(monkeypatch):
    """A dead thread object must not hide a queue item from ``_ensure_thread``."""
    calls: list[str] = []
    monkeypatch.setattr(animated, "generate", lambda path: calls.append(path))
    animated.cancel()
    animated.enqueue(["drain-check.mp4"])

    deadline = time.time() + 5
    while animated._thread is not None and time.time() < deadline:
        time.sleep(0.01)

    assert calls == ["drain-check.mp4"]
    assert animated._thread is None


def test_poster_chooser_works_for_a_zip_member(media_env, tmp_path, monkeypatch):
    """A video inside an archive is extracted before ffmpeg runs on it."""
    import zipfile

    from media_library import config as v2_config
    from media_library.cache import thumbnail_path

    monkeypatch.setenv("ZIP_CACHE_DIR", str(tmp_path / "zip_cache"))
    v2_config.reload_settings()

    library = media_env["library"]
    archive = library / "clips.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.write(media_env["video"], "inside.mp4")
    member = f"{archive}/inside.mp4"
    with sqlite3.connect(media_env["db_path"]) as conn:
        conn.execute(
            "INSERT INTO folders (path, name, parent_path, file_count, "
            "subfolder_count, last_scanned, is_zip, total_size) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (str(archive), "clips.zip", str(library), 1, 0, time.time(), 1, 0),
        )
        conn.execute(
            "INSERT INTO files (path, name, folder_path, type, ext, size, "
            "size_formatted, mtime, needs_transcode, has_thumb, duration) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                member,
                "inside.mp4",
                str(archive),
                "video",
                ".mp4",
                1234,
                "1 KB",
                1.0,
                0,
                0,
                4.0,
            ),
        )

    client = media_env["client"]
    candidates = client.get("/api/thumbnails/candidates", params={"path": member})
    assert candidates.status_code == 200, candidates.text
    assert len(candidates.json()["frames"]) >= 8

    poster = client.post(
        "/api/thumbnails/poster", json={"path": member, "timestamp": 1.0}
    )
    assert poster.status_code == 200, poster.text
    target = thumbnail_path(member, 1.0, 1234)
    assert target.is_file() and target.stat().st_size > 0

    frame = client.get(
        "/api/thumbnails/frame", params={"path": member, "timestamp": 2.0}
    )
    assert frame.status_code == 200
    assert frame.content[:2] == b"\xff\xd8"
