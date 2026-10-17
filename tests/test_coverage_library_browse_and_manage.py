"""Comprehensive tests for library_browse.py and library_manage.py routers."""

import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

from media_library.core.state import state
from media_library.db.connection import db_conn
from media_library.routers.library_browse import (
    _browse_index_inflight,
    _browse_index_lock,
    _schedule_background_index,
)
from media_library.workers.library_ops_worker import library_ops_worker_instance


def test_schedule_background_index_debouncing_and_pruning(tmp_path):
    target = str(tmp_path / "folder_a")
    base = str(tmp_path)

    with _browse_index_lock:
        _browse_index_inflight.clear()

    # First call schedules
    with patch("threading.Thread") as mock_thread:
        mock_thread.return_value.start = MagicMock()
        assert _schedule_background_index(target, base, False) is True

    # Immediate second call is debounced
    assert _schedule_background_index(target, base, False) is False

    # Simulate cap exceeded with stale entries
    with _browse_index_lock:
        _browse_index_inflight.clear()
        for i in range(20):
            _browse_index_inflight[f"/path/{i}"] = time.time() - 400.0

    with (
        patch("threading.Thread") as mock_thread,
        patch("media_library.routers.library_browse.INDEX_INFLIGHT_CAP", 10),
        patch("media_library.routers.library_browse.INDEX_INFLIGHT_PRUNE_S", 10.0),
    ):
        mock_thread.return_value.start = MagicMock()
        assert _schedule_background_index(target, base, False) is True
        # Stale entries should be pruned
        with _browse_index_lock:
            assert len(_browse_index_inflight) < 20


def test_library_browse_path_clamping_and_scandir_fallback(api_client, tmp_path):
    dest = Path(state["destination_dir"])
    sub = dest / "test_cat"
    sub.mkdir(exist_ok=True)
    f = sub / "video.mp4"
    f.write_bytes(b"\x00" * 10)

    # 1. Path pointing outside library clamps to root
    res_outside = api_client.get("/api/backup/browse", params={"path": "/etc/passwd"})
    assert res_outside.status_code == 200
    assert res_outside.json()["is_root"] is True

    # 2. Path pointing to a file inside library clamps to its parent
    res_file = api_client.get("/api/backup/browse", params={"path": str(f)})
    assert res_file.status_code == 200
    assert res_file.json()["current_path"] == str(sub)

    # 3. Browse with refresh=True
    res_refresh = api_client.get(
        "/api/backup/browse", params={"path": str(sub), "refresh": "true"}
    )
    assert res_refresh.status_code == 200

    # 4. Root folders fallback via os.scandir when DB has no root entries
    with patch("media_library.routers.library_browse.db_conn") as mock_db:
        # Mock empty fetchall from DB
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = []
        mock_db.return_value.__enter__.return_value = mock_conn

        res_root = api_client.get("/api/backup/browse")
        assert res_root.status_code == 200
        data = res_root.json()
        assert any(rf["name"] == "test_cat" for rf in data["root_folders"])


def test_library_browse_folders_endpoint(api_client):
    dest = Path(state["destination_dir"])
    child = dest / "child_folder"
    child.mkdir(exist_ok=True)

    # 1. Root children
    res_root = api_client.get("/api/backup/folders")
    assert res_root.status_code == 200
    assert "subfolders" in res_root.json()

    # 2. Outside path clamps safely
    res_out = api_client.get("/api/backup/folders", params={"path": "/var/log"})
    assert res_out.status_code == 200
    assert res_out.json()["is_root"] is True

    # 3. Unindexed folder schedules background index
    unindexed = dest / "unindexed_dir"
    unindexed.mkdir(exist_ok=True)
    res_unindexed = api_client.get(
        "/api/backup/folders", params={"path": str(unindexed)}
    )
    assert res_unindexed.status_code == 200


def test_library_search_and_index_status_controls(api_client):
    # 1. Search endpoint
    res_search = api_client.get(
        "/api/backup/search",
        params={"q": "clip", "type": "video", "sort": "date_desc", "limit": 10},
    )
    assert res_search.status_code == 200
    assert res_search.json()["query"] == "clip"

    # 2. Index status
    res_status = api_client.get("/api/backup/index/status")
    assert res_status.status_code == 200
    assert "total_files" in res_status.json()

    # 3. Index sync when crawling vs idle
    from media_library.db.indexer import indexer_state

    orig_status = indexer_state["status"]
    indexer_state["status"] = "crawling"
    res_sync_busy = api_client.post("/api/backup/index/sync")
    assert res_sync_busy.status_code == 200
    assert res_sync_busy.json()["success"] is False
    assert "already running" in res_sync_busy.json()["message"]

    indexer_state["status"] = "idle"

    async def fake_crawler(**kwargs):
        pass

    with patch(
        "media_library.routers.library_browse.run_background_crawler",
        side_effect=fake_crawler,
    ):
        res_sync_ok = api_client.post("/api/backup/index/sync")
        assert res_sync_ok.status_code == 200
        assert res_sync_ok.json()["success"] is True

    indexer_state["status"] = orig_status

    # 4. Index prune — must be the explicit forced variant, otherwise a large
    #    remote deletion can never clear the index (the mount-safety limit
    #    aborts every automatic prune).
    with patch(
        "media_library.routers.library_browse.prune_stale_index_entries",
        return_value={"pruned": 0},
    ) as prune_mock:
        res_prune = api_client.post("/api/backup/index/prune")
        assert res_prune.status_code == 200
        assert res_prune.json()["success"] is True
        assert prune_mock.call_args.args[3] is True  # force


