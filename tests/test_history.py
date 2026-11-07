"""Watch-history coverage: the rollup write, the maintained path, its listings.

``watch_history`` is one per-file rollup row: recording again bumps
``play_count`` and keeps the longer ``seconds_watched`` mark. Rename/move and
delete keep the rows aligned with the index; a read-only index answers 503.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from media_library import config as api_config
from media_library.app import create_app


@pytest.fixture
def seeded(tmp_path, monkeypatch):
    library = tmp_path / "library"
    trip = library / "Iceland 2023"
    portrait = library / "Portraits"
    for folder in (library, trip, portrait):
        folder.mkdir(parents=True, exist_ok=True)
    video = trip / "aurora.mp4"
    video.write_bytes(b"x" * 256)
    other = trip / "glacier.mp4"
    other.write_bytes(b"w" * 128)
    image = portrait / "face.jpg"
    image.write_bytes(b"y" * 64)

    monkeypatch.setenv("LIBRARY_DIR", str(library))
    api_config.reload_settings()
    db = Path(api_config.get_settings().db_path)

    with sqlite3.connect(db) as conn:
        for path, name, parent in (
            (library, "library", library.parent),
            (trip, "Iceland 2023", library),
            (portrait, "Portraits", library),
        ):
            conn.execute(
                "INSERT INTO folders (path, name, parent_path, is_zip, file_count) "
                "VALUES (?,?,?,0,0)",
                (str(path), name, str(parent)),
            )
        for path, name, folder, kind, ext, size, duration in (
            (video, "aurora.mp4", trip, "video", ".mp4", 256, 300.0),
            (other, "glacier.mp4", trip, "video", ".mp4", 128, 60.0),
            (image, "face.jpg", portrait, "image", ".jpg", 64, None),
        ):
            conn.execute(
                "INSERT INTO files (path, name, folder_path, type, ext, size, "
                "size_formatted, mtime, duration) VALUES (?,?,?,?,?,?,'size',0,?)",
                (str(path), name, str(folder), kind, ext, size, duration),
            )

    with TestClient(create_app()) as client:
        yield client, library


def test_record_creates_and_second_view_keeps_longer_progress(seeded):
    client, library = seeded
    target = str(library / "Iceland 2023" / "aurora.mp4")
    first = client.post("/api/history/record", json={"path": target, "seconds": 42.0})
    assert first.status_code == 200
    body = first.json()
    assert body["play_count"] == 1
    assert body["seconds_watched"] == 42.0

    # A shorter second viewing keeps the high-water mark.
    second = client.post("/api/history/record", json={"path": target, "seconds": 10.0})
    assert second.status_code == 200
    body = second.json()
    assert body["play_count"] == 2
    assert body["seconds_watched"] == 42.0

    # Going further extends progress.
    third = client.post("/api/history/record", json={"path": target, "seconds": 120.0})
    assert third.json()["seconds_watched"] == 120.0
    assert third.json()["play_count"] == 3


def test_record_unknown_path_is_404(seeded):
    client, library = seeded
    response = client.post(
        "/api/history/record",
        json={"path": str(library / "missing.mp4"), "seconds": 1.0},
    )
    assert response.status_code == 404


def test_record_outside_root_is_403(seeded):
    client, _ = seeded
    response = client.post(
        "/api/history/record", json={"path": "../outside.mp4", "seconds": 1.0}
    )
    assert response.status_code == 403


def test_record_negative_seconds_is_422(seeded):
    client, library = seeded
    response = client.post(
        "/api/history/record",
        json={"path": str(library / "Iceland 2023" / "aurora.mp4"), "seconds": -1},
    )
    assert response.status_code == 422


def test_read_only_answers_503(seeded, monkeypatch):
    client, library = seeded
    monkeypatch.setenv("DB_READ_ONLY", "true")
    api_config.reload_settings()
    response = client.post(
        "/api/history/record",
        json={"path": str(library / "Iceland 2023" / "aurora.mp4"), "seconds": 1.0},
    )
    assert response.status_code == 503


def test_recent_lists_newest_view_first_with_type_filter(seeded):
    client, library = seeded
    video = str(library / "Iceland 2023" / "aurora.mp4")
    other = str(library / "Iceland 2023" / "glacier.mp4")
    image = str(library / "Portraits" / "face.jpg")
    for path in (video, other, image):
        assert (
            client.post(
                "/api/history/record", json={"path": path, "seconds": 1.0}
            ).status_code
            == 200
        )
    client.post("/api/history/record", json={"path": video, "seconds": 2.0})

    body = client.get("/api/history/recent").json()
    assert [f["name"] for f in body["files"]] == [
        "aurora.mp4",
        "face.jpg",
        "glacier.mp4",
    ]
    assert body["total"] == 3
    assert body["files"][0]["seconds_watched"] == 2.0

    videos = client.get("/api/history/recent", params={"type": "video"}).json()
    assert [f["name"] for f in videos["files"]] == ["aurora.mp4", "glacier.mp4"]

    images = client.get("/api/history/recent", params={"type": "image"}).json()
    assert [f["name"] for f in images["files"]] == ["face.jpg"]


def test_resume_lists_only_unfinished_videos(seeded):
    client, library = seeded
    video = str(library / "Iceland 2023" / "aurora.mp4")  # duration 300 s
    other = str(library / "Iceland 2023" / "glacier.mp4")  # duration 60 s
    image = str(library / "Portraits" / "face.jpg")
    client.post("/api/history/record", json={"path": video, "seconds": 100.0})
    client.post("/api/history/record", json={"path": other, "seconds": 45.0})
    client.post("/api/history/record", json={"path": image, "seconds": 1.0})

    body = client.get("/api/history/resume").json()
    assert [f["name"] for f in body["files"]] == ["aurora.mp4"]
    assert body["files"][0]["seconds_watched"] == 100.0

    # Finishing a video (within its last 30 s takes it out of the list.
    client.post("/api/history/record", json={"path": video, "seconds": 290.0})
    body = client.get("/api/history/resume").json()
    assert body["files"] == []
    assert body["total"] == 0


def test_rename_and_move_follow_the_row(seeded):
    client, library = seeded
    video = str(library / "Iceland 2023" / "aurora.mp4")
    client.post("/api/history/record", json={"path": video, "seconds": 33.0})

    response = client.post(
        "/api/manage/rename", json={"path": video, "name": "midnight.mp4"}
    )
    assert response.status_code == 200
    body = client.get("/api/history/recent").json()
    assert [f["name"] for f in body["files"]] == ["midnight.mp4"]
    assert body["files"][0]["seconds_watched"] == 33.0


def test_delete_drops_the_row(seeded):
    client, library = seeded
    video = str(library / "Iceland 2023" / "aurora.mp4")
    client.post("/api/history/record", json={"path": video, "seconds": 5.0})

    response = client.post("/api/manage/delete", json={"paths": [video]})
    assert response.status_code == 200
    body = client.get("/api/history/recent").json()
    assert body["files"] == []
    assert body["total"] == 0
    assert client.get("/api/system/info").json()["counts"]["watched"] == 0


def test_counts_include_watched(seeded):
    client, library = seeded
    video = str(library / "Iceland 2023" / "aurora.mp4")
    client.post("/api/history/record", json={"path": video, "seconds": 3.0})
    assert client.get("/api/system/info").json()["counts"]["watched"] == 1


def test_schema_is_idempotent_with_the_new_table(seeded):
    from media_library.schema import init_schema

    db = Path(api_config.get_settings().db_path)
    init_schema(db)
    init_schema(db)
    with sqlite3.connect(db) as conn:
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name = 'watch_history'"
        ).fetchone()
    assert tables is not None


def test_unwatched_lists_only_never_viewed(seeded):
    client, library = seeded
    video = str(library / "Iceland 2023" / "aurora.mp4")
    assert (
        client.post(
            "/api/history/record", json={"path": video, "seconds": 8.0}
        ).status_code
        == 200
    )

    body = client.get("/api/history/unwatched").json()
    assert sorted(f["name"] for f in body["files"]) == ["face.jpg", "glacier.mp4"]
    assert body["total"] == 2

    videos = client.get("/api/history/unwatched", params={"type": "video"}).json()
    assert [f["name"] for f in videos["files"]] == ["glacier.mp4"]
    assert all(f["last_viewed_at"] is None for f in body["files"])


def test_second_record_without_bump_keeps_the_play_count(seeded):
    client, library = seeded
    target = str(library / "Iceland 2023" / "aurora.mp4")
    first = client.post(
        "/api/history/record",
        json={"path": target, "seconds": 42.0, "bump": True},
    )
    assert first.json()["play_count"] == 1
    second = client.post(
        "/api/history/record",
        json={"path": target, "seconds": 10.0, "bump": False},
    )
    body = second.json()
    assert body["play_count"] == 1
    assert body["seconds_watched"] == 42.0

    # The next session bumps again, even after a non-bumping record.
    third = client.post(
        "/api/history/record",
        json={"path": target, "seconds": 7.0, "bump": True},
    )
    assert third.json()["play_count"] == 2
