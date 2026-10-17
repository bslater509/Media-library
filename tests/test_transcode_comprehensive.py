"""Comprehensive integration and unit tests for transcode router and worker."""

import asyncio
import threading
from pathlib import Path
from unittest.mock import AsyncMock, patch

from media_library.core.state import state
from media_library.db.connection import db_conn
from media_library.workers.transcode_worker import (
    TranscodeWorker,
    transcode_worker_instance,
)


def test_transcode_router_controls(api_client, tmp_path):
    # 1. Pause and Resume
    res_pause = api_client.post("/api/transcode/pause")
    assert res_pause.status_code == 200
    assert res_pause.json()["success"] is True

    # Call again to toggle resume
    res_resume = api_client.post("/api/transcode/pause")
    assert res_resume.status_code == 200
    assert res_resume.json()["success"] is True

    # 2. Status
    res_status = api_client.get("/api/transcode/status")
    assert res_status.status_code == 200
    st = res_status.json()
    assert "paused" in st
    assert "queue_length" in st

    # 3. Cancel task - 404 for missing
    res_cancel_404 = api_client.post(
        "/api/transcode/cancel", json={"task_id": "nonexistent_id"}
    )
    assert res_cancel_404.status_code == 404

    # Add a mock task and cancel it
    mock_task = {
        "id": "test_t1",
        "file_id": "test.mkv",
        "filename": "test.mkv",
        "source_path": "/fake/test.mkv",
        "status": "queued",
        "abort_event": threading.Event(),
    }
    transcode_worker_instance.transcode_items_map["test_t1"] = mock_task

    res_cancel_ok = api_client.post(
        "/api/transcode/cancel", json={"task_id": "test_t1"}
    )
    assert res_cancel_ok.status_code == 200
    assert res_cancel_ok.json()["action"] == "cancelled"

    # 4. Cancel All
    res_cancel_all = api_client.post("/api/transcode/cancel_all")
    assert res_cancel_all.status_code == 200
    assert res_cancel_all.json()["success"] is True


def test_backup_transcode_summary_and_enqueue(api_client):
    dest = Path(state["destination_dir"])
    mkv_file = dest / "movie.mkv"
    avi_file = dest / "home.avi"
    mkv_file.write_bytes(b"\x1a\x45\xdf\xa3" + b"X" * 1024)
    avi_file.write_bytes(b"RIFF" + b"Y" * 2048)

    with db_conn() as conn, conn:
        conn.execute(
            """INSERT INTO files (path, name, folder_path, type, ext, size, size_formatted, mtime)
               VALUES (?, ?, ?, 'video', '.mkv', 1024, '1 KB', 100.0)""",
            (str(mkv_file), "movie.mkv", str(dest)),
        )
        conn.execute(
            """INSERT INTO files (path, name, folder_path, type, ext, size, size_formatted, mtime)
               VALUES (?, ?, ?, 'video', '.avi', 2048, '2 KB', 200.0)""",
            (str(avi_file), "home.avi", str(dest)),
        )

    # 1. Summary
    res_summary = api_client.get("/api/backup/transcode/summary")
    assert res_summary.status_code == 200
    s_data = res_summary.json()
    assert s_data["pending_count"] >= 2
    assert ".mkv" in s_data["breakdown"]
    assert ".avi" in s_data["breakdown"]

    # 2. Enqueue single file
    res_enq_file = api_client.post(
        "/api/backup/transcode/enqueue", json={"file_path": str(mkv_file)}
    )
    assert res_enq_file.status_code == 200
    assert res_enq_file.json()["enqueued_count"] == 1

    # 3. Enqueue folder
    res_enq_folder = api_client.post(
        "/api/backup/transcode/enqueue", json={"folder_path": str(dest)}
    )
    assert res_enq_folder.status_code == 200

    # 4. Enqueue all
    res_enq_all = api_client.post("/api/backup/transcode/enqueue_all")
    assert res_enq_all.status_code == 200


