"""Line-coverage tests for media_library/x265.py.

Complements tests/test_v2_x265.py with the branches that file does not
reach: the probe fallback, the raw ffmpeg reader, the cross-device commit,
GC, and every defensive except branch.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import time
from pathlib import Path

import pytest

from media_library import cache, writes
from media_library import config as v2_config
from media_library import x265 as x265_module
from media_library.app import create_app
from media_library.x265 import X265Manager, parse_progress_line


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A library with three indexed videos and no transcode cache yet."""
    library = tmp_path / "library"
    (library / "A").mkdir(parents=True)
    thumbs = tmp_path / "thumbs"
    (thumbs / "animated").mkdir(parents=True)
    monkeypatch.setenv("LIBRARY_DIR", str(library))
    monkeypatch.setenv("THUMBNAIL_DIR", str(thumbs))
    monkeypatch.setenv("X265_PRESET", "ultrafast")
    monkeypatch.setenv("X265_CRF", "30")
    monkeypatch.setenv("DB_READ_ONLY", "false")
    monkeypatch.setenv("WRITES", "true")
    v2_config.reload_settings()

    mkv = library / "A" / "clip.mkv"
    mkv.write_bytes(b"x" * 500)
    mp4 = library / "A" / "clip2.mp4"
    mp4.write_bytes(b"y" * 600)
    hevc = library / "A" / "old.mp4"
    hevc.write_bytes(b"z" * 700)

    db_path = Path(os.environ["DB_PATH"])
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO folders (path, name, parent_path, file_count, "
            "subfolder_count, last_scanned, is_zip, total_size) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (str(library / "A"), "A", str(library), 3, 0, 1.0, 0, 0),
        )
        for path, codec, audio, duration in [
            (mkv, "h264", "aac", 10.0),
            (mp4, "h264", "opus", 4.0),
            (hevc, "hevc", "aac", 2.0),
        ]:
            stat = path.stat()
            conn.execute(
                "INSERT INTO files (path, name, folder_path, type, ext, size, "
                "size_formatted, mtime, needs_transcode, has_thumb, "
                "width, height, video_codec, audio_codec, duration) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    str(path),
                    path.name,
                    str(path.parent),
                    "video",
                    path.suffix,
                    stat.st_size,
                    "500 B",
                    stat.st_mtime,
                    0,
                    1,
                    1280,
                    720,
                    codec,
                    audio,
                    duration,
                ),
            )
    try:
        yield {
            "library": library,
            "db_path": db_path,
            "mkv": mkv,
            "mp4": mp4,
            "hevc": hevc,
            "thumbs": thumbs,
        }
    finally:
        v2_config.reload_settings()


def _row(db_path, sql, params=()):
    with sqlite3.connect(db_path) as conn:
        return conn.execute(sql, params).fetchone()


async def _wait_for(predicate, timeout=3.0):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.01)
    return False


def _job(duration=2.0, cancel=False):
    return {
        "id": "job1",
        "source_path": "/x/clip.mkv",
        "duration": duration,
        "percent": 0.0,
        "fps": "",
        "speed": "",
        "eta": "",
        "_cancel": cancel,
    }


# -- parse_progress_line -------------------------------------------------


def test_unknown_keys_are_ignored():
    assert parse_progress_line("codec_name=h264") == {}
    assert parse_progress_line("foo=bar") == {}
    assert parse_progress_line("no-equals") == {}


def test_manager_skips_unparsed_lines():
    manager = X265Manager()
    job = _job()
    manager._apply_progress(job, "unknown_key=7")
    assert job == _job()


def test_eta_tolerates_unparsable_speed():
    manager = X265Manager()
    job = _job()
    manager._apply_progress(job, "speed=garbagex")
    manager._apply_progress(job, "speed=0x")
    manager._apply_progress(job, "out_time_us=abc")
    assert job["speed"] == "0x"
    assert job["eta"] == ""
    assert job["percent"] == 0.0


# -- enqueue validation --------------------------------------------------


