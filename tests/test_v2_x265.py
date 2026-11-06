"""x265 conversion tests for v2 (fake ffmpeg, real index rewrite)."""

from __future__ import annotations

import asyncio
import os
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from media_library_v2 import cache, writes
from media_library_v2 import config as v2_config
from media_library_v2.mount import build_v2_app
from media_library_v2.x265 import X265Manager, parse_progress_line


@pytest.fixture
def x265_env(tmp_path, monkeypatch):
    library = tmp_path / "library"
    (library / "A").mkdir(parents=True)
    thumbs = tmp_path / "thumbs"
    (thumbs / "animated").mkdir(parents=True)
    transcodes = tmp_path / "transcodes"
    transcodes.mkdir()
    monkeypatch.setenv("LIBRARY_DIR", str(library))
    monkeypatch.setenv("V2_LIBRARY_DIR", str(library))
    monkeypatch.setenv("THUMBNAIL_DIR", str(thumbs))
    monkeypatch.setenv("TRANSCODE_DIR", str(transcodes))
    monkeypatch.setenv("X265_PRESET", "ultrafast")
    monkeypatch.setenv("X265_CRF", "30")
    monkeypatch.setenv("V2_DB_READ_ONLY", "false")
    monkeypatch.setenv("V2_WRITES", "true")
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
        rows = [
            (mkv, "h264", "aac", 10.0),
            (mp4, "h264", "opus", 4.0),
            (hevc, "hevc", "aac", 2.0),
        ]
        for path, codec, audio, duration in rows:
            stat = path.stat()
            conn.execute(
                "INSERT INTO files (path, name, folder_path, type, ext, size, "
                "size_formatted, mtime, needs_transcode, has_thumb, width, height, "
                "video_codec, audio_codec, duration) "
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
        conn.execute(
            "INSERT INTO favorites (path, created_at) VALUES (?, ?)",
            (str(mkv), 1.0),
        )
    thumb = cache.thumbnail_path(str(mkv), mkv.stat().st_mtime, mkv.stat().st_size)
    thumb.write_bytes(b"poster")

    try:
        yield {
            "library": library,
            "db_path": db_path,
            "mkv": mkv,
            "mp4": mp4,
            "hevc": hevc,
            "thumb": thumb,
            "transcodes": transcodes,
        }
    finally:
        v2_config.reload_settings()


def _db(env):
    return sqlite3.connect(env["db_path"])


async def _wait_for(predicate, timeout=3.0):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.01)
    return False


def _fake_run(manager: X265Manager, payload: bytes = b"encoded"):
    async def run(job, temp, command):
        manager._apply_progress(job, "out_time_us=5000000")
        manager._apply_progress(job, "fps=24")
        manager._apply_progress(job, "speed=2.5x")
        Path(temp).write_bytes(payload)
        return 0, ["progress=end"]

    return run


def test_parse_progress_line():
    assert parse_progress_line("out_time_us=2500000") == {"out_time_us": "2500000"}
    assert parse_progress_line("speed=1.5x") == {"speed": "1.5x"}
    assert parse_progress_line("[info] something") == {}


def test_command_uses_the_configured_preset_and_audio_mode(x265_env):
    manager = X265Manager()
    command = manager._command("/in/clip.mkv", "/out/temp.mp4", True)
    assert "libx265" in command
    assert command[command.index("-preset") + 1] == "ultrafast"
    assert command[command.index("-crf") + 1] == "30"
    assert command[command.index("-c:a") + 1] == "copy"
    assert command[command.index("-tag:v") + 1] == "hvc1"

    reencode = manager._command("/in/clip.mkv", "/out/temp.mp4", False)
    assert reencode[reencode.index("-c:a") + 1] == "aac"


def test_conversion_commits_and_rewrites_the_index(x265_env):
    manager = X265Manager()
    manager.run = _fake_run(manager)  # type: ignore[method-assign]

    async def scenario():
        await manager.enqueue(str(x265_env["mkv"]))
        await manager._task

    asyncio.run(scenario())
    output = x265_env["mkv"].with_suffix(".mp4")
    assert output.is_file()
    assert not x265_env["mkv"].exists()
    assert not any(x265_env["transcodes"].glob("h265_*.mp4"))

    entry = manager.status(str(x265_env["mkv"]))["job"]
    assert entry is not None
    assert entry["status"] == "completed"
    assert entry["percent"] == 100.0
    assert entry["output_path"] == str(output)

    with _db(x265_env) as conn:
        row = conn.execute(
            "SELECT path, video_codec, needs_transcode, has_thumb, size, "
            "quick_hash, thumb_hash FROM files WHERE path = ?",
            (str(output),),
        ).fetchone()
        favorite = conn.execute(
            "SELECT path FROM favorites WHERE path = ?", (str(output),)
        ).fetchone()
        old = conn.execute(
            "SELECT COUNT(*) FROM files WHERE path = ?", (str(x265_env["mkv"]),)
        ).fetchone()[0]
    assert row[0] == str(output)
    assert row[1] == "hevc"
    assert row[2] == 0
    assert row[3] == 0
    assert row[4] == len(b"encoded")
    assert row[5] is None and row[6] is None
    assert favorite == (str(output),)
    assert old == 0
    assert not x265_env["thumb"].exists()


