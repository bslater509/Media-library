"""Activity polling stays local; scheduling only stops work at safe boundaries."""

from __future__ import annotations

import asyncio
import threading
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient

from media_library import activity, animated, backups, dedupe, indexer, tools, zip_batch
from media_library.app import create_app
from media_library.routers import activity as routes
from media_library.writes import WriteError


@pytest.fixture(autouse=True)
def clean_activity(monkeypatch):
    monkeypatch.setattr(activity, "_priority", False)
    monkeypatch.setattr(activity, "_work", {})
    monkeypatch.setattr(activity, "_waiting", {})
    monkeypatch.setattr(activity, "_local", threading.local())


def test_summary_is_memory_only(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Activity must not touch storage or discover tools")

    monkeypatch.setattr(animated, "status", forbidden)
    monkeypatch.setattr(backups, "status", forbidden)
    monkeypatch.setattr(tools, "tools_status", forbidden)
    monkeypatch.setattr(routes.downloads, "status", forbidden)
    monkeypatch.setattr(routes.downloads, "jobs", {})
    monkeypatch.setattr(routes.downloads, "history", [])
    monkeypatch.setattr(
        routes.x265,
        "status",
        lambda: {
            "active": None,
            "queued": [],
            "history": [],
        },
    )
    monkeypatch.setattr(tools, "_public_job", lambda: None)
    monkeypatch.setattr(indexer, "status", lambda: {"status": "idle"})
    with TestClient(create_app(), raise_server_exceptions=True) as client:
        # Lifespan's startup/shutdown is tested elsewhere. Storage prohibition
        # applies to the summary itself, including backup status.
        result = client.get("/api/system/activity")
        assert result.status_code == 200
        assert result.json()["prioritize_browsing"] is False
        enabled = client.post(
            "/api/system/activity/priority",
            json={
                "prioritize_browsing": True,
            },
        )
        assert enabled.status_code == 200
        assert enabled.json()["prioritize_browsing"] is True
        assert client.get("/api/system/status").json()["prioritize_browsing"] is True
        activity.set_priority(False)


def test_tracked_progress_errors_and_manual_bypass():
    @activity.tracked("metadata")
    def work(result=None, fail=False):
        assert activity.checkpoint("metadata", "sample.mp4")
        assert activity._work["metadata"]["current"] == "sample.mp4"
        if fail:
            raise ValueError("probe broke")
        return result

    assert work({"probed": 1, "remaining": 2})["probed"] == 1
    assert "probed: 1" in activity._work["metadata"]["detail"]
    work({"failed": 2})
    assert "2 item(s)" in activity._work["metadata"]["error"]
    work()
    assert activity._work["metadata"]["error"]  # no-work pass retains last error
    with pytest.raises(ValueError, match="probe broke"):
        work(fail=True)
    assert activity._work["metadata"]["active"] == 0
    assert activity._work["metadata"]["current"] is None
    activity.set_priority(True)
    work({"probed": 1})  # explicit user work is not gated
    assert activity._work["metadata"]["error"] is None
    stop = threading.Event()
    stop.set()
    with activity.background(stop):
        assert not activity.checkpoint("metadata", "not-started")
    assert activity.checkpoint("untracked")


def test_sync_wait_release_cancel_and_nested_background(monkeypatch):
    activity.set_priority(True)
    calls = []

    def release(_delay):
        calls.append(True)
        assert activity._waiting["metadata"] == 1
        assert activity.item("metadata", "Metadata", "/tools")["state"] == "deferred"
        activity.set_priority(False)

    monkeypatch.setattr(activity.time, "sleep", release)
    assert activity.wait_turn("metadata")
    assert calls and not activity._waiting
    activity.set_priority(True)
    assert not activity.wait_turn("metadata", lambda: True)
    assert not activity._waiting
    first, second = threading.Event(), threading.Event()
    with activity.background(first):
        with activity.background(second):
            assert activity._local.stop is second
        assert activity._local.stop is first
    assert activity._local.stop is None


def test_async_wait_release_and_task_cancellation(monkeypatch):
    async def run():
        activity.set_priority(True)

        async def release(_delay):
            activity.set_priority(False)

        monkeypatch.setattr(activity.asyncio, "sleep", release)
        assert await activity.wait_turn_async("download:one")
        activity.set_priority(True)
        assert not await activity.wait_turn_async("download:one", lambda: True)

        async def cancelled(_delay):
            raise asyncio.CancelledError

        monkeypatch.setattr(activity.asyncio, "sleep", cancelled)
        with pytest.raises(asyncio.CancelledError):
            await activity.wait_turn_async("download:one")
        assert not activity._waiting

    asyncio.run(run())


def test_summary_all_states_and_controls(monkeypatch):
    activity._work["metadata"] = {"active": 1, "current": "a.mp4", "error": "broken"}
    monkeypatch.setattr(
        indexer,
        "status",
        lambda: {
            "status": "crawling",
            "current_folder": "folder",
            "error": "unavailable",
        },
    )
    monkeypatch.setattr(
        animated,
        "activity_status",
        lambda: {
            "active": "preview.mp4",
            "queued": 2,
            "completed": 3,
            "failed": 1,
        },
    )
    monkeypatch.setattr(
        dedupe,
        "visual_check_status",
        lambda: {
            "status": "running",
            "errors": 2,
        },
    )
    monkeypatch.setattr(
        zip_batch.runner,
        "status",
        lambda: {
            "status": "paused",
            "current_path": None,
            "percent": 50,
            "processed": 1,
            "total": 2,
            "errors": [{"error": "missing volume"}],
        },
    )
    monkeypatch.setattr(
        routes.x265,
        "status",
        lambda: {
            "active": {"id": "encode", "status": "running", "source_path": "a.mp4"},
            "queued": [],
            "history": [],
        },
    )
    monkeypatch.setattr(
        routes.downloads,
        "jobs",
        {
            "queued": {
                "id": "queued",
                "status": "queued",
                "url": "https://example.invalid",
            },
        },
    )
    monkeypatch.setattr(
        routes.downloads,
        "history",
        [
            {
                "id": "failed",
                "status": "failed",
                "url": "https://example.invalid",
                "error": "oops",
            },
            {
                "id": "done",
                "status": "completed",
                "title": "Album",
                "url": "",
                "percent": 100,
            },
        ],
    )
    monkeypatch.setattr(
        tools, "_public_job", lambda: {"status": "running", "phase": "installing"}
    )
    result = activity.summary()
    rows = {row["id"]: row for row in result["items"]}
    assert result["active_count"] >= 6
    assert result["failure_count"] >= 5
    assert rows["download:failed"]["controls"] == ["retry"]
    assert rows["download:done"]["controls"] == []
    assert rows["zip"]["controls"] == ["resume", "cancel"]
    assert rows["tool-update"]["controls"] == ["cancel"]
    assert rows["x265:encode"]["controls"] == ["cancel"]
    assert activity.item("test", "test", "/", percent=200)["percent"] == 100
    assert activity.item("test", "test", "/", percent=-1)["percent"] == 0


@pytest.mark.parametrize(
    ("key", "control", "method"),
    [
        ("download:job", "retry", "download_retry"),
        ("download:job", "cancel", "download_cancel"),
        ("x265:job", "cancel", "encode_cancel"),
        ("zip", "pause", "zip_pause"),
        ("zip", "resume", "zip_resume"),
        ("zip", "cancel", "zip_cancel"),
        ("animated", "cancel", "animated_cancel"),
        ("tool-update", "cancel", "tools_cancel"),
    ],
)
def test_action_dispatch(monkeypatch, key, control, method):
    entry = activity.item(key, "test", "/", controls=[control], job_id="job")
    monkeypatch.setattr(
        activity,
        "summary",
        lambda: {
            "items": [entry],
            "prioritize_browsing": False,
            "active_count": 1,
            "failure_count": 0,
        },
    )
    mocks = {
        "download_retry": AsyncMock(),
        "download_cancel": Mock(),
        "encode_cancel": Mock(),
        "zip_pause": Mock(),
        "zip_resume": Mock(),
        "zip_cancel": Mock(),
        "animated_cancel": Mock(),
        "tools_cancel": Mock(),
    }
    monkeypatch.setattr(routes.downloads, "retry", mocks["download_retry"])
    monkeypatch.setattr(routes.downloads, "cancel", mocks["download_cancel"])
    monkeypatch.setattr(routes.x265, "cancel", mocks["encode_cancel"])
    for name in ("pause", "resume", "cancel"):
        monkeypatch.setattr(zip_batch.runner, name, mocks[f"zip_{name}"])
    monkeypatch.setattr(animated, "cancel", mocks["animated_cancel"])
    monkeypatch.setattr(tools, "cancel_update", mocks["tools_cancel"])
    client = TestClient(create_app())
    assert (
        client.post(
            "/api/system/activity/action",
            json={
                "id": key,
                "action": control,
            },
        ).status_code
        == 200
    )
    mocks[method].assert_called_once()
    if method == "download_retry":
        mocks[method].assert_awaited_once_with("job")


def test_action_errors_and_validation(monkeypatch):
    client = TestClient(create_app())
    assert (
        client.post(
            "/api/system/activity/action",
            json={
                "id": "missing",
                "action": "cancel",
            },
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/api/system/activity/action",
            json={
                "id": "metadata",
                "action": "pause",
            },
        ).status_code
        == 409
    )
    assert (
        client.post(
            "/api/system/activity/action",
            json={
                "id": "metadata",
                "action": "invented",
            },
        ).status_code
        == 422
    )
    entry = activity.item("animated", "test", "/", controls=["cancel"])
    monkeypatch.setattr(
        activity,
        "summary",
        lambda: {
            "items": [entry],
            "prioritize_browsing": False,
            "active_count": 1,
            "failure_count": 0,
        },
    )
    monkeypatch.setattr(
        animated, "cancel", Mock(side_effect=WriteError("Already done", 409))
    )
    assert (
        client.post(
            "/api/system/activity/action",
            json={
                "id": "animated",
                "action": "cancel",
            },
        ).status_code
        == 409
    )


def test_queued_download_cancels_while_deferred(monkeypatch):
    from media_library.downloads import DownloadManager

    async def run():
        manager = DownloadManager()
        manager._save_soon = Mock()
        job = manager._new_job("https://example.invalid/video", "destination")
        manager.jobs[job["id"]] = job
        activity.set_priority(True)

        async def cancel(_delay):
            manager.cancel(job["id"])

        monkeypatch.setattr(activity.asyncio, "sleep", cancel)
        manager._download = AsyncMock()
        await manager._execute_job(job["id"])
        manager._download.assert_not_awaited()
        assert job["status"] == "cancelled"
        assert not activity._waiting

    asyncio.run(run())


def test_queued_encode_cancels_while_deferred(monkeypatch):
    from media_library.x265 import X265Manager

    async def run():
        manager = X265Manager()
        manager.queued = [{"id": "encode", "status": "queued"}]
        manager._execute = AsyncMock()
        activity.set_priority(True)

        async def cancel(_delay):
            manager.cancel("encode")

        monkeypatch.setattr(activity.asyncio, "sleep", cancel)
        await manager._loop()
        manager._execute.assert_not_awaited()
        assert manager.history[-1]["status"] == "cancelled"

    asyncio.run(run())


def test_stopped_sweeps_leave_items_pending(monkeypatch):
    from media_library import db, probe, thumbnail_worker, verify, webp
    from media_library.config import get_settings

    root = str(get_settings().library_dir)
    with db.writer() as conn, conn:
        for name in ("one.jpg", "two.jpg"):
            conn.execute(
                "INSERT INTO files (path, name, folder_path, type, ext, size) "
                "VALUES (?, ?, ?, 'image', '.jpg', 10)",
                (f"{root}/{name}", name, root),
            )
    activity.set_priority(True)
    stopped = threading.Event()
    stopped.set()
    with activity.background(stopped):
        assert probe.probe_batch()["probed"] == 0
        assert thumbnail_worker.ThumbnailSweep().run_once()["generated"] == 0
        assert webp.optimize_batch()["converted"] == 0
        assert dedupe.hash_content()["hashed"] == 0
        with db.writer() as conn, conn:
            conn.execute("UPDATE files SET has_thumb = 1")
        assert dedupe.hash_visual()["hashed"] == 0
        assert indexer.crawl()["scanned_folders"] == 0
        assert verify.VerifyWorker().run_pass()["scanned"] == 0
    with db.reader() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM files WHERE width IS NULL AND quick_hash IS NULL "
            "AND thumb_hash IS NULL"
        ).fetchone()
        assert row[0] == 2


