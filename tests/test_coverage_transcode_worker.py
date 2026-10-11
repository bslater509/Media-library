"""Tests targeting deep code coverage of TranscodeWorker and transcode services."""

import asyncio
import os
import shutil
import threading
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from media_library.core.state import state
from media_library.db.connection import db_conn
from media_library.workers.transcode_worker import (
    TranscodeWorker,
    enqueue_pretranscode_for_source,
    transcode_worker_instance,
)


def test_enqueue_pretranscode_for_source(tmp_path):
    # 1. Non-existent directory
    enqueue_pretranscode_for_source(str(tmp_path / "nonexistent"))

    # 2. Directory with various files
    src_dir = tmp_path / "source_videos"
    src_dir.mkdir()
    (src_dir / ".hidden.mkv").write_bytes(b"\x00" * 10)
    (src_dir / "clip1.mkv").write_bytes(b"\x00" * 10)
    (src_dir / "clip2.avi").write_bytes(b"\x00" * 10)
    (src_dir / "clip3.mp4").write_bytes(b"\x00" * 10)  # Native, skipped
    (src_dir / "clip4.txt").write_bytes(b"text")       # Non-video, skipped

    worker = TranscodeWorker()
    with patch(
        "media_library.workers.transcode_worker.transcode_worker_instance", worker
    ):
        enqueue_pretranscode_for_source(str(src_dir))
        assert len(worker.transcode_items_map) == 2
        names = {t["filename"] for t in worker.transcode_items_map.values()}
        assert names == {"clip1.mkv", "clip2.avi"}

        # Running again should skip already queued items
        enqueue_pretranscode_for_source(str(src_dir))
        assert len(worker.transcode_items_map) == 2

    # 3. Exception in scandir
    with patch("os.scandir", side_effect=PermissionError("Permission denied")):
        enqueue_pretranscode_for_source(str(src_dir))


def test_transcode_worker_enqueue_for_backup(sandbox_env):
    media = sandbox_env["media_dir"]
    state["destination_dir"] = str(media)
    sub = media / "backup_videos"
    sub.mkdir()
    f_mkv = sub / "video.mkv"
    f_mkv.write_bytes(b"\x00" * 64)
    f_mp4 = sub / "video.mp4"
    f_mp4.write_bytes(b"\x00" * 64)

    worker = TranscodeWorker()

    # 1. Enqueue folder
    enqueued_folder = worker.enqueue_for_backup(folder_path=str(sub))
    assert len(enqueued_folder) == 1
    assert enqueued_folder[0]["filename"] == "video.mkv"

    # Enqueue folder again skips existing
    assert len(worker.enqueue_for_backup(folder_path=str(sub))) == 0

    # 2. Enqueue single file
    f2_avi = sub / "another.avi"
    f2_avi.write_bytes(b"\x00" * 64)
    enqueued_file = worker.enqueue_for_backup(file_path=str(f2_avi))
    assert len(enqueued_file) == 1
    assert enqueued_file[0]["filename"] == "another.avi"

    # 3. Enqueue invalid / non-video file
    f_txt = sub / "doc.txt"
    f_txt.write_text("hello")
    assert len(worker.enqueue_for_backup(file_path=str(f_txt))) == 0

    # 4. Enqueue non-existent folder
    assert len(worker.enqueue_for_backup(folder_path=str(sub / "ghost"))) == 0


def test_transcode_worker_enqueue_all_for_backup(sandbox_env):
    media = sandbox_env["media_dir"]
    state["destination_dir"] = str(media)
    sub1 = media / "dir1"
    sub1.mkdir()
    f1 = sub1 / "movie1.mkv"
    f1.write_bytes(b"\x00" * 64)

    with db_conn() as conn, conn:
        conn.execute(
            """INSERT INTO files (path, name, folder_path, type, ext, size, size_formatted, mtime)
               VALUES (?, ?, ?, 'video', '.mkv', 64, '64 B', 100.0)""",
            (str(f1), "movie1.mkv", str(sub1)),
        )

    worker = TranscodeWorker()
    enqueued = worker.enqueue_all_for_backup()
    assert len(enqueued) == 1
    assert enqueued[0]["filename"] == "movie1.mkv"

    # Enqueue again skips existing
    assert len(worker.enqueue_all_for_backup()) == 0


def test_transcode_worker_pause_resume_cancel():
    worker = TranscodeWorker()
    assert worker.transcode_paused is False
    worker.pause()
    assert worker.transcode_paused is True
    assert worker._status == "paused"
    worker.resume()
    assert worker.transcode_paused is False
    assert worker._status == "running"

    # Cancel non-existent
    assert worker.cancel("nonexistent") is False

    # Cancel existing
    ev = threading.Event()
    worker.transcode_items_map["t1"] = {
        "id": "t1",
        "status": "queued",
        "abort_event": ev,
    }
    assert worker.cancel("t1") is True
    assert worker.transcode_items_map["t1"]["status"] == "cancelled"
    assert ev.is_set()

    # Cancel all
    ev2 = threading.Event()
    worker.transcode_items_map["t2"] = {
        "id": "t2",
        "status": "transcoding",
        "abort_event": ev2,
    }
    worker.cancel_all()
    assert worker.transcode_items_map["t2"]["status"] == "cancelled"
    assert ev2.is_set()


