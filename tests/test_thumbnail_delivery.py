"""Cached artwork must not depend on the remote original or block the loop."""

import asyncio
import sqlite3
import threading
from contextlib import closing

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from media_library import config
from media_library.app import create_app
from media_library.cache import cache_key
from media_library.config import get_settings
from media_library.library import _file_entry, _folder_entry, _thumb_url
from media_library.routers import media


@pytest.fixture(autouse=True)
def isolated_artwork(tmp_path, monkeypatch):
    monkeypatch.setenv("THUMBNAIL_DIR", str(tmp_path / "artwork"))
    config.reload_settings()


@pytest.mark.parametrize("animated", [False, True])
def test_cached_artwork_never_checks_source(monkeypatch, animated):
    settings = get_settings()
    key = cache_key("/unavailable/original.mp4", 123.0, 456)
    target = (
        settings.thumbnail_dir / "animated" / f"anim_{key}.webp"
        if animated
        else settings.thumbnail_dir / f"thumb_{key}.jpg"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"cached-artwork")

    def forbidden(*args, **kwargs):
        pytest.fail("A cache hit must not validate/read/generate the original")

    monkeypatch.setattr(media, "resolve_library_path", forbidden)
    monkeypatch.setattr(media, "_row_stat", forbidden)
    monkeypatch.setattr(media, "generate_thumbnail", forbidden)
    with TestClient(create_app()) as client:
        response = client.get(
            f"/api/thumbnails/cache/{key}",
            params={
                "path": "/unavailable/original.mp4",
                "kind": "animated" if animated else "static",
            },
        )
    assert response.status_code == 200
    assert response.content == b"cached-artwork"
    assert response.headers["content-type"] == (
        "image/webp" if animated else "image/jpeg"
    )
    assert response.headers["cache-control"] == "private, max-age=31536000, immutable"


@pytest.mark.parametrize("key", ["bad", "A" * 24, "a" * 23, "a" * 25, "." * 24])
def test_cache_key_cannot_address_arbitrary_files(key):
    with TestClient(create_app()) as client:
        response = client.get(
            f"/api/thumbnails/cache/{key}", params={"path": "/etc/passwd"}
        )
    assert response.status_code == 404


def test_cache_miss_preserves_path_security():
    with TestClient(create_app()) as client:
        response = client.get(
            f"/api/thumbnails/cache/{'a' * 24}", params={"path": "/etc/passwd"}
        )
    assert response.status_code == 403


def test_cache_miss_preserves_symlink_security(tmp_path):
    link = get_settings().library_dir / "escape"
    link.symlink_to(tmp_path)
    with TestClient(create_app()) as client:
        response = client.get(
            f"/api/thumbnails/cache/{'a' * 24}",
            params={"path": str(link / "image.jpg")},
        )
    assert response.status_code == 403


def test_cache_miss_regenerates_and_does_not_cache_stale_key(monkeypatch):
    settings = get_settings()
    source = settings.library_dir / "image.jpg"
    target = settings.thumbnail_dir / "regenerated.jpg"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"regenerated")
    monkeypatch.setattr(media, "_row_stat", lambda path: (2.0, 3, "image", False))
    calls = []

    def generate(path, kind, mtime, size):
        calls.append((path, kind, mtime, size))
        return target

    monkeypatch.setattr(media, "generate_thumbnail", generate)
    with TestClient(create_app()) as client:
        response = client.get(
            f"/api/thumbnails/cache/{'a' * 24}", params={"path": str(source)}
        )
    assert response.status_code == 200
    assert response.content == b"regenerated"
    assert calls == [(str(source), "image", 2.0, 3)]
    assert response.headers["cache-control"] == "private, no-cache"


def test_cache_route_requires_auth(monkeypatch):
    monkeypatch.setenv("APP_PASSWORD", "test-password")
    with TestClient(create_app()) as client:
        response = client.get(
            f"/api/thumbnails/cache/{'a' * 24}", params={"path": "ignored"}
        )
    assert response.status_code == 401


def test_remote_validation_does_not_block_event_loop(monkeypatch):
    async def exercise():
        entered = threading.Event()
        release = threading.Event()
        loop_thread = threading.get_ident()

        def validate(path):
            assert threading.get_ident() != loop_thread
            entered.set()
            assert release.wait(timeout=5)
            raise HTTPException(status_code=403)

        monkeypatch.setattr(media, "resolve_library_path", validate)
        task = asyncio.create_task(media.get_thumbnail("/remote/image.jpg"))
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            # This coroutine is still running while validation is blocked.
            assert not task.done()
        finally:
            release.set()
        with pytest.raises(HTTPException):
            await task

    asyncio.run(exercise())


def test_library_artwork_urls_include_cache_identity_and_poster_version():
    settings = get_settings()
    path = str(settings.library_dir / "video.mp4")
    with closing(sqlite3.connect(settings.db_path)) as conn, conn:
        conn.row_factory = sqlite3.Row
        conn.execute(
            "INSERT INTO files (path,name,folder_path,type,mtime,size,thumb_mtime) "
            "VALUES (?,?,?,?,?,?,?)",
            (path, "video.mp4", str(settings.library_dir), "video", 123.5, 456, 789.0),
        )
        row = conn.execute("SELECT * FROM files").fetchone()
    entry = _file_entry(row)
    key = cache_key(path, 123.5, 456)
    assert f"/cache/{key}?" in entry["thumb_url"]
    assert entry["thumb_url"].endswith("&v=789")
    assert f"/cache/{key}?" in entry["animated_url"]
    assert entry["animated_url"].endswith("&kind=animated")
    folder = _folder_entry(
        {
            "path": str(settings.library_dir),
            "name": "library",
            "file_count": 1,
            "subfolder_count": 0,
            "is_zip": 0,
            "total_size": 456,
            "cover_path": path,
            "cover_mtime": 123.5,
            "cover_size": 456,
            "cover_thumb_mtime": 789.0,
            "cover_w": 360,
            "cover_h": 240,
        }
    )
    assert folder["cover_thumb_url"] == entry["thumb_url"]
    assert _thumb_url(path, None, 124.0, 456) != entry["thumb_url"]
