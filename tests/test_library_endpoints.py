"""Library endpoint coverage: folders, folder search, search, favorites.

The feature suites exercise browse; these routes translate query parameters into
index reads and a bad ``type`` must not become a 500. Each test seeds the index
directly so the assertions are about the endpoint, not the crawler.
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
    image = portrait / "face.jpg"
    image.write_bytes(b"y" * 128)

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
        for path, name, folder, kind, ext, size in (
            (video, "aurora.mp4", trip, "video", ".mp4", 256),
            (image, "face.jpg", portrait, "image", ".jpg", 128),
        ):
            conn.execute(
                "INSERT INTO files (path, name, folder_path, type, ext, size, "
                "size_formatted, mtime) VALUES (?,?,?,?,?,?,'size',0)",
                (str(path), name, str(folder), kind, ext, size),
            )
        conn.execute(
            "INSERT INTO favorites (path, created_at) VALUES (?, 1.0)",
            (str(video),),
        )

    with TestClient(create_app()) as client:
        yield client, library


def test_folders_lists_immediate_children(seeded):
    client, library = seeded
    body = client.get("/api/library/folders", params={"path": str(library)}).json()
    names = [f["name"] for f in body["folders"]]
    assert names == ["Iceland 2023", "Portraits"]


def test_folder_search_matches_a_name(seeded):
    client, _ = seeded
    body = client.get("/api/library/folders/search", params={"q": "ice"}).json()
    assert [f["name"] for f in body["results"]] == ["Iceland 2023"]


def test_folder_search_requires_a_query(seeded):
    client, _ = seeded
    assert client.get("/api/library/folders/search").status_code == 422


def test_search_filters_by_type(seeded):
    client, _ = seeded
    videos = client.get(
        "/api/library/search", params={"q": "a", "type": "video"}
    ).json()
    assert [f["name"] for f in videos["results"]] == ["aurora.mp4"]
    images = client.get(
        "/api/library/search", params={"q": "a", "type": "image"}
    ).json()
    assert [f["name"] for f in images["results"]] == ["face.jpg"]


def test_search_matches_a_relative_folder_name(seeded):
    client, _ = seeded
    body = client.get("/api/library/search", params={"q": "portraits"}).json()
    assert [f["name"] for f in body["results"]] == ["face.jpg"]


def test_favorites_list_and_type_filter(seeded):
    client, _ = seeded
    body = client.get("/api/library/favorites").json()
    assert [f["name"] for f in body["files"]] == ["aurora.mp4"]
    assert body["counts"]["favorites"] == 1
    images = client.get("/api/library/favorites", params={"type": "image"}).json()
    assert images["files"] == []
    assert images["counts"]["favorites"] == 1


def test_index_errors_are_reported_as_503(seeded, monkeypatch):
    client, _ = seeded
    from media_library.routers import library as library_router

    def boom(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(library_router.library, "search", boom)
    response = client.get("/api/library/search", params={"q": "a"})
    assert response.status_code == 503
    assert "Index unavailable" in response.json()["detail"]
