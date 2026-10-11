"""Comprehensive integration and unit tests for video sorter media, queue, move, and transfer workers."""

import asyncio
import os
from pathlib import Path
import threading
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from media_library.core.state import state
from media_library.db.connection import db_conn
from media_library.routers.sorter_queue import _resolve_directory_tolerant
from media_library.services.deduplication_service import compute_quick_hash
from media_library.workers.transfer_worker import (
    TransferWorker,
    transfer_worker_instance,
)


def test_resolve_directory_tolerant(tmp_path):
    # 1. Empty/blank returns abspath
    assert _resolve_directory_tolerant("") == os.path.abspath("/")
    assert _resolve_directory_tolerant("   ") == os.path.abspath("   ")

    # 2. Normal existing directory
    normal_dir = tmp_path / "normal_folder"
    normal_dir.mkdir()
    assert _resolve_directory_tolerant(str(normal_dir)) == str(normal_dir)

    # 3. Directory with trailing whitespace
    ws_dir = tmp_path / "Movies "
    ws_dir.mkdir()
    # Query without trailing whitespace should match sibling
    resolved = _resolve_directory_tolerant(str(tmp_path / "Movies"))
    assert resolved == str(ws_dir)

    # 4. Nonexistent directory
    missing = tmp_path / "nonexistent"
    assert _resolve_directory_tolerant(str(missing)) == str(missing)


def test_sorter_media_streaming_and_duplicate_check(api_client):
    source_dir = Path(state["source_dir"])
    dest_dir = Path(state["destination_dir"])

    vid_file = source_dir / "sample_stream.mp4"
    data = b"\x00\x00\x00 ftypmp42" + b"MEDIA CONTENT" * 100
    vid_file.write_bytes(data)

    # 1. Video streaming endpoint
    res_stream = api_client.get(f"/api/stream/{vid_file.name}")
    assert res_stream.status_code == 200
    assert "video/mp4" in res_stream.headers.get("content-type", "")

    # Range stream
    res_range = api_client.get(
        f"/api/stream/{vid_file.name}", headers={"Range": "bytes=0-49"}
    )
    assert res_range.status_code == 206
    assert len(res_range.content) == 50

    # 404 for missing video
    res_stream_404 = api_client.get("/api/stream/ghost_video.mp4")
    assert res_stream_404.status_code == 404

    # 2. Sorter duplicate check
    # A. 0-byte or nonexistent file
    res_none = api_client.get("/api/sorter/check-duplicate?path=/none/file.mp4")
    assert res_none.status_code == 200
    assert res_none.json()["is_duplicate"] is False

    empty_f = source_dir / "empty.mp4"
    empty_f.write_bytes(b"")
    res_empty = api_client.get(f"/api/sorter/check-duplicate?path={empty_f}")
    assert res_empty.json()["is_duplicate"] is False

    # B. Match existing library duplicate
    lib_file = dest_dir / "existing_lib_video.mp4"
    lib_file.write_bytes(data)
    h = compute_quick_hash(str(lib_file))

    with db_conn() as conn, conn:
        conn.execute(
            """INSERT INTO files (path, name, folder_path, type, ext, size, size_formatted, mtime, quick_hash, has_thumb)
               VALUES (?, ?, ?, 'video', '.mp4', ?, '1 KB', 100.0, ?, 0)""",
            (str(lib_file), "existing_lib_video.mp4", str(dest_dir), len(data), h),
        )

    res_dupe = api_client.get(f"/api/sorter/check-duplicate?path={vid_file}")
    assert res_dupe.status_code == 200
    d_data = res_dupe.json()
    assert d_data["is_duplicate"] is True
    assert len(d_data["matches"]) >= 1
    assert d_data["matches"][0]["path"] == str(lib_file)

    # Check duplicate with file_id
    res_dupe_id = api_client.get(f"/api/sorter/check-duplicate?file_id={vid_file.name}")
    assert res_dupe_id.status_code == 200
    assert res_dupe_id.json()["is_duplicate"] is True


