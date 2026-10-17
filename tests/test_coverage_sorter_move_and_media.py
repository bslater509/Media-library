"""Deep coverage tests for sorter_move.py and sorter_media.py routers."""

import threading
from pathlib import Path

from media_library.core.state import state
from media_library.db.connection import db_conn
from media_library.workers.transcode_worker import transcode_worker_instance
from media_library.workers.transfer_worker import transfer_worker_instance


def test_sorter_move_errors_and_collisions(api_client, tmp_path):
    src_dir = tmp_path / "incoming"
    src_dir.mkdir(exist_ok=True)
    state["source_dir"] = str(src_dir)

    dest_dir = Path(state["destination_dir"])

    # 1. No source dir
    orig_src = state["source_dir"]
    state["source_dir"] = ""
    res_no_src = api_client.post(
        "/api/move", json={"file_id": "clip.mp4", "destination_folder": "action"}
    )
    assert res_no_src.status_code == 400
    state["source_dir"] = orig_src

    # 2. File not found
    res_404 = api_client.post(
        "/api/move", json={"file_id": "missing.mp4", "destination_folder": "action"}
    )
    assert res_404.status_code == 404

    # 3. Empty destination folder
    clip = src_dir / "clip1.mp4"
    clip.write_bytes(b"\x00" * 1024)
    res_empty_dest = api_client.post(
        "/api/move", json={"file_id": "clip1.mp4", "destination_folder": "   "}
    )
    assert res_empty_dest.status_code == 400

    # 4. Target collision renaming with active transfer link
    action_dest = dest_dir / "action"
    action_dest.mkdir(exist_ok=True)
    (action_dest / "clip1.mp4").write_bytes(b"\x00" * 20)
    (action_dest / "clip1_1.mp4").write_bytes(b"\x00" * 20)

    # Link a pending transcode
    tc_task = {
        "id": "tc_link_1",
        "filename": "clip1.mp4",
        "status": "queued",
        "abort_event": threading.Event(),
    }
    transcode_worker_instance.transcode_items_map["tc_link_1"] = tc_task

    res_collision = api_client.post(
        "/api/move", json={"file_id": "clip1.mp4", "destination_folder": "action"}
    )
    assert res_collision.status_code == 200
    c_data = res_collision.json()
    assert c_data["success"] is True
    assert c_data["collision_renamed"] is True
    assert c_data["final_filename"] == "clip1_2.mp4"
    tid = c_data["task_id"]
    assert (
        transfer_worker_instance.transfer_items_map[tid]["waiting_on_transcode"]
        == "tc_link_1"
    )


def test_sorter_undo_transferring_and_collisions(api_client, tmp_path):
    src_dir = Path(state["source_dir"])
    dest_dir = Path(state["destination_dir"])

    # 1. Undo active transferring task
    abort_ev = threading.Event()
    transferring_task = {
        "id": "active_trans_1",
        "status": "transferring",
        "filename": "stream_vid.mp4",
        "source_path": str(src_dir / "stream_vid.mp4"),
        "target_path": str(dest_dir / "action" / "stream_vid.mp4"),
        "abort_event": abort_ev,
    }
    transfer_worker_instance.transfer_items_map["active_trans_1"] = transferring_task

    undo_op = {
        "task_id": "active_trans_1",
        "filename": "stream_vid.mp4",
        "source_path": str(src_dir / "stream_vid.mp4"),
        "target_path": str(dest_dir / "action" / "stream_vid.mp4"),
        "destination_folder": "action",
    }
    state["undo_stack"] = [undo_op]

    (src_dir / "stream_vid.mp4").write_bytes(b"\x00" * 1024)

    res_undo_act = api_client.post("/api/undo")
    assert res_undo_act.status_code == 200
    assert res_undo_act.json()["action"] == "aborted_active"
    assert abort_ev.is_set()

    # 2. Undo completed move when restored target already exists at source
    target_f = dest_dir / "action" / "done.mp4"
    target_f.parent.mkdir(exist_ok=True)
    target_f.write_bytes(b"\x00" * 50)

    orig_f = src_dir / "done.mp4"
    orig_f.write_bytes(b"\x00" * 50)  # Collision at source

    undo_completed = {
        "task_id": "done_1",
        "filename": "done.mp4",
        "source_path": str(orig_f),
        "target_path": str(target_f),
        "destination_folder": "action",
    }
    state["undo_stack"] = [undo_completed]

    res_undo_col = api_client.post("/api/undo")
    assert res_undo_col.status_code == 200
    assert res_undo_col.json()["action"] == "restored"
    assert "_restored_1.mp4" in res_undo_col.json()["restored_filename"]

    # 3. Undo completed move when target file is missing
    undo_ghost = {
        "task_id": "ghost_1",
        "filename": "ghost.mp4",
        "source_path": str(src_dir / "ghost.mp4"),
        "target_path": str(dest_dir / "action" / "ghost.mp4"),
        "destination_folder": "action",
    }
    state["undo_stack"] = [undo_ghost]
    res_undo_miss = api_client.post("/api/undo")
    assert res_undo_miss.status_code == 404


