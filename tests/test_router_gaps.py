"""Router-level tests for HTTP surfaces previously covered only by adjacency.

Covers the cleanup endpoints (``/folders/empty``, which had no API-level test
at all), the manage mutations through their HTTP contract, the uploads
finalize error path and the favorites toggle/count endpoints."""

from __future__ import annotations

import os
import sqlite3

from fastapi.testclient import TestClient

from media_library import config as v2_config
from media_library.app import create_app


def _library_env(tmp_path, monkeypatch):
    library = tmp_path / "library"
    (library / "Videos").mkdir(parents=True)
    monkeypatch.setenv("LIBRARY_DIR", str(library))
    monkeypatch.setenv("WRITES", "true")
    monkeypatch.setenv("DB_READ_ONLY", "false")
    v2_config.reload_settings()
    return library


def _index_file(library, path: str, name: str, folder: str) -> None:
    with sqlite3.connect(os.environ["DB_PATH"]) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO folders (path, name, parent_path, last_scanned, "
            "is_zip, file_count) VALUES (?, ?, ?, 0, 0, 1)",
            (folder, os.path.basename(folder), str(library)),
        )
        conn.execute(
            "INSERT OR IGNORE INTO files (path, name, folder_path, type, ext, "
            "size, size_formatted, mtime) VALUES (?, ?, ?, 'video', '.mp4', "
            "1, '1.0 B', 0)",
            (path, name, folder),
        )


def test_empty_folder_round_trip(tmp_path, monkeypatch):
    library = _library_env(tmp_path, monkeypatch)
    empty = library / "Videos" / "Empty"
    empty.mkdir()
    db = os.environ["DB_PATH"]
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO folders (path, name, parent_path, last_scanned, is_zip) "
            "VALUES (?, ?, ?, 0, 0)",
            (str(empty), "Empty", str(library / "Videos")),
        )

    client = TestClient(create_app())
    found = client.get("/api/folders/empty")
    assert found.status_code == 200
    assert any(row["path"] == str(empty) for row in found.json()["folders"])

    removed = client.post("/api/folders/empty/delete", json={"paths": [str(empty)]})
    assert removed.status_code == 200
    assert removed.json()["deleted"] == [str(empty)]
    assert not empty.exists()


def test_empty_folder_delete_rejects_outside_root(tmp_path, monkeypatch):
    _library_env(tmp_path, monkeypatch)
    client = TestClient(create_app())
    response = client.post(
        "/api/folders/empty/delete", json={"paths": [str(tmp_path / "elsewhere")]}
    )
    assert response.status_code == 403


def test_empty_folder_delete_report_includes_failures(tmp_path, monkeypatch):
    _library_env(tmp_path, monkeypatch)
    client = TestClient(create_app())
    ghost = f"{os.environ['LIBRARY_DIR']}/Videos/i-do-not-exist"
    response = client.post("/api/folders/empty/delete", json={"paths": [ghost]})
    assert response.status_code == 200
    payload = response.json()
    assert payload["deleted"] == []
    assert payload["failed"][0]["path"] == ghost


def test_manage_rename_and_delete_round_trip(tmp_path, monkeypatch):
    library = _library_env(tmp_path, monkeypatch)
    folder = str(library / "Videos")
    original = str(library / "Videos" / "clip.mp4")
    (library / "Videos" / "clip.mp4").write_bytes(b"0" * 32)
    _index_file(library, original, "clip.mp4", folder)

    client = TestClient(create_app())
    renamed = client.post(
        "/api/manage/rename", json={"path": original, "name": "renamed.mp4"}
    )
    assert renamed.status_code == 200
    new_path = renamed.json()["path"]
    assert new_path == str(library / "Videos" / "renamed.mp4")
    assert not os.path.isfile(original)
    assert os.path.isfile(new_path)

    deleted = client.post("/api/manage/delete", json={"paths": [new_path]})
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] == [str(new_path)]
    assert not os.path.isfile(new_path)


def test_manage_create_folder_and_move(tmp_path, monkeypatch):
    library = _library_env(tmp_path, monkeypatch)
    source = str(library / "Videos" / "clip.mp4")
    (library / "Videos" / "clip.mp4").write_bytes(b"0" * 32)
    _index_file(library, source, "clip.mp4", str(library / "Videos"))

    client = TestClient(create_app())
    created = client.post(
        "/api/manage/folders",
        json={"parent": str(library), "name": "Staging"},
    )
    assert created.status_code == 200

    moved = client.post(
        "/api/manage/move",
        json={
            "paths": [source],
            "destination": str(library / "Staging"),
            "conflict": "auto_rename",
        },
    )
    assert moved.status_code == 200
    entry = moved.json()["moved"][0]
    assert entry["target"] == str(library / "Staging" / "clip.mp4")
    assert os.path.isfile(entry["target"])
    assert not os.path.isfile(source)


def test_uploads_finalize_404_for_unknown_id(tmp_path, monkeypatch):
    _library_env(tmp_path, monkeypatch)
    client = TestClient(create_app())
    response = client.post(
        "/api/uploads/finalize",
        json={
            "upload_id": "uunknown00",
            "name": "clip.mp4",
            "folder": str(tmp_path / "library"),
            "total_size": 10,
        },
    )
    assert response.status_code == 404


def test_favorites_toggle_and_counts_http(tmp_path, monkeypatch):
    library = _library_env(tmp_path, monkeypatch)
    folder = str(library / "Videos")
    path = str(library / "Videos" / "favorite.mp4")
    (library / "Videos" / "favorite.mp4").write_bytes(b"0" * 32)
    _index_file(library, path, "favorite.mp4", folder)

    client = TestClient(create_app())
    toggled = client.post("/api/favorites/toggle", json={"path": path})
    assert toggled.status_code == 200
    assert toggled.json()["is_favorite"] is True

    counts = client.get("/api/favorites/counts")
    assert counts.status_code == 200
    assert counts.json()["favorites"] == 1

    listing = client.get("/api/library/favorites", params={"limit": 10})
    assert listing.status_code == 200
    assert [f["path"] for f in listing.json()["files"]] == [path]
