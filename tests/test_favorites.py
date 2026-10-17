"""Tests for Favorites & Bookmarking System."""

import time

from starlette.testclient import TestClient

from media_library.db.connection import db_conn
from media_library.db.indexer_files import delete_path_from_index, move_path_in_index
from media_library.services.library_query import _read_indexed_files
from media_library.services.media_service import do_transfer


def _insert_indexed_file(
    conn, path, name, folder_path, ftype="video", ext=".mp4", size=1024
):
    conn.execute(
        """
        INSERT OR REPLACE INTO files (path, name, folder_path, type, ext, size, size_formatted, mtime, needs_transcode, has_thumb)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (path, name, folder_path, ftype, ext, size, f"{size} B", time.time(), False, 0),
    )


def test_favorites_table_schema(sandbox_env):
    """Verify favorites table and index exist."""
    with db_conn() as conn:
        tables = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        ]
        assert "favorites" in tables
        indices = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            ).fetchall()
        ]
        assert "idx_favorites_created_at" in indices


def test_favorites_toggle_endpoint(api_client: TestClient):
    """Test POST /api/backup/favorites/toggle."""
    test_path = "/media/action/clip1.mp4"

    # Initially not favorite
    res = api_client.get("/api/backup/favorites/status", params={"path": test_path})
    assert res.status_code == 200
    assert res.json()["is_favorite"] is False

    # Toggle ON
    res = api_client.post("/api/backup/favorites/toggle", json={"path": test_path})
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert data["is_favorite"] is True
    assert data["path"] == test_path

    # Check status again
    res = api_client.get("/api/backup/favorites/status", params={"path": test_path})
    assert res.status_code == 200
    assert res.json()["is_favorite"] is True

    # Toggle OFF
    res = api_client.post("/api/backup/favorites/toggle", json={"path": test_path})
    assert res.status_code == 200
    assert res.json()["is_favorite"] is False

    # Error handling on empty path
    res = api_client.post("/api/backup/favorites/toggle", json={"path": ""})
    assert res.status_code == 400


def test_favorites_batch_endpoint(api_client: TestClient):
    """Test POST /api/backup/favorites/batch."""
    paths = ["/media/action/c1.mp4", "/media/action/c2.mp4", "/media/action/c3.mp4"]

    # Batch add
    res = api_client.post(
        "/api/backup/favorites/batch", json={"paths": paths, "action": "add"}
    )
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert data["action"] == "add"
    assert data["updated"] == 3

    # Check all are favorites
    for p in paths:
        res = api_client.get("/api/backup/favorites/status", params={"path": p})
        assert res.json()["is_favorite"] is True

    # Batch remove
    res = api_client.post(
        "/api/backup/favorites/batch",
        json={"paths": [paths[0], paths[1]], "action": "remove"},
    )
    assert res.status_code == 200
    assert res.json()["updated"] == 2

    assert (
        api_client.get(
            "/api/backup/favorites/status", params={"path": paths[0]}
        ).json()["is_favorite"]
        is False
    )
    assert (
        api_client.get(
            "/api/backup/favorites/status", params={"path": paths[1]}
        ).json()["is_favorite"]
        is False
    )
    assert (
        api_client.get(
            "/api/backup/favorites/status", params={"path": paths[2]}
        ).json()["is_favorite"]
        is True
    )

    # Batch toggle
    res = api_client.post(
        "/api/backup/favorites/batch", json={"paths": paths, "action": "toggle"}
    )
    assert res.status_code == 200
    assert (
        api_client.get(
            "/api/backup/favorites/status", params={"path": paths[0]}
        ).json()["is_favorite"]
        is True
    )
    assert (
        api_client.get(
            "/api/backup/favorites/status", params={"path": paths[1]}
        ).json()["is_favorite"]
        is True
    )
    assert (
        api_client.get(
            "/api/backup/favorites/status", params={"path": paths[2]}
        ).json()["is_favorite"]
        is False
    )

    # Empty paths
    res = api_client.post(
        "/api/backup/favorites/batch", json={"paths": [], "action": "add"}
    )
    assert res.status_code == 200
    assert res.json()["updated"] == 0


def test_favorites_list_and_media_type_filter(api_client: TestClient):
    """Test GET /api/backup/favorites with media_type filter."""
    vid_path = "/media/action/hero.mp4"
    img_path = "/media/action/hero.jpg"

    with db_conn() as conn, conn:
        _insert_indexed_file(
            conn, vid_path, "hero.mp4", "/media/action", "video", ".mp4", 10240
        )
        _insert_indexed_file(
            conn, img_path, "hero.jpg", "/media/action", "image", ".jpg", 5120
        )

    # Add both to favorites
    api_client.post("/api/backup/favorites/toggle", json={"path": vid_path})
    api_client.post("/api/backup/favorites/toggle", json={"path": img_path})

    # List all favorites
    res = api_client.get("/api/backup/favorites")
    assert res.status_code == 200
    data = res.json()
    assert data["counts"]["all"] == 2
    paths = [f["path"] for f in data["files"]]
    assert vid_path in paths
    assert img_path in paths
    assert all(f["is_favorite"] is True for f in data["files"])

    # Filter videos only
    res_v = api_client.get("/api/backup/favorites", params={"type": "video"})
    assert res_v.status_code == 200
    v_files = res_v.json()["files"]
    assert len(v_files) == 1
    assert v_files[0]["path"] == vid_path

    # Filter images only
    res_i = api_client.get("/api/backup/favorites", params={"type": "image"})
    assert res_i.status_code == 200
    i_files = res_i.json()["files"]
    assert len(i_files) == 1
    assert i_files[0]["path"] == img_path


def test_library_query_joins_is_favorite(sandbox_env):
    """Verify _read_indexed_files attaches is_favorite boolean."""
    folder = "/media/scifi"
    file1 = f"{folder}/movie1.mp4"
    file2 = f"{folder}/movie2.mp4"

    with db_conn() as conn, conn:
        _insert_indexed_file(conn, file1, "movie1.mp4", folder, "video", ".mp4", 1000)
        _insert_indexed_file(conn, file2, "movie2.mp4", folder, "video", ".mp4", 2000)
        conn.execute(
            "INSERT INTO favorites (path, created_at) VALUES (?, ?)",
            (file1, time.time()),
        )

    with db_conn() as conn:
        files = _read_indexed_files(conn, folder)
        f_map = {f["path"]: f["is_favorite"] for f in files}
        assert f_map[file1] is True
        assert f_map[file2] is False


def test_indexer_move_and_delete_hooks(sandbox_env):
    """Verify favorites table stays in sync when files and folders are moved or deleted."""
    old_file = "/media/docs/report.mp4"
    new_file = "/media/archive/report_2026.mp4"
    folder_file = "/media/series/s1/ep1.mp4"

    with db_conn() as conn, conn:
        _insert_indexed_file(
            conn, old_file, "report.mp4", "/media/docs", "video", ".mp4", 500
        )
        _insert_indexed_file(
            conn, folder_file, "ep1.mp4", "/media/series/s1", "video", ".mp4", 800
        )
        conn.execute(
            "INSERT INTO favorites (path, created_at) VALUES (?, ?)",
            (old_file, time.time()),
        )
        conn.execute(
            "INSERT INTO favorites (path, created_at) VALUES (?, ?)",
            (folder_file, time.time()),
        )

    # Move single file
    move_path_in_index(old_file, new_file, "file")
    with db_conn() as conn:
        fav_paths = {
            r[0] for r in conn.execute("SELECT path FROM favorites").fetchall()
        }
        assert old_file not in fav_paths
        assert new_file in fav_paths

    # Move folder
    move_path_in_index("/media/series", "/media/tv", "folder")
    with db_conn() as conn:
        fav_paths = {
            r[0] for r in conn.execute("SELECT path FROM favorites").fetchall()
        }
        assert folder_file not in fav_paths
        assert "/media/tv/s1/ep1.mp4" in fav_paths

    # Delete path
    delete_path_from_index(new_file, "file")
    with db_conn() as conn:
        fav_paths = {
            r[0] for r in conn.execute("SELECT path FROM favorites").fetchall()
        }
        assert new_file not in fav_paths


def test_sorter_do_transfer_preserves_favorite(tmp_path, sandbox_env):
    """Verify do_transfer automatically inserts into favorites when item['is_favorite'] is True."""
    src = tmp_path / "incoming.mp4"
    src.write_bytes(b"DATA123")
    dest_dir = tmp_path / "dest"
    dest_dir.mkdir()
    dest_file = dest_dir / "incoming.mp4"

    item = {
        "id": "t1",
        "source_path": str(src),
        "target_path": str(dest_file),
        "filename": "incoming.mp4",
        "is_favorite": True,
    }

    do_transfer(item)

    assert dest_file.exists()
    with db_conn() as conn:
        row = conn.execute(
            "SELECT path FROM favorites WHERE path = ?", (str(dest_file),)
        ).fetchone()
        assert row is not None
        assert row[0] == str(dest_file)
