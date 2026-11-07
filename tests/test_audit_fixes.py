"""Regression tests for the code-audit fixes.

Covers: the sorter's symlink-escape rejection, non-ASCII password handling,
favorites pagination, /system/index/sync's 409 on a concurrent crawl, RFC 5987
attachment headers, the truncated-staging temp-file cleanup and the streaming
archive WebP rewrite."""

from __future__ import annotations

import os
import sqlite3
import zipfile

import pytest
from fastapi.testclient import TestClient

from media_library import auth, indexer, library, sorter, writes, zip_webp
from media_library import config as v2_config
from media_library.app import create_app
from media_library.routers.media import _attachment_disposition

# --- auth ----------------------------------------------------------------


def test_password_compare_handles_non_ascii(monkeypatch):
    """Non-ASCII input must read as a wrong password, not raise TypeError."""
    monkeypatch.setenv("APP_PASSWORD", "secret")
    assert auth.check_password("sécret") is False
    assert auth.check_password("secret") is True
    assert auth.check_password("") is False


# --- sorter path validation ----------------------------------------------


@pytest.fixture
def incoming_env(tmp_path, monkeypatch):
    incoming = tmp_path / "incoming"
    incoming.mkdir()
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    (secrets / "passwd").write_text("root:x:0")
    monkeypatch.setenv("LIBRARY_DIR", str(tmp_path / "library"))
    monkeypatch.setenv("SOURCE_DIR", str(incoming))
    monkeypatch.setenv("SORTER_STATE", str(tmp_path / "sorter.json"))
    monkeypatch.setenv("THUMBNAIL_DIR", str(tmp_path / "thumbs"))
    monkeypatch.setenv("WRITES", "true")
    monkeypatch.setenv("DB_READ_ONLY", "false")
    v2_config.reload_settings()
    return {"incoming": incoming, "secrets": secrets}


def test_source_file_rejects_symlink_escaping_incoming(incoming_env):
    """A symlink inside incoming may not reach a file outside it."""
    target = incoming_env["secrets"] / "passwd"
    link = incoming_env["incoming"] / "escape.mkv"
    os.symlink(target, link)
    with pytest.raises(writes.WriteError) as caught:
        sorter.resolve_source_file(str(link))
    assert caught.value.status == 403


def test_source_file_accepts_regular_incoming_file(incoming_env):
    video = incoming_env["incoming"] / "clip.mkv"
    video.write_bytes(b"0" * 64)
    assert sorter.resolve_source_file(str(video)) == str(video)


# --- favorites pagination ------------------------------------------------


def _seed_favorite(path: str, name: str, folder: str, created: float) -> None:
    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute(
            "INSERT INTO files (path, name, folder_path, type, ext, size, mtime) "
            "VALUES (?, ?, ?, 'video', '.mp4', 1, ?)",
            (path, name, folder, 1_700_000_000.0),
        )
        conn.execute(
            "INSERT INTO favorites (path, created_at) VALUES (?, ?)",
            (path, created),
        )


def test_favorites_supports_limit_offset_total(tmp_path, monkeypatch):
    library_dir = tmp_path / "lib"
    folder = library_dir / "f"
    folder.mkdir(parents=True)
    monkeypatch.setenv("LIBRARY_DIR", str(library_dir))
    v2_config.reload_settings()
    for index in range(5):
        path = str(folder / f"v{index}.mp4")
        _seed_favorite(path, f"v{index}.mp4", str(folder), 1_700_000_000 + index)

    page = library.favorites(limit=2)
    assert page["total"] == 5
    assert len(page["files"]) == 2
    assert page["offset"] == 0
    second = library.favorites(limit=2, offset=2)
    assert second["offset"] == 2
    assert {f["path"] for f in page["files"]}.isdisjoint(
        {f["path"] for f in second["files"]}
    )
    assert len(library.favorites()["files"]) == 5


def test_favorites_endpoint_paginates(tmp_path, monkeypatch):
    library_dir = tmp_path / "lib"
    folder = library_dir / "f"
    folder.mkdir(parents=True)
    monkeypatch.setenv("LIBRARY_DIR", str(library_dir))
    v2_config.reload_settings()
    for index in range(3):
        _seed_favorite(
            str(folder / f"v{index}.mp4"), f"v{index}.mp4", str(folder), index
        )
    client = TestClient(create_app())
    response = client.get("/api/library/favorites", params={"limit": 2})
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 3
    assert len(payload["files"]) == 2


# --- index/sync 409 ------------------------------------------------------


def test_index_sync_conflicts_while_crawling(monkeypatch):
    monkeypatch.setenv("OWN_WORKERS", "true")
    v2_config.reload_settings()
    client = TestClient(create_app())
    if not indexer._crawl_lock.acquire(blocking=False):
        pytest.skip("crawl lock unexpectedly busy")
    try:
        response = client.post("/api/system/index/sync")
        assert response.status_code == 409
        assert response.json()["detail"] == "A crawl is already running"
    finally:
        indexer._crawl_lock.release()


# --- headers -------------------------------------------------------------


def test_attachment_disposition_escapes_quotes_and_control_chars():
    header = _attachment_disposition('we"ird\nname.mp4')
    assert "\n" not in header
    assert 'filename="we_ird_name.mp4"' in header
    header = _attachment_disposition("café.mp4")
    assert "filename*=UTF-8''caf%C3%A9.mp4" in header
    assert 'filename="caf__pending.mp4"' not in header


# --- upload staging cleanup ---------------------------------------------


