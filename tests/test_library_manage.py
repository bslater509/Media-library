"""Tests for the Library page file/folder management endpoints and worker."""

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

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


# ---------------------------------------------------------------------------
# Delete Empty Folders (service & endpoints)
# ---------------------------------------------------------------------------


def test_find_empty_folders_service(api_client):
    from media_library.db.indexer import index_folder
    from media_library.services.library_manage_service import find_empty_folders

    _client, media_dir, _ = api_client

    # 1. Truly empty folder
    empty1 = media_dir / "empty1"
    empty1.mkdir()
    index_folder(str(empty1), str(media_dir))

    # 2. Nested empty folders
    empty_parent = media_dir / "empty_parent"
    empty_parent.mkdir()
    empty_child = empty_parent / "empty_child"
    empty_child.mkdir()
    index_folder(str(empty_parent), str(media_dir))
    index_folder(str(empty_child), str(media_dir))

    # 3. Non-empty folder with media file
    not_empty = media_dir / "not_empty"
    not_empty.mkdir()
    (not_empty / "pic.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"P" * 64)
    index_folder(str(not_empty), str(media_dir))

    # 4. Folder with non-empty child
    parent_of_full = media_dir / "parent_of_full"
    parent_of_full.mkdir()
    child_full = parent_of_full / "child_full"
    child_full.mkdir()
    (child_full / "vid.mp4").write_bytes(b"\x00\x00\x00 ftypmp42" + b"V" * 64)
    index_folder(str(parent_of_full), str(media_dir))
    index_folder(str(child_full), str(media_dir))

    # 5. Folder with non-media file on disk (e.g. notes.txt)
    txt_folder = media_dir / "txt_folder"
    txt_folder.mkdir()
    (txt_folder / "notes.txt").write_text("important user note")
    index_folder(str(txt_folder), str(media_dir))

    res = find_empty_folders(str(media_dir))
    candidate_paths = [c["path"] for c in res["candidates"]]

    # empty1, empty_child, and empty_parent should be candidates
    assert str(empty1) in candidate_paths
    assert str(empty_child) in candidate_paths
    assert str(empty_parent) in candidate_paths

    # not_empty, parent_of_full, child_full, and txt_folder must NOT be candidates
    assert str(not_empty) not in candidate_paths
    assert str(parent_of_full) not in candidate_paths
    assert str(child_full) not in candidate_paths
    assert str(txt_folder) not in candidate_paths

    # Verify bottom-up sorting: empty_child comes before empty_parent
    assert candidate_paths.index(str(empty_child)) < candidate_paths.index(str(empty_parent))


def test_delete_empty_folder_service(api_client):
    from media_library.db.indexer import index_folder
    from media_library.services.library_manage_service import delete_empty_folder

    _client, media_dir, _ = api_client

    folder = media_dir / "clean_me"
    folder.mkdir()
    index_folder(str(folder), str(media_dir))

    # Deleting an empty folder succeeds
    result = delete_empty_folder(str(folder), base_path=str(media_dir))
    assert result["deleted_path"] == str(folder)
    assert not folder.exists()
    assert _count(str(folder)) == (0, 0)

    # Deleting a folder with real files fails safely without deleting files
    full_folder = media_dir / "keep_me"
    full_folder.mkdir()
    doc = full_folder / "document.pdf"
    doc.write_bytes(b"%PDF-1.4 sample")
    index_folder(str(full_folder), str(media_dir))

    with pytest.raises(OSError):
        delete_empty_folder(str(full_folder), base_path=str(media_dir))

    assert full_folder.exists()
    assert doc.exists()

    # Refuses to delete the base media directory
    with pytest.raises(ValueError):
        delete_empty_folder(str(media_dir), base_path=str(media_dir))


def test_delete_empty_folders_nested_and_ds_store(api_client):
    from media_library.db.indexer import index_folder
    from media_library.services.library_manage_service import delete_empty_folders

    _client, media_dir, _ = api_client

    # Nested tree: /media/A/B/C
    dir_a = media_dir / "A"
    dir_b = dir_a / "B"
    dir_c = dir_b / "C"
    dir_c.mkdir(parents=True)
    # Add a .DS_Store file in B
    (dir_b / ".DS_Store").write_bytes(b"dummy metadata")

    index_folder(str(dir_a), str(media_dir))
    index_folder(str(dir_b), str(media_dir))
    index_folder(str(dir_c), str(media_dir))

    res = delete_empty_folders(base_path=str(media_dir))
    assert res["success"] is True
    assert res["deleted_count"] == 3
    assert not dir_c.exists()
    assert not dir_b.exists()
    assert not dir_a.exists()


def test_empty_folders_api_endpoints(api_client):
    from media_library.db.indexer import index_folder

    client, media_dir, _ = api_client

    f1 = media_dir / "target_empty1"
    f2 = media_dir / "target_empty2"
    f1.mkdir()
    f2.mkdir()
    index_folder(str(f1), str(media_dir))
    index_folder(str(f2), str(media_dir))

    # GET candidates
    cand_res = client.get("/api/backup/folders/empty/candidates")
    assert cand_res.status_code == 200
    data = cand_res.json()
    assert data["total_candidates"] >= 2
    paths = [c["path"] for c in data["candidates"]]
    assert str(f1) in paths
    assert str(f2) in paths

    # POST delete specific
    del_res = client.post(
        "/api/backup/folders/empty/delete",
        json={"folder_paths": [str(f1)]},
    )
    assert del_res.status_code == 200
    del_data = del_res.json()
    assert del_data["success"] is True
    assert str(f1) in del_data["deleted_paths"]
    assert not f1.exists()
    assert f2.exists()

    # POST delete with an external path mixed in (should skip safely, not fail with 403)
    del_mixed_res = client.post(
        "/api/backup/folders/empty/delete",
        json={"folder_paths": ["/etc/shadow", str(f2)]},
    )
    assert del_mixed_res.status_code == 200
    del_mixed_data = del_mixed_res.json()
    assert str(f2) in del_mixed_data["deleted_paths"]
    assert not f2.exists()


def test_bulk_rename_zip_extension_preserved(api_client):
    client, media_dir, _ = api_client
    z = media_dir / "my_archive.zip"
    z.write_bytes(b"PK\x05\x06" + b"\x00" * 18)

    res = client.post(
        "/api/backup/bulk/rename/preview",
        json={
            "paths": [str(z)],
            "mode": "base_counter",
            "params": {"base": "Album_", "start": 1},
        },
    )
    assert res.status_code == 200
    items = res.json()["items"]
    assert len(items) == 1
    assert items[0]["new_name"] == "Album_1.zip"
    assert items[0]["kind"] == "zip"


def test_update_file_in_index_updates_both_folders(api_client):
    from media_library.db.connection import db_conn
    from media_library.db.indexer import update_file_in_index

    _, media_dir, _ = api_client
    folder_a = media_dir / "FolderA"
    folder_b = media_dir / "FolderB"
    folder_a.mkdir(exist_ok=True)
    folder_b.mkdir(exist_ok=True)

    file_a = folder_a / "clip.mp4"
    file_a.write_bytes(b"\x00" * 500)
    file_b = folder_b / "clip.mp4"

    with db_conn() as conn, conn:
        conn.execute(
            "INSERT INTO folders (path, name, file_count, total_size) VALUES (?, 'FolderA', 1, 500)",
            (str(folder_a),),
        )
        conn.execute(
            "INSERT INTO folders (path, name, file_count, total_size) VALUES (?, 'FolderB', 0, 0)",
            (str(folder_b),),
        )
        conn.execute(
            "INSERT INTO files (path, name, folder_path, type, ext, size) VALUES (?, 'clip.mp4', ?, 'video', '.mp4', 500)",
            (str(file_a), str(folder_a)),
        )

    # Move file from FolderA to FolderB
    file_b.write_bytes(b"\x00" * 500)
    file_a.unlink()

    update_file_in_index(
        str(file_a),
        str(file_b),
        "clip.mp4",
        500,
        ".mp4",
    )

    with db_conn() as conn:
        fa = conn.execute("SELECT file_count, total_size FROM folders WHERE path = ?", (str(folder_a),)).fetchone()
        fb = conn.execute("SELECT file_count, total_size FROM folders WHERE path = ?", (str(folder_b),)).fetchone()
        assert fa["file_count"] == 0
        assert fa["total_size"] == 0
        assert fb["file_count"] == 1
        assert fb["total_size"] == 500



