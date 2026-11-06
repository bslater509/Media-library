"""Live library transcoding and on-demand video posters."""

from __future__ import annotations

import os
import sqlite3
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from media_library_v2 import config as v2_config
from media_library_v2.mount import build_v2_app
from media_library_v2.transcode_stream import build_command


@pytest.fixture
def video_env(tmp_path, monkeypatch):
    library = tmp_path / "library"
    (library / "Clips").mkdir(parents=True)
    thumbs = tmp_path / "thumbs"
    (thumbs / "animated").mkdir(parents=True)
    monkeypatch.setenv("LIBRARY_DIR", str(library))
    monkeypatch.setenv("V2_LIBRARY_DIR", str(library))
    monkeypatch.setenv("THUMBNAIL_DIR", str(thumbs))
    v2_config.reload_settings()

    mkv = library / "Clips" / "clip.mkv"
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
            str(mkv),
        ],
        check=True,
    )

    db_path = Path(os.environ["DB_PATH"])
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO folders (path, name, parent_path, file_count, "
            "subfolder_count, last_scanned, is_zip, total_size) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (str(library / "Clips"), "Clips", str(library), 1, 0, 1.0, 0, 0),
        )
        stat = mkv.stat()
        conn.execute(
            "INSERT INTO files (path, name, folder_path, type, ext, size, "
            "size_formatted, mtime, needs_transcode, has_thumb, width, height, "
            "duration, video_codec) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                str(mkv),
                mkv.name,
                str(mkv.parent),
                "video",
                ".mkv",
                stat.st_size,
                "1 KB",
                stat.st_mtime,
                1,
                0,
                160,
                120,
                1.0,
                "h264",
            ),
        )

    try:
        yield {"library": library, "mkv": mkv, "db_path": db_path, "thumbs": thumbs}
    finally:
        v2_config.reload_settings()


def test_command_is_fragmented_mp4(video_env):
    command = build_command("/tmp/in.mkv", 2.0)
    assert "libx264" in command
    assert command[command.index("-f") + 1] == "mp4"
    assert "pipe:1" in command
    assert command[command.index("-ss") + 1] == "2.0"


def test_library_transcodes_an_mkv(video_env):
    client = TestClient(build_v2_app())
    response = client.get(
        "/api/v2/media/transcode", params={"path": str(video_env["mkv"])}
    )
    assert response.status_code == 200, response.text
    assert len(response.content) > 1000
    assert b"ftyp" in response.content[:64]


def test_file_entry_exposes_a_transcode_url(video_env):
    client = TestClient(build_v2_app())
    payload = client.get(
        "/api/v2/library/browse", params={"path": str(video_env["mkv"].parent)}
    ).json()
    entry = payload["files"][0]
    assert entry["playable"] is False
    assert entry["transcode_url"].startswith("/api/v2/media/transcode?path=")
    assert entry["media_url"].startswith("/api/v2/media?path=")


def test_video_poster_is_generated_on_demand(video_env):
    client = TestClient(build_v2_app())
    response = client.get("/api/v2/thumbnails", params={"path": str(video_env["mkv"])})
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("image/jpeg")
    assert response.content[:2] == b"\xff\xd8"

    with sqlite3.connect(video_env["db_path"]) as conn:
        row = conn.execute(
            "SELECT has_thumb FROM files WHERE path = ?", (str(video_env["mkv"]),)
        ).fetchone()
    assert row[0] == 1


def test_unknown_media_is_refused(video_env):
    client = TestClient(build_v2_app())
    assert client.get(
        "/api/v2/media/transcode", params={"path": "/etc/passwd"}
    ).status_code in (400, 403, 404)
