"""Tests for the in-place H.265 (x265) conversion worker and its routes."""

import asyncio
import os
import threading
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from media_library.core.state import state
from media_library.db.connection import db_conn
from media_library.workers.h265_worker import (
    H265ConvertWorker,
    h265_worker_instance,
)


def _make_item(worker, path, status="queued"):
    job = {
        "id": uuid.uuid4().hex[:10],
        "source_path": str(path),
        "filename": os.path.basename(str(path)),
        "target_path": str(path),
        "status": status,
        "percent": 0.0,
        "fps": 0.0,
        "speed": "",
        "eta": "",
        "method": "x265",
        "message": "",
        "error": None,
        "created_at": 0.0,
        "size_before": 0,
        "size_after": 0,
        "abort_event": threading.Event(),
    }
    worker.jobs[job["id"]] = job
    return job


def test_enqueue_and_duplicate_rejection(tmp_path):
    worker = H265ConvertWorker()
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"\x00" * 2048)

    job = worker.enqueue(str(src))
    assert job["status"] == "queued"
    assert worker.get_status()["queued_count"] == 1

    # A second enqueue for the same path (idle worker) is refused.
    try:
        worker.enqueue(str(src))
        raise AssertionError("expected ValueError for duplicate enqueue")
    except ValueError:
        pass


def test_status_for_path_and_cancel(tmp_path):
    worker = H265ConvertWorker()
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"\x00" * 2048)
    job = worker.enqueue(str(src))

    status = worker.get_status(str(src))
    assert status["job"] is not None
    assert status["job"]["id"] == job["id"]
    # The non-serializable abort Event never leaks into the payload.
    assert "abort_event" not in status["job"]

    assert worker.cancel(job["id"]) is True
    assert job["status"] == "cancelled"
    assert job["abort_event"].is_set()
    # Cancelling a terminal job is a no-op.
    assert worker.cancel(job["id"]) is False


def test_build_command_uses_x265_and_audio_policy():
    worker = H265ConvertWorker()
    copy_cmd = worker._build_command("/in.mkv", "/tmp/out.mp4", True)
    assert "libx265" in copy_cmd
    assert copy_cmd[copy_cmd.index("-crf") + 1] == "18"
    assert copy_cmd[copy_cmd.index("-c:a") + 1] == "copy"
    assert copy_cmd[copy_cmd.index("-tag:v") + 1] == "hvc1"

    encode_cmd = worker._build_command("/in.mkv", "/tmp/out.mp4", False)
    assert encode_cmd[encode_cmd.index("-c:a") + 1] == "aac"
    assert encode_cmd[encode_cmd.index("-f") + 1] == "mp4"


def test_convert_skips_already_hevc(tmp_path):
    async def _test():
        worker = H265ConvertWorker()
        src = tmp_path / "already.mp4"
        src.write_bytes(b"\x00" * 2048)
        item = _make_item(worker, src)

        with patch(
            "media_library.workers.h265_worker.probe_video_meta",
            return_value={"duration": 5.0, "video_codec": "hevc", "audio_codec": "aac"},
        ):
            await worker._convert(item)

        assert item["status"] == "completed"
        assert item["method"] == "skipped"
        assert "H.265" in item["message"]
        # Original untouched.
        assert src.exists()

    asyncio.run(_test())


def test_convert_success_replaces_and_indexes(sandbox_env):
    media_dir = sandbox_env["media_dir"]
    state["destination_dir"] = str(media_dir)

    async def _test():
        worker = H265ConvertWorker()
        src = media_dir / "movie.mkv"
        src.write_bytes(b"\x00" * 2048)
        item = _make_item(worker, src)
        worker.jobs[item["id"]] = item

        from media_library.workers import h265_worker as h265_mod

        target = media_dir / "movie.mp4"
        temp_path = h265_mod.TRANSCODE_TEMP_DIR / f"h265_{item['id']}_movie.mp4"

        progress = [b"out_time_us=5000000\n", b"fps=33.2\n", b"speed=4.0x\n", b""]

        class FakeStdout:
            def __init__(self):
                self.lines = list(progress)

            async def readline(self):
                return self.lines.pop(0) if self.lines else b""

        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.wait = AsyncMock(return_value=0)
        mock_proc.stdout = FakeStdout()

        async def fake_proc(*args, **kwargs):
            temp_path.write_bytes(b"ENCODED" * 1000)
            return mock_proc

        with (
            patch(
                "media_library.workers.h265_worker.probe_video_meta",
                return_value={
                    "duration": 10.0,
                    "video_codec": "h264",
                    "audio_codec": "aac",
                    "width": 1920,
                    "height": 1080,
                    "fps": 30.0,
                },
            ),
            patch("asyncio.create_subprocess_exec", side_effect=fake_proc),
        ):
            await worker._convert(item)

        assert item["status"] == "completed"
        assert item["percent"] == 100.0
        assert item["fps"] == 33.2
        # Original container replaced by the MP4 output.
        assert target.exists()
        assert not src.exists()
        assert item["size_after"] == target.stat().st_size

        with db_conn() as conn:
            row = conn.execute(
                "SELECT video_codec, audio_codec, has_thumb, has_animated, quick_hash "
                "FROM files WHERE path = ?",
                (str(target),),
            ).fetchone()
        assert row is not None
        assert row["video_codec"] == "hevc"
        assert row["audio_codec"] == "aac"
        assert row["has_thumb"] == 0
        assert row["quick_hash"] is None

    asyncio.run(_test())