def test_enqueue_rejects_non_video_and_not_indexed(env):
    notes = env["library"] / "A" / "notes.txt"
    notes.write_text("hi")
    orphan = env["library"] / "A" / "missing.mkv"
    orphan.write_bytes(b"x")
    manager = X265Manager()

    async def scenario():
        with pytest.raises(writes.WriteError) as wrong_type:
            await manager.enqueue(str(notes))
        with pytest.raises(writes.WriteError) as not_indexed:
            await manager.enqueue(str(orphan))
        with pytest.raises(writes.WriteError) as missing:
            await manager.enqueue(str(env["library"] / "A" / "nope.mkv"))
        with pytest.raises(writes.WriteError) as already:
            await manager.enqueue(str(env["hevc"]))
        return wrong_type.value, not_indexed.value, missing.value, already.value

    wrong, not_indexed, not_found, already = asyncio.run(scenario())
    assert wrong.status == 400
    assert not_indexed.status == 404
    assert not_found.status == 404
    assert already.status == 409


def test_enqueue_rejects_file_already_queued(env):
    manager = X265Manager()

    async def run(job, temp, command):
        while not job.get("_cancel"):
            await asyncio.sleep(0.01)
        return 143, []

    manager.run = run  # type: ignore[method-assign]

    async def scenario():
        first = await manager.enqueue(str(env["mkv"]))
        assert await _wait_for(lambda: manager.active is not None)
        second = await manager.enqueue(str(env["mp4"]))
        with pytest.raises(writes.WriteError) as already_active:
            await manager.enqueue(str(env["mkv"]))
        with pytest.raises(writes.WriteError) as queued_dup:
            await manager.enqueue(str(env["mp4"]))
        manager.cancel(first["id"])
        manager.cancel(second["id"])
        await manager._task
        return already_active.value, queued_dup.value

    active_dup, queued_dup = asyncio.run(scenario())
    assert active_dup.status == 409
    assert queued_dup.status == 409


def test_enqueue_probes_duration_when_index_lacks_it(env):
    db_path = env["db_path"]
    no_duration = env["mkv"]
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE files SET duration = NULL WHERE path = ?", (str(no_duration),)
        )
    manager = X265Manager()
    fail = asyncio.Event()

    async def probe(path):
        return 12.5

    async def run(job, temp, command):
        fail.set()
        return 1, ["nope"]

    manager._probe_duration = probe  # type: ignore[method-assign]
    manager.run = run  # type: ignore[method-assign]

    async def scenario():
        first = await manager.enqueue(str(no_duration))
        assert first["id"] == manager.queued[0]["id"]
        await manager._task

    asyncio.run(scenario())
    entry = manager.history[0]
    assert entry["duration"] == 12.5
    assert entry["status"] == "failed"


# -- _probe_duration ------------------------------------------------------


class _FakeProbeStdout:
    def __init__(self, chunks):
        self._chunks = list(chunks)

    async def readline(self):
        return b""


class _FakeProbeProcess:
    """Mimics asyncio.subprocess.Process for the probe call only."""

    def __init__(self, stdout=b"", error=None, timeout_communicate=False):
        self._stdout = stdout
        self._error = error
        self._timeout = timeout_communicate
        self.killed = False
        self.stdout = _FakeProbeStdout([])

    async def communicate(self):
        if self._error is not None:
            raise self._error
        if self._timeout:
            raise asyncio.TimeoutError("simulated stall")
        return self._stdout, b""

    def kill(self):
        self.killed = True

    async def wait(self):
        return 0


def _patch_probe_process(monkeypatch, process):
    async def fake_exec(*args, **kwargs):
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)


def test_probe_duration_reads_ffprobe_json(env, monkeypatch):
    payload = b'{"format": {"duration": "12.75"}}'
    process = _FakeProbeProcess(stdout=payload)
    _patch_probe_process(monkeypatch, process)
    manager = X265Manager()
    duration = asyncio.run(manager._probe_duration(str(env["mkv"])))
    assert duration == 12.75