def test_cancelled_scheduling_exits_watchers_and_zip(monkeypatch):
    from media_library import runtime

    wait = Mock(return_value=False)
    monkeypatch.setattr(activity, "wait_turn", wait)
    step = Mock()
    sweep = runtime._BatchSweep("webp-sweep", step)
    sweep._loop()
    wait.assert_called_once_with("webp", sweep._stop.is_set)
    step.assert_not_called()
    monkeypatch.setattr(runtime, "_backup_stop", threading.Event())
    create = Mock()
    monkeypatch.setattr(backups, "create_snapshot", create)
    runtime._BackupWatcher(1, first_delay=0)._loop()
    create.assert_not_called()
    runner = zip_batch.ZipBatchRunner()
    operation = Mock()
    runner._run(["one.zip"], operation)
    operation.assert_not_called()


def test_download_shutdown_does_not_start_deferred_job():
    from media_library.downloads import DownloadManager

    async def run():
        manager = DownloadManager()
        manager._closing = True
        job = manager._new_job("https://example.invalid/video", "destination")
        manager.jobs[job["id"]] = job
        manager._download = AsyncMock()
        await manager._execute_job(job["id"])
        manager._download.assert_not_awaited()
        assert job["status"] == "queued"

    asyncio.run(run())


def test_zip_cancel_during_manual_pause_starts_no_archive(monkeypatch):
    runner = zip_batch.ZipBatchRunner()
    runner._pause.set()
    operation = Mock()
    monkeypatch.setattr(zip_batch.time, "sleep", lambda _delay: runner._cancel.set())
    runner._run(["one.zip"], operation)
    operation.assert_not_called()
    assert runner.status()["status"] == "cancelled"
