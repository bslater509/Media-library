"""Tests verifying performance optimizations: consolidated status endpoint, GZip, caching, and indexes."""

import os

import pytest
from starlette.testclient import TestClient

from media_library.app_factory import create_app
from media_library.core.state import _file_stat_cache, get_thumbnail_cache_path, state
from media_library.db.connection import get_db
from media_library.db.schema import init_backup_db
from tests.conftest import patch_runtime_directories


@pytest.fixture
def perf_api_client(tmp_path, monkeypatch):
    db_file = tmp_path / "test_perf.db"
    dest_dir = tmp_path / "media_dest"
    source_dir = tmp_path / "media_src"
    thumb_dir = tmp_path / "thumbnails"
    dest_dir.mkdir()
    source_dir.mkdir()
    thumb_dir.mkdir()

    monkeypatch.setenv("DB_PATH", str(db_file))
    monkeypatch.setenv("LIBRARY_DIR", str(dest_dir))
    monkeypatch.setenv("INCOMING_DIR", str(source_dir))
    monkeypatch.setenv("THUMBNAIL_DIR", str(thumb_dir))

    patch_runtime_directories(monkeypatch, tmp_path, thumb_dir)

    state["source_dir"] = str(source_dir)
    state["destination_dir"] = str(dest_dir)

    init_backup_db(db_file)

    app = create_app(
        source_dir=str(source_dir),
        destination_dir=str(dest_dir),
        run_startup_workers=False,
    )

    with TestClient(app) as client:
        yield client, dest_dir, thumb_dir, db_file


def test_system_status_endpoint(perf_api_client):
    client, _, _, _ = perf_api_client
    resp = client.get("/api/system/status")
    assert resp.status_code == 200
    data = resp.json()

    # Verify all consolidated sections exist
    required_keys = [
        "is_active",
        "transfers",
        "transcode",
        "index",
        "thumbnails",
        "duplicate_hash",
        "thumb_hash",
        "webp",
        "library_backup",
        "zip",
        "batch_zip",
    ]
    for key in required_keys:
        assert key in data, f"Missing key '{key}' in consolidated system status"

    assert isinstance(data["is_active"], bool)
    assert "status" in data["index"]
    assert "total_files" in data["index"]


def test_composite_indexes_and_no_temp_btree(perf_api_client):
    _, _, _, db_file = perf_api_client
    conn = get_db(db_file)
    try:
        cur = conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
        indexes = [row[0] for row in cur.fetchall()]
        assert "idx_files_folder_lower_name" in indexes
        assert "idx_folders_parent_lower_name" in indexes

        # Verify EXPLAIN QUERY PLAN uses index without temporary btree
        explain_files = conn.execute(
            "EXPLAIN QUERY PLAN SELECT * FROM files WHERE folder_path = ? ORDER BY LOWER(name)",
            ("/test",),
        ).fetchall()
        explain_text = " ".join(row[3] for row in explain_files)
        assert "USE TEMP B-TREE FOR ORDER BY" not in explain_text
        assert "idx_files_folder_lower_name" in explain_text
    finally:
        conn.close()


def test_thumbnail_cache_headers_and_304(perf_api_client):
    client, dest_dir, _thumb_dir, _ = perf_api_client

    # Create dummy media file
    media_file = dest_dir / "sample_video.mp4"
    media_file.write_bytes(b"\x00" * 2048)

    # Pre-populate thumbnail file to simulate cached thumbnail
    thumb_path = get_thumbnail_cache_path(str(media_file), mtime=12345.0, size=2048)
    os.makedirs(os.path.dirname(thumb_path), exist_ok=True)
    with open(thumb_path, "wb") as f:
        f.write(b"\xff\xd8\xff\xe0" + b"THUMBNAIL_BYTES")

    # Request thumbnail with mtime and size params
    resp = client.get(
        "/api/backup/thumbnail",
        params={"path": str(media_file), "mtime": 12345.0, "size": 2048},
    )
    assert resp.status_code == 200
    assert "Cache-Control" in resp.headers
    assert "immutable" in resp.headers["Cache-Control"]
    assert "ETag" in resp.headers
    etag = resp.headers["ETag"]

    # Request again with If-None-Match to verify HTTP 304 Not Modified
    resp_304 = client.get(
        "/api/backup/thumbnail",
        params={"path": str(media_file), "mtime": 12345.0, "size": 2048},
        headers={"If-None-Match": etag},
    )
    assert resp_304.status_code == 304


def test_thumbnail_stat_cache():
    path = "/tmp/test_stat_cache_video.mp4"
    # Seed cache
    _file_stat_cache[path] = (54321.0, 99999)
    cached_path = get_thumbnail_cache_path(path)
    # Check that the hash is generated without stat call throwing FileNotFoundError
    assert cached_path is not None
    assert str(cached_path).endswith(".jpg")


def test_browse_thumb_urls_include_mtime_and_size(perf_api_client):
    client, dest_dir, _, _db_file = perf_api_client
    sub = dest_dir / "Subfolder"
    sub.mkdir()
    f = sub / "video.mp4"
    f.write_bytes(b"\x00" * 1024)

    from media_library.db.indexer import index_folder

    index_folder(str(sub))

    resp = client.get("/api/backup/browse", params={"path": str(sub)})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["files"]) == 1
    thumb_url = data["files"][0]["thumb_url"]
    assert "mtime=" in thumb_url
    assert "size=" in thumb_url