def test_probe_duration_handles_problems(env, monkeypatch):
    timed_out = _FakeProbeProcess(timeout_communicate=True)
    _patch_probe_process(monkeypatch, timed_out)
    manager = X265Manager()
    assert asyncio.run(manager._probe_duration(str(env["mkv"]))) == 0.0
    assert timed_out.killed

    _patch_probe_process(monkeypatch, None)
    assert asyncio.run(manager._probe_duration(str(env["mkv"]))) == 0.0


# -- run: the raw reader --------------------------------------------------


class _FakeStdout:
    def __init__(self, lines, stall=False):
        self._lines = list(lines)
        self._stall = stall

    async def readline(self):
        await asyncio.sleep(0)
        if self._lines:
            return self._lines.pop(0)
        if self._stall:
            await asyncio.get_event_loop().create_future()
        return b""  # EOF


class _Hanging:
    async def readline(self):
        await asyncio.get_event_loop().create_future()


class _FakeProcess:
    def __init__(self, stdout, code=7):
        self.stdout = stdout
        self._code = code
        self.killed = False

    def kill(self):
        self.killed = True

    async def wait(self):
        return self._code


def _patch_run_process(monkeypatch, process):
    async def fake_exec(*args, **kwargs):
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)


def test_run_reads_and_trims_the_progress_stream(env, monkeypatch):
    job = _job()
    job["_cancel"] = False
    payload = [
        b"out_time_us=500000\n",
        b"fps=24\n",
        b"speed=2.0x\n",
        b"out_time_us=abc\n",
        b"\n",
        b"weird=stuff\n",
    ] + [f"frame={i}\n".encode() for i in range(205)]
    process = _FakeProcess(_FakeStdout(payload))
    _patch_run_process(monkeypatch, process)
    manager = X265Manager()
    code, lines = asyncio.run(manager.run(job, "/out/t.mp4", ["ffmpeg"]))
    assert code == 7
    assert len(lines) == 110
    assert lines[-1] == "frame=204"
    assert job["fps"] == "24"
    assert job["speed"] == "2.0x"
    assert not process.killed
    assert manager._process is None


def test_run_kills_when_cancel_requested(env, monkeypatch):
    process = _FakeProcess(_FakeStdout([b"out_time_us=100000\n"]), code=9)
    _patch_run_process(monkeypatch, process)
    manager = X265Manager()
    job = _job(cancel=True, duration=1.0)
    code, lines = asyncio.run(manager.run(job, "/out/t.mp4", ["ffmpeg"]))
    assert code == 9
    assert lines == ["out_time_us=100000"]
    assert process.killed


def test_run_stall_reports_and_returns(env, monkeypatch):
    from media_library import config

    assert config.get_settings().x265_preset == "ultrafast"
    monkeypatch.setattr(x265_module, "_READ_TIMEOUT", 0.02)
    process = _FakeProcess(_Hanging())
    _patch_run_process(monkeypatch, process)
    manager = X265Manager()
    code, lines = asyncio.run(manager.run(_job(), "/out/t.mp4", ["ffmpeg"]))
    assert code == 1
    assert lines == ["ffmpeg stalled (no output for 0s)"]
    assert process.killed


def test_run_stall_keeps_collected_output(env, monkeypatch):
    monkeypatch.setattr(x265_module, "_READ_TIMEOUT", 0.02)
    process = _FakeProcess(_FakeStdout([b"fps=12\n"], stall=True))
    _patch_run_process(monkeypatch, process)
    manager = X265Manager()
    code, lines = asyncio.run(manager.run(_job(), "/out/t.mp4", ["ffmpeg"]))
    assert code == 1
    assert lines == ["fps=12"]
    assert process.killed