def test_sorter_queue_and_browse(api_client, tmp_path):
    source_dir = Path(state["source_dir"])
    dest_dir = Path(state["destination_dir"])

    # 1. Browse directory
    res_browse = api_client.get("/api/browse", params={"path": str(source_dir)})
    assert res_browse.status_code == 200
    b_data = res_browse.json()
    assert b_data["current_path"] == str(source_dir)
    assert "folders" in b_data
    assert "video_count_here" in b_data

    # Browse 404
    res_browse_404 = api_client.get("/api/browse", params={"path": str(tmp_path / "ghost")})
    assert res_browse_404.status_code == 404

    # 2. Quick navigation
    res_qnav = api_client.get("/api/quick-nav")
    assert res_qnav.status_code == 200
    shortcuts = res_qnav.json()["shortcuts"]
    labels = [s["label"] for s in shortcuts]
    assert "Media Library" in labels
    assert "Incoming" in labels

    # 3. Queue with refresh
    res_q_ref = api_client.get("/api/queue?refresh=true")
    assert res_q_ref.status_code == 200
    assert "videos" in res_q_ref.json()

    # 4. Source scan error branches
    res_scan_missing = api_client.post(
        "/api/source/scan", json={"source_dir": str(tmp_path / "missing_dir")}
    )
    assert res_scan_missing.status_code == 400

    not_a_dir = tmp_path / "some_file.txt"
    not_a_dir.write_text("hello")
    res_scan_file = api_client.post(
        "/api/source/scan", json={"source_dir": str(not_a_dir)}
    )
    assert res_scan_file.status_code == 400

    # 5. Destination creation error branches
    res_dest_empty = api_client.post("/api/destinations/create", json={"folder_name": " "})
    assert res_dest_empty.status_code == 400

    res_dest_dot = api_client.post("/api/destinations/create", json={"folder_name": ".hidden"})
    assert res_dest_dot.status_code == 400


def test_sorter_move_and_undo_operations(api_client):
    source_dir = Path(state["source_dir"])
    dest_dir = Path(state["destination_dir"])

    src_vid = source_dir / "action_clip.mp4"
    src_vid.write_bytes(b"\x00" * 512)

    # 1. Error branches for move
    res_move_missing = api_client.post(
        "/api/move",
        json={"file_id": "ghost.mp4", "destination_folder": "Action"},
    )
    assert res_move_missing.status_code == 404

    res_move_empty_dest = api_client.post(
        "/api/move",
        json={"file_id": src_vid.name, "destination_folder": "  "},
    )
    assert res_move_empty_dest.status_code == 400

    # 2. Successful move
    res_move = api_client.post(
        "/api/move",
        json={"file_id": src_vid.name, "destination_folder": "Action"},
    )
    assert res_move.status_code == 200
    m_data = res_move.json()
    assert m_data["success"] is True
    task_id = m_data["task_id"]

    # 3. Transfers status
    res_transfers = api_client.get("/api/transfers")
    assert res_transfers.status_code == 200
    assert "queued" in res_transfers.json()
    assert "active" in res_transfers.json()

    # 4. Cancel transfer
    # Add a mock transfer item
    mock_t = {
        "id": "mock_transfer_1",
        "status": "queued",
        "source_path": str(src_vid),
        "target_path": str(dest_dir / "Action" / "action_clip.mp4"),
        "filename": "action_clip.mp4",
        "abort_event": threading.Event(),
    }
    transfer_worker_instance.transfer_items_map["mock_transfer_1"] = mock_t

    res_cancel = api_client.post(
        "/api/transfers/cancel", json={"task_id": "mock_transfer_1"}
    )
    assert res_cancel.status_code == 200
    assert res_cancel.json()["action"] == "cancelled"

    # Cancel nonexistent transfer -> 404
    res_cancel_404 = api_client.post(
        "/api/transfers/cancel", json={"task_id": "nonexistent_task"}
    )
    assert res_cancel_404.status_code == 404

    # 5. Undo operation
    # Queue cancellation undo
    mock_op = {
        "task_id": "mock_transfer_2",
        "source_path": str(source_dir / "undo_test.mp4"),
        "target_path": str(dest_dir / "Action" / "undo_test.mp4"),
        "filename": "undo_test.mp4",
    }
    state.push_undo(mock_op)
    transfer_worker_instance.transfer_items_map["mock_transfer_2"] = {
        "id": "mock_transfer_2",
        "status": "queued",
        "source_path": mock_op["source_path"],
        "target_path": mock_op["target_path"],
        "filename": mock_op["filename"],
        "abort_event": threading.Event(),
    }

    res_undo_q = api_client.post("/api/undo")
    assert res_undo_q.status_code == 200
    assert res_undo_q.json()["action"] == "cancelled_queued"

    # Completed move undo (file restored from dest to src)
    target_restored = dest_dir / "Action" / "restored.mp4"
    target_restored.parent.mkdir(parents=True, exist_ok=True)
    target_restored.write_bytes(b"RESTORED" * 20)

    state.push_undo(
        {
            "task_id": "mock_completed",
            "source_path": str(source_dir / "restored.mp4"),
            "target_path": str(target_restored),
            "filename": "restored.mp4",
        }
    )
    res_undo_completed = api_client.post("/api/undo")
    assert res_undo_completed.status_code == 200
    assert res_undo_completed.json()["action"] == "restored"
    assert (source_dir / "restored.mp4").exists()