def test_transcode_task_remux_flow(tmp_path):
    async def _test():
        worker = TranscodeWorker()
        src_file = tmp_path / "clip.mkv"
        src_file.write_bytes(b"\x00" * 2048)

        item = {
            "id": "remux1",
            "source_path": str(src_file),
            "status": "queued",
            "abort_event": threading.Event(),
        }

        with patch(
            "media_library.workers.transcode_worker.probe_video_meta",
            return_value={"duration": 10.0, "video_codec": "h264", "audio_codec": "aac"},
        ):
            mock_proc = AsyncMock()
            mock_proc.returncode = 0
            mock_proc.wait = AsyncMock(return_value=0)

            async def fake_proc(*args, **kwargs):
                from media_library.core.config import TRANSCODE_TEMP_DIR
                temp_p = TRANSCODE_TEMP_DIR / f"temp_{item['id']}_clip.mp4"
                temp_p.write_bytes(b"MP4DATA" * 500)
                return mock_proc

            with patch("asyncio.create_subprocess_exec", side_effect=fake_proc):
                await worker._run_transcode_task(item)

            assert item["status"] == "completed"
            assert item["method"] == "remux"
            assert os.path.exists(item["target_path"])
            assert not os.path.exists(src_file)

    asyncio.run(_test())


def test_transcode_task_remux_abort(tmp_path):
    async def _test():
        worker = TranscodeWorker()
        src_file = tmp_path / "clip_abort.mkv"
        src_file.write_bytes(b"\x00" * 2048)

        abort_ev = threading.Event()
        item = {
            "id": "remux_abort",
            "source_path": str(src_file),
            "status": "queued",
            "abort_event": abort_ev,
        }

        with patch(
            "media_library.workers.transcode_worker.probe_video_meta",
            return_value={"duration": 10.0, "video_codec": "h264", "audio_codec": "aac"},
        ):
            mock_proc = AsyncMock()
            mock_proc.returncode = None
            mock_proc.terminate = MagicMock()

            async def fake_wait():
                abort_ev.set()
                raise asyncio.TimeoutError()

            mock_proc.wait = fake_wait

            with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
                await worker._run_transcode_task(item)

            assert item["status"] == "cancelled"
            mock_proc.terminate.assert_called()

    asyncio.run(_test())


def test_transcode_task_reencode_progress_and_success(tmp_path):
    async def _test():
        worker = TranscodeWorker()
        src_file = tmp_path / "clip_reencode.avi"
        src_file.write_bytes(b"\x00" * 2048)

        item = {
            "id": "reencode1",
            "source_path": str(src_file),
            "status": "queued",
            "abort_event": threading.Event(),
        }

        with patch(
            "media_library.workers.transcode_worker.probe_video_meta",
            return_value={"duration": 20.0, "video_codec": "mpeg4", "audio_codec": "mp3"},
        ):
            progress_lines = [
                b"out_time_us=10000000\n",
                b"fps=24.5\n",
                b"speed=2.0x\n",
                b"out_time_us=20000000\n",
                b"",
            ]

            class FakeStdout:
                def __init__(self):
                    self.lines = list(progress_lines)

                async def readline(self):
                    if self.lines:
                        return self.lines.pop(0)
                    return b""

            mock_proc = AsyncMock()
            mock_proc.returncode = 0
            mock_proc.wait = AsyncMock(return_value=0)
            mock_proc.stdout = FakeStdout()

            async def fake_proc(*args, **kwargs):
                from media_library.core.config import TRANSCODE_TEMP_DIR
                temp_p = TRANSCODE_TEMP_DIR / f"temp_{item['id']}_clip_reencode.mp4"
                temp_p.write_bytes(b"REENCODED" * 500)
                return mock_proc

            with patch("asyncio.create_subprocess_exec", side_effect=fake_proc):
                await worker._run_transcode_task(item)

            assert item["status"] == "completed"
            assert item["method"] == "reencode"
            assert item["fps"] == 24.5
            assert item["speed"] == "2.0x"
            assert os.path.exists(item["target_path"])

    asyncio.run(_test())


