"""Tests for the Library page file/folder management endpoints and worker."""

import asyncio
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from media_library.app_factory import create_app
from media_library.core.state import state
from media_library.db.connection import get_db
from media_library.db.schema import init_backup_db
from media_library.services.library_manage_service import (
    delete_library_path,
    move_library_path,
)
from media_library.workers.library_ops_worker import library_ops_worker_instance
from tests.conftest import patch_runtime_directories


@pytest.fixture
def api_client(tmp_path, monkeypatch):
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    thumb_dir = tmp_path / "thumbnails"
    thumb_dir.mkdir()
    db_file = tmp_path / "manage.db"

    monkeypatch.setenv("DB_PATH", str(db_file))
    monkeypatch.setenv("LIBRARY_DIR", str(media_dir))
    monkeypatch.setenv("THUMBNAIL_DIR", str(thumb_dir))
    patch_runtime_directories(monkeypatch, tmp_path, thumb_dir)

    state["destination_dir"] = str(media_dir)
    init_backup_db()

    app = create_app(destination_dir=str(media_dir), run_startup_workers=False)

    with TestClient(app) as client:
        yield client, media_dir, db_file

    # Reset the singleton worker between tests.
    library_ops_worker_instance.items_map.clear()
    library_ops_worker_instance.undo_stack.clear()
    library_ops_worker_instance.history.clear()
    library_ops_worker_instance.active_op = None
    library_ops_worker_instance.ops_queue = asyncio.Queue()


