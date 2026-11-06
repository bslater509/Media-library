"""Sorter tests for v2: queue scan, moves, undo, rename, delete, streaming."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from media_library_v2 import config as v2_config
from media_library_v2 import sorter as sorter_service
from media_library_v2.mount import build_v2_app


@pytest.fixture
def sorter_env(tmp_path, monkeypatch):
    incoming = tmp_path / "incoming"
    incoming.mkdir()
    (incoming / "a-first.mp4").write_bytes(b"0" * 600)
    (incoming / "b-second.mkv").write_bytes(b"1" * 700)
    (incoming / "notes.txt").write_text("not a video")
    (incoming / "subfolder").mkdir()

    library = tmp_path / "library"
    (library / "Trips" / "Iceland").mkdir(parents=True)
    (library / "Inbox").mkdir()

    monkeypatch.setenv("LIBRARY_DIR", str(library))
    monkeypatch.setenv("V2_LIBRARY_DIR", str(library))
    monkeypatch.setenv("V2_SOURCE_DIR", str(incoming))
    monkeypatch.setenv("THUMBNAIL_DIR", str(tmp_path / "thumbs"))
    monkeypatch.setenv("V2_SORTER_STATE", str(tmp_path / "sorter.json"))
    monkeypatch.setenv("V2_UPLOAD_STAGING_DIR", str(tmp_path / "staging"))
    monkeypatch.setenv("V2_DB_READ_ONLY", "false")
    monkeypatch.setenv("V2_WRITES", "true")
    v2_config.reload_settings()

    db_path = Path(os.environ["DB_PATH"])
    with sqlite3.connect(db_path) as conn:
        for folder in (
            library,
            library / "Trips",
            library / "Trips" / "Iceland",
            library / "Inbox",
        ):
            conn.execute(
                "INSERT INTO folders (path, name, parent_path, file_count, "
                "subfolder_count, last_scanned, is_zip, total_size) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (
                    str(folder),
                    folder.name,
                    str(folder.parent) if folder != library else None,
                    0,
                    0,
                    1.0,
                    0,
                    0,
                ),
            )

    sorter_service._undo_stack.clear()
    sorter_service.invalidate_queue()
    client = TestClient(build_v2_app())
    try:
        yield {
            "client": client,
            "incoming": incoming,
            "library": library,
            "db_path": db_path,
            "state": tmp_path / "sorter.json",
        }
    finally:
        sorter_service._undo_stack.clear()
        sorter_service.invalidate_queue()
        v2_config.reload_settings()


def _db(env):
    return sqlite3.connect(env["db_path"])


def test_queue_lists_videos_only_and_sorted(sorter_env):
    client = sorter_env["client"]
    payload = client.get("/api/v2/sorter/queue").json()

    assert payload["available"] is True
    assert [item["name"] for item in payload["items"]] == [
        "a-first.mp4",
        "b-second.mkv",
    ]
    first, second = payload["items"]
    assert first["playable"] is True
    assert first["transcode_url"] is None
    assert second["playable"] is False
    assert second["transcode_url"].startswith("/api/v2/sorter/transcode")
    assert payload["undo_count"] == 0


def test_config_reports_source_and_recent_destinations(sorter_env):
    payload = sorter_env["client"].get("/api/v2/sorter/config").json()
    assert payload["source_dir"] == str(sorter_env["incoming"])
    assert payload["source_available"] is True
    assert payload["queue_count"] == 2
    assert payload["recent_destinations"] == []


def test_move_indexes_the_file_and_shrinks_the_queue(sorter_env):
    client = sorter_env["client"]
    source = sorter_env["incoming"] / "a-first.mp4"
    destination = sorter_env["library"] / "Trips" / "Iceland"

    payload = client.post(
        "/api/v2/sorter/move",
        json={"path": str(source), "destination": str(destination)},
    ).json()

    assert payload["moved"]["target"] == str(destination / "a-first.mp4")
    assert (destination / "a-first.mp4").is_file()
    assert not source.exists()
    assert [item["name"] for item in payload["items"]] == ["b-second.mkv"]
    assert payload["undo_count"] == 1

    with _db(sorter_env) as conn:
        row = conn.execute(
            "SELECT folder_path, size FROM files WHERE path = ?",
            (str(destination / "a-first.mp4"),),
        ).fetchone()
        folder = conn.execute(
            "SELECT file_count FROM folders WHERE path = ?", (str(destination),)
        ).fetchone()
    assert row == (str(destination), 600)
    assert folder[0] == 1

    config = client.get("/api/v2/sorter/config").json()
    assert config["recent_destinations"][0]["path"] == str(destination)
    assert sorter_env["state"].is_file()


def test_move_auto_renames_a_collision_in_the_destination(sorter_env):
    client = sorter_env["client"]
    source = sorter_env["incoming"] / "a-first.mp4"
    destination = sorter_env["library"] / "Inbox"
    (destination / "a-first.mp4").write_bytes(b"existing")

    payload = client.post(
        "/api/v2/sorter/move",
        json={"path": str(source), "destination": str(destination)},
    ).json()

    assert Path(payload["moved"]["target"]).name == "a-first (1).mp4"


def test_undo_restores_the_file_and_removes_the_index_row(sorter_env):
    client = sorter_env["client"]
    source = sorter_env["incoming"] / "a-first.mp4"
    destination = sorter_env["library"] / "Trips"

    client.post(
        "/api/v2/sorter/move",
        json={"path": str(source), "destination": str(destination)},
    )
    moved = destination / "a-first.mp4"
    assert moved.is_file()

    payload = client.post("/api/v2/sorter/undo").json()
    assert payload["restored"] == str(source)
    assert source.is_file()
    assert not moved.exists()
    assert [item["name"] for item in payload["items"]] == [
        "a-first.mp4",
        "b-second.mkv",
    ]
    assert payload["undo_count"] == 0

    with _db(sorter_env) as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM files WHERE path = ?", (str(moved),)
            ).fetchone()[0]
            == 0
        )


def test_undo_with_an_empty_stack_is_a_conflict(sorter_env):
    response = sorter_env["client"].post("/api/v2/sorter/undo")
    assert response.status_code == 409


def test_rename_and_delete_the_current_file(sorter_env):
    client = sorter_env["client"]
    source = sorter_env["incoming"] / "a-first.mp4"

    renamed = client.post(
        "/api/v2/sorter/rename",
        json={"path": str(source), "name": "renamed.mp4"},
    ).json()
    assert renamed["path"] == str(sorter_env["incoming"] / "renamed.mp4")
    assert [item["name"] for item in renamed["items"]] == [
        "b-second.mkv",
        "renamed.mp4",
    ]

    target = sorter_env["incoming"] / "renamed.mp4"
    deleted = client.post("/api/v2/sorter/delete", json={"path": str(target)}).json()
    assert not target.exists()
    assert [item["name"] for item in deleted["items"]] == ["b-second.mkv"]


def test_rename_rejects_bad_names(sorter_env):
    client = sorter_env["client"]
    source = sorter_env["incoming"] / "a-first.mp4"
    response = client.post(
        "/api/v2/sorter/rename",
        json={"path": str(source), "name": "../escape.mp4"},
    )
    assert response.status_code == 400


def test_create_destination_records_it_as_recent(sorter_env):
    client = sorter_env["client"]
    parent = sorter_env["library"] / "Trips"

    created = client.post(
        "/api/v2/sorter/folders",
        json={"parent": str(parent), "name": "Day 1"},
    ).json()
    assert (parent / "Day 1").is_dir()

    config = client.get("/api/v2/sorter/config").json()
    assert config["recent_destinations"][0]["path"] == created["path"]


def test_destination_search_matches_folder_names(sorter_env):
    client = sorter_env["client"]
    found = client.get("/api/v2/sorter/destinations", params={"q": "ice"}).json()
    assert [item["name"] for item in found] == ["Iceland"]

    everything = client.get("/api/v2/sorter/destinations").json()
    names = {item["name"] for item in everything}
    assert {"Trips", "Iceland", "Inbox"} <= names


def test_sorter_stream_supports_ranges(sorter_env):
    client = sorter_env["client"]
    source = sorter_env["incoming"] / "a-first.mp4"

    partial = client.get(
        "/api/v2/sorter/stream",
        params={"path": str(source)},
        headers={"Range": "bytes=10-19"},
    )
    assert partial.status_code == 206
    assert partial.content == b"0" * 10
    assert partial.headers["content-range"] == "bytes 10-19/600"


def test_sorter_rejects_paths_outside_the_incoming_folder(sorter_env):
    client = sorter_env["client"]
    assert (
        client.get(
            "/api/v2/sorter/stream", params={"path": "/etc/hostname"}
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/v2/sorter/move",
            json={"path": "/etc/hostname", "destination": str(sorter_env["library"])},
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/v2/sorter/delete", json={"path": str(sorter_env["library"])}
        ).status_code
        == 403
    )


def test_undo_keeps_the_entry_when_the_moved_file_vanished(sorter_env):
    """A failed undo must not consume the entry (or an older undo is lost)."""
    client = sorter_env["client"]
    source = sorter_env["incoming"] / "a-first.mp4"
    destination = sorter_env["library"] / "Inbox"
    client.post(
        "/api/v2/sorter/move",
        json={"path": str(source), "destination": str(destination)},
    )
    (destination / "a-first.mp4").unlink()

    response = client.post("/api/v2/sorter/undo")
    assert response.status_code == 409
    assert sorter_service.undo_count() == 1