def test_library_manage_move_validations(api_client):
    dest = Path(state["destination_dir"])
    f_dir = dest / "from_dir"
    f_dir.mkdir(exist_ok=True)
    f1 = f_dir / "file1.mp4"
    f1.write_bytes(b"\x00" * 10)

    # 1. Empty paths
    res_empty = api_client.post(
        "/api/backup/move",
        json={"paths": [], "destination_folder": str(dest / "to_dir")},
    )
    assert res_empty.status_code == 400

    # 2. Moving folder into itself or its descendant raises 400
    sub_sub = f_dir / "sub"
    sub_sub.mkdir(parents=True, exist_ok=True)
    res_self = api_client.post(
        "/api/backup/move",
        json={"paths": [str(f_dir)], "destination_folder": str(sub_sub)},
    )
    assert res_self.status_code == 400
    assert "Cannot move folder into itself" in res_self.json()["detail"]

    # 3. Conflict policy "error" when collision exists
    to_dir = dest / "to_dir"
    to_dir.mkdir(exist_ok=True)
    (to_dir / "file1.mp4").write_bytes(b"\x00" * 20)

    res_conflict = api_client.post(
        "/api/backup/move",
        json={
            "paths": [str(f1)],
            "destination_folder": str(to_dir),
            "conflict_policy": "error",
        },
    )
    assert res_conflict.status_code == 409


def test_library_ops_status_and_cancel(api_client):
    # 1. Status
    res_st = api_client.get("/api/backup/library-ops/status")
    assert res_st.status_code == 200
    assert "active" in res_st.json()

    # 2. Cancel without task_id
    res_bad = api_client.post("/api/backup/library-ops/cancel", json={})
    assert res_bad.status_code == 400

    # 3. Cancel non-existent
    res_404 = api_client.post(
        "/api/backup/library-ops/cancel", json={"task_id": "fake_id"}
    )
    assert res_404.status_code == 404

    # 4. Cancel existing
    mock_task = {
        "id": "op_task_1",
        "op": "move",
        "status": "queued",
        "abort_event": threading.Event(),
    }
    library_ops_worker_instance.items_map["op_task_1"] = mock_task
    res_cancel_ok = api_client.post(
        "/api/backup/library-ops/cancel", json={"task_id": "op_task_1"}
    )
    assert res_cancel_ok.status_code == 200
    assert res_cancel_ok.json()["cancelled"] == "op_task_1"


def test_library_undo_edge_cases(api_client, tmp_path):
    # 1. Undo when empty
    library_ops_worker_instance.undo_stack.clear()
    res_empty = api_client.post("/api/backup/undo")
    assert res_empty.status_code == 400

    # 2. Target item missing on disk
    rec_missing = {
        "op": "move",
        "source_path": str(tmp_path / "orig.mp4"),
        "target_path": str(tmp_path / "ghost.mp4"),
        "kind": "file",
    }
    library_ops_worker_instance.push_undo(rec_missing)
    res_miss = api_client.post("/api/backup/undo")
    assert res_miss.status_code == 404

    # 3. Successful undo with restore collision handling
    dest_file = tmp_path / "moved.mp4"
    dest_file.write_bytes(b"\x00" * 10)
    orig_file = tmp_path / "orig2.mp4"
    orig_file.write_bytes(b"\x00" * 10)  # Already exists at orig path

    rec_collision = {
        "op": "move",
        "source_path": str(orig_file),
        "target_path": str(dest_file),
        "kind": "file",
    }
    library_ops_worker_instance.push_undo(rec_collision)

    with patch("media_library.routers.library_manage.move_library_path") as mock_move:
        res_ok = api_client.post("/api/backup/undo")
        assert res_ok.status_code == 200
        assert "_restored_1.mp4" in res_ok.json()["restored_path"]
        mock_move.assert_called()


