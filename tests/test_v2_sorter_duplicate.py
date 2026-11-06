"""Sorter duplicate check against the indexed library."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from media_library_v2 import config as v2_config
from media_library_v2 import dedupe
from media_library_v2.mount import build_v2_app


@pytest.fixture
def sorter_env(tmp_path, monkeypatch):
    library = tmp_path / "library"
    incoming = tmp_path / "incoming"
    library.mkdir()
    incoming.mkdir()
    monkeypatch.setenv("LIBRARY_DIR", str(library))
    monkeypatch.setenv("V2_LIBRARY_DIR", str(library))
    monkeypatch.setenv("INCOMING_DIR", str(incoming))
    monkeypatch.setenv("V2_SOURCE_DIR", str(incoming))
    monkeypatch.setenv("V2_DB_READ_ONLY", "false")
    v2_config.reload_settings()

    payload = b"same bytes" * 5000
    library_copy = library / "kept.mp4"
    library_copy.write_bytes(payload)
    duplicate = incoming / "duplicate.mp4"
    duplicate.write_bytes(payload)
    fresh = incoming / "fresh.mp4"
    fresh.write_bytes(b"different payload" * 5000)

    digest = dedupe.quick_hash(str(library_copy))
    db_path = Path(os.environ["DB_PATH"])
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO folders (path, name, parent_path, file_count, "
            "subfolder_count, total_size, is_zip) VALUES (?,?,?,?,?,?,?)",
            (str(library), "library", str(library.parent), 1, 0, 0, 0),
        )
        conn.execute(
            "INSERT INTO files (path, name, folder_path, type, ext, size, "
            "size_formatted, mtime, needs_transcode, has_thumb, quick_hash) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                str(library_copy),
                library_copy.name,
                str(library),
                "video",
                ".mp4",
                library_copy.stat().st_size,
                "55 KB",
                library_copy.stat().st_mtime,
                0,
                0,
                digest,
            ),
        )

    try:
        yield {
            "client": TestClient(build_v2_app()),
            "library": library,
            "incoming": incoming,
            "duplicate": duplicate,
            "fresh": fresh,
            "library_copy": library_copy,
        }
    finally:
        v2_config.reload_settings()


def test_duplicate_match_returns_the_library_copy(sorter_env):
    response = sorter_env["client"].get(
        "/api/v2/sorter/duplicate",
        params={"path": str(sorter_env["duplicate"])},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["matched"] is True
    assert body["duplicates"][0]["path"] == str(sorter_env["library_copy"])


def test_different_content_is_not_a_match(sorter_env):
    body = (
        sorter_env["client"]
        .get(
            "/api/v2/sorter/duplicate",
            params={"path": str(sorter_env["fresh"])},
        )
        .json()
    )
    assert body["matched"] is False
    assert body["duplicates"] == []


def test_paths_outside_the_incoming_folder_are_refused(sorter_env):
    response = sorter_env["client"].get(
        "/api/v2/sorter/duplicate",
        params={"path": str(sorter_env["library_copy"])},
    )
    assert response.status_code == 403