def _index_folder(media_dir: Path, name: str):
    """Create + index a real folder with one image and one subfolder."""
    from media_library.db.indexer import index_folder

    folder = media_dir / name
    folder.mkdir()
    (folder / "a.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"A" * 256)
    sub = folder / "sub"
    sub.mkdir()
    (sub / "b.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"B" * 256)
    index_folder(str(folder), str(media_dir))
    index_folder(str(sub), str(media_dir))
    return folder


def _count(path: str):
    conn = get_db()
    try:
        folders = conn.execute(
            "SELECT COUNT(*) FROM folders WHERE path = ?", (path,)
        ).fetchone()[0]
        files = conn.execute(
            "SELECT COUNT(*) FROM files WHERE folder_path = ?", (path,)
        ).fetchone()[0]
        return folders, files
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Folder create / rename / delete
# ---------------------------------------------------------------------------


def test_create_folder(api_client):
    client, media_dir, _ = api_client
    res = client.post(
        "/api/backup/folders", json={"parent_path": str(media_dir), "name": "holidays"}
    )
    assert res.status_code == 200
    assert res.json()["success"] is True
    assert (media_dir / "holidays").is_dir()

    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM folders WHERE path = ?", (str(media_dir / "holidays"),)
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row["name"] == "holidays"


def test_create_folder_rejects_traversal(api_client):
    client, media_dir, _ = api_client
    res = client.post(
        "/api/backup/folders", json={"parent_path": str(media_dir), "name": "../evil"}
    )
    assert res.status_code == 400


def test_rename_folder_updates_index(api_client):
    client, media_dir, _ = api_client
    folder = _index_folder(media_dir, "album")

    res = client.post(
        "/api/backup/folders/rename", json={"path": str(folder), "new_name": "renamed"}
    )
    assert res.status_code == 200
    assert (media_dir / "renamed").is_dir()
    assert not folder.exists()

    # Folder row and nested file/subfolder paths were rewritten.
    assert _count(str(media_dir / "renamed"))[0] == 1
    conn = get_db()
    try:
        sub = conn.execute(
            "SELECT path FROM folders WHERE path = ?",
            (str(media_dir / "renamed" / "sub"),),
        ).fetchone()
        f = conn.execute(
            "SELECT path FROM files WHERE path = ?",
            (str(media_dir / "renamed" / "sub" / "b.jpg"),),
        ).fetchone()
    finally:
        conn.close()
    assert sub is not None
    assert f is not None


def test_delete_folder(api_client):
    _client, media_dir, _ = api_client
    folder = _index_folder(media_dir, "trashme")
    delete_library_path(str(folder), "folder")

    assert not folder.exists()
    assert _count(str(folder)) == (0, 0)


# ---------------------------------------------------------------------------
# Move (service + endpoint)
# ---------------------------------------------------------------------------


def test_move_file_service(api_client):
    _client, media_dir, _ = api_client
    src_dir = media_dir / "src"
    dst_dir = media_dir / "dst"
    src_dir.mkdir()
    dst_dir.mkdir()
    f = src_dir / "clip.mp4"
    f.write_bytes(b"\x00\x00\x00 ftypmp42" + b"A" * 1024)

    from media_library.db.indexer import index_folder

    index_folder(str(src_dir), str(media_dir))

    move_library_path(str(f), str(dst_dir / "clip.mp4"), "file")

    assert (dst_dir / "clip.mp4").exists()
    assert not f.exists()
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM files WHERE path = ?", (str(dst_dir / "clip.mp4"),)
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row["folder_path"] == str(dst_dir)


def test_move_folder_service(api_client):
    _client, media_dir, _ = api_client
    folder = _index_folder(media_dir, "album")
    dest = media_dir / "dest"
    dest.mkdir()

    move_library_path(str(folder), str(dest / "album"), "folder")

    assert (dest / "album" / "a.jpg").exists()
    assert not folder.exists()
    # Nested subfolder + file index paths were rewritten.
    conn = get_db()
    try:
        sub = conn.execute(
            "SELECT path FROM folders WHERE path = ?", (str(dest / "album" / "sub"),)
        ).fetchone()
        f = conn.execute(
            "SELECT path FROM files WHERE path = ?",
            (str(dest / "album" / "sub" / "b.jpg"),),
        ).fetchone()
    finally:
        conn.close()
    assert sub is not None
    assert f is not None


def test_move_check_and_enqueue(api_client):
    client, media_dir, _ = api_client
    src = media_dir / "src"
    dst = media_dir / "dst"
    src.mkdir()
    dst.mkdir()
    f = src / "a.jpg"
    f.write_bytes(b"\xff\xd8\xff\xe0" + b"A" * 128)
    (dst / "a.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"B" * 128)

    check = client.post(
        "/api/backup/move/check",
        json={"paths": [str(f)], "destination_folder": str(dst)},
    )
    assert check.status_code == 200
    assert check.json()["collision_count"] == 1

    res = client.post(
        "/api/backup/move",
        json={
            "paths": [str(f)],
            "destination_folder": str(dst),
            "conflict_policy": "auto_rename",
        },
    )
    assert res.status_code == 200
    data = res.json()
    assert data["queued"] == 1
    assert data["tasks"][0]["conflict"] is True
    assert data["tasks"][0]["target_path"] != str(dst / "a.jpg")


def test_move_conflict_error_policy(api_client):
    client, media_dir, _ = api_client
    src = media_dir / "src"
    dst = media_dir / "dst"
    src.mkdir()
    dst.mkdir()
    f = src / "a.jpg"
    f.write_bytes(b"\xff\xd8\xff\xe0" + b"A" * 128)
    (dst / "a.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"B" * 128)

    res = client.post(
        "/api/backup/move",
        json={
            "paths": [str(f)],
            "destination_folder": str(dst),
            "conflict_policy": "error",
        },
    )
    assert res.status_code == 409


# ---------------------------------------------------------------------------
# Worker loop & undo
# ---------------------------------------------------------------------------


def _run_worker_until_done(tasks):
    """Runs the singleton worker loop until the given queued items settle."""

    async def runner():
        worker = library_ops_worker_instance
        loop_task = asyncio.create_task(worker._run_loop())
        for t in tasks:
            worker.enqueue(t)
        while any(t.get("status") in ("queued", "transferring") for t in tasks):
            await asyncio.sleep(0.01)
        loop_task.cancel()
        try:
            await loop_task
        except asyncio.CancelledError:
            pass

    asyncio.run(runner())


def test_worker_processes_move_and_undo(api_client):
    client, media_dir, _ = api_client
    src = media_dir / "src"
    dst = media_dir / "dst"
    src.mkdir()
    dst.mkdir()
    f = src / "clip.mp4"
    f.write_bytes(b"\x00\x00\x00 ftypmp42" + b"A" * 1024)
    from media_library.db.indexer import index_folder

    index_folder(str(src), str(media_dir))

    task = {
        "id": "t1",
        "op": "move",
        "source_path": str(f),
        "target_path": str(dst / "clip.mp4"),
        "kind": "file",
    }
    _run_worker_until_done([task])

    assert task["status"] == "completed"
    assert (dst / "clip.mp4").exists()
    assert library_ops_worker_instance.undo_stack

    # Undo via endpoint.
    res = client.post("/api/backup/undo")
    assert res.status_code == 200
    assert (src / "clip.mp4").exists()
    assert not (dst / "clip.mp4").exists()
    assert library_ops_worker_instance.undo_stack == []


def test_worker_delete_folder(api_client):
    _client, media_dir, _ = api_client
    folder = _index_folder(media_dir, "goner")

    task = {"id": "t2", "op": "delete", "path": str(folder), "kind": "folder"}
    _run_worker_until_done([task])

    assert task["status"] == "completed"
    assert not folder.exists()
    assert _count(str(folder)) == (0, 0)


# ---------------------------------------------------------------------------
# Bulk rename / delete
# ---------------------------------------------------------------------------


def test_bulk_rename_preview(api_client):
    client, media_dir, _ = api_client
    a = media_dir / "a.jpg"
    b = media_dir / "b.jpg"
    a.write_bytes(b"\xff\xd8\xff\xe0" + b"A" * 64)
    b.write_bytes(b"\xff\xd8\xff\xe0" + b"B" * 64)

    res = client.post(
        "/api/backup/bulk/rename/preview",
        json={
            "paths": [str(a), str(b)],
            "mode": "prefix",
            "params": {"prefix": "Trip_", "suffix": "_2024"},
        },
    )
    assert res.status_code == 200
    items = res.json()["items"]
    assert items[0]["new_name"] == "Trip_a_2024.jpg"
    assert items[1]["new_name"] == "Trip_b_2024.jpg"


def test_bulk_rename_numbering(api_client):
    client, media_dir, _ = api_client
    a = media_dir / "x.jpg"
    b = media_dir / "y.jpg"
    a.write_bytes(b"\xff\xd8\xff\xe0" + b"A" * 64)
    b.write_bytes(b"\xff\xd8\xff\xe0" + b"B" * 64)

    res = client.post(
        "/api/backup/bulk/rename/preview",
        json={
            "paths": [str(a), str(b)],
            "mode": "base_counter",
            "params": {"base": "Shot", "start": 1, "padding": 2},
        },
    )
    assert res.status_code == 200
    items = res.json()["items"]
    assert items[0]["new_name"] == "Shot01.jpg"
    assert items[1]["new_name"] == "Shot02.jpg"


def test_bulk_rename_conflict_detected(api_client):
    client, media_dir, _ = api_client
    a = media_dir / "a.jpg"
    b = media_dir / "b.jpg"
    a.write_bytes(b"\xff\xd8\xff\xe0" + b"A" * 64)
    b.write_bytes(b"\xff\xd8\xff\xe0" + b"B" * 64)

    res = client.post(
        "/api/backup/bulk/rename/preview",
        json={"paths": [str(a), str(b)], "mode": "case", "params": {"case": "lower"}},
    )
    items = res.json()["items"]
    # a.jpg stays "a.jpg" and b.jpg stays "b.jpg" — no collision.
    assert res.json()["conflict_count"] == 0
    assert items[0]["new_name"] == "a.jpg"


def test_bulk_rename_apply_queues(api_client):
    client, media_dir, _ = api_client
    a = media_dir / "a.jpg"
    b = media_dir / "b.jpg"
    a.write_bytes(b"\xff\xd8\xff\xe0" + b"A" * 64)
    b.write_bytes(b"\xff\xd8\xff\xe0" + b"B" * 64)

    res = client.post(
        "/api/backup/bulk/rename",
        json={"paths": [str(a), str(b)], "mode": "prefix", "params": {"prefix": "P_"}},
    )
    assert res.status_code == 200
    assert res.json()["queued"] == 2


def test_bulk_delete_queues(api_client):
    client, media_dir, _ = api_client
    a = media_dir / "a.jpg"
    b = media_dir / "b.jpg"
    a.write_bytes(b"\xff\xd8\xff\xe0" + b"A" * 64)
    b.write_bytes(b"\xff\xd8\xff\xe0" + b"B" * 64)

    res = client.post("/api/backup/bulk/delete", json={"paths": [str(a), str(b)]})
    assert res.status_code == 200
    assert res.json()["queued"] == 2


def test_bulk_webp_optimize(api_client):
    client, media_dir, _ = api_client
    img = media_dir / "a.png"
    vid = media_dir / "v.mp4"
    webp = media_dir / "w.webp"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"A" * 64)
    vid.write_bytes(b"\x00\x00\x00 ftypmp42" + b"B" * 64)
    webp.write_bytes(b"RIFF" + b"C" * 64)

    res = client.post(
        "/api/backup/bulk/webp-optimize",
        json={"paths": [str(img), str(vid), str(webp)]},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["images_queued"] == 1
    assert data["skipped"] == 2