def test_transcode_task_reencode_abort(tmp_path):
    async def _test():
        worker = TranscodeWorker()
        src_file = tmp_path / "clip_reenc_abort.avi"
        src_file.write_bytes(b"\x00" * 2048)

        abort_ev = threading.Event()
        item = {
            "id": "reencode_abort",
            "source_path": str(src_file),
            "status": "queued",
            "abort_event": abort_ev,
        }

        with patch(
            "media_library.workers.transcode_worker.probe_video_meta",
            return_value={"duration": 20.0, "video_codec": "mpeg4", "audio_codec": "mp3"},
        ):
            mock_proc = AsyncMock()
            mock_proc.returncode = None
            mock_proc.terminate = MagicMock()

            class SlowStdout:
                async def readline(self):
                    abort_ev.set()
                    await asyncio.sleep(0.01)
                    return b""

            mock_proc.stdout = SlowStdout()

            async def fake_wait():
                await asyncio.sleep(0.01)
                raise asyncio.TimeoutError()

            mock_proc.wait = fake_wait

            with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
                await worker._run_transcode_task(item)

            assert item["status"] == "cancelled"
            mock_proc.terminate.assert_called()

    asyncio.run(_test())


def test_transcode_task_fallback_and_failure(tmp_path):
    async def _test():
        worker = TranscodeWorker()
        src_file = tmp_path / "clip_fallback.wmv"
        src_file.write_bytes(b"\x00" * 2048)

        item = {
            "id": "fallback1",
            "source_path": str(src_file),
            "status": "queued",
            "abort_event": threading.Event(),
        }

        with patch(
            "media_library.workers.transcode_worker.probe_video_meta",
            return_value={"duration": 15.0, "video_codec": "wmv2", "audio_codec": "wmav2"},
        ):
            proc_reencode_fail = AsyncMock()
            proc_reencode_fail.returncode = 1
            proc_reencode_fail.wait = AsyncMock(return_value=1)
            proc_reencode_fail.stdout.readline = AsyncMock(return_value=b"")

            proc_fallback_ok = AsyncMock()
            proc_fallback_ok.returncode = 0
            proc_fallback_ok.wait = AsyncMock(return_value=0)

            call_count = 0

            async def fake_proc(*args, **kwargs):
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    return proc_reencode_fail
                else:
                    from media_library.core.config import TRANSCODE_TEMP_DIR
                    temp_p = TRANSCODE_TEMP_DIR / f"temp_{item['id']}_clip_fallback.mp4"
                    temp_p.write_bytes(b"FALLBACK_DATA" * 500)
                    return proc_fallback_ok

            with patch("asyncio.create_subprocess_exec", side_effect=fake_proc):
                await worker._run_transcode_task(item)

            assert item["status"] == "completed"
            assert item["method"] == "fallback_reencode"

    asyncio.run(_test())


def test_transcode_commit_cross_device_and_errors(tmp_path):
    async def _test():
        worker = TranscodeWorker()
        src_file = tmp_path / "cross_dev.mkv"
        src_file.write_bytes(b"\x00" * 2048)

        item = {
            "id": "cross_dev",
            "source_path": str(src_file),
            "status": "queued",
            "abort_event": threading.Event(),
        }

        with patch(
            "media_library.workers.transcode_worker.probe_video_meta",
            return_value={"duration": 10.0, "video_codec": "h264", "audio_codec": "aac"},
        ):
            mock_proc = AsyncMock()
            mock_proc.returncode = 0
            mock_proc.wait = AsyncMock(return_value=0)

            async def fake_proc(*args, **kwargs):
                from media_library.core.config import TRANSCODE_TEMP_DIR
                temp_p = TRANSCODE_TEMP_DIR / f"temp_{item['id']}_cross_dev.mp4"
                temp_p.write_bytes(b"DATA" * 500)
                return mock_proc

            real_replace = os.replace
            replace_calls = 0

            def fake_replace(src, dst):
                nonlocal replace_calls
                replace_calls += 1
                if replace_calls == 1:
                    raise OSError(18, "Invalid cross-device link")
                return real_replace(src, dst)

            with patch("asyncio.create_subprocess_exec", side_effect=fake_proc), patch(
                "os.replace", side_effect=fake_replace
            ):
                await worker._run_transcode_task(item)

            assert item["status"] == "completed"
            assert os.path.exists(item["target_path"])

    asyncio.run(_test())


def test_transcode_worker_run_loop():
    async def _test():
        worker = TranscodeWorker()

        item_cancelled = {"id": "c1", "status": "cancelled", "source_path": "/fake/p.mkv"}
        await worker.transcode_queue.put(item_cancelled)

        item_missing = {
            "id": "m1",
            "status": "queued",
            "source_path": "/fake/ghost.mkv",
            "abort_event": threading.Event(),
        }
        await worker.transcode_queue.put(item_missing)

        # Also add a valid task to exercise active_transcode and task execution
        item_valid = {
            "id": "v1",
            "status": "queued",
            "source_path": "/fake/valid.mkv",
            "abort_event": threading.Event(),
        }
        await worker.transcode_queue.put(item_valid)

        async def fake_run_task(item):
            item["status"] = "completed"

        worker._run_transcode_task = fake_run_task

        with patch("os.path.exists", side_effect=lambda p: p == "/fake/valid.mkv"):
            loop_task = asyncio.create_task(worker._run_loop())
            await asyncio.sleep(0.05)
            loop_task.cancel()
            try:
                await loop_task
            except asyncio.CancelledError:
                pass

        assert item_missing["status"] == "failed"
        assert item_valid["status"] == "completed"

    asyncio.run(_test())