def test_mp4_source_is_replaced_in_place(x265_env):
    manager = X265Manager()
    manager.run = _fake_run(manager, b"mp4-encoded")  # type: ignore[method-assign]

    async def scenario():
        await manager.enqueue(str(x265_env["mp4"]))
        await manager._task

    asyncio.run(scenario())
    assert x265_env["mp4"].is_file()
    assert x265_env["mp4"].read_bytes() == b"mp4-encoded"
    with _db(x265_env) as conn:
        row = conn.execute(
            "SELECT video_codec, size FROM files WHERE path = ?",
            (str(x265_env["mp4"]),),
        ).fetchone()
    assert row == ("hevc", len(b"mp4-encoded"))


def test_already_hevc_is_refused(x265_env):
    manager = X265Manager()

    async def scenario():
        with pytest.raises(writes.WriteError) as caught:
            await manager.enqueue(str(x265_env["hevc"]))
        return caught.value

    error = asyncio.run(scenario())
    assert error.status == 409
    assert "HEVC" in error.message


def test_duplicate_and_missing_files_are_refused(x265_env):
    manager = X265Manager()

    async def run(job, temp, command):
        while not job.get("_cancel"):
            await asyncio.sleep(0.01)
        return 143, []

    manager.run = run  # type: ignore[method-assign]

    async def scenario():
        await manager.enqueue(str(x265_env["mkv"]))
        await _wait_for(lambda: manager.active is not None)
        with pytest.raises(writes.WriteError) as caught:
            await manager.enqueue(str(x265_env["mkv"]))
        assert caught.value.status == 409
        manager.cancel(manager.active["id"])
        await manager._task
        with pytest.raises(writes.WriteError) as missing:
            await manager.enqueue(str(x265_env["library"] / "A" / "nope.mp4"))
        return missing.value

    assert asyncio.run(scenario()).status == 404


def test_cancel_active_and_queued_jobs(x265_env):
    manager = X265Manager()
    started = asyncio.Event()

    async def run(job, temp, command):
        started.set()
        while not job.get("_cancel"):
            await asyncio.sleep(0.01)
        return 143, []

    manager.run = run  # type: ignore[method-assign]

    async def scenario():
        first = await manager.enqueue(str(x265_env["mkv"]))
        await asyncio.wait_for(started.wait(), timeout=2)
        second = await manager.enqueue(str(x265_env["mp4"]))
        manager.cancel(second["id"])
        manager.cancel(first["id"])
        await manager._task
        return first, second

    _first, second = asyncio.run(scenario())
    assert manager.status(str(x265_env["mkv"]))["job"]["status"] == "cancelled"
    assert (
        next(job for job in manager.history if job["id"] == second["id"])["status"]
        == "cancelled"
    )


def test_router_reports_status_and_validates(x265_env):
    client = TestClient(build_v2_app())
    idle = client.get("/api/v2/transcode/x265/status").json()
    assert idle["active"] is None
    assert idle["queued"] == []

    missing = client.post(
        "/api/v2/transcode/x265",
        json={"path": str(x265_env["library"] / "A" / "nope.mp4")},
    )
    assert missing.status_code == 404

    already = client.post(
        "/api/v2/transcode/x265", json={"path": str(x265_env["hevc"])}
    )
    assert already.status_code == 409


def test_index_rewrite_runs_off_the_event_loop(x265_env, monkeypatch):
    """The commit's write transaction must not run on the loop (AGENTS Rule 3)."""
    import threading

    from media_library_v2 import x265 as x265_module

    manager = X265Manager()
    manager.run = _fake_run(manager, b"encoded")  # type: ignore[method-assign]
    real_update = x265_module._update_index
    seen: list[int] = []

    def record(*args, **kwargs):
        seen.append(threading.get_ident())
        real_update(*args, **kwargs)

    monkeypatch.setattr(x265_module, "_update_index", record)

    async def scenario():
        await manager.enqueue(str(x265_env["mp4"]))
        await manager._task

    loop_thread = threading.get_ident()
    asyncio.run(scenario())
    assert seen, "the index commit did not run"
    assert seen[0] != loop_thread