def test_transcode_stream_error_branches(api_client):
    dest = Path(state["destination_dir"])
    # Missing source file in stream_backup_transcode raises 404
    res_missing = api_client.get(
        "/api/backup/transcode_stream", params={"path": str(dest / "missing.mkv")}
    )
    assert res_missing.status_code == 404

    # transcode_video 404 for missing video
    res_404 = api_client.get("/api/transcode/nonexistent.mkv")
    assert res_404.status_code == 404


def test_transcode_worker_map_pruning():
    worker = TranscodeWorker()
    # Populate with 1050 completed items
    for i in range(1050):
        worker.transcode_items_map[f"task_{i}"] = {
            "id": f"task_{i}",
            "filename": f"f_{i}.mkv",
            "status": "completed",
        }
    new_item = {
        "id": "new_task",
        "filename": "new.mkv",
        "status": "queued",
    }
    worker.enqueue(new_item)
    # The map should have pruned older completed tasks
    assert len(worker.transcode_items_map) <= 1001


def test_transcode_worker_enqueue_indexed_folder(sandbox_env):
    media = sandbox_env["media_dir"]
    sub = media / "indexed_series"
    sub.mkdir()
    f1 = sub / "ep1.mkv"
    f2 = sub / "ep2.mp4"  # MP4 should not be queued
    f1.write_bytes(b"\x00" * 100)
    f2.write_bytes(b"\x00" * 100)

    with db_conn() as conn, conn:
        conn.execute(
            """INSERT INTO files (path, name, folder_path, type, ext, size, size_formatted, mtime)
               VALUES (?, ?, ?, 'video', '.mkv', 100, '100 B', 100.0)""",
            (str(f1), "ep1.mkv", str(sub)),
        )
        conn.execute(
            """INSERT INTO files (path, name, folder_path, type, ext, size, size_formatted, mtime)
               VALUES (?, ?, ?, 'video', '.mp4', 100, '100 B', 100.0)""",
            (str(f2), "ep2.mp4", str(sub)),
        )

    worker = TranscodeWorker()
    enqueued = worker.enqueue_indexed_folder(str(sub))
    assert len(enqueued) == 1
    assert enqueued[0]["filename"] == "ep1.mkv"

    # Enqueue again should skip existing queued item
    enqueued2 = worker.enqueue_indexed_folder(str(sub))
    assert len(enqueued2) == 0


def test_transcode_task_execution_handling(tmp_path):
    worker = TranscodeWorker()

    async def _run():
        # 1. Missing source file sets status to failed
        item_missing = {
            "id": "t_miss",
            "source_path": str(tmp_path / "ghost.mkv"),
            "status": "queued",
            "abort_event": threading.Event(),
        }
        await worker._run_transcode_task(item_missing)
        assert item_missing["status"] == "failed"
        assert "missing" in item_missing["error"].lower()

        # 2. Existing file collision handling
        src_file = tmp_path / "video.mkv"
        src_file.write_bytes(b"\x00" * 64)
        # Target already exists
        existing_mp4 = tmp_path / "video.mp4"
        existing_mp4.write_bytes(b"\x00" * 64)

        item_collision = {
            "id": "t_col",
            "source_path": str(src_file),
            "status": "queued",
            "abort_event": threading.Event(),
        }

        # Mock probe and subprocess to avoid needing real ffmpeg conversion
        with patch(
            "media_library.workers.transcode_worker.probe_video_meta",
            return_value={
                "duration": 10.0,
                "video_codec": "h264",
                "audio_codec": "aac",
            },
        ):
            mock_proc = AsyncMock()
            mock_proc.returncode = 0
            mock_proc.communicate.return_value = (b"", b"")
            mock_proc.wait.return_value = 0
            mock_proc.stdout = AsyncMock()
            mock_proc.stdout.readline.return_value = b""

            with (
                patch("asyncio.create_subprocess_exec", return_value=mock_proc),
                patch("shutil.move"),
            ):
                await worker._run_transcode_task(item_collision)

        # Collision target should end with _converted_1.mp4
        assert "_converted_1.mp4" in item_collision["target_path"]

    asyncio.run(_run())
