"""The face sweep's single-pass frame reader.

``iter_video_scan_frames`` is what makes a 2-second grid affordable: FFmpeg
decodes each video once and samples the timeline itself, instead of the sweep
re-seeking for every frame, and the frames are streamed rather than written to
the shared candidate cache. These tests drive it against a fake subprocess, so
no FFmpeg and no media file is needed.
"""

import asyncio
import io

import pytest
from PIL import Image

from media_library.services.ffmpeg_frames import _split_jpegs, iter_video_scan_frames


def _jpeg(color="red", size=(64, 48)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color=color).save(buffer, format="JPEG")
    return buffer.getvalue()


class FakeProcess:
    """A subprocess that hands out fixed bytes (optionally too slowly)."""

    def __init__(self, stdout: bytes = b"", stall: bool = False):
        self._stdout = stdout
        self.returncode = None
        self.killed = False
        self.stalled = stall
        self.stdout = self

    async def read(self, _n):
        if self.stalled:
            await asyncio.sleep(30)
        chunk, self._stdout = self._stdout, b""
        return chunk

    async def wait(self):
        if self.returncode is None:
            self.returncode = 0
        return self.returncode

    def kill(self):
        self.killed = True
        self.returncode = -9


def _spawn(monkeypatch, process, capture=None):
    async def fake_exec(*cmd, **kwargs):
        if capture is not None:
            capture.append(list(cmd))
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    return process


def _collect(path, interval, width, max_frames=0, timeout_s=0.0):
    async def run():
        return [
            (index, stamp, len(jpeg))
            async for index, stamp, jpeg in iter_video_scan_frames(
                path, interval, width, max_frames, timeout_s
            )
        ]

    return asyncio.run(run())


def test_split_jpegs_handles_partial_and_multiple_frames():
    one, two = _jpeg(), _jpeg("blue")
    buffer = bytearray(one + two[:5])

    frames = _split_jpegs(buffer)

    assert frames == [one]
    # The incomplete frame stays in the buffer for the next chunk.
    assert bytes(buffer) == two[:5]

    buffer.extend(two[5:])
    assert _split_jpegs(buffer) == [two]
    assert bytes(buffer) == b""


def test_split_jpegs_drops_leading_garbage():
    frame = _jpeg()
    buffer = bytearray(b"not a jpeg at all" + frame)

    assert _split_jpegs(buffer) == [frame]


def test_frame_reader_samples_the_grid_and_never_touches_the_cache(
    sandbox_env, monkeypatch
):
    from media_library.services.candidate_cache import get_candidate_cache_dir

    before = list(get_candidate_cache_dir().rglob("*.jpg"))
    command: list[list[str]] = []
    process = _spawn(monkeypatch, FakeProcess(_jpeg() + _jpeg("blue")), command)

    frames = _collect("/media/clip.mp4", 2.0, 960)

    assert [index for index, _stamp, _size in frames] == [0, 1]
    assert [stamp for _index, stamp, _size in frames] == [0.0, 2.0]
    # One decode pass, sampling the timeline itself at the scan width.
    cmd = command[0]
    assert cmd[0] == "nice"
    assert "-i" in cmd and cmd[cmd.index("-i") + 1] == "/media/clip.mp4"
    assert "fps=0.500000,scale='min(960,iw)':-2:flags=fast_bilinear" in cmd
    assert "image2pipe" in cmd
    assert process.killed  # the pass is closed down once the stream ends
    # Nothing was written to the frame cache the chooser and previews share.
    assert list(get_candidate_cache_dir().rglob("*.jpg")) == before


def test_frame_reader_stops_at_the_frame_budget(sandbox_env, monkeypatch):
    command: list[list[str]] = []
    process = _spawn(monkeypatch, FakeProcess(_jpeg() * 5), command)

    frames = _collect("/media/clip.mp4", 2.0, 960, max_frames=2)

    assert [index for index, _stamp, _size in frames] == [0, 1]
    assert "-frames:v" in command[0]
    assert command[0][command[0].index("-frames:v") + 1] == "2"
    assert process.killed


def test_frame_reader_gives_up_on_a_stalled_pass(sandbox_env, monkeypatch):
    process = _spawn(monkeypatch, FakeProcess(b"", stall=True))

    frames = _collect("/media/clip.mp4", 2.0, 960, timeout_s=0.05)

    assert frames == []
    assert process.killed


def test_frame_reader_survives_a_failing_pass(sandbox_env, monkeypatch):
    async def boom(*_cmd, **_kwargs):
        raise OSError("ffmpeg is not installed")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", boom)

    assert _collect("/media/clip.mp4", 2.0, 960) == []


def test_frame_reader_survives_a_missing_interval(sandbox_env, monkeypatch):
    command: list[list[str]] = []
    _spawn(monkeypatch, FakeProcess(_jpeg()), command)

    # A zero interval must not divide by zero: it is clamped to the minimum.
    frames = _collect("/media/clip.mp4", 0.0, 960)

    assert [stamp for _index, stamp, _size in frames] == [0.0]
    assert "fps=20.000000" in " ".join(command[0])


@pytest.mark.parametrize("width", [0, 100])
def test_frame_reader_keeps_a_usable_scan_width(sandbox_env, monkeypatch, width):
    command: list[list[str]] = []
    _spawn(monkeypatch, FakeProcess(_jpeg()), command)

    _collect("/media/clip.mp4", 2.0, width)

    assert any("scale='min(160,iw)'" in arg for arg in command[0])