def test_sorter_rename_and_delete(api_client):
    source_dir = Path(state["source_dir"])
    vid = source_dir / "clip_rename.mp4"
    vid.write_bytes(b"\x00" * 256)

    # 1. Rename error branches
    res_ren_missing = api_client.post(
        "/api/rename", json={"file_id": "ghost.mp4", "new_name": "new.mp4"}
    )
    assert res_ren_missing.status_code == 404

    res_ren_empty = api_client.post(
        "/api/rename", json={"file_id": vid.name, "new_name": "  "}
    )
    assert res_ren_empty.status_code == 400

    # Rename collision
    collision_file = source_dir / "already_exists.mp4"
    collision_file.write_bytes(b"\x00" * 128)
    res_ren_col = api_client.post(
        "/api/rename",
        json={"file_id": vid.name, "new_name": "already_exists.mp4"},
    )
    assert res_ren_col.status_code == 400

    # Successful rename
    res_ren_ok = api_client.post(
        "/api/rename", json={"file_id": vid.name, "new_name": "brand_new_name"}
    )
    assert res_ren_ok.status_code == 200
    assert res_ren_ok.json()["new_filename"] == "brand_new_name.mp4"
    assert (source_dir / "brand_new_name.mp4").exists()

    # 2. Delete
    res_del_missing = api_client.post(
        "/api/delete", json={"file_id": "ghost.mp4"}
    )
    assert res_del_missing.status_code == 404

    res_del_ok = api_client.post(
        "/api/delete", json={"file_id": "brand_new_name.mp4"}
    )
    assert res_del_ok.status_code == 200
    assert not (source_dir / "brand_new_name.mp4").exists()


def test_transfer_worker_map_pruning_and_cancel():
    worker = TransferWorker()
    for i in range(1050):
        worker.transfer_items_map[f"t_{i}"] = {
            "id": f"t_{i}",
            "filename": f"v_{i}.mp4",
            "status": "completed",
        }
    new_t = {
        "id": "new_t",
        "filename": "new.mp4",
        "status": "queued",
    }
    worker.enqueue(new_t)
    assert len(worker.transfer_items_map) <= 1001

    assert worker.cancel("new_t") is True
    assert worker.cancel("nonexistent") is False
