"""Coverage tests for the sorter service and its router.

Complements ``test_v2_sorter.py`` with the defensive paths: missing source
files, corrupted state, OSError handlers, the undo-restore collision naming,
and the transcode/thumbnail endpoints with stubbed ffmpeg subprocesses.
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from media_library import config as v2_config
from media_library import dedupe as dedupe_service
from media_library import sorter as sorter_service
from media_library import writes
from media_library.app import create_app
from media_library.paths import stable_id
from media_library.writes import WriteError


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
    monkeypatch.setenv("SOURCE_DIR", str(incoming))
    monkeypatch.setenv("THUMBNAIL_DIR", str(tmp_path / "thumbs"))
    monkeypatch.setenv("SORTER_STATE", str(tmp_path / "sorter.json"))
    monkeypatch.setenv("DB_READ_ONLY", "false")
    monkeypatch.setenv("WRITES", "true")
    v2_config.reload_settings()

    db_path = Path(os.environ["DB_PATH"])
    with sqlite3.connect(db_path) as conn:
        for folder in (library, library / "Trips", library / "Trips" / "Iceland"):
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
    try:
        yield {
            "client": TestClient(create_app()),
            "incoming": incoming,
            "library": library,
            "thumbs": tmp_path / "thumbs",
            "state": tmp_path / "sorter.json",
        }
    finally:
        sorter_service._undo_stack.clear()
        sorter_service.invalidate_queue()
        v2_config.reload_settings()


def _reswitch_source(monkeypatch, path) -> None:
    """Point SOURCE_DIR somewhere else for one test."""
    monkeypatch.setenv("SOURCE_DIR", str(path))
    v2_config.reload_settings()


# ---------------------------------------------------------------------------
# Source resolution (service)
# ---------------------------------------------------------------------------


def test_resolve_source_dir_validates_the_folder(sorter_env, monkeypatch, tmp_path):
    incoming = sorter_env["incoming"]
    assert sorter_service._resolve_source_dir() == os.path.abspath(incoming)

    _reswitch_source(monkeypatch, tmp_path / "nowhere")
    with pytest.raises(WriteError) as exc:
        sorter_service._resolve_source_dir()
    assert exc.value.status == 404


def test_root_that_is_a_file_is_an_invalid_path(sorter_env, monkeypatch, tmp_path):
    stub = tmp_path / "incoming-file.mp4"
    stub.write_bytes(b"x")
    _reswitch_source(monkeypatch, stub)
    with pytest.raises(WriteError) as exc:
        sorter_service.resolve_source_file(str(stub))
    assert exc.value.status == 400
    assert exc.value.message == "Invalid path"


def test_missing_target_file_is_a_404(sorter_env):
    with pytest.raises(WriteError) as exc:
        sorter_service.resolve_source_file(str(sorter_env["incoming"] / "ghost.mp4"))
    assert exc.value.status == 404


def test_breaking_path_comparison_rejects_the_path(sorter_env, monkeypatch):
    def _boom(_paths):
        raise ValueError("impossible comparison")

    monkeypatch.setattr(os.path, "commonpath", _boom)
    with pytest.raises(WriteError) as exc:
        sorter_service.resolve_source_file(str(sorter_env["incoming"] / "a-first.mp4"))
    assert exc.value.status == 403


# ---------------------------------------------------------------------------
# Duplicate check edge cases
# ---------------------------------------------------------------------------


def test_duplicate_defaults_to_zero_when_the_size_vanishes(sorter_env, monkeypatch):
    monkeypatch.setattr(dedupe_service, "quick_hash", lambda _path: "digest")
    monkeypatch.setattr(os.path, "getsize", _raise_os_error)

    result = sorter_service.check_duplicate(str(sorter_env["incoming"] / "a-first.mp4"))
    assert result["matched"] is False
    assert result["duplicates"] == []


def test_duplicate_without_a_digest_short_circuits(sorter_env, monkeypatch):
    monkeypatch.setattr(dedupe_service, "quick_hash", lambda _path: "")
    result = sorter_service.check_duplicate(str(sorter_env["incoming"] / "a-first.mp4"))
    assert result == {
        "path": str(sorter_env["incoming"] / "a-first.mp4"),
        "matched": False,
        "duplicates": [],
    }


def _raise_os_error(*_args, **_kwargs):
    raise OSError("gone")


# ---------------------------------------------------------------------------
# Queue scan edge cases
# ---------------------------------------------------------------------------


def test_scan_queue_without_a_source_folder(sorter_env, monkeypatch):
    _reswitch_source(monkeypatch, sorter_env["incoming"] / "never-created")
    sorter_service.invalidate_queue()
    assert sorter_service.scan_queue() == []
    assert sorter_service.scan_queue(force=True) == []


def test_scan_queue_skips_hidden_videos(sorter_env, monkeypatch):
    hidden = sorter_env["incoming"] / ".hidden.mp4"
    hidden.write_bytes(b"0" * 10)
    try:
        names = [item["name"] for item in sorter_service.scan_queue(force=True)]
    finally:
        hidden.unlink()
    assert names == ["a-first.mp4", "b-second.mkv"]


def test_scan_queue_survives_per_item_errors(sorter_env, monkeypatch):
    monkeypatch.setattr(sorter_service, "_queue_item", _raise_os_error)
    assert sorter_service.scan_queue(force=True) == []


def test_scan_queue_survives_a_failed_scandir(sorter_env, monkeypatch):
    monkeypatch.setattr(os, "scandir", _raise_os_error)
    assert sorter_service.scan_queue(force=True) == []


# ---------------------------------------------------------------------------
# Recent-destination state file fallbacks
# ---------------------------------------------------------------------------


def test_recent_destinations_ignores_a_corrupt_state_file(sorter_env):
    sorter_env["state"].write_text("{not json", encoding="utf-8")
    assert sorter_service.recent_destinations() == []


def test_recent_destinations_requires_a_list(sorter_env):
    sorter_env["state"].write_text(
        json.dumps({"recent_destinations": {"wrong": "shape"}}), encoding="utf-8"
    )
    assert sorter_service.recent_destinations() == []


def test_recent_destinations_drops_missing_folders(sorter_env, tmp_path):
    incoming = sorter_env["incoming"]
    fake = tmp_path / "ghost"
    sorter_service._write_state({"recent_destinations": [str(fake), str(incoming)]})
    recent = sorter_service.recent_destinations()
    assert [item["path"] for item in recent] == [str(incoming)]
    assert recent[0]["name"] == incoming.name


# ---------------------------------------------------------------------------
# Move / undo edge cases
# ---------------------------------------------------------------------------


def test_move_rejects_a_missing_destination(sorter_env):
    source = sorter_env["incoming"] / "a-first.mp4"
    response = sorter_env["client"].post(
        "/api/sorter/move",
        json={"path": str(source), "destination": str(sorter_env["library"] / "gone")},
    )
    assert response.status_code == 404


def test_move_reports_a_skipped_import(sorter_env, monkeypatch):
    monkeypatch.setattr(writes, "import_file", lambda *_a, **_k: {"skipped": True})
    source = sorter_env["incoming"] / "a-first.mp4"
    with pytest.raises(WriteError) as exc:
        sorter_service.move_to_library(
            str(source), str(sorter_env["library"] / "Inbox")
        )
    assert exc.value.status == 409


def test_undo_stack_stays_capped(sorter_env):
    """One entry past the cap is forgotten, never the newest move."""
    sorter_service._undo_stack[:] = [
        {"source": f"s{index}", "target": f"t{index}", "destination": "d", "at": 0.0}
        for index in range(50)
    ]
    source = sorter_env["incoming"] / "a-first.mp4"
    target = sorter_env["library"] / "Inbox" / "a-first.mp4"
    payload = sorter_service.move_to_library(
        str(source), str(sorter_env["library"] / "Inbox")
    )
    assert payload["undo_count"] == 50
    assert sorter_service._undo_stack[-1]["target"] == str(target)


def test_undo_picks_a_free_restore_name(sorter_env):
    incoming = sorter_env["incoming"]
    source = incoming / "a-first.mp4"
    sorter_env["client"].post(
        "/api/sorter/move",
        json={"path": str(source), "destination": str(sorter_env["library"] / "Inbox")},
    )
    # The original name and the first collision are both taken already.
    source.write_bytes(b"other")
    (incoming / "a-first (restored 1).mp4").write_bytes(b"again")

    restored = sorter_env["client"].post("/api/sorter/undo").json()["restored"]
    assert restored == str(incoming / "a-first (restored 2).mp4")
    assert (incoming / "a-first (restored 2).mp4").is_file()


def test_undo_reports_a_failed_restore(sorter_env, monkeypatch):
    source = sorter_env["incoming"] / "a-first.mp4"
    sorter_env["client"].post(
        "/api/sorter/move",
        json={"path": str(source), "destination": str(sorter_env["library"] / "Inbox")},
    )
    monkeypatch.setattr("shutil.move", _raise_os_error)
    response = sorter_env["client"].post("/api/sorter/undo")
    assert response.status_code == 500
    assert sorter_service.undo_count() == 1


# ---------------------------------------------------------------------------
# Rename / delete edge cases
# ---------------------------------------------------------------------------


def test_rename_to_the_same_name_is_a_noop(sorter_env):
    """The noop shortcut surfaces only through the service call."""
    source = sorter_env["incoming"] / "a-first.mp4"
    assert sorter_service.rename_incoming(str(source), "a-first.mp4") == {
        "path": str(source)
    }


def test_rename_moves_the_file_and_refreshes_the_queue(sorter_env):
    source = sorter_env["incoming"] / "a-first.mp4"
    payload = (
        sorter_env["client"]
        .post("/api/sorter/rename", json={"path": str(source), "name": "renamed.mp4"})
        .json()
    )
    assert payload["path"] == str(sorter_env["incoming"] / "renamed.mp4")
    assert [item["name"] for item in payload["items"]] == [
        "b-second.mkv",
        "renamed.mp4",
    ]


def test_delete_files_and_reports_the_queue(sorter_env):
    target = sorter_env["incoming"] / "a-first.mp4"
    payload = (
        sorter_env["client"]
        .post("/api/sorter/delete", json={"path": str(target)})
        .json()
    )
    assert not target.exists()
    assert [item["name"] for item in payload["items"]] == ["b-second.mkv"]


def test_undo_on_an_empty_stack_is_a_conflict(sorter_env):
    response = sorter_env["client"].post("/api/sorter/undo")
    assert response.status_code == 409


def test_undo_keeps_the_entry_when_the_moved_file_vanished(sorter_env):
    source = sorter_env["incoming"] / "a-first.mp4"
    destination = sorter_env["library"] / "Inbox"
    sorter_env["client"].post(
        "/api/sorter/move", json={"path": str(source), "destination": str(destination)}
    )
    moved = destination / "a-first.mp4"
    moved.unlink()

    response = sorter_env["client"].post("/api/sorter/undo")
    assert response.status_code == 409
    assert sorter_service.undo_count() == 1


def test_scan_queue_reuses_the_fresh_cache(sorter_env):
    first = sorter_service.scan_queue()
    second = sorter_service.scan_queue()
    assert first == second


def test_rename_onto_an_existing_file_is_a_conflict(sorter_env):
    source = sorter_env["incoming"] / "a-first.mp4"
    response = sorter_env["client"].post(
        "/api/sorter/rename", json={"path": str(source), "name": "b-second.mkv"}
    )
    assert response.status_code == 409


def test_delete_failure_is_a_500(sorter_env, monkeypatch):
    monkeypatch.setattr(os, "remove", _raise_os_error)
    source = sorter_env["incoming"] / "a-first.mp4"
    response = sorter_env["client"].post(
        "/api/sorter/delete", json={"path": str(source)}
    )
    assert response.status_code == 500


# ---------------------------------------------------------------------------
# Router plumbing around the simple endpoints
# ---------------------------------------------------------------------------


def test_queue_and_refresh_endpoints(sorter_env):
    client = sorter_env["client"]
    queue = client.get("/api/sorter/queue").json()
    assert [item["name"] for item in queue["items"]] == ["a-first.mp4", "b-second.mkv"]

    (sorter_env["incoming"] / "a-third.webm").write_bytes(b"2" * 100)
    refreshed = client.post("/api/sorter/refresh").json()
    assert "a-third.webm" in [item["name"] for item in refreshed["items"]]


def test_config_endpoint(sorter_env):
    payload = sorter_env["client"].get("/api/sorter/config").json()
    assert payload["source_dir"] == str(sorter_env["incoming"])
    assert payload["source_available"] is True
    assert payload["queue_count"] == 2


def test_duplicate_endpoint_translates_errors(sorter_env, monkeypatch):
    client = sorter_env["client"]
    ok = client.get(
        "/api/sorter/duplicate",
        params={"path": str(sorter_env["incoming"] / "a-first.mp4")},
    )
    assert ok.status_code == 200
    assert ok.json()["matched"] is False

    def _boom(_path):
        raise WriteError("boom", 451)

    monkeypatch.setattr(sorter_service, "check_duplicate", _boom)
    assert (
        client.get(
            "/api/sorter/duplicate",
            params={"path": str(sorter_env["incoming"] / "a-first.mp4")},
        ).status_code
        == 451
    )


def test_destinations_endpoint_translates_errors(sorter_env, monkeypatch):
    client = sorter_env["client"]
    (sorter_env["library"] / "Trips" / "Iceland").mkdir(parents=True, exist_ok=True)
    found = client.get("/api/sorter/destinations", params={"q": "ice"})
    assert found.status_code == 200
    assert [item["name"] for item in found.json()] == ["Iceland"]

    def _boom(_query, _limit):
        raise WriteError("boom", 452)

    monkeypatch.setattr(sorter_service, "search_destinations", _boom)
    response = client.get("/api/sorter/destinations")
    assert response.status_code == 452


def test_create_folder_endpoint_translates_errors(sorter_env, monkeypatch):
    client = sorter_env["client"]
    parent = sorter_env["library"] / "Trips"
    created = client.post(
        "/api/sorter/folders", json={"parent": str(parent), "name": "Day 1"}
    )
    assert created.status_code == 200
    assert created.json()["path"] == str(parent / "Day 1")
    assert created.json()["name"] == "Day 1"

    def _boom(_parent, _name):
        raise WriteError("boom", 453)

    monkeypatch.setattr(sorter_service, "create_destination", _boom)
    response = client.post(
        "/api/sorter/folders", json={"parent": str(parent), "name": "Day 2"}
    )
    assert response.status_code == 453


def test_rename_rejects_bad_names(sorter_env):
    source = sorter_env["incoming"] / "a-first.mp4"
    response = sorter_env["client"].post(
        "/api/sorter/rename", json={"path": str(source), "name": "../escape.mp4"}
    )
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# Streaming endpoint
# ---------------------------------------------------------------------------


def test_stream_endpoint_ranges_and_rejections(sorter_env):
    client = sorter_env["client"]
    source = sorter_env["incoming"] / "a-first.mp4"

    partial = client.get(
        "/api/sorter/stream",
        params={"path": str(source)},
        headers={"Range": "bytes=5-9"},
    )
    assert partial.status_code == 206
    assert partial.content == b"00000"

    assert (
        client.get("/api/sorter/stream", params={"path": "/etc/hostname"}).status_code
        == 403
    )


# ---------------------------------------------------------------------------
# Transcode endpoint (stubbed ffmpeg subprocess)
# ---------------------------------------------------------------------------


class _StubProcess:
    """Minimal stand-in for an asyncio subprocess the router can pipe."""

    def __init__(self, chunks: list[bytes]) -> None:
        self.stdout = _StubReader(chunks)
        self.killed = False
        self.terminated = False

    async def wait(self):
        return 0

    def kill(self):
        self.killed = True

    def terminate(self):
        self.terminated = True


class _StubReader:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    async def read(self, _size: int) -> bytes:
        if self._chunks:
            return self._chunks.pop(0)
        return b""


def test_transcode_endpoint_yields_the_pipe(sorter_env, monkeypatch):
    async def _fake_exec(*_command, **_kwargs):
        return _StubProcess([b"fakefrag", b""])

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)
    response = sorter_env["client"].get(
        "/api/sorter/transcode",
        params={"path": str(sorter_env["incoming"] / "a-first.mp4"), "ss": 2.5},
    )
    assert response.status_code == 200
    assert response.content == b"fakefrag"


def test_transcode_endpoint_refuses_paths_outside_incoming(sorter_env):
    response = sorter_env["client"].get(
        "/api/sorter/transcode", params={"path": "/etc/hostname"}
    )
    assert response.status_code == 403


def test_transcode_endpoint_releases_the_slot_on_spawn_failure(sorter_env, monkeypatch):
    async def _fake_exec(*_command, **_kwargs):
        raise HTTPException(status_code=503, detail="no encoder")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)
    response = sorter_env["client"].get(
        "/api/sorter/transcode",
        params={"path": str(sorter_env["incoming"] / "a-first.mp4")},
    )
    assert response.status_code == 503


# ---------------------------------------------------------------------------
# Thumbnail endpoint (stubbed ffmpeg subprocess)
# ---------------------------------------------------------------------------


def _thumbnail_key(file_path: Path) -> str:
    target = os.path.abspath(str(file_path))
    return stable_id(f"{target}:{file_path.stat().st_mtime}")


def test_thumbnail_generates_and_then_serves_the_cache(sorter_env, monkeypatch):
    """A stubbed ffmpeg writes the temp sibling; the cache serves the replay."""
    client = sorter_env["client"]
    source = sorter_env["incoming"] / "a-first.mp4"
    key = _thumbnail_key(source)
    temp = sorter_env["thumbs"] / "sorter" / f".mllibtmp-{key}-{os.getpid()}.jpg"

    async def _fake_exec(*_command, **_kwargs):
        temp.write_bytes(b"\xff\xd8stubframe")
        return _StubProcess([])

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)
    first = client.get("/api/sorter/thumbnail", params={"path": str(source)})
    assert first.status_code == 200
    assert first.content == b"\xff\xd8stubframe"
    assert not temp.exists()

    # Second request never spawns a process: the cached frame answers.
    cached = client.get("/api/sorter/thumbnail", params={"path": str(source)})
    assert cached.status_code == 200
    assert cached.content == b"\xff\xd8stubframe"


def test_thumbnail_without_a_produced_frame_is_a_404(sorter_env, monkeypatch):
    async def _fake_exec(*_command, **_kwargs):
        return _StubProcess([])

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)
    response = sorter_env["client"].get(
        "/api/sorter/thumbnail",
        params={"path": str(sorter_env["incoming"] / "a-first.mp4")},
    )
    assert response.status_code == 404


def test_thumbnail_refuses_paths_outside_incoming(sorter_env):
    response = sorter_env["client"].get(
        "/api/sorter/thumbnail", params={"path": "/etc/hostname"}
    )
    assert response.status_code == 403


def test_thumbnail_timeout_kills_the_process(sorter_env, monkeypatch):
    """A hung encoder is killed and the temp sibling cleaned up."""
    real_wait_for = asyncio.wait_for
    process = _StubProcess([])

    def _smart_wait_for(coro, timeout=None):
        if timeout == 30:
            coro.close()
            raise asyncio.TimeoutError()
        return real_wait_for(coro, timeout=timeout)

    async def _fake_exec(*_command, **_kwargs):
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)
    monkeypatch.setattr(asyncio, "wait_for", _smart_wait_for)

    response = sorter_env["client"].get(
        "/api/sorter/thumbnail",
        params={"path": str(sorter_env["incoming"] / "a-first.mp4")},
    )
    assert response.status_code == 504
    assert process.killed is True