def test_finalize_upload_cleans_temp_file_when_copy_truncates(tmp_path, monkeypatch):
    library_dir = tmp_path / "library" / "sub"
    library_dir.mkdir(parents=True)
    monkeypatch.setenv("LIBRARY_DIR", str(tmp_path / "library"))
    monkeypatch.setenv("UPLOAD_STAGING_DIR", str(tmp_path / "upload_staging"))
    v2_config.reload_settings()
    from media_library.config import get_settings

    staging_dir = get_settings().upload_staging_dir
    staging_dir.mkdir(parents=True, exist_ok=True)
    library_dir.mkdir(parents=True, exist_ok=True)
    staged = staging_dir / "uabcdef12345.part"
    staged.write_bytes(b"x" * 10)

    def truncated_copy(src, dst):
        with open(dst, "wb") as handle:
            handle.write(b"x" * 4)

    monkeypatch.setattr(writes.shutil, "copyfile", truncated_copy)
    with pytest.raises(writes.WriteError) as caught:
        writes.finalize_upload("uabcdef12345", "clip.mp4", str(library_dir), 10)
    assert caught.value.status == 500
    assert not list(library_dir.glob(".mllibtmp-*"))


# --- zip_webp streaming conversion --------------------------------------


def test_zip_webp_streaming_members_are_copied_and_converted(tmp_path, monkeypatch):
    """skip_larger is honoured when converting through the streaming path."""
    library_dir = tmp_path / "library" / "zips"
    library_dir.mkdir(parents=True)
    monkeypatch.setenv("LIBRARY_DIR", str(tmp_path / "library"))
    v2_config.reload_settings()
    archive = library_dir / "a.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("photo.png", os.urandom(64))
        bundle.writestr("notes.txt", b"keep me")

    seen: list[str] = []

    def fake_stream(member, quality):
        seen.append(member.name)
        return b"w" * (1024 * 1024)

    monkeypatch.setattr(zip_webp, "_webp_bytes_stream", fake_stream)
    monkeypatch.setenv("WEBP_SKIP_LARGER", "true")
    v2_config.reload_settings()
    result = zip_webp.optimize_archive(str(archive))
    assert result["changed"] is False
    assert result["skipped"] == 1
    assert seen == ["photo.png"]
    with zipfile.ZipFile(archive) as bundle:
        assert bundle.namelist() == ["photo.png", "notes.txt"]


# --- real directory named *.zip ------------------------------------------


def test_is_zip_member_consults_the_index(tmp_path, monkeypatch):
    """A real directory named foo.zip is a folder, not a virtual archive."""
    library_dir = tmp_path / "library"
    real_dir = library_dir / "album.zip"
    real_dir.mkdir(parents=True)
    (real_dir / "photo.jpg").write_bytes(b"\xff\xd8\xe0" + b"0" * 32)
    archive = library_dir / "true.zip"
    archive.write_bytes(b"PK\x05\x06" + b"\x00" * 18)
    monkeypatch.setenv("LIBRARY_DIR", str(library_dir))
    v2_config.reload_settings()

    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO folders (path, name, parent_path, last_scanned, is_zip) "
            "VALUES (?, ?, ?, 0, 0)",
            (str(real_dir), "album.zip", str(library_dir)),
        )
        conn.execute(
            "INSERT INTO folders (path, name, parent_path, last_scanned, is_zip) "
            "VALUES (?, ?, ?, 0, 1)",
            (str(archive), "true.zip", str(library_dir)),
        )
        conn.execute(
            "INSERT INTO files (path, name, folder_path, type, ext, size, mtime) "
            "VALUES (?, ?, ?, 'image', '.jpg', 35, 0)",
            (str(real_dir / "photo.jpg"), "photo.jpg", str(real_dir)),
        )

    from media_library.paths import is_zip_member

    assert is_zip_member(str(real_dir / "photo.jpg")) is False
    assert is_zip_member(str(archive) + "/inner.jpg") is True
    # No row for the component: the legacy string contract still decides.
    assert is_zip_member(str(library_dir / "unknown.zip" / "x.jpg")) is True


def test_media_router_serves_members_of_real_zip_named_dirs(tmp_path, monkeypatch):
    """A file inside a real directory named *.zip streams like any file."""
    library_dir = tmp_path / "library"
    real_dir = library_dir / "album.zip"
    real_dir.mkdir(parents=True)
    photo = real_dir / "photo.jpg"
    photo.write_bytes(b"\xff\xd8\xe0" + b"0" * 64)
    monkeypatch.setenv("LIBRARY_DIR", str(library_dir))
    v2_config.reload_settings()
    db_path = os.environ["DB_PATH"]
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO folders (path, name, parent_path, last_scanned, is_zip) "
            "VALUES (?, ?, ?, 0, 0)",
            (str(real_dir), "album.zip", str(library_dir)),
        )
        conn.execute(
            "INSERT INTO files (path, name, folder_path, type, ext, size, mtime) "
            "VALUES (?, ?, ?, 'image', '.jpg', 67, 0)",
            (str(photo), "photo.jpg", str(real_dir)),
        )
    client = TestClient(create_app())
    response = client.get("/api/media", params={"path": str(photo)})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/jpeg")


# --- HEIC/HEIF opener ----------------------------------------------------


def test_register_heif_supports_declaration():
    """pillow-heif is a listed dependency; its opener must be wired."""
    pytest.importorskip("pillow_heif")
    from PIL import Image

    from media_library.pillow_init import register_heif

    register_heif()
    register_heif()  # idempotent, no double registration side effects
    Image.init()
    assert "HEIF" in Image.SAVE or "HEIF" in Image.OPEN