def test_conversion_reaches_completed_and_history_lookup(env):
    manager = X265Manager()
    payload = b"encoded" * 3

    async def run(job, temp, command):
        manager._apply_progress(job, "out_time_us=5000000")
        Path(temp).write_bytes(payload)
        return 0, ["progress=end"]

    manager.run = run  # type: ignore[method-assign]

    async def scenario():
        job = await manager.enqueue(str(env["mkv"]))
        await manager._task
        return job

    job = asyncio.run(scenario())
    output = env["mkv"].with_suffix(".mp4")
    assert output.is_file() and output.read_bytes() == payload
    assert not env["mkv"].exists()

    entry = manager.status(str(env["mkv"]))["job"]
    assert entry is not None and entry["id"] == job["id"]
    assert entry["status"] == "completed"
    assert entry["percent"] == 100.0
    assert entry["output_path"] == str(output)
    assert manager.status()["active_count"] == 0

    row = _row(
        env["db_path"],
        "SELECT video_codec, size FROM files WHERE path = ?",
        (str(output),),
    )
    assert row == ("hevc", len(payload))


# -- _loop / _execute -----------------------------------------------------


def test_loop_survives_run_exception(env):
    manager = X265Manager()

    async def boom(job, temp, command):
        raise RuntimeError("boom")

    manager.run = boom  # type: ignore[method-assign]

    async def scenario():
        await manager.enqueue(str(env["mkv"]))
        await manager._task

    asyncio.run(scenario())
    entry = manager.history[0]
    assert entry["status"] == "failed"
    assert entry["error"] == "boom"
    assert manager.active is None


def test_execute_marks_failed_with_ffmpeg_tail(env):
    manager = X265Manager()
    started = asyncio.Event()

    async def run(job, temp, command):
        started.set()
        return 1, ["alpha", "", "beta"]

    manager.run = run  # type: ignore[method-assign]

    async def scenario():
        job = await manager.enqueue(str(env["mkv"]))
        assert await _wait_for(started.is_set)
        await manager._task
        return job

    job = asyncio.run(scenario())
    entry = manager.history[0]
    assert entry["id"] == job["id"]
    assert entry["status"] == "failed"
    assert entry["error"] == "alpha\nbeta"
    assert entry["audio_mode"] == "copy"


def test_execute_tolerates_temp_unlink_errors(env, monkeypatch):
    manager = X265Manager()

    async def run(job, temp, command):
        return 1, []

    manager.run = run  # type: ignore[method-assign]

    real_unlink = Path.unlink

    def flaky_unlink(self, *, missing_ok=False):
        if self.name.startswith("h265_"):
            raise OSError("temp locked")
        return real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", flaky_unlink)

    async def scenario():
        await manager.enqueue(str(env["mp4"]))
        await manager._task

    asyncio.run(scenario())
    assert manager.history[0]["status"] == "failed"


# -- status / cancel ------------------------------------------------------


def test_status_reports_active_and_queued_paths(env):
    manager = X265Manager()

    async def run(job, temp, command):
        while not job.get("_cancel"):
            await asyncio.sleep(0.01)
        return 143, []

    manager.run = run  # type: ignore[method-assign]

    async def scenario():
        first = await manager.enqueue(str(env["mkv"]))
        assert await _wait_for(lambda: manager.active is not None)
        second = await manager.enqueue(str(env["mp4"]))

        active = manager.status(first["source_path"])["job"]
        queued = manager.status(second["source_path"])["job"]
        assert active["source_path"] == first["source_path"]
        assert queued["id"] == second["id"]
        assert manager.status()["active_count"] == 1
        assert manager.status()["queued_count"] == 1

        manager.cancel(first["id"])
        manager.cancel(second["id"])
        await manager._task

    asyncio.run(scenario())


def _broken_killer(error):
    class _Bad:
        def kill(self):
            raise error

    return _Bad()


def test_cancel_kills_active_process_and_tolerates_kill_errors(env):
    manager = X265Manager()
    manager.active = {
        "id": "abc",
        "source_path": "/a.mkv",
        "_cancel": False,
    }
    manager._process = _broken_killer(OSError("gone"))
    assert manager.cancel("abc") == {"cancelled": "abc"}
    assert manager.active["_cancel"] is True

    manager.active = {"id": "def", "source_path": "/b.mkv", "_cancel": False}
    manager._process = _broken_killer(ProcessLookupError())
    assert manager.cancel("def") == {"cancelled": "def"}
    manager._process = None


def test_cancel_unknown_job_is_a_404(env):
    manager = X265Manager()
    with pytest.raises(writes.WriteError) as caught:
        manager.cancel("missing")
    assert caught.value.status == 404


