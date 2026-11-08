"""ZIP unpacking tests, including the index rewrite."""

from __future__ import annotations

import os
import shutil
import sqlite3
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from media_library import config as v2_config
from media_library import zip_ops
from media_library.app import create_app


@pytest.fixture
def ops_env(tmp_path, monkeypatch):
    library = tmp_path / "library"
    (library / "Album").mkdir(parents=True)
    (library / "one.jpg").write_bytes(b"one" * 100)
    (library / "Album" / "one.jpg").write_bytes(b"one" * 100)
    (library / "Album" / "sub").mkdir()
    (library / "Album" / "sub" / "two.png").write_bytes(b"two" * 150)
    cache = tmp_path / "zip_cache"

    monkeypatch.setenv("LIBRARY_DIR", str(library))
    monkeypatch.setenv("THUMBNAIL_DIR", str(tmp_path / "thumbs"))
    monkeypatch.setenv("ZIP_CACHE_DIR", str(cache))
    monkeypatch.setenv("DB_READ_ONLY", "false")
    monkeypatch.setenv("WRITES", "true")
    v2_config.reload_settings()

    db_path = Path(os.environ["DB_PATH"])
    with sqlite3.connect(db_path) as conn:
        for folder in (
            library,
            library / "Album",
            library / "Album" / "sub",
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
        rows = [
            (library / "one.jpg", "image", ".jpg", 300),
            (library / "Album" / "one.jpg", "image", ".jpg", 300),
            (library / "Album" / "sub" / "two.png", "image", ".png", 450),
        ]
        for path, kind, ext, size in rows:
            conn.execute(
                "INSERT INTO files (path, name, folder_path, type, ext, size, "
                "size_formatted, mtime, needs_transcode, has_thumb, width, height) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    str(path),
                    path.name,
                    str(path.parent),
                    kind,
                    ext,
                    size,
                    f"{size} B",
                    1.0,
                    0,
                    0,
                    640,
                    480,
                ),
            )

    try:
        yield {"library": library, "db_path": db_path}
    finally:
        v2_config.reload_settings()


def _db(env):
    return sqlite3.connect(env["db_path"])


def _make_archive(source: Path, archive: Path) -> None:
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for full in sorted(source.rglob("*")):
            if full.is_file():
                bundle.write(full, full.relative_to(source).as_posix())


def test_unpack_restores_the_tree_and_reindexes(ops_env):
    album = ops_env["library"] / "Album"
    archive = ops_env["library"] / "Album.zip"
    _make_archive(album, archive)
    shutil.rmtree(album)  # like a folder that compress left behind
    with _db(ops_env) as conn:
        conn.execute("UPDATE folders SET is_zip = 1 WHERE path = ?", (str(archive),))

    result = zip_ops.unpack_archive(str(archive))
    assert result["folder"] == str(album)
    assert result["files"] == 2
    assert album.is_dir()
    assert (album / "one.jpg").is_file()
    assert (album / "sub" / "two.png").is_file()
    assert not archive.exists()

    with _db(ops_env) as conn:
        zip_row = conn.execute(
            "SELECT COUNT(*) FROM folders WHERE path = ?", (str(archive),)
        ).fetchone()[0]
        zip_files = conn.execute(
            "SELECT COUNT(*) FROM files WHERE folder_path = ?", (str(archive),)
        ).fetchone()[0]
        folder_rows = conn.execute(
            "SELECT COUNT(*) FROM folders WHERE path = ? OR path = ?",
            (str(album), str(album / "sub")),
        ).fetchone()[0]
        files = conn.execute(
            "SELECT COUNT(*) FROM files WHERE folder_path = ?",
            (str(album),),
        ).fetchone()[0]
    assert zip_row == 0
    assert zip_files == 0
    assert folder_rows == 2
    assert files == 1  # one.jpg directly in Album; sub/two.png is in sub


def test_unpack_nudges_the_destination_when_it_exists(ops_env):
    (ops_env["library"] / "two" / "three").mkdir(parents=True)
    (ops_env["library"] / "two" / "three" / "note.txt").write_text("hi")
    archive = ops_env["library"] / "two.zip"
    _make_archive(ops_env["library"] / "two", archive)

    result = zip_ops.unpack_archive(str(archive))
    destination = Path(result["folder"])
    assert destination == ops_env["library"] / "two (1)"
    assert (destination / "three" / "note.txt").is_file()
    assert result["removed"] == str(archive)


def test_unpack_skips_traversal_members(ops_env):
    library = ops_env["library"]
    bad = library / "bad.zip"
    with zipfile.ZipFile(bad, "w") as bundle:
        bundle.writestr("ok.txt", "keep me")
        bundle.writestr("../escape.txt", "should not escape")
    with _db(ops_env) as conn:
        conn.execute(
            "INSERT INTO folders (path, name, parent_path, file_count, "
            "subfolder_count, last_scanned, is_zip, total_size) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (str(bad), "bad.zip", str(library), 1, 0, 1.0, 1, 4),
        )

    result = zip_ops.unpack_archive(str(bad))
    destination = Path(result["folder"])
    assert (destination / "ok.txt").is_file()
    assert not (library.parent / "escape.txt").exists()
    assert not (destination.parent / "escape.txt").exists()


def test_router_exposes_unpack_and_overview(ops_env):
    client = TestClient(create_app())
    album = ops_env["library"] / "Album"
    archive = ops_env["library"] / "Album.zip"
    _make_archive(album, archive)
    shutil.rmtree(album)  # like a folder that compress left behind
    with _db(ops_env) as conn:
        conn.execute(
            "INSERT INTO folders (path, name, parent_path, file_count, "
            "subfolder_count, last_scanned, is_zip, total_size) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (str(archive), "Album.zip", str(ops_env["library"]), 2, 0, 1.0, 1, 450),
        )

    overview = client.get("/api/zip/overview").json()
    assert any(item["path"] == str(archive) for item in overview["archives"])
    assert overview["cache"]["files"] >= 0

    assert client.get("/api/zip/check").status_code == 404
    assert client.post("/api/zip/compress", json={"path": str(album)}).status_code in (
        404,
        405,
    )

    unpacked = client.post("/api/zip/unpack", json={"path": str(archive)})
    assert unpacked.status_code == 200, unpacked.text
    assert (album / "one.jpg").is_file()
