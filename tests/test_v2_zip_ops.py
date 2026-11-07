"""ZIP compression/unpacking tests for v2, including the index rewrite."""

from __future__ import annotations

import os
import sqlite3
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from media_library import config as v2_config
from media_library import writes, zip_ops
from media_library.app import create_app


@pytest.fixture
def ops_env(tmp_path, monkeypatch):
    library = tmp_path / "library"
    (library / "Album" / "sub").mkdir(parents=True)
    (library / "VideoFolder").mkdir()
    (library / "Album" / "one.jpg").write_bytes(b"one" * 100)
    (library / "Album" / "sub" / "two.png").write_bytes(b"two" * 150)
    (library / "VideoFolder" / "clip.mp4").write_bytes(b"clip" * 300)
    staging = tmp_path / "zip_staging"
    staging.mkdir()
    cache = tmp_path / "zip_cache"

    monkeypatch.setenv("LIBRARY_DIR", str(library))
    monkeypatch.setenv("LIBRARY_DIR", str(library))
    monkeypatch.setenv("THUMBNAIL_DIR", str(tmp_path / "thumbs"))
    monkeypatch.setenv("ZIP_STAGING_DIR", str(staging))
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
            library / "VideoFolder",
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
            (library / "Album" / "one.jpg", "image", ".jpg", 300),
            (library / "Album" / "sub" / "two.png", "image", ".png", 450),
            (library / "VideoFolder" / "clip.mp4", "video", ".mp4", 1200),
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
        conn.execute(
            "INSERT INTO favorites (path, created_at) VALUES (?, ?)",
            (str(library / "Album" / "one.jpg"), 1.0),
        )

    try:
        yield {"library": library, "db_path": db_path, "staging": staging}
    finally:
        v2_config.reload_settings()


def _db(env):
    return sqlite3.connect(env["db_path"])


def test_check_marks_image_folders_eligible(ops_env):
    album = ops_env["library"] / "Album"
    video = ops_env["library"] / "VideoFolder"

    checked = zip_ops.check_folder(str(album))
    assert checked["eligible"] is True
    assert checked["images"] == 2
    assert checked["videos"] == 0
    assert checked["target"] == str(album) + ".zip"

    refused = zip_ops.check_folder(str(video))
    assert refused["eligible"] is False
    assert refused["videos"] == 1


def test_compress_builds_the_archive_and_rewrites_the_index(ops_env):
    album = ops_env["library"] / "Album"
    archive = ops_env["library"] / "Album.zip"

    result = zip_ops.compress_folder(str(album))
    assert result["archive"] == str(archive)
    assert result["members"] == 2
    assert archive.is_file()
    assert not album.exists()

    with zipfile.ZipFile(archive) as bundle:
        assert set(bundle.namelist()) == {"one.jpg", "sub/two.png"}

    with _db(ops_env) as conn:
        folder = conn.execute(
            "SELECT is_zip, file_count, name FROM folders WHERE path = ?",
            (str(archive),),
        ).fetchone()
        members = conn.execute(
            "SELECT path, folder_path, type FROM files WHERE folder_path = ? "
            "ORDER BY path",
            (str(archive),),
        ).fetchall()
        gone = conn.execute(
            "SELECT COUNT(*) FROM folders WHERE path = ? OR path LIKE ?",
            (str(album), str(album) + "/%"),
        ).fetchone()[0]
        favorite = conn.execute(
            "SELECT COUNT(*) FROM favorites WHERE path = ?",
            (str(album / "one.jpg"),),
        ).fetchone()[0]
    assert folder == (1, 2, "Album.zip")
    assert members == [
        (f"{archive}/one.jpg", str(archive), "image"),
        (f"{archive}/sub/two.png", str(archive), "image"),
    ]
    assert gone == 0
    assert favorite == 0


def test_compress_refuses_videos_and_collisions(ops_env):
    video = ops_env["library"] / "VideoFolder"
    with pytest.raises(writes.WriteError) as caught:
        zip_ops.compress_folder(str(video))
    assert caught.value.status == 409

    album = ops_env["library"] / "Album"
    (ops_env["library"] / "Album.zip").write_bytes(b"existing")
    with pytest.raises(writes.WriteError) as caught:
        zip_ops.compress_folder(str(album))
    assert "already exists" in caught.value.message
    assert album.is_dir()


def test_unpack_restores_the_tree_and_reindexes(ops_env):
    album = ops_env["library"] / "Album"
    archive = ops_env["library"] / "Album.zip"
    zip_ops.compress_folder(str(album))

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


def test_router_exposes_check_and_overview(ops_env):
    client = TestClient(create_app())
    album = ops_env["library"] / "Album"

    checked = client.get("/api/zip/check", params={"path": str(album)}).json()
    assert checked["eligible"] is True

    overview = client.get("/api/zip/overview").json()
    assert isinstance(overview["folders"], list)
    assert overview["cache"]["files"] >= 0

    compressed = client.post("/api/zip/compress", json={"path": str(album)})
    assert compressed.status_code == 200, compressed.text
    archive = compressed.json()["archive"]
    after = client.get("/api/zip/overview").json()
    assert any(item["path"] == archive for item in after["archives"])

    unpacked = client.post("/api/zip/unpack", json={"path": archive})
    assert unpacked.status_code == 200, unpacked.text
    assert (album / "one.jpg").is_file()
