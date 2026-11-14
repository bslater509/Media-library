"""Durable MP4 checkpoints, bounded parallel checks and serial replacements."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient
from test_web_mp4 import indexed, info

from media_library import config, web_mp4
from media_library.app import create_app
from media_library.db import DatabaseReadOnlyError
from media_library.routers import web_mp4 as routes


def path():
    return config.get_settings().data_dir / "web_mp4_state.json"


def payload(manager, remaining):
    manager._remaining = list(remaining)
    manager.state["total"] = manager.state["processed"] + len(remaining)
    return {
        "version": 1,
        "library_root": str(config.get_settings().library_dir),
        "state": manager.snapshot(),
        "remaining": remaining,
        "cache": dict(manager._cache),
    }


def test_empty_and_invalid_restore():
    async def run():
        manager = web_mp4.WebMP4Manager()
        assert (await manager.restore())["status"] == "idle"
        path().write_text("not json")
        assert (await manager.restore())["status"] == "error"
        assert path().read_text() == "not json"

    asyncio.run(run())


@pytest.mark.parametrize(
    "bad", ["version", "root", "remaining", "cache", "counts", "schema"]
)
def test_load_rejects_invalid_checkpoint(bad):
    data = payload(web_mp4.WebMP4Manager(), ["a"])
    if bad == "version":
        data["version"] = 9
    elif bad == "root":
        data["library_root"] = "another-library"
    elif bad == "remaining":
        data["remaining"] = [None]
    elif bad == "cache":
        data["cache"] = []
    elif bad == "counts":
        data["state"]["processed"] = 1
    else:
        data["state"]["skipped"] = -1
    path().write_text(json.dumps(data))
    with pytest.raises(ValueError):
        web_mp4.load_state()


def test_cache_survives_and_invalidates_changed_files(monkeypatch):
    source = indexed("clip.mp4")
    mocked_probe = Mock(return_value=info())
    monkeypatch.setattr(web_mp4, "probe", mocked_probe)

    async def run():
        manager = web_mp4.WebMP4Manager()
        assert not (await manager._check(str(source)))["cached"]
        assert (await manager._check(str(source)))["cached"]
        await manager._persist(force=True)
        restored = web_mp4.WebMP4Manager()
        await restored.restore()
        assert (await restored._check(str(source)))["cached"]
        assert mocked_probe.call_count == 1
        source.write_bytes(b"changed-video")
        assert not (await restored._check(str(source)))["cached"]
        assert mocked_probe.call_count == 2
        restored._cache[str(source)] = {
            "fingerprint": list(web_mp4._fingerprint(source))
        }
        assert not (await restored._check(str(source)))["cached"]

    asyncio.run(run())


def test_restore_resumes_exact_remaining_without_rechecking_completed(monkeypatch):
    async def run():
        manager = web_mp4.WebMP4Manager()
        manager.state.update(status="running", processed=7, skipped=7)
        web_mp4.save_state(payload(manager, ["pending-b", "pending-a"]))
        restored = web_mp4.WebMP4Manager()
        restored._check = AsyncMock(return_value={"cached": True})
        restored._convert = AsyncMock(return_value="skipped")
        state = await restored.restore()
        assert state["processed"] == 7 and state["remaining"] == 2
        await restored._task
        assert restored.state["processed"] == restored.state["skipped"] == 9
        assert restored.state["cached"] == 2
        assert {call.args[0] for call in restored._check.call_args_list} == {
            "pending-a",
            "pending-b",
        }
        final = web_mp4.load_state()
        assert final["remaining"] == [] and final["state"]["status"] == "completed"

    asyncio.run(run())


def test_cancelled_queue_needs_manual_resume(monkeypatch):
    async def run():
        original = web_mp4.WebMP4Manager()
        original.state.update(status="running", cancel_requested=True)
        web_mp4.save_state(payload(original, ["pending"]))
        restored = web_mp4.WebMP4Manager()
        assert (await restored.restore())["status"] == "cancelled"
        assert restored._task is None
        restored._check = AsyncMock(return_value={"cached": False})
        restored._convert = AsyncMock(return_value="skipped")
        await restored.resume()
        await restored._task
        assert restored.state["processed"] == 1
        with pytest.raises(web_mp4.writes.WriteError, match="unfinished"):
            await restored.resume()

    asyncio.run(run())


def test_read_only_restore_keeps_checkpoint_paused(monkeypatch):
    async def run():
        original = web_mp4.WebMP4Manager()
        original.state["status"] = "paused"
        web_mp4.save_state(payload(original, ["pending"]))
        monkeypatch.setattr(web_mp4, "writes_available", lambda: False)
        restored = web_mp4.WebMP4Manager()
        assert (await restored.restore())["status"] == "paused"
        assert restored._task is None
        with pytest.raises(DatabaseReadOnlyError):
            await restored.resume()

    asyncio.run(run())


def test_resume_refuses_a_second_worker():
    async def run():
        manager = web_mp4.WebMP4Manager()
        gate = asyncio.Event()
        manager._task = asyncio.create_task(gate.wait())
        with pytest.raises(web_mp4.writes.WriteError, match="already running"):
            await manager.resume()
        gate.set()
        await manager._task

    asyncio.run(run())


def test_four_parallel_checks_but_one_conversion():
    async def run():
        manager = web_mp4.WebMP4Manager()
        gate = asyncio.Event()
        four_started = asyncio.Event()
        checks = 0
        max_checks = 0
        conversions = 0
        max_conversions = 0

        async def check(path):
            nonlocal checks, max_checks
            checks += 1
            max_checks = max(max_checks, checks)
            if checks == 4:
                four_started.set()
            await gate.wait()
            checks -= 1
            return {"cached": False}

        async def convert(path, prepared):
            nonlocal conversions, max_conversions
            conversions += 1
            max_conversions = max(max_conversions, conversions)
            await asyncio.sleep(0)
            conversions -= 1
            return "converted"

        manager._check = check
        manager._convert = convert
        manager.state["total"] = 30
        task = asyncio.create_task(manager._batch([str(n) for n in range(30)]))
        await asyncio.wait_for(four_started.wait(), 2)
        assert max_checks == 4 and not task.done()
        gate.set()
        await asyncio.wait_for(task, 3)
        assert max_conversions == 1
        assert manager.state["converted"] == 30

    asyncio.run(run())


def test_cancelling_full_pipeline_does_not_deadlock():
    async def run():
        manager = web_mp4.WebMP4Manager()
        started = asyncio.Event()
        manager._check = AsyncMock(return_value={"cached": False})

        async def convert(path, prepared):
            started.set()
            while not manager.state["cancel_requested"]:
                await asyncio.sleep(0)
            raise web_mp4.writes.WriteError("Cancelled")

        manager._convert = convert
        manager.state["total"] = 40
        manager._task = asyncio.create_task(manager._batch([str(n) for n in range(40)]))
        await started.wait()
        for _ in range(20):
            await asyncio.sleep(0)
        await manager.request_cancel()
        await asyncio.wait_for(manager._task, 3)
        saved = web_mp4.load_state()
        assert saved["state"]["status"] == "cancelled"
        assert len(saved["remaining"]) == 40

    asyncio.run(run())


def test_shutdown_saves_paused_queue_and_restores_it():
    async def run():
        manager = web_mp4.WebMP4Manager()
        started = asyncio.Event()
        manager._check = AsyncMock(return_value={"cached": False})

        async def convert(path, prepared):
            started.set()
            while not manager.state["cancel_requested"]:
                await asyncio.sleep(0)
            raise web_mp4.writes.WriteError("Cancelled")

        manager._convert = convert
        manager.state["total"] = 2
        manager._task = asyncio.create_task(manager._batch(["a", "b"]))
        await started.wait()
        await manager.shutdown()
        assert web_mp4.load_state()["state"]["status"] == "paused"
        restored = web_mp4.WebMP4Manager()
        restored._check = AsyncMock(return_value={"cached": False})
        restored._convert = AsyncMock(return_value="skipped")
        await restored.restore()
        await restored._task
        assert restored.state["processed"] == 2

    asyncio.run(run())


def test_persistence_throttle_and_failure_stops_work(monkeypatch):
    async def run():
        manager = web_mp4.WebMP4Manager()
        saver = Mock()
        monkeypatch.setattr(web_mp4, "save_state", saver)
        await manager._persist(force=True)
        await manager._persist()
        assert saver.call_count == 1
        saver.side_effect = OSError("disk full")
        manager._remaining = ["pending"]
        with pytest.raises(web_mp4.writes.WriteError, match="Cannot save"):
            await manager._launch()
        assert manager._task is None and manager.state["status"] == "error"
        manager._last_save = 0
        manager._check = AsyncMock(return_value={"cached": False})
        manager._convert = AsyncMock(return_value="skipped")
        await manager._batch(["one", "two"])
        assert manager.state["status"] == "error"
        assert manager.state["processed"] == 1
        assert manager._remaining == ["two"]

    asyncio.run(run())


def test_save_failure_leaves_previous_state(monkeypatch):
    manager = web_mp4.WebMP4Manager()
    original = payload(manager, [])
    web_mp4.save_state(original)

    def fail(*args):
        raise OSError("disk full")

    monkeypatch.setattr(web_mp4.os, "replace", fail)
    with pytest.raises(OSError):
        web_mp4.save_state({**original, "version": 9})
    assert web_mp4.load_state()["version"] == 1
    assert not list(path().parent.glob("web_mp4_state.*.tmp"))


def test_queued_conversion_rechecks_changed_source(monkeypatch):
    source = indexed("clip.mp4")

    async def run():
        manager = web_mp4.WebMP4Manager()
        # Prepared incompatible metadata is obsolete by the time conversion starts.
        prepared = {
            "source": str(source),
            "before": (0, 0),
            "info": info("hevc"),
            "cached": False,
        }
        monkeypatch.setattr(web_mp4, "probe", lambda _: info())
        assert await manager._convert(str(source), prepared) == "skipped"

    asyncio.run(run())


def test_checker_failures_are_counted_and_remaining_paths_preserved():
    async def run():
        manager = web_mp4.WebMP4Manager()
        manager.state["total"] = 1
        manager._check = AsyncMock(side_effect=FileNotFoundError("missing"))
        await manager._batch(["missing"])
        assert manager.state["failed"] == manager.state["processed"] == 1
        assert manager.snapshot()["remaining"] == 0
        assert manager.state["errors"][0]["path"] == "missing"

    asyncio.run(run())


def test_cancellation_at_consumer_priority_boundary(monkeypatch):
    async def run():
        manager = web_mp4.WebMP4Manager()
        prepared = False

        async def check(path):
            nonlocal prepared
            prepared = True
            return {"cached": False}

        async def wait(*args):
            return not prepared

        manager._check = check
        monkeypatch.setattr(web_mp4.activity, "wait_turn_async", wait)
        manager.state["total"] = 1
        await manager._batch(["pending"])
        assert manager.state["processed"] == 0
        assert manager.snapshot()["remaining"] == 1

    asyncio.run(run())


def test_resume_and_cancel_error_routes(monkeypatch):
    manager = web_mp4.WebMP4Manager()
    monkeypatch.setattr(routes, "manager", manager)
    client = TestClient(create_app())
    assert client.post("/api/transcode/web-mp4/resume").status_code == 409
    manager.resume = AsyncMock(return_value=manager.snapshot())
    assert client.post("/api/transcode/web-mp4/resume").status_code == 200
    manager.request_cancel = AsyncMock(side_effect=DatabaseReadOnlyError("Cannot save"))
    assert client.post("/api/transcode/web-mp4/cancel").status_code == 503


def test_startup_restore_failure_does_not_stop_the_app(monkeypatch):
    manager = web_mp4.WebMP4Manager()
    manager.restore = AsyncMock(side_effect=OSError("Cannot read checkpoint"))
    monkeypatch.setattr(routes, "manager", manager)
    with TestClient(create_app()) as client:
        assert client.get("/api/health").status_code == 200