def test_library_folder_crud_endpoints(api_client):
    dest = Path(state["destination_dir"])

    # 1. Create folder invalid name
    res_inv = api_client.post(
        "/api/backup/folders",
        json={"parent_path": str(dest), "name": "../sneaky"},
    )
    assert res_inv.status_code == 400

    # 2. Rename folder invalid
    res_ren_inv = api_client.post(
        "/api/backup/folders/rename",
        json={"path": str(dest / "nonexistent"), "new_name": "../sneaky"},
    )
    assert res_ren_inv.status_code == 400

    # 3. Delete folder when path is a regular file
    reg_file = dest / "test.txt"
    reg_file.write_text("hello")
    res_del_file = api_client.post(
        "/api/backup/folders/delete",
        json={"path": str(reg_file)},
    )
    assert res_del_file.status_code == 400
    assert "Only folders or ZIP" in res_del_file.json()["detail"]


def test_library_bulk_operations(api_client, tmp_path):
    dest = Path(state["destination_dir"])
    f1 = dest / "photo1.jpg"
    f2 = dest / "photo2.jpg"
    f1.write_bytes(b"\x00" * 10)
    f2.write_bytes(b"\x00" * 10)

    # 1. Bulk rename preview empty
    res_empty_prev = api_client.post(
        "/api/backup/bulk/rename/preview",
        json={"paths": [], "mode": "find_replace", "params": {}},
    )
    assert res_empty_prev.status_code == 400

    # 2. Bulk rename preview valid
    res_prev = api_client.post(
        "/api/backup/bulk/rename/preview",
        json={
            "paths": [str(f1), str(f2)],
            "mode": "find_replace",
            "params": {"find": "photo", "replace": "image"},
        },
    )
    assert res_prev.status_code == 200
    assert len(res_prev.json()["items"]) == 2

    # 3. Bulk rename apply
    res_apply = api_client.post(
        "/api/backup/bulk/rename",
        json={
            "paths": [str(f1), str(f2)],
            "mode": "find_replace",
            "params": {"find": "photo", "replace": "image"},
        },
    )
    assert res_apply.status_code == 200
    assert res_apply.json()["queued"] == 2

    # 4. Bulk delete empty
    assert (
        api_client.post("/api/backup/bulk/delete", json={"paths": []}).status_code
        == 400
    )

    # 5. Bulk webp-optimize
    res_webp = api_client.post(
        "/api/backup/bulk/webp-optimize",
        json={"paths": [str(f1), str(f2)]},
    )
    assert res_webp.status_code == 200
    assert res_webp.json()["success"] is True

    # 6. Empty folder cleanup endpoints
    res_empty_cands = api_client.get("/api/backup/folders/empty/candidates")
    assert res_empty_cands.status_code == 200

    res_empty_del = api_client.post("/api/backup/folders/empty/delete", json={})
    assert res_empty_del.status_code == 200


def test_search_limit_and_truncation_behavior(api_client, sandbox_env):
    from media_library.core.config import SEARCH_LIMIT
    from media_library.services.media_service import search_backup_index

    # Default query without limit parameter uses SEARCH_LIMIT
    res = api_client.get("/api/backup/search", params={"q": "clip"})
    assert res.status_code == 200
    data = res.json()
    assert data["limit"] == SEARCH_LIMIT
    assert "truncated" in data
    assert isinstance(data["truncated"], bool)

    # Insert 6 test files matching 'searchlimit_test'
    with db_conn() as conn, conn:
        for i in range(6):
            conn.execute(
                """INSERT OR REPLACE INTO files (path, name, folder_path, type, ext, size, mtime)
                       VALUES (?, ?, ?, 'video', '.mp4', 1024, 100.0)""",
                (
                    f"/dest/searchlimit_test_{i}.mp4",
                    f"searchlimit_test_{i}.mp4",
                    "/dest",
                ),
            )

    # Test direct service call with limit=3 (truncation expected: 6 items in DB, limit 3)
    results, truncated = search_backup_index(
        "searchlimit_test", limit=3, return_meta=True
    )
    assert len(results) == 3
    assert truncated is True

    # Test direct service call without return_meta (backwards compatibility returns list)
    plain_results = search_backup_index("searchlimit_test", limit=3)
    assert isinstance(plain_results, list)
    assert len(plain_results) == 3

    # Test API endpoint with limit=3
    res_lim3 = api_client.get(
        "/api/backup/search", params={"q": "searchlimit_test", "limit": 3}
    )
    assert res_lim3.status_code == 200
    data3 = res_lim3.json()
    assert data3["total"] == 3
    assert data3["limit"] == 3
    assert data3["truncated"] is True
    assert len(data3["results"]) == 3

    # Test API endpoint with limit=10 (no truncation: 6 items in DB, limit 10)
    res_lim10 = api_client.get(
        "/api/backup/search", params={"q": "searchlimit_test", "limit": 10}
    )
    assert res_lim10.status_code == 200
    data10 = res_lim10.json()
    assert data10["total"] == 6
    assert data10["limit"] == 10
    assert data10["truncated"] is False
    assert len(data10["results"]) == 6
