"""Web MP4 replacement never sacrifices an original on conversion/SQL failure."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from media_library import config, web_mp4
from media_library.app import create_app
from media_library.db import reader, writer
from media_library.routers import web_mp4 as routes


def info(codec="h264", audio="aac", **kwargs):
    return {
        "video": {
            "index": 0,
            "codec_name": codec,
            "pix_fmt": "yuv420p",
            "width": 32,
            "height": 32,
        },
        "audio": [{"codec_name": audio, "channels": 2}] if audio else [],
        "duration": 2.0,
        "format": "mov,mp4,m4a,3gp,3g2,mj2",
        **kwargs,
    }


def indexed(name="clip.mkv"):
    source = config.get_settings().library_dir / name
    source.write_bytes(b"original")
    stat = source.stat()
    with writer() as conn, conn:
        conn.execute(
            "INSERT INTO folders(path,name,file_count) VALUES(?,?,1) "
            "ON CONFLICT(path) DO NOTHING",
            (str(source.parent), source.parent.name),
        )
        conn.execute(
            "INSERT INTO files(path,name,folder_path,type,ext,size,mtime) "
            "VALUES(?,?,?,'video',?,?,?)",
            (
                str(source),
                name,
                str(source.parent),
                source.suffix,
                stat.st_size,
                stat.st_mtime,
            ),
        )
    return source


def staged(source):
    temp = source.parent / ".mllibtmp-test.mp4"
    temp.write_bytes(b"converted")
    return temp


def test_compatible_and_commands():
    assert web_mp4.compatible(info())
    assert web_mp4.compatible(info(audio=None))
    assert not web_mp4.compatible(info("hevc"))
    assert not web_mp4.compatible(info(audio="ac3"))
    data = info()
    data["video"]["pix_fmt"] = "yuv420p10le"
    data["audio"][0]["channels"] = 6
    cmd = web_mp4.command("source", "temp", data)
    assert "libx264" in cmd and "aac" in cmd and "-vf" in cmd
    assert "copy" in web_mp4.command("source", "temp", info())


def test_probe(monkeypatch):
    streams = [
        {"codec_type": "video", "disposition": {"attached_pic": 1}},
        {"codec_type": "video", **info()["video"]},
        {"codec_type": "audio", **info()["audio"][0]},
    ]
    payload = {"streams": streams, "format": {"duration": "2", "format_name": "mp4"}}
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(
            [], 0, json.dumps(payload).encode()
        ),
    )
    assert web_mp4.probe("file")["duration"] == 2
    payload["streams"] = []
    with pytest.raises(web_mp4.writes.WriteError, match="exactly one"):
        web_mp4.probe("file")
    payload["streams"] = streams
    payload["format"]["duration"] = "0"
    with pytest.raises(web_mp4.writes.WriteError, match="duration"):
        web_mp4.probe("file")


@pytest.mark.parametrize("name", ["clip.mkv", "clip.mp4"])
def test_commit_and_related_rows(name):
    source = indexed(name)
    temp = staged(source)
    target = source.with_suffix(".mp4")
    with writer() as conn, conn:
        conn.execute(
            "INSERT INTO favorites(path,created_at) VALUES(?,1)", (str(source),)
        )
        conn.execute("INSERT INTO watch_history VALUES(?,1,2,1,3)", (str(source),))
        conn.execute(
            "INSERT INTO visual_frames(path,hashes,captured_at) VALUES(?,'[]',1)",
            (str(source),),
        )
        conn.execute(
            "INSERT INTO file_issues(path,issue,detected_at) VALUES(?,'probe_failed',1)",
            (str(source),),
        )
    web_mp4.commit(
        str(source), str(target), str(temp), web_mp4._fingerprint(source), info()
    )
    assert target.read_bytes() == b"converted"
    assert not temp.exists()
    if target != source:
        assert not source.exists()
    assert not list(source.parent.glob("*.original"))
    with reader() as conn:
        row = conn.execute("SELECT * FROM files").fetchone()
        assert row["path"] == str(target)
        assert row["video_codec"] == "h264" and row["audio_codec"] == "aac"
        for table in ("favorites", "watch_history", "visual_frames"):
            assert conn.execute(f"SELECT path FROM {table}").fetchone()[0] == str(
                target
            )
        assert not conn.execute("SELECT * FROM file_issues").fetchall()


@pytest.mark.parametrize(
    "reason", ["disabled", "changed", "conflict", "missing", "indexed"]
)
def test_commit_rejections(reason, monkeypatch):
    source = indexed()
    target = source.with_suffix(".mp4")
    temp = staged(source)
    before = web_mp4._fingerprint(source)
    if reason == "disabled":
        monkeypatch.setattr(web_mp4, "writes_available", lambda: False)
    elif reason == "changed":
        before = (0, 0)
    elif reason == "conflict":
        target.write_bytes(b"other")
    elif reason == "missing":
        with writer() as conn, conn:
            conn.execute("DELETE FROM files")
    else:
        indexed("clip.mp4").unlink()
    with pytest.raises((web_mp4.writes.WriteError, web_mp4.DatabaseReadOnlyError)):
        web_mp4.commit(str(source), str(target), str(temp), before, info())
    assert source.read_bytes() == b"original"
    assert temp.exists()
    if reason == "conflict":
        assert target.read_bytes() == b"other"


@pytest.mark.parametrize("same", [True, False])
def test_commit_rollback(same, monkeypatch):
    source = indexed("clip.mp4" if same else "clip.mkv")
    target = source.with_suffix(".mp4")
    temp = staged(source)

    def fail(*args):
        raise RuntimeError("SQL failed")

    monkeypatch.setattr(web_mp4.writes, "recompute_folder", fail)
    with pytest.raises(RuntimeError, match="SQL failed"):
        web_mp4.commit(
            str(source), str(target), str(temp), web_mp4._fingerprint(source), info()
        )
    assert source.read_bytes() == b"original"
    assert same or not target.exists()
    with reader() as conn:
        assert conn.execute("SELECT path FROM files").fetchone()[0] == str(source)


def test_commit_index_changed(monkeypatch):
    source = indexed()
    temp = staged(source)
    original = web_mp4._install

    def replace(a, b):
        original(a, b)
        if str(a) == str(temp):
            with writer() as conn, conn:
                conn.execute("UPDATE files SET size=999")

    monkeypatch.setattr(web_mp4, "_install", replace)
    with pytest.raises(web_mp4.writes.WriteError, match="Index changed"):
        web_mp4.commit(
            str(source),
            str(source.with_suffix(".mp4")),
            str(temp),
            web_mp4._fingerprint(source),
            info(),
        )
    assert source.read_bytes() == b"original"


def test_atomic_install_conflict_and_portable_fallback(monkeypatch, tmp_path):
    source = tmp_path / "temp"
    target = tmp_path / "target"
    source.write_bytes(b"new")
    target.write_bytes(b"existing")
    with pytest.raises(FileExistsError):
        web_mp4._install(str(source), str(target))
    assert target.read_bytes() == b"existing"
    monkeypatch.setattr(web_mp4.ctypes, "CDLL", lambda *a, **k: object())
    target.unlink()
    web_mp4._install(str(source), str(target))
    assert target.read_bytes() == b"new" and not source.exists()


def test_commit_collision_after_backup(monkeypatch):
    source = indexed()
    target = source.with_suffix(".mp4")
    temp = staged(source)
    original = web_mp4._install

    def collision(a, b):
        target.write_bytes(b"concurrent")
        original(a, b)

    monkeypatch.setattr(web_mp4, "_install", collision)
    with pytest.raises(FileExistsError):
        web_mp4.commit(
            str(source), str(target), str(temp), web_mp4._fingerprint(source), info()
        )
    assert source.read_bytes() == b"original"
    assert target.read_bytes() == b"concurrent"


def test_commit_cleanup_failures_do_not_undo_success(monkeypatch):
    source = indexed()
    temp = staged(source)
    unlink = os.unlink

    def fail_backup(path, *args, **kwargs):
        if str(path).endswith(".original"):
            raise OSError("Mount refused cleanup")
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(web_mp4.os, "unlink", fail_backup)

    class Stale:
        def unlink(self, **kwargs):
            raise OSError("Cache cleanup refused")

    monkeypatch.setattr(web_mp4, "thumbnail_path", lambda *a: Stale())
    monkeypatch.setattr(web_mp4, "animated_path", lambda *a: Stale())
    web_mp4.commit(
        str(source),
        str(source.with_suffix(".mp4")),
        str(temp),
        web_mp4._fingerprint(source),
        info(audio=None),
    )
    assert source.with_suffix(".mp4").read_bytes() == b"converted"
    assert next(source.parent.glob(".mllibtmp-*.original")).read_bytes() == b"original"


def test_batch_cancel_boundaries(monkeypatch):
    async def run():
        manager = web_mp4.WebMP4Manager()

        # Cancellation immediately after a cooperative priority wait.
        async def cancel_wait(*args):
            manager.cancel_batch()
            return True

        monkeypatch.setattr(web_mp4.activity, "wait_turn_async", cancel_wait)
        await manager._batch(["one"])
        assert manager.state["processed"] == 0
        # Cancellation during the active operation must not count as failure.
        manager.state["cancel_requested"] = False
        monkeypatch.setattr(
            web_mp4.activity, "wait_turn_async", AsyncMock(return_value=True)
        )

        async def convert(path, prepared=None):
            manager.cancel_batch()
            raise web_mp4.writes.WriteError("Cancelled")

        manager._convert = convert
        manager._check = AsyncMock(return_value={"cached": False})
        await manager._batch(["one"])
        assert manager.state["status"] == "cancelled" and manager.state["failed"] == 0

    asyncio.run(run())


def test_cancel_during_probe(monkeypatch):
    source = indexed()

    async def run():
        manager = web_mp4.WebMP4Manager()

        def cancel_probe(path):
            manager.state["cancel_requested"] = True
            return info()

        monkeypatch.setattr(web_mp4, "probe", cancel_probe)
        with pytest.raises(web_mp4.writes.WriteError, match="Cancelled"):
            await manager._convert(str(source))
        assert source.read_bytes() == b"original"

    asyncio.run(run())


def test_preview_is_index_only(monkeypatch):
    source = indexed()
    with writer() as conn, conn:
        conn.execute(
            "INSERT INTO files(path,name,type,ext) VALUES(?,'a.mp4','video','.mp4')",
            (str(source.parent / "archive.zip" / "a.mp4"),),
        )
    monkeypatch.setattr(web_mp4, "probe", lambda *_: pytest.fail("preview probed"))
    manager = web_mp4.WebMP4Manager()
    preview = manager.preview()
    assert preview["total"] == 1 and preview["archives"] == 1
    assert preview["sample"][0]["path"] == str(source)


def test_start_guards(monkeypatch):
    async def run():
        manager = web_mp4.WebMP4Manager()
        with pytest.raises(web_mp4.writes.WriteError, match="expired"):
            await manager.start("bad")
        preview = manager.preview()
        manager._preview["time"] = 0
        with pytest.raises(web_mp4.writes.WriteError, match="expired"):
            await manager.start(preview["token"])
        monkeypatch.setattr(web_mp4, "writes_available", lambda: False)
        with pytest.raises(web_mp4.DatabaseReadOnlyError):
            await manager.start("bad")
        monkeypatch.setattr(web_mp4, "writes_available", lambda: True)
        blocker = asyncio.Event()
        manager._task = asyncio.create_task(blocker.wait())
        with pytest.raises(web_mp4.writes.WriteError, match="already running"):
            await manager.start("bad")
        blocker.set()
        await manager._task

    asyncio.run(run())


def test_batch_counts_and_failures(monkeypatch):
    async def run():
        manager = web_mp4.WebMP4Manager()
        source = indexed()
        preview = manager.preview()
        manager._preview["paths"] = [str(source)] * 3
        manager._convert = AsyncMock(
            side_effect=["converted", "skipped", RuntimeError("broken")]
        )
        manager._check = AsyncMock(return_value={"cached": False})
        await manager.start(preview["token"])
        await manager._task
        state = manager.snapshot()
        assert state["status"] == "completed"
        assert state["processed"] == 3
        assert state["converted"] == state["skipped"] == state["failed"] == 1
        assert state["errors"][0]["error"] == "broken"

    asyncio.run(run())


def test_cancel_and_shutdown(monkeypatch):
    async def run():
        manager = web_mp4.WebMP4Manager()
        await manager.shutdown()
        manager.state["cancel_requested"] = False

        async def convert(path):
            manager.cancel_batch()
            raise RuntimeError("cancelled")

        manager._convert = convert
        manager._task = asyncio.create_task(manager._batch(["a", "b"]))
        await manager.shutdown()
        assert manager.state["status"] == "paused"
        assert manager.state["processed"] == 0
        manager.active = {"_cancel": False}

        class Process:
            returncode = None

            def kill(self):
                self.returncode = -9

        manager._process = Process()
        manager.state["phase"] = "converting"
        manager.cancel_batch()
        assert manager.active["_cancel"] and manager._process.returncode == -9
        manager._process.returncode = None

        def gone():
            raise ProcessLookupError()

        manager._process.kill = gone
        manager.cancel_batch()
        manager.active["_cancel"] = False
        manager.state["phase"] = "committing"
        manager.cancel_batch()
        assert not manager.active["_cancel"]

    asyncio.run(run())


@pytest.mark.parametrize(
    "mode",
    [
        "success",
        "skip",
        "conflict",
        "ffmpeg",
        "verify",
        "decode",
        "cancel",
        "cancel_decode",
        "disabled",
        "archive",
    ],
)
def test_conversion_paths(mode, monkeypatch):
    source = indexed("clip.mp4" if mode == "skip" else "clip.mkv")
    if mode == "conflict":
        source.with_suffix(".mp4").write_bytes(b"other")
    monkeypatch.setattr(web_mp4, "probe", lambda *_: info())
    if mode == "disabled":
        monkeypatch.setattr(web_mp4, "writes_available", lambda: False)
    if mode == "archive":
        monkeypatch.setattr(web_mp4, "is_zip_member", lambda _: True)

    async def run():
        manager = web_mp4.WebMP4Manager()
        count = 0

        async def ffmpeg(job, temp, args):
            nonlocal count
            count += 1
            manager._apply_progress(job, "out_time_us=1000000")
            assert manager.state["percent"] == 50
            if count == 1:
                Path(temp).write_bytes(b"converted")
                if mode == "cancel":
                    manager.cancel_batch()
                if mode == "verify":
                    monkeypatch.setattr(web_mp4, "probe", lambda *_: info("hevc"))
                return (1 if mode == "ffmpeg" else 0), ["bad input"]
            if mode == "cancel_decode":
                manager.cancel_batch()
            return (1 if mode == "decode" else 0), []

        manager.run = ffmpeg
        if mode in ("success", "skip"):
            result = await manager._convert(str(source))
            assert result == ("skipped" if mode == "skip" else "converted")
        else:
            with pytest.raises(
                (web_mp4.writes.WriteError, web_mp4.DatabaseReadOnlyError)
            ):
                await manager._convert(str(source))
            assert source.read_bytes() == b"original"
        assert not list(source.parent.glob(".mllibtmp-*.mp4"))

    asyncio.run(run())


def test_routes(monkeypatch):
    manager = web_mp4.WebMP4Manager()
    monkeypatch.setattr(routes, "manager", manager)
    client = TestClient(create_app())
    indexed()
    preview = client.get("/api/transcode/web-mp4/preview").json()
    assert preview["total"] == 1
    assert client.get("/api/transcode/web-mp4/status").json()["status"] == "idle"
    assert (
        client.post(
            "/api/transcode/web-mp4/start",
            json={
                "token": preview["token"],
                "confirm_replace": False,
            },
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/transcode/web-mp4/start",
            json={
                "token": "bad",
                "confirm_replace": True,
            },
        ).status_code
        == 409
    )
    manager.start = AsyncMock(return_value=manager.snapshot())
    assert (
        client.post(
            "/api/transcode/web-mp4/start",
            json={
                "token": preview["token"],
                "confirm_replace": True,
            },
        ).status_code
        == 200
    )
    assert client.post("/api/transcode/web-mp4/cancel").json()["cancel_requested"]
    monkeypatch.setattr(
        manager,
        "preview",
        lambda: (_ for _ in ()).throw(web_mp4.writes.WriteError("Unavailable", 503)),
    )
    assert client.get("/api/transcode/web-mp4/preview").status_code == 503


def test_real_ffmpeg_replacement():
    """Real container conversion and decode verification against isolated media."""
    source = indexed()
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=32x32:d=2",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=2",
            "-c:v",
            "mpeg4",
            "-c:a",
            "pcm_s16le",
            "-shortest",
            str(source),
        ],
        check=True,
    )
    stat = source.stat()
    with writer() as conn, conn:
        conn.execute("UPDATE files SET size=?,mtime=?", (stat.st_size, stat.st_mtime))

    async def run():
        manager = web_mp4.WebMP4Manager()
        assert await manager._convert(str(source)) == "converted"
        target = source.with_suffix(".mp4")
        assert target.exists() and not source.exists()
        assert web_mp4.compatible(web_mp4.probe(str(target)))
        assert await manager._convert(str(target)) == "skipped"

    asyncio.run(run())