def test_sorter_cancel_transfer(api_client, tmp_path):
    src_dir = Path(state["source_dir"])
    t_file = src_dir / "active_cancel.mp4"
    t_file.write_bytes(b"\x00" * 100)

    # 1. Non-existent task
    assert (
        api_client.post("/api/transfers/cancel", json={"task_id": "none"}).status_code
        == 404
    )

    # 2. Transferring task
    abort_ev = threading.Event()
    transferring_task = {
        "id": "t_cancel_act",
        "status": "transferring",
        "filename": "active_cancel.mp4",
        "source_path": str(t_file),
        "abort_event": abort_ev,
    }
    transfer_worker_instance.transfer_items_map["t_cancel_act"] = transferring_task

    res_act = api_client.post("/api/transfers/cancel", json={"task_id": "t_cancel_act"})
    assert res_act.status_code == 200
    assert res_act.json()["action"] == "aborted"
    assert abort_ev.is_set()

    # 3. Completed state (cannot cancel)
    transferring_task["status"] = "completed"
    res_done = api_client.post(
        "/api/transfers/cancel", json={"task_id": "t_cancel_act"}
    )
    assert res_done.status_code == 200
    assert res_done.json()["success"] is False


def test_sorter_rename_video(api_client, tmp_path):
    src_dir = Path(state["source_dir"])
    v1 = src_dir / "video_to_rename.mp4"
    v1.write_bytes(b"\x00" * 200)

    # 1. Empty new name
    res_empty = api_client.post(
        "/api/rename", json={"file_id": "video_to_rename.mp4", "new_name": " "}
    )
    assert res_empty.status_code == 400

    # 2. Name collision
    existing = src_dir / "already_exists.mp4"
    existing.write_bytes(b"\x00" * 200)
    res_col = api_client.post(
        "/api/rename",
        json={"file_id": "video_to_rename.mp4", "new_name": "already_exists.mp4"},
    )
    assert res_col.status_code == 400

    # 3. Successful rename with active transcode cancellation
    tc_task = {
        "id": "tc_rename",
        "filename": "video_to_rename.mp4",
        "status": "queued",
        "abort_event": threading.Event(),
    }
    transcode_worker_instance.transcode_items_map["tc_rename"] = tc_task

    res_ok = api_client.post(
        "/api/rename",
        json={"file_id": "video_to_rename.mp4", "new_name": "renamed_video"},
    )
    assert res_ok.status_code == 200
    assert res_ok.json()["new_filename"] == "renamed_video.mp4"
    assert tc_task["status"] == "cancelled"
    assert (src_dir / "renamed_video.mp4").exists()


def test_sorter_delete_video(api_client, tmp_path):
    src_dir = Path(state["source_dir"])
    v_del = src_dir / "delete_me.mp4"
    v_del.write_bytes(b"\x00" * 200)

    tc_task = {
        "id": "tc_del",
        "filename": "delete_me.mp4",
        "status": "queued",
        "abort_event": threading.Event(),
    }
    transcode_worker_instance.transcode_items_map["tc_del"] = tc_task

    res_del = api_client.post("/api/delete", json={"file_id": "delete_me.mp4"})
    assert res_del.status_code == 200
    assert res_del.json()["deleted_file"] == "delete_me.mp4"
    assert not v_del.exists()
    assert tc_task["status"] == "cancelled"


def test_sorter_media_stream_and_duplicate_check(api_client, sandbox_env, tmp_path):
    src_dir = Path(state["source_dir"])
    dest_dir = Path(state["destination_dir"])

    # 1. Stream video
    vid_stream = src_dir / "streamable.mp4"
    vid_stream.write_bytes(b"VIDDATA" * 500)

    res_stream = api_client.get("/api/stream/streamable.mp4")
    assert res_stream.status_code in (200, 206)

    # 404 for missing stream
    assert api_client.get("/api/stream/ghost.mp4").status_code == 404

    # 2. Duplicate check by path
    # Create library indexed file
    lib_file = dest_dir / "library_match.mp4"
    lib_file.write_bytes(b"VIDDATA" * 500)

    with db_conn() as conn, conn:
        conn.execute(
            """INSERT INTO files (path, name, folder_path, type, ext, size, size_formatted, mtime, quick_hash)
               VALUES (?, ?, ?, 'video', '.mp4', ?, '3.5 KB', 100.0, NULL)""",
            (
                str(lib_file),
                "library_match.mp4",
                str(dest_dir),
                lib_file.stat().st_size,
            ),
        )

    # Exact content match (quick_hash computed on the fly)
    res_dup_content = api_client.get(
        "/api/sorter/check-duplicate", params={"path": str(vid_stream)}
    )
    assert res_dup_content.status_code == 200
    d_data = res_dup_content.json()
    assert d_data["is_duplicate"] is True
    assert d_data["match_type"] == "content"

    # Exact filename match
    diff_file = src_dir / "library_match.mp4"
    diff_file.write_bytes(b"DIFFERENT_CONTENT" * 20)  # different size, same name
    res_dup_name = api_client.get(
        "/api/sorter/check-duplicate", params={"file_id": "library_match.mp4"}
    )
    assert res_dup_name.status_code == 200
    assert res_dup_name.json()["is_duplicate"] is True
    assert res_dup_name.json()["match_type"] == "name"

    # Empty file is not duplicate
    empty_f = src_dir / "empty.mp4"
    empty_f.write_bytes(b"")
    res_empty = api_client.get(
        "/api/sorter/check-duplicate", params={"path": str(empty_f)}
    )
    assert res_empty.status_code == 200
    assert res_empty.json()["is_duplicate"] is False