def test_convert_failure_preserves_original(tmp_path):
    async def _test():
        worker = H265ConvertWorker()
        src = tmp_path / "broken.mp4"
        src.write_bytes(b"\x00" * 2048)
        item = _make_item(worker, src)

        mock_proc = AsyncMock()
        mock_proc.returncode = 1
        mock_proc.wait = AsyncMock(return_value=1)

        class FakeStdout:
            async def readline(self):
                return b""

        mock_proc.stdout = FakeStdout()

        with (
            patch(
                "media_library.workers.h265_worker.probe_video_meta",
                return_value={
                    "duration": 10.0,
                    "video_codec": "h264",
                    "audio_codec": "aac",
                },
            ),
            patch("asyncio.create_subprocess_exec", return_value=mock_proc),
        ):
            await worker._convert(item)

        assert item["status"] == "failed"
        assert src.exists()  # never destroyed on failure

    asyncio.run(_test())


def test_convert_abort_cancels(tmp_path):
    async def _test():
        worker = H265ConvertWorker()
        src = tmp_path / "cancel.mp4"
        src.write_bytes(b"\x00" * 2048)
        item = _make_item(worker, src)

        abort_ev = item["abort_event"]

        mock_proc = AsyncMock()
        mock_proc.returncode = None
        mock_proc.terminate = MagicMock()

        async def fake_wait():
            abort_ev.set()
            raise asyncio.TimeoutError()

        mock_proc.wait = fake_wait

        with (
            patch(
                "media_library.workers.h265_worker.probe_video_meta",
                return_value={
                    "duration": 10.0,
                    "video_codec": "h264",
                    "audio_codec": "aac",
                },
            ),
            patch("asyncio.create_subprocess_exec", return_value=mock_proc),
        ):
            await worker._convert(item)

        assert item["status"] == "cancelled"
        mock_proc.terminate.assert_called()
        assert src.exists()

    asyncio.run(_test())


def test_x265_routes(api_client):
    dest = state["destination_dir"]
    vid = os.path.join(dest, "route_clip.mp4")
    with open(vid, "wb") as f:
        f.write(b"\x00" * 2048)

    # Non-video paths are rejected.
    txt = os.path.join(dest, "notes.txt")
    with open(txt, "w") as f:
        f.write("hi")
    bad = api_client.post("/api/backup/transcode/x265", json={"path": txt})
    assert bad.status_code == 400

    missing = api_client.post(
        "/api/backup/transcode/x265", json={"path": os.path.join(dest, "nope.mp4")}
    )
    assert missing.status_code == 404

    ok = api_client.post("/api/backup/transcode/x265", json={"path": vid})
    assert ok.status_code == 200
    payload = ok.json()
    assert payload["success"] is True
    job_id = payload["job_id"]

    # Re-queueing the same path while it is pending conflicts.
    dup = api_client.post("/api/backup/transcode/x265", json={"path": vid})
    assert dup.status_code == 409

    status = api_client.get("/api/backup/transcode/x265/status", params={"path": vid})
    assert status.status_code == 200
    assert status.json()["job"]["id"] == job_id

    cancel = api_client.post(
        "/api/backup/transcode/x265/cancel", json={"job_id": job_id}
    )
    assert cancel.status_code == 200
    assert h265_worker_instance.jobs[job_id]["status"] == "cancelled"

    # Unknown job id -> 404.
    assert (
        api_client.post(
            "/api/backup/transcode/x265/cancel", json={"job_id": "definitely-missing"}
        ).status_code
        == 404
    )


def test_status_endpoint_exposes_h265(api_client):
    body = api_client.get("/api/system/status").json()
    assert "h265" in body
    assert body["h265"]["status"] == "idle"
    assert body["h265"]["active_count"] == 0
