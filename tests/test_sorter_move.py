"""Tests for Sorter Move, Undo, Rename, Delete, and Transfer Queue APIs."""

import os
from pathlib import Path
from media_library.core.state import state
from media_library.services.media_service import do_transfer
from media_library.workers.transfer_worker import transfer_worker_instance


def test_sorter_move_and_undo(api_client):
    source_dir = Path(state["source_dir"])
    dest_dir = Path(state["destination_dir"])
    
    # Create a test video in source_dir
    test_video = source_dir / "clip_to_move.mp4"
    test_video.write_bytes(b"TEST_VIDEO_DATA_123")
    
    file_id = "clip_to_move.mp4"
    target_folder = "action_movies"
    
    res = api_client.post("/api/move", json={
        "file_id": file_id,
        "destination_folder": target_folder
    })
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert "task_id" in data
    task_id = data["task_id"]
    
    # Execute the transfer synchronously
    item = transfer_worker_instance.transfer_items_map[task_id]
    do_transfer(item)
    
    expected_path = dest_dir / target_folder / "clip_to_move.mp4"
    assert expected_path.exists()
    assert not test_video.exists()

    # Now test undo
    res_undo = api_client.post("/api/undo")
    assert res_undo.status_code == 200
    undo_data = res_undo.json()
    assert undo_data["success"] is True
    assert undo_data["action"] == "restored"
    assert undo_data["restored_filename"] == "clip_to_move.mp4"
    
    # File should be back in source_dir
    assert test_video.exists()
    assert not expected_path.exists()


def test_sorter_undo_queued_item(api_client):
    source_dir = Path(state["source_dir"])
    test_video = source_dir / "queued_clip.mp4"
    test_video.write_bytes(b"QUEUED_DATA")
    
    res = api_client.post("/api/move", json={
        "file_id": "queued_clip.mp4",
        "destination_folder": "somewhere"
    })
    assert res.status_code == 200
    
    # Do NOT execute transfer -> it remains in "queued" state
    res_undo = api_client.post("/api/undo")
    assert res_undo.status_code == 200
    undo_data = res_undo.json()
    assert undo_data["success"] is True
    assert undo_data["action"] == "cancelled_queued"
    assert test_video.exists()


def test_sorter_undo_empty_stack(api_client):
    state["undo_stack"].clear()
    res = api_client.post("/api/undo")
    assert res.status_code == 400


def test_sorter_move_validation_errors(api_client):
    # Non-existent file
    res = api_client.post("/api/move", json={
        "file_id": "non_existent_file.mp4",
        "destination_folder": "somewhere"
    })
    assert res.status_code == 404

    # Empty destination
    res = api_client.post("/api/move", json={
        "file_id": "test_clip.mp4",
        "destination_folder": "   "
    })
    assert res.status_code == 400


def test_sorter_rename(api_client):
    source_dir = Path(state["source_dir"])
    orig = source_dir / "orig_name.mp4"
    orig.write_bytes(b"ORIG_DATA")
    
    res = api_client.post("/api/rename", json={
        "file_id": "orig_name.mp4",
        "new_name": "renamed_clip.mp4"
    })
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert data["new_id"] == "renamed_clip.mp4"
    assert (source_dir / "renamed_clip.mp4").exists()
    assert not orig.exists()


def test_sorter_rename_errors(api_client):
    # Missing file
    res = api_client.post("/api/rename", json={
        "file_id": "does_not_exist.mp4",
        "new_name": "new.mp4"
    })
    assert res.status_code == 404

    # Target already exists
    source_dir = Path(state["source_dir"])
    f1 = source_dir / "f1.mp4"
    f2 = source_dir / "f2.mp4"
    f1.write_bytes(b"1")
    f2.write_bytes(b"2")

    res = api_client.post("/api/rename", json={
        "file_id": "f1.mp4",
        "new_name": "f2.mp4"
    })
    assert res.status_code == 400


def test_sorter_delete(api_client):
    source_dir = Path(state["source_dir"])
    to_delete = source_dir / "to_delete.mp4"
    to_delete.write_bytes(b"TO_DELETE")

    res = api_client.post("/api/delete", json={
        "file_id": "to_delete.mp4"
    })
    assert res.status_code == 200
    assert res.json()["success"] is True
    assert not to_delete.exists()

    # Deleting again returns 404
    res2 = api_client.post("/api/delete", json={
        "file_id": "to_delete.mp4"
    })
    assert res2.status_code == 404


def test_sorter_transfers_endpoints(api_client):
    res = api_client.get("/api/transfers")
    assert res.status_code == 200
    data = res.json()
    assert "active" in data
    assert "queued" in data
    assert "history" in data

    # Cancel transfer non-existent task returns 404
    res_cancel = api_client.post("/api/transfers/cancel", json={"task_id": "non_existent"})
    assert res_cancel.status_code == 404