def test_transcode_worker_status():
    worker = TranscodeWorker()
    worker.active_transcode = {"id": "act1", "status": "transcoding"}
    st = worker.get_status()
    assert st["active"]["id"] == "act1"
    assert st["paused"] is False
    assert st["queue_length"] == 0


def test_transcode_task_reencode_invalid_progress(tmp_path):
    async def _test():
        worker = TranscodeWorker()
        src_file = tmp_path / "clip_inv.avi"
        src_file.write_bytes(b"\x00" * 2048)

        item = {
            "id": "reenc_inv",
            "source_path": str(src_file),
            "status": "queued",
            "abort_event": threading.Event(),
        }

        with patch(
            "media_library.workers.transcode_worker.probe_video_meta",
            return_value={"duration": 10.0, "video_codec": "mpeg4", "audio_codec": "mp3"},
        ):
            # Invalid progress lines triggering ValueError
            progress_lines = [
                b"out_time_us=invalid_number\n",
                b"fps=not_a_float\n",
                b"speed=not_a_speed\n",
                b"",
            ]

            class FakeStdout:
                def __init__(self):
                    self.lines = list(progress_lines)

                async def readline(self):
                    if self.lines:
                        return self.lines.pop(0)
                    return b""

            mock_proc = AsyncMock()
            mock_proc.returncode = 0
            mock_proc.wait = AsyncMock(return_value=0)
            mock_proc.stdout = FakeStdout()

            async def fake_proc(*args, **kwargs):
                from media_library.core.config import TRANSCODE_TEMP_DIR
                temp_p = TRANSCODE_TEMP_DIR / f"temp_{item['id']}_clip_inv.mp4"
                temp_p.write_bytes(b"DATA" * 500)
                return mock_proc

            with patch("asyncio.create_subprocess_exec", side_effect=fake_proc):
                await worker._run_transcode_task(item)

            assert item["status"] == "completed"

    asyncio.run(_test())


def test_transcode_task_commit_failure(tmp_path):
    async def _test():
        worker = TranscodeWorker()
        src_file = tmp_path / "clip_commit_fail.mkv"
        src_file.write_bytes(b"\x00" * 2048)

        item = {
            "id": "commit_fail",
            "source_path": str(src_file),
            "status": "queued",
            "abort_event": threading.Event(),
        }

        with patch(
            "media_library.workers.transcode_worker.probe_video_meta",
            return_value={"duration": 10.0, "video_codec": "h264", "audio_codec": "aac"},
        ):
            mock_proc = AsyncMock()
            mock_proc.returncode = 0
            mock_proc.wait = AsyncMock(return_value=0)

            async def fake_proc(*args, **kwargs):
                from media_library.core.config import TRANSCODE_TEMP_DIR
                temp_p = TRANSCODE_TEMP_DIR / f"temp_{item['id']}_clip_commit_fail.mp4"
                temp_p.write_bytes(b"DATA" * 500)
                return mock_proc

            with patch("asyncio.create_subprocess_exec", side_effect=fake_proc), patch(
                "os.replace", side_effect=PermissionError("Cannot write to target")
            ):
                await worker._run_transcode_task(item)

            assert item["status"] == "failed"
            assert "Failed to commit" in item["error"]

    asyncio.run(_test())


def test_transcode_task_all_failed(tmp_path):
    async def _test():
        worker = TranscodeWorker()
        src_file = tmp_path / "clip_total_fail.mkv"
        src_file.write_bytes(b"\x00" * 2048)

        item = {
            "id": "total_fail",
            "source_path": str(src_file),
            "status": "queued",
            "abort_event": threading.Event(),
        }

        with patch(
            "media_library.workers.transcode_worker.probe_video_meta",
            return_value={"duration": 10.0, "video_codec": "h264", "audio_codec": "aac"},
        ):
            mock_proc = AsyncMock()
            mock_proc.returncode = 1
            mock_proc.wait = AsyncMock(return_value=1)
            mock_proc.stdout = AsyncMock()
            mock_proc.stdout.readline = AsyncMock(return_value=b"")

            with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
                await worker._run_transcode_task(item)

            assert item["status"] == "failed"
            assert "Conversion failed" in item["error"]

    asyncio.run(_test())