# -- _gc_temp -------------------------------------------------------------


def test_gc_temp_missing_dir_and_old_files(tmp_path, monkeypatch):
    data_dir = tmp_path / "gcdata"
    data_dir.mkdir()
    transcodes = data_dir / "transcodes"
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("TRANSCODE_DIR", str(transcodes))
    v2_config.reload_settings()

    manager = X265Manager()
    manager._gc_temp()  # root missing: no-op

    transcodes.mkdir()
    old = transcodes / "h265_old.mp4"
    old.write_bytes(b"old")
    fresh = transcodes / "h265_new.mp4"
    fresh.write_bytes(b"new")
    stale_dir = transcodes / "h265_dir.mp4"
    stale_dir.mkdir()
    old_time = time.time() - 25 * 3600
    os.utime(old, (old_time, old_time))
    os.utime(stale_dir, (old_time, old_time))

    manager._gc_temp()
    assert not old.exists()
    assert fresh.exists()
    assert stale_dir.exists()  # unlink failed -> caught and skipped

    v2_config.reload_settings()


def test_gc_temp_works_from_enqueue(env):
    """The enqueue-side GC stat-walk runs off the event loop."""
    manager = X265Manager()
    manager._gc_temp()


# -- _commit / _commit_output --------------------------------------------


def _temp_file(tmp_path, content=b"encoded" * 4):
    temp = tmp_path / f"tmp_{content[:2].hex()}.mp4"
    temp.write_bytes(content)
    return temp


def _flaky_replace(monkeypatch, temp):
    real_replace = os.replace

    def flaky(src, dst):
        if src == str(temp):
            raise OSError("Invalid cross-device link")
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", flaky)


def test_commit_cross_device_copy_succeeds(tmp_path, monkeypatch):
    temp = _temp_file(tmp_path, b"payload" * 50)
    target = tmp_path / "out.mp4"
    _flaky_replace(monkeypatch, temp)

    x265_module._commit(str(temp), str(target))
    assert target.read_bytes() == b"payload" * 50
    assert not temp.exists()
    assert not list(tmp_path.glob(".tmp_h265_*"))


def test_commit_cross_device_size_check(tmp_path, monkeypatch):
    temp = _temp_file(tmp_path, b"payload" * 50)
    target = tmp_path / "out.mp4"
    _flaky_replace(monkeypatch, temp)

    import shutil

    real_copy2 = shutil.copy2

    def partial(src, dst):
        real_copy2(src, dst)
        with open(dst, "r+b") as handle:
            handle.truncate(16)

    monkeypatch.setattr(shutil, "copy2", partial)
    with pytest.raises(OSError, match="verification failed"):
        x265_module._commit(str(temp), str(target))
    assert not target.exists()
    assert temp.exists()
    assert not list(tmp_path.glob(".tmp_h265_*"))


def test_commit_cross_device_sample_check(tmp_path, monkeypatch):
    temp = _temp_file(tmp_path, b"payload" * 50)
    target = tmp_path / "out.mp4"
    _flaky_replace(monkeypatch, temp)

    import shutil

    real_copy2 = shutil.copy2

    def corrupting(src, dst):
        real_copy2(src, dst)
        with open(dst, "r+b") as handle:
            handle.seek(1)
            handle.write(b"\x00")

    monkeypatch.setattr(shutil, "copy2", corrupting)
    real_remove = os.remove

    def stubborn_remove(path, *args):
        if os.path.basename(str(path)).startswith(".tmp_h265_"):
            raise OSError("stuck")
        return real_remove(path, *args)

    monkeypatch.setattr(os, "remove", stubborn_remove)
    with pytest.raises(OSError, match="verification failed"):
        x265_module._commit(str(temp), str(target))
    assert not target.exists()
    assert temp.exists()
    assert list(tmp_path.glob(".tmp_h265_*"))

    real_remove(str(next(iter(tmp_path.glob(".tmp_h265_*")))))


