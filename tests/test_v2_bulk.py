"""Bulk mutations: favorites, renames and path-scoped WebP conversion."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from media_library import config as v2_config
from media_library.app import create_app


@pytest.fixture
def bulk_env(tmp_path, monkeypatch):
    library = tmp_path / "library"
    thumbs = tmp_path / "thumbs"
    (thumbs / "animated").mkdir(parents=True)
    library.mkdir()
    monkeypatch.setenv("LIBRARY_DIR", str(library))
    monkeypatch.setenv("LIBRARY_DIR", str(library))
    monkeypatch.setenv("THUMBNAIL_DIR", str(thumbs))
    monkeypatch.setenv("DB_READ_ONLY", "false")
    monkeypatch.setenv("WRITES", "true")
    monkeypatch.setenv("WEBP_QUALITY", "80")
    monkeypatch.setenv("WEBP_SKIP_LARGER", "false")
    v2_config.reload_settings()

    db_path = Path(os.environ["DB_PATH"])
    names = ["one.jpg", "two.jpg", "three.png", "clip.mp4"]
    files: dict[str, Path] = {}
    for index, name in enumerate(names):
        path = library / name
        if path.suffix == ".mp4":
            path.write_bytes(b"not really a video")
        else:
            Image.new("RGB", (80 + index, 60), (30 * index, 90, 200)).save(path)
        files[name] = path

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO folders (path, name, parent_path, file_count, "
            "subfolder_count, total_size, is_zip) VALUES (?,?,?,?,?,?,?)",
            (str(library), "library", str(library.parent), len(names), 0, 0, 0),
        )
        for name, path in files.items():
            stat = path.stat()
            conn.execute(
                "INSERT INTO files (path, name, folder_path, type, ext, size, "
                "size_formatted, mtime, needs_transcode, has_thumb) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    str(path),
                    path.name,
                    str(path.parent),
                    "video" if path.suffix == ".mp4" else "image",
                    path.suffix,
                    stat.st_size,
                    f"{stat.st_size} B",
                    stat.st_mtime,
                    0,
                    0,
                ),
            )

    try:
        yield {
            "client": TestClient(create_app()),
            "library": library,
            "db_path": db_path,
            "files": files,
        }
    finally:
        v2_config.reload_settings()


def _path(env, name: str) -> str:
    return str(env["files"][name])


def test_favorite_batch_add_toggle_and_remove(bulk_env):
    client = bulk_env["client"]
    first = _path(bulk_env, "one.jpg")
    second = _path(bulk_env, "two.jpg")

    added = client.post(
        "/api/favorites/batch",
        json={"paths": [first, second], "action": "add"},
    )
    assert added.status_code == 200
    body = added.json()
    assert body["updated"] == 2
    assert body["counts"]["images"] == 2

    toggled = client.post(
        "/api/favorites/batch",
        json={"paths": [first], "action": "toggle"},
    ).json()
    assert toggled["counts"]["all"] == 1

    removed = client.post(
        "/api/favorites/batch",
        json={"paths": [first, second], "action": "remove"},
    ).json()
    assert removed["updated"] == 2
    assert removed["counts"]["all"] == 0


def test_favorite_batch_reports_unindexed_paths(bulk_env):
    result = bulk_env["client"].post(
        "/api/favorites/batch",
        json={"paths": [str(bulk_env["library"] / "missing.jpg")], "action": "add"},
    )
    assert result.status_code == 200
    body = result.json()
    assert body["updated"] == 0
    assert body["failed"][0]["error"] == "Not indexed"


def test_bulk_rename_updates_disk_and_index(bulk_env):
    client = bulk_env["client"]
    first = _path(bulk_env, "one.jpg")
    second = _path(bulk_env, "two.jpg")
    result = client.post(
        "/api/manage/bulk-rename",
        json={
            "renames": [
                {"path": first, "name": "01-one.jpg"},
                {"path": second, "name": "02-two.jpg"},
            ]
        },
    )
    assert result.status_code == 200
    body = result.json()
    assert body["failed"] == []
    assert (bulk_env["library"] / "01-one.jpg").is_file()
    assert (bulk_env["library"] / "02-two.jpg").is_file()
    with sqlite3.connect(bulk_env["db_path"]) as conn:
        paths = {row[0] for row in conn.execute("SELECT path FROM files").fetchall()}
    assert str(bulk_env["library"] / "01-one.jpg") in paths
    assert str(bulk_env["library"] / "one.jpg") not in paths


def test_bulk_rename_continues_after_a_failure(bulk_env):
    client = bulk_env["client"]
    result = client.post(
        "/api/manage/bulk-rename",
        json={
            "renames": [
                {"path": str(bulk_env["library"] / "missing.jpg"), "name": "x.jpg"},
                {"path": _path(bulk_env, "three.png"), "name": "three-renamed.png"},
            ]
        },
    )
    body = result.json()
    assert len(body["failed"]) == 1
    assert body["renamed"] == [str(bulk_env["library"] / "three-renamed.png")]


def test_webp_optimize_only_converts_the_selected_paths(bulk_env):
    client = bulk_env["client"]
    selected = _path(bulk_env, "one.jpg")
    untouched = bulk_env["library"] / "two.jpg"

    result = client.post(
        "/api/webp/optimize",
        json={"paths": [selected], "limit": 20},
    )
    assert result.status_code == 200
    body = result.json()
    assert body["converted"] == 1
    assert body["remaining"] >= 2

    converted = bulk_env["library"] / "one.webp"
    assert converted.is_file()
    assert not (bulk_env["library"] / "one.jpg").exists()
    assert untouched.is_file()
    with sqlite3.connect(bulk_env["db_path"]) as conn:
        row = conn.execute(
            "SELECT path, webp_optimized FROM files WHERE path = ?",
            (str(converted),),
        ).fetchone()
        other = conn.execute(
            "SELECT webp_optimized FROM files WHERE path = ?",
            (str(untouched),),
        ).fetchone()
    assert row is not None and row[1] == 1
    assert other[0] == 0
