"""Archive WebP rewrite tests for v2."""

from __future__ import annotations

import io
import os
import sqlite3
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from media_library import config as v2_config
from media_library import zip_webp, zipfs
from media_library.app import create_app


def _png_bytes(size: int = 160) -> bytes:
    image = Image.new("RGB", (size, size))
    for x in range(size):
        for y in range(size):
            image.putpixel((x, y), ((x * 3) % 256, (y * 5) % 256, (x + y) % 256))
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def _jpg_bytes(size: int = 160) -> bytes:
    import random

    image = Image.new("RGB", (size, size))
    rng = random.Random(5)
    image.putdata(
        [
            (rng.randrange(256), rng.randrange(256), rng.randrange(256))
            for _ in range(size * size)
        ]
    )
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=92)
    return buffer.getvalue()


@pytest.fixture
def zip_webp_env(tmp_path, monkeypatch):
    library = tmp_path / "library"
    library.mkdir()
    staging = tmp_path / "zip_staging"
    staging.mkdir()
    cache_dir = tmp_path / "zip_cache"
    monkeypatch.setenv("LIBRARY_DIR", str(library))
    monkeypatch.setenv("LIBRARY_DIR", str(library))
    monkeypatch.setenv("THUMBNAIL_DIR", str(tmp_path / "thumbs"))
    monkeypatch.setenv("ZIP_STAGING_DIR", str(staging))
    monkeypatch.setenv("ZIP_CACHE_DIR", str(cache_dir))
    monkeypatch.setenv("WEBP_QUALITY", "80")
    monkeypatch.setenv("WEBP_SKIP_LARGER", "false")
    monkeypatch.setenv("DB_READ_ONLY", "false")
    monkeypatch.setenv("WRITES", "true")
    v2_config.reload_settings()

    archive = library / "album.zip"
    png = _png_bytes()
    jpg = _jpg_bytes()
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("photo.png", png)
        bundle.writestr("notes.txt", "keep this text")
        bundle.writestr("sub/photo2.jpg", jpg)
        bundle.writestr("already.webp", b"RIFF....WEBP")

    db_path = Path(os.environ["DB_PATH"])
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO folders (path, name, parent_path, file_count, "
            "subfolder_count, last_scanned, is_zip, total_size) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (str(archive), "album.zip", str(library), 4, 0, 1.0, 1, 1000),
        )
        for name, kind, ext in (
            ("photo.png", "image", ".png"),
            ("notes.txt", "other", ".txt"),
            ("sub/photo2.jpg", "image", ".jpg"),
        ):
            conn.execute(
                "INSERT INTO files (path, name, folder_path, type, ext, size, "
                "size_formatted, mtime, needs_transcode, has_thumb) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    f"{archive}/{name}",
                    os.path.basename(name),
                    str(archive),
                    kind,
                    ext,
                    len(png),
                    "1 KB",
                    1.0,
                    0,
                    0,
                ),
            )
        conn.execute(
            "INSERT INTO favorites (path, created_at) VALUES (?, ?)",
            (f"{archive}/photo.png", 1.0),
        )
        conn.execute(
            "UPDATE webp_stats SET saved_bytes = 0, converted_count = 0, "
            "skipped_count = 0 WHERE id = 1"
        )

    cache_root = zipfs.cache_dir_for(str(archive))
    cache_root.mkdir(parents=True, exist_ok=True)
    (cache_root / "member.jpg").write_bytes(b"cached")

    try:
        yield {
            "library": library,
            "archive": archive,
            "db_path": db_path,
            "cache": cache_dir,
        }
    finally:
        v2_config.reload_settings()


def _db(env):
    return sqlite3.connect(env["db_path"])


def test_rewrite_converts_members_and_preserves_everything_else(zip_webp_env):
    archive = zip_webp_env["archive"]
    result = zip_webp.optimize_archive(str(archive))
    assert result["changed"] is True
    assert result["converted"] == 2
    assert result["members"] == 4

    with zipfile.ZipFile(archive) as bundle:
        names = set(bundle.namelist())
        assert names == {"photo.webp", "notes.txt", "sub/photo2.webp", "already.webp"}
        assert bundle.read("notes.txt") == b"keep this text"
        with Image.open(io.BytesIO(bundle.read("photo.webp"))) as image:
            assert image.format == "WEBP"
        with Image.open(io.BytesIO(bundle.read("sub/photo2.webp"))) as image:
            assert image.format == "WEBP"

    with _db(zip_webp_env) as conn:
        folder = conn.execute(
            "SELECT is_zip, file_count FROM folders WHERE path = ?", (str(archive),)
        ).fetchone()
        members = {
            row[0]
            for row in conn.execute(
                "SELECT path FROM files WHERE folder_path = ?", (str(archive),)
            ).fetchall()
        }
        favorite = conn.execute(
            "SELECT path FROM favorites WHERE path LIKE ?", (f"{archive}/%",)
        ).fetchone()
        stats = conn.execute(
            "SELECT converted_count, saved_bytes FROM webp_stats WHERE id = 1"
        ).fetchone()
    assert folder == (1, 4)
    assert members == {
        f"{archive}/photo.webp",
        f"{archive}/notes.txt",
        f"{archive}/sub/photo2.webp",
        f"{archive}/already.webp",
    }
    assert favorite == (f"{archive}/photo.webp",)
    assert stats[0] == 2
    assert stats[1] == result["saved_bytes"]
    assert not zipfs.cache_dir_for(str(archive)).exists()


def test_skip_larger_keeps_the_original_member(zip_webp_env, monkeypatch):
    archive = zip_webp_env["archive"]
    monkeypatch.setenv("WEBP_SKIP_LARGER", "true")
    v2_config.reload_settings()
    # Force the rewritten bytes to be larger than the source image.
    monkeypatch.setattr(
        zip_webp, "_webp_bytes", lambda data, quality: b"w" * (len(data) + 100)
    )
    before = archive.read_bytes()

    result = zip_webp.optimize_archive(str(archive))
    assert result["changed"] is False
    assert result["converted"] == 0
    assert result["skipped"] == 2
    assert archive.read_bytes() == before
    with zipfile.ZipFile(archive) as bundle:
        assert "photo.png" in bundle.namelist()
        assert "sub/photo2.jpg" in bundle.namelist()


def test_archive_without_images_is_untouched(zip_webp_env):
    archive = zip_webp_env["library"] / "text.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("readme.txt", "nothing to convert")
    before = archive.read_bytes()
    result = zip_webp.optimize_archive(str(archive))
    assert result["changed"] is False
    assert result["members"] == 1
    assert archive.read_bytes() == before


def test_router_optimizes_and_validates(zip_webp_env):
    client = TestClient(create_app())
    response = client.post(
        "/api/zip/webp/optimize", json={"path": str(zip_webp_env["archive"])}
    )
    assert response.status_code == 200, response.text
    assert response.json()["converted"] == 2

    assert (
        client.post(
            "/api/zip/webp/optimize",
            json={"path": str(zip_webp_env["library"] / "missing.zip")},
        ).status_code
        == 404
    )
    assert client.post(
        "/api/zip/webp/optimize", json={"path": "/etc/passwd"}
    ).status_code in (400, 403, 404)