def test_commit_tolerates_temp_remove_errors(tmp_path, monkeypatch):
    temp = _temp_file(tmp_path, b"payload" * 50)
    target = tmp_path / "out.mp4"
    _flaky_replace(monkeypatch, temp)
    real_remove = os.remove

    def stubborn_remove(path, *args):
        if str(path) == str(temp):
            raise OSError("stuck")
        return real_remove(path, *args)

    monkeypatch.setattr(os, "remove", stubborn_remove)
    x265_module._commit(str(temp), str(target))
    assert target.read_bytes() == b"payload" * 50
    assert not list(tmp_path.glob(".tmp_h265_*"))


def test_commit_output_picks_unique_target(tmp_path, monkeypatch):
    target_dir = tmp_path / "lib"
    target_dir.mkdir()
    source = target_dir / "clip.mkv"
    source.write_bytes(b"source")
    sibling = target_dir / "clip.mp4"
    sibling.write_bytes(b"existing")
    temp = _temp_file(tmp_path, b"encoded")
    real_remove = os.remove

    def stubborn_remove(path, *args):
        if str(path) == str(source):
            raise OSError("stuck")
        return real_remove(path, *args)

    monkeypatch.setattr(os, "remove", stubborn_remove)

    target, _size, _mtime = x265_module._commit_output(str(source), str(temp))
    assert target == str(target_dir / "clip (1).mp4")
    assert Path(target).read_bytes() == b"encoded"
    assert _size == len(b"encoded")
    assert source.exists()  # os.remove failed, tolerated
    assert sibling.read_bytes() == b"existing"


# -- _update_index --------------------------------------------------------


def test_update_index_moves_row_across_folders(env):
    library = env["library"]
    destination = library / "B"
    destination.mkdir()
    source = library / "A" / "clip.mkv"
    target = destination / "clip.mp4"
    stat = source.stat()
    job = {"id": "job1", "_mtime_before": stat.st_mtime, "_size_before": stat.st_size}

    db_path = env["db_path"]
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO folders (path, name, parent_path, file_count, "
            "subfolder_count, last_scanned, is_zip, total_size) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (str(destination), "B", str(library), 0, 0, 1.0, 0, 0),
        )
        conn.execute(
            "INSERT INTO favorites (path, created_at) VALUES (?, 1.0)",
            (str(source),),
        )

    old_thumb = cache.thumbnail_path(str(source), stat.st_mtime, stat.st_size)
    old_thumb.parent.mkdir(parents=True, exist_ok=True)
    old_thumb.mkdir()  # unlink will fail with IsADirectoryError
    stale_target_thumb = cache.thumbnail_path(str(target), 777.0, 4321)
    stale_target_thumb.parent.mkdir(parents=True, exist_ok=True)
    stale_target_thumb.mkdir()

    x265_module._update_index(job, str(source), str(target), 4321, 777.0, "opus")

    row = _row(
        db_path,
        "SELECT path, folder_path, ext, video_codec, size, mtime, name "
        "FROM files WHERE path = ?",
        (str(target),),
    )
    assert row == (
        str(target),
        str(destination),
        ".mp4",
        "hevc",
        4321,
        777.0,
        "clip.mp4",
    )
    assert (
        _row(db_path, "SELECT COUNT(*) FROM files WHERE path = ?", (str(source),))[0]
        == 0
    )
    assert (
        _row(db_path, "SELECT COUNT(*) FROM favorites WHERE path = ?", (str(target),))[
            0
        ]
        == 1
    )
    assert (
        _row(db_path, "SELECT COUNT(*) FROM favorites WHERE path = ?", (str(source),))[
            0
        ]
        == 0
    )
    counts = _row(
        db_path,
        "SELECT file_count, total_size FROM folders WHERE path = ?",
        (str(destination),),
    )
    assert counts == (1, 4321)
    assert old_thumb.is_dir()  # unlink failed -> tolerated
    assert stale_target_thumb.is_dir()


def test_status_route_reports_idle():
    from fastapi.testclient import TestClient

    client = TestClient(create_app())
    assert client.get("/api/transcode/x265/status").json()["active"] is None
