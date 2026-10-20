"""Guards for a frame pass that a stalled mount (or a wedged detector) never ends.

A remote FUSE mount can stop answering mid-read. Before the stall timeout the
sweep would wait on that read for as long as the mount stayed wedged, holding a
decode slot, and the ordered pipeline could wait forever on a detection that
never came back. These tests pin the two properties that keep that local: the
pass reports *why* it ended, and a cut-short pass leaves the file pending
instead of recording a partial scan as final.
"""

import asyncio
import io
import json

import numpy as np
from PIL import Image

from media_library.services.ffmpeg_frames import iter_video_scan_frames


class _StalledProc:
    """A subprocess whose stdout never yields (a wedged mount read)."""

    def __init__(self):
        self.returncode = None
        self.stdout = self
        self.killed = False
        self.waited = False

    async def read(self, _size):
        await asyncio.sleep(3600)

    def kill(self):
        self.killed = True

    async def wait(self):
        self.waited = True
        self.returncode = -9
        return self.returncode

    def feed_eof(self):
        pass


class _ScriptedProc:
    """A subprocess that hands out prepared chunks, then EOF."""

    def __init__(self, chunks):
        self.returncode = None
        self.stdout = self
        self._chunks = list(chunks)
        self.killed = False

    async def read(self, _size):
        if self._chunks:
            return self._chunks.pop(0)
        return b""

    def kill(self):
        self.killed = True

    async def wait(self):
        self.returncode = -9
        return self.returncode

    def feed_eof(self):
        pass


def _jpeg(color=(220, 30, 30)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), color).save(buf, "JPEG")
    return buf.getvalue()


def _collect(gen_factory):
    async def run():
        return [item async for item in gen_factory()]

    return asyncio.run(run())


def test_stalled_frame_pass_gives_up_and_reports_a_stall(monkeypatch):
    proc = _StalledProc()

    async def fake_exec(*_args, **_kwargs):
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    report: dict = {}

    frames = _collect(
        lambda: iter_video_scan_frames(
            "/library/clip.mp4",
            2.0,
            960,
            100,
            timeout_s=600.0,
            stall_timeout_s=0.2,
            report=report,
        )
    )

    assert frames == []
    assert report == {"frames": 0, "complete": False, "reason": "stall"}
    # The decode slot is only really released if the subprocess is gone.
    assert proc.killed and proc.waited


def test_finished_frame_pass_reports_completion(monkeypatch):
    frame = _jpeg()
    proc = _ScriptedProc([frame[:10], frame[10:] + b"\xff\xd9", b""])

    async def fake_exec(*_args, **_kwargs):
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    report: dict = {}

    frames = _collect(
        lambda: iter_video_scan_frames(
            "/library/clip.mp4",
            2.0,
            960,
            100,
            stall_timeout_s=5.0,
            report=report,
        )
    )

    assert len(frames) == 1
    assert report["complete"] is True
    assert report["reason"] == "eof"
    assert report["frames"] == 1


def test_stalled_detection_pipeline_leaves_the_file_pending(
    monkeypatch, sandbox_env, fake_face_engine
):
    """A detection that never returns ends the file's pass as *pending*."""
    from media_library.workers import face_worker

    async def stalled_pass(_path, _interval, _width, _cap, _timeout, **kwargs):
        report = kwargs.get("report")
        if report is not None:
            report.update({"frames": 0, "complete": False, "reason": "stall"})
        if False:  # pragma: no cover - keeps this an async generator
            yield

    monkeypatch.setattr(face_worker, "iter_video_scan_frames", stalled_pass)
    worker = face_worker.FaceWorker()
    row = {
        "path": str(sandbox_env["media_dir"] / "clip.mp4"),
        "type": "video",
        "duration": 20.0,
        "mtime": 1.0,
        "size": 1024,
    }

    assert asyncio.run(worker._scan_video(fake_face_engine, row)) is None


def test_a_complete_pass_still_returns_its_faces(
    monkeypatch, sandbox_env, fake_face_engine
):
    """The stall guard must not turn a normal pass into a pending file."""
    from media_library.workers import face_worker

    frames = [_jpeg() for _ in range(3)]

    async def good_pass(_path, interval, _width, _cap, _timeout, **kwargs):
        report = kwargs.get("report")
        for index, frame in enumerate(frames):
            yield index, index * interval, frame
        if report is not None:
            report.update({"frames": len(frames), "complete": True, "reason": "eof"})

    monkeypatch.setattr(face_worker, "iter_video_scan_frames", good_pass)
    worker = face_worker.FaceWorker()
    row = {
        "path": str(sandbox_env["media_dir"] / "clip.mp4"),
        "type": "video",
        "duration": 6.0,
        "mtime": 1.0,
        "size": 1024,
    }

    faces = asyncio.run(worker._scan_video(fake_face_engine, row))

    assert faces is not None
    # One 2 s-spaced appearance of the same (red) person folds into one track.
    assert len(faces) == 1
    assert faces[0]["frame_time"] == 0.0
    assert np.asarray(faces[0]["embedding"]).shape == (128,)
    assert json.dumps({"faces": faces[0]["x"]})  # sanity: plain JSON-safe box
