"""ZIP virtual-folder tests for v2: browse, member streaming, safety."""

from __future__ import annotations

import os
import sqlite3
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from media_library_v2 import config as v2_config
from media_library_v2 import zipfs
from media_library_v2.mount import build_v2_app


@pytest.fixture
def zip_env(tmp_path, monkeypatch):
    library = tmp_path / "library"
    library.mkdir()
    cache = tmp_path / "zip_cache"
    monkeypatch.setenv("LIBRARY_DIR", str(library))
    monkeypatch.setenv("V2_LIBRARY_DIR", str(library))
    monkeypatch.setenv("THUMBNAIL_DIR", str(tmp_path / "thumbs"))
    monkeypatch.setenv("ZIP_CACHE_DIR", str(cache))
    v2_config.reload_settings()

    archive = library / "album.zip"
    photo = b"photo-bytes" * 100
    clip = b"clip-bytes" * 200
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("sub/photo.jpg", photo)
        bundle.writestr("clip.mp4", clip)

    db_path = Path(os.environ["DB_PATH"])
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO folders (path, name, parent_path, file_count, "
            "subfolder_count, last_scanned, is_zip, total_size) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (
                str(archive),
                "album.zip",
                str(library),
                2,
                0,
                1.0,
                1,
                len(photo) + len(clip),
            ),
        )
        for inner, kind, ext, size in (
            ("sub/photo.jpg", "image", ".jpg", len(photo)),
            ("clip.mp4", "video", ".mp4", len(clip)),
        ):
            conn.execute(
                "INSERT INTO files (path, name, folder_path, type, ext, size, "
                "size_formatted, mtime, needs_transcode, has_thumb) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    f"{archive}/{inner}",
                    Path(inner).name,
                    str(archive),
                    kind,
                    ext,
                    size,
                    f"{size} B",
                    1.0,
                    0,
                    0,
                ),
            )

    try:
        yield {
            "library": library,
            "archive": archive,
            "cache": cache,
            "db_path": db_path,
        }
    finally:
        v2_config.reload_settings()


def test_split_zip_path():
    assert zipfs.split_zip_path("/lib/a.zip/sub/x.jpg") == ("/lib/a.zip", "sub/x.jpg")
    assert zipfs.split_zip_path("/lib/a.mp4") is None
    assert zipfs.is_zip_member("/lib/a.zip/x") is True


def test_browse_a_zip_as_a_virtual_folder(zip_env):
    client = TestClient(build_v2_app())
    path = str(zip_env["archive"])
    response = client.get("/api/v2/library/browse", params={"path": path})
    assert response.status_code == 200
    payload = response.json()
    assert payload["path"] == path
    assert payload["indexed"] is True
    assert {file["name"] for file in payload["files"]} == {"photo.jpg", "clip.mp4"}
    assert [crumb["name"] for crumb in payload["breadcrumbs"]] == ["library", "album"]


def test_member_media_is_extracted_and_range_streamed(zip_env):
    client = TestClient(build_v2_app())
    path = f"{zip_env['archive']}/sub/photo.jpg"

    partial = client.get(
        "/api/v2/media", params={"path": path}, headers={"Range": "bytes=0-9"}
    )
    assert partial.status_code == 206
    assert partial.content == b"photo-byte"
    assert partial.headers["content-range"].startswith("bytes 0-9/")

    full = client.get("/api/v2/media", params={"path": path})
    assert full.status_code == 200
    assert full.content == b"photo-bytes" * 100

    # The extraction cache is reused and lives under the configured directory.
    assert any(zip_env["cache"].rglob("*.jpg"))


def test_member_traversal_and_missing_member_are_rejected(zip_env):
    client = TestClient(build_v2_app())
    traversal = client.get(
        "/api/v2/media", params={"path": f"{zip_env['archive']}/../etc/passwd"}
    )
    assert traversal.status_code in (400, 403)

    missing = client.get(
        "/api/v2/media", params={"path": f"{zip_env['archive']}/nope.jpg"}
    )
    assert missing.status_code == 404


def test_member_outside_the_library_archive_is_denied(tmp_path):
    client = TestClient(build_v2_app())
    response = client.get("/api/v2/media", params={"path": "/etc/foo.zip/member.jpg"})
    assert response.status_code in (403, 404)
