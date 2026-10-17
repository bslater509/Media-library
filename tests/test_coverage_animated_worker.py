"""Coverage tests for AnimatedThumbWorker, shared status payloads and the WebP router.

Targets the branches that the existing animated-preview tests never reach: cache
reconciliation, enqueue de-duplication/priority promotion, the worker run loop
(success / soft failure / hard failure / DB write failure) and the status
payload builders shared by ``/api/system/status``.
"""

import asyncio
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

import media_library.workers.animated_thumb_worker as anim_worker_mod
from media_library.core.state import state
from media_library.db.connection import db_conn
from media_library.workers.animated_thumb_worker import (
    AnimatedThumbWorker,
    sync_animated_thumb_db_cache,
)


@pytest.fixture
def sandbox_media(sandbox_env):
    """Sandbox library wired as the app's active destination for path validation."""
    previous = state.get("destination_dir")
    state["destination_dir"] = str(sandbox_env["media_dir"])
    try:
        yield sandbox_env
    finally:
        state["destination_dir"] = previous


def _insert_video_row(video: Path, folder: str, has_animated: int = 0) -> None:
    with db_conn() as conn, conn:
        conn.execute(
            """INSERT INTO files (path, name, folder_path, type, ext, size, size_formatted,
                                  mtime, duration, width, height, has_animated)
               VALUES (?, ?, ?, 'video', '.mp4', ?, '1 KB', ?, 60.0, 640, 360, ?)""",
            (
                str(video),
                video.name,
                folder,
                video.stat().st_size,
                video.stat().st_mtime,
                has_animated,
            ),
        )


def _has_animated(video: Path) -> int:
    with db_conn() as conn:
        row = conn.execute(
            "SELECT has_animated FROM files WHERE path = ?", (str(video),)
        ).fetchone()
    return int(row["has_animated"] or 0)


def _write_anim_file(target: Path) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    frames = [Image.new("RGB", (32, 32), color=c) for c in ("red", "blue")]
    frames[0].save(
        target,
        format="WEBP",
        save_all=True,
        append_images=frames[1:],
        duration=100,
        loop=0,
    )
    return target


# --------------------------------------------------------------------------
# sync_animated_thumb_db_cache
# --------------------------------------------------------------------------


def test_sync_cache_marks_existing_anim_files(sandbox_env):
    from media_library.services.ffmpeg_service import get_animated_preview_path

    media = sandbox_env["media_dir"]
    video = media / "sync_me.mp4"
    video.write_bytes(b"\x00" * 256)
    _insert_video_row(video, str(media), has_animated=0)

    anim_path = get_animated_preview_path(str(video))
    _write_anim_file(anim_path)

    sync_animated_thumb_db_cache()

    assert _has_animated(video) == 1


def test_sync_cache_noop_without_dir_or_files(sandbox_env):
    media = sandbox_env["media_dir"]
    video = media / "untouched.mp4"
    video.write_bytes(b"\x00" * 256)
    _insert_video_row(video, str(media), has_animated=0)

    # Animated cache directory does not exist yet: nothing to reconcile.
    sync_animated_thumb_db_cache()
    assert _has_animated(video) == 0

    # Empty (and zero-byte only) directory must not touch the index either.
    anim_dir = Path(sandbox_env["thumb_dir"]) / "animated"
    anim_dir.mkdir(parents=True, exist_ok=True)
    (anim_dir / "anim_zero.webp").write_bytes(b"")
    sync_animated_thumb_db_cache()
    assert _has_animated(video) == 0


def test_sync_cache_survives_db_errors(sandbox_env):
    import media_library.db.connection as db_connection_mod

    with patch.object(
        db_connection_mod,
        "db_conn",
        MagicMock(side_effect=sqlite3.DatabaseError("boom")),
    ):
        # Must swallow the error instead of raising into the worker startup path.
        sync_animated_thumb_db_cache()


# --------------------------------------------------------------------------
# enqueue / enqueue_batch / enqueue_folder
# --------------------------------------------------------------------------


def test_enqueue_falls_back_when_path_validation_fails(sandbox_env):
    """A path outside the library still gets queued (validation is best-effort)."""
    worker = AnimatedThumbWorker()
    stray = sandbox_env["base_dir"] / "stray_clip.mp4"
    stray.write_bytes(b"\x00" * 64)

    assert worker.enqueue(str(stray), priority=True) is True
    assert str(stray) in worker.items_map
    assert worker.get_status()["queue_length"] == 1


def test_enqueue_short_circuits_when_preview_already_cached(sandbox_env):
    from media_library.services.ffmpeg_service import get_animated_preview_path

    worker = AnimatedThumbWorker()
    media = sandbox_env["media_dir"]
    video = media / "already_done.mp4"
    video.write_bytes(b"\x00" * 128)
    _insert_video_row(video, str(media), has_animated=0)

    _write_anim_file(get_animated_preview_path(str(video)))

    assert worker.enqueue(str(video)) is False
    assert worker.get_status()["queue_length"] == 0
    assert str(video) in worker.recent_completed
    assert _has_animated(video) == 1
    # Cached path is appended at most once.
    assert worker.recent_completed.count(str(video)) == 1
    assert worker.enqueue(str(video)) is False


def test_enqueue_deduplicates_and_promotes_priority(sandbox_env):
    worker = AnimatedThumbWorker()
    media = sandbox_env["media_dir"]
    video = media / "dup_clip.mp4"
    video.write_bytes(b"\x00" * 64)

    assert worker.enqueue(str(video)) is True
    # Second enqueue of the same path is a no-op while still queued.
    assert worker.enqueue(str(video)) is False

    item = worker.items_map[str(video)]
    assert item["status"] == "queued"
    assert item["priority"] is False

    # Promote to the priority queue.
    assert worker.enqueue(str(video), priority=True) is True
    assert worker.items_map[str(video)]["priority"] is True
    assert not worker.priority_queue.empty()
    assert worker.priority_queue.get_nowait() is item

    # Once the item is generating, further enqueues are rejected.
    item["status"] = "generating"
    assert worker.enqueue(str(video), priority=True) is False


def test_enqueue_batch_and_folder(sandbox_media):
    worker = AnimatedThumbWorker()
    media = sandbox_media["media_dir"]
    folder = media / "batch_folder"
    folder.mkdir()
    vid_a = folder / "a.mp4"
    vid_b = folder / "b.mp4"
    vid_c = folder / "c.mp4"
    for v in (vid_a, vid_b, vid_c):
        v.write_bytes(b"\x00" * 32)
    _insert_video_row(vid_a, str(folder), has_animated=0)
    _insert_video_row(vid_b, str(folder), has_animated=0)
    # Already compiled: must be skipped by the folder sweep.
    _insert_video_row(vid_c, str(folder), has_animated=1)

    assert worker.enqueue_batch([str(vid_a), str(vid_b)]) == 2
    # Already queued paths are not counted twice.
    assert worker.enqueue_batch([str(vid_a), str(vid_b)]) == 0
    worker.cancel_all()

    assert worker.enqueue_folder(str(folder)) == 2
    assert str(vid_c) not in worker.items_map


def test_enqueue_folder_handles_db_errors(sandbox_media, caplog):
    worker = AnimatedThumbWorker()
    with patch.object(
        anim_worker_mod, "db_conn", MagicMock(side_effect=sqlite3.DatabaseError("x"))
    ):
        assert worker.enqueue_folder(str(sandbox_media["media_dir"])) == 0


# --------------------------------------------------------------------------
# cancel / status reporting
# --------------------------------------------------------------------------


def test_cancel_all_clears_both_queues_and_aborts_active(sandbox_env):
    worker = AnimatedThumbWorker()
    media = sandbox_env["media_dir"]
    for name in ("p1.mp4", "b1.mp4"):
        p = media / name
        p.write_bytes(b"\x00" * 16)
        worker.enqueue(str(p), priority=name.startswith("p1"))

    import threading

    abort = threading.Event()
    worker.active_task = {"path": str(media / "p1.mp4"), "abort_event": abort}
    worker.items_map[str(media / "b1.mp4")]["status"] = "queued"

    cancelled = worker.cancel_all()

    assert cancelled >= 2
    assert abort.is_set()
    assert worker.priority_queue.empty()
    assert worker.batch_queue.empty()
    assert worker.items_map == {}


def test_get_status_percent_and_active_task(sandbox_env):
    worker = AnimatedThumbWorker()
    st = worker.get_status()
    assert st["status"] == "idle"
    assert st["percent"] == 0.0
    assert st["active"] is None
    assert st["current_file"] == ""

    video = sandbox_env["media_dir"] / "status_clip.mp4"
    video.write_bytes(b"\x00" * 16)
    worker.enqueue(str(video))
    worker.processed_count = 3
    st = worker.get_status()
    # 3 done + 1 queued => 75%
    assert st["percent"] == 75.0
    assert st["total_queued"] == 1

    worker.active_task = {
        "path": str(video),
        "name": "status_clip.mp4",
        "started_at": 123.0,
        "percent": 40.0,
    }
    worker._status = "generating"
    st = worker.get_status()
    assert st["status"] == "generating"
    assert st["active"]["percent"] == 40.0
    assert st["current_file"] == "status_clip.mp4"
    # 3 done + 1 queued + 1 active => 60%
    assert st["percent"] == 60.0

    worker.pause()
    assert worker.get_status()["status"] == "paused"
    worker.resume()
    assert worker.get_status()["status"] == "generating"
    worker.active_task = None
    worker.resume()
    assert worker.get_status()["status"] == "idle"


def test_get_status_history_is_bounded(sandbox_env):
    worker = AnimatedThumbWorker()
    worker.history = [
        {"name": f"f{i}", "path": f"/p/{i}", "status": "completed", "completed_at": 0.0}
        for i in range(60)
    ]
    worker.recent_completed = [f"/p/{i}" for i in range(120)]
    st = worker.get_status()
    assert len(st["history"]) == 20
    assert len(st["recent_completed"]) == 50


# --------------------------------------------------------------------------
# run loop
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_run_loop_success_path_updates_index(sandbox_env, monkeypatch):
    media = sandbox_env["media_dir"]
    video = media / "loop_ok.mp4"
    video.write_bytes(b"\x00" * 64)
    _insert_video_row(video, str(media), has_animated=0)

    anim_target = Path(sandbox_env["thumb_dir"]) / "animated" / "anim_loop_ok.webp"
    _write_anim_file(anim_target)

    calls = []

    async def fake_generate(path, *args, **kwargs):
        calls.append(path)
        return anim_target

    monkeypatch.setattr(anim_worker_mod, "generate_animated_preview", fake_generate)

    worker = AnimatedThumbWorker()
    worker.enqueue(str(video))
    await worker.start()
    try:
        for _ in range(100):
            if worker.processed_count:
                break
            await asyncio.sleep(0.02)
    finally:
        await worker.stop()

    assert calls == [str(video)]
    assert worker.processed_count == 1
    assert worker.failed_count == 0
    assert _has_animated(video) == 1
    assert str(video) in worker.recent_completed
    assert worker.history[-1]["status"] == "completed"
    assert worker.items_map == {}
    assert worker.active_task is None
    assert worker.get_status()["status"] in ("idle", "generating")


@pytest.mark.anyio
async def test_run_loop_soft_failure_when_generation_returns_nothing(
    sandbox_env, monkeypatch
):
    media = sandbox_env["media_dir"]
    video = media / "loop_fail.mp4"
    video.write_bytes(b"\x00" * 64)
    _insert_video_row(video, str(media), has_animated=0)

    async def fake_generate(path, *args, **kwargs):
        return None

    monkeypatch.setattr(anim_worker_mod, "generate_animated_preview", fake_generate)

    worker = AnimatedThumbWorker()
    worker.enqueue(str(video))
    await worker.start()
    try:
        for _ in range(100):
            if worker.failed_count:
                break
            await asyncio.sleep(0.02)
    finally:
        await worker.stop()

    assert worker.processed_count == 0
    assert worker.failed_count == 1
    assert worker.history[-1]["status"] == "failed"
    assert _has_animated(video) == 0
    assert worker.items_map == {}


@pytest.mark.anyio
async def test_run_loop_hard_failure_and_db_write_error(sandbox_env, monkeypatch):
    media = sandbox_env["media_dir"]
    boom = media / "loop_boom.mp4"
    boom.write_bytes(b"\x00" * 64)
    ok = media / "loop_db_boom.mp4"
    ok.write_bytes(b"\x00" * 64)

    anim_target = Path(sandbox_env["thumb_dir"]) / "animated" / "anim_loop_db.webp"
    _write_anim_file(anim_target)

    async def fake_generate(path, *args, **kwargs):
        if path.endswith("loop_boom.mp4"):
            raise RuntimeError("ffmpeg exploded")
        return anim_target

    monkeypatch.setattr(anim_worker_mod, "generate_animated_preview", fake_generate)

    worker = AnimatedThumbWorker()
    worker.enqueue(str(boom))
    worker.enqueue(str(ok))
    await worker.start()
    try:
        for _ in range(100):
            if worker.failed_count and worker.processed_count:
                break
            await asyncio.sleep(0.02)
        # The DB update for the successful item fails but must not fail the item.
        monkeypatch.setattr(
            anim_worker_mod,
            "db_conn",
            MagicMock(side_effect=sqlite3.DatabaseError("index locked")),
        )
        worker.enqueue(str(ok))
        for _ in range(100):
            if worker.processed_count >= 2:
                break
            await asyncio.sleep(0.02)
    finally:
        await worker.stop()

    assert worker.failed_count == 1
    assert worker.processed_count == 2
    assert worker.history[-1]["status"] == "completed"


@pytest.mark.anyio
async def test_run_loop_skips_cancelled_items(sandbox_env, monkeypatch):
    media = sandbox_env["media_dir"]
    video = media / "loop_cancelled.mp4"
    video.write_bytes(b"\x00" * 64)

    calls = []

    async def fake_generate(path, *args, **kwargs):
        calls.append(path)

    monkeypatch.setattr(anim_worker_mod, "generate_animated_preview", fake_generate)

    worker = AnimatedThumbWorker()
    worker.enqueue(str(video))
    # Mark the queued item cancelled before the loop consumes it.
    worker.items_map[str(video)]["status"] = "cancelled"
    await worker.start()
    await asyncio.sleep(0.2)
    await worker.stop()

    assert calls == []
    assert worker.failed_count == 0


@pytest.mark.anyio
async def test_run_loop_survives_transient_loop_errors(sandbox_env, monkeypatch):
    """A failure in the pause gate must be logged and retried, not kill the loop."""
    media = sandbox_env["media_dir"]
    video = media / "loop_recover.mp4"
    video.write_bytes(b"\x00" * 64)
    anim_target = Path(sandbox_env["thumb_dir"]) / "animated" / "anim_recover.webp"
    _write_anim_file(anim_target)

    async def fake_generate(path, *args, **kwargs):
        return anim_target

    monkeypatch.setattr(anim_worker_mod, "generate_animated_preview", fake_generate)

    worker = AnimatedThumbWorker()
    calls = {"n": 0}
    real_wait = worker._pause_event.wait

    async def flaky_wait():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient gate failure")
        return await real_wait()

    monkeypatch.setattr(worker._pause_event, "wait", flaky_wait)

    worker.enqueue(str(video))
    await worker.start()
    try:
        for _ in range(150):
            if worker.processed_count:
                break
            await asyncio.sleep(0.02)
    finally:
        await worker.stop()

    assert worker.processed_count == 1


# --------------------------------------------------------------------------
# WebP router branches
# --------------------------------------------------------------------------


def test_webp_router_pause_toggle_and_folder_enqueue(api_client):
    from media_library.core.state import state
    from media_library.routers.webp import webp_worker_instance

    res_status = api_client.get("/api/backup/webp/status")
    assert res_status.status_code == 200
    assert "saved_formatted" in res_status.json()

    first = api_client.post("/api/backup/webp/pause").json()
    assert first["is_paused"] is True
    second = api_client.post("/api/backup/webp/pause").json()
    assert second["is_paused"] is False
    assert api_client.post("/api/backup/webp/resume").json()["is_paused"] is False

    folder = state["destination_dir"]
    enqueued = []
    with patch.object(
        webp_worker_instance,
        "enqueue_folder",
        side_effect=lambda p: enqueued.append(p) or 2,
    ):
        res = api_client.post(
            "/api/backup/webp/optimize-folder",
            json={"folder_path": folder},
        )
    assert res.status_code == 200
    body = res.json()
    assert body["success"] is True
    assert body["enqueued"] == 2
    assert enqueued == [folder]


# --------------------------------------------------------------------------
# Shared status payload builders
# --------------------------------------------------------------------------


def test_index_counts_cache_and_db_fallback():
    from media_library.services import status as status_mod

    with patch.object(status_mod, "db_conn") as mock_db:
        conn = MagicMock()
        conn.execute.return_value.fetchone.side_effect = [(7,), (3,)]
        mock_db.return_value.__enter__.return_value = conn

        assert status_mod.get_cached_index_counts() == (7, 3)
        # Cached within the TTL: no second DB round trip.
        assert status_mod.get_cached_index_counts() == (7, 3)
        assert conn.execute.call_count == 2

    with patch.object(
        status_mod, "db_conn", MagicMock(side_effect=sqlite3.DatabaseError("gone"))
    ):
        files, folders = status_mod.get_cached_index_counts()
        assert isinstance(files, int)
        assert isinstance(folders, int)


def test_index_status_payload_while_crawling():
    from media_library.db.indexer import indexer_state
    from media_library.services.status import index_status_payload

    saved = dict(indexer_state)
    try:
        indexer_state.update(
            {
                "status": "crawling",
                "scanned_folders": 5,
                "total_folders": 10,
                "skipped_folders": 2,
                "scanned_files": 42,
                "current_folder": "/media/x",
            }
        )
        payload = index_status_payload()
        assert payload["status"] == "crawling"
        assert payload["percent"] == 50.0
        assert payload["total_folders"] == 10
        assert payload["current_folder"] == "/media/x"

        # Clamped between 1% and 99% while crawling.
        indexer_state["scanned_folders"] = 0
        assert index_status_payload()["percent"] == 1.0
        indexer_state["scanned_folders"] = 999
        assert index_status_payload()["percent"] == 99.0
    finally:
        indexer_state.clear()
        indexer_state.update(saved)


def test_transfers_payload_shapes_active_and_history():
    from media_library.services import status as status_mod

    transfer = MagicMock()
    transfer.get_status.return_value = {
        "active": {
            "id": "t1",
            "filename": "a.mp4",
            "destination_folder": "/media/x",
            "bytes_copied": 10,
            "total_bytes": 100,
            "percent": 10.0,
        },
        "active_count": 1,
    }
    transfer.transfer_items_map = {
        "q1": {
            "id": "q1",
            "filename": "b.mp4",
            "destination_folder": "/media/y",
            "size_formatted": "1 MB",
            "status": "queued",
            "created_at": 2.0,
        },
        "q2": {
            "id": "q2",
            "filename": "c.mp4",
            "destination_folder": "/media/y",
            "size_formatted": "2 MB",
            "status": "queued",
            "created_at": 1.0,
        },
        "done": {
            "id": "x",
            "filename": "skip.mp4",
            "destination_folder": "/media/y",
            "status": "completed",
        },
    }
    transfer.transfers_history = [
        {
            "id": "h1",
            "filename": "old.mp4",
            "destination_folder": "/media/z",
            "status": "completed",
            "completed_at": 9.0,
        },
        {
            "id": "h2",
            "filename": "pending.mp4",
            "destination_folder": "/media/z",
            "status": "moving",
        },
    ]

    transcode = MagicMock()
    transcode.transcode_paused = True
    transcode.get_status.return_value = {"active": None}
    transcode.transcode_items_map = {
        "tq": {"id": "tq", "filename": "d.mkv", "status": "queued", "created_at": 5.0}
    }
    transcode.transcode_history = [
        {
            "id": "th",
            "filename": "e.mkv",
            "target_path": "/media/out/e.mp4",
            "status": "failed",
            "method": "reencode",
            "completed_at": 3.0,
            "error": "boom",
        }
    ]

    with (
        patch.object(status_mod, "transfer_worker_instance", transfer),
        patch.object(status_mod, "transcode_worker_instance", transcode),
    ):
        payload = status_mod.transfers_payload()

    assert payload["active"]["filename"] == "a.mp4"
    assert payload["active_count"] == 1
    assert [q["id"] for q in payload["queued"]] == ["q2", "q1"]  # sorted by created_at
    assert payload["queued_count"] == 2
    assert payload["total_pending"] == 3
    assert payload["history"][0]["id"] == "h1"
    assert payload["transcode"]["queued"][0]["id"] == "tq"
    assert payload["transcode"]["history"][0]["target_filename"] == "e.mp4"
    assert payload["transcode"]["is_paused"] is True
    assert payload["transcode"]["total_pending"] == 1

    # Empty state: everything collapses to a null active item.
    transfer.get_status.return_value = {"active": None}
    transfer.transfer_items_map = {}
    transfer.transfers_history = []
    transcode.transcode_items_map = {}
    transcode.transcode_history = []
    with (
        patch.object(status_mod, "transfer_worker_instance", transfer),
        patch.object(status_mod, "transcode_worker_instance", transcode),
    ):
        empty = status_mod.transfers_payload()
    assert empty["active"] is None
    assert empty["queued"] == []
    assert empty["history"] == []
    assert empty["transcode"]["active"] is None


def test_zip_status_payload_lists_running_tasks():
    from media_library.services.status import zip_status_payload
    from media_library.services.zip import zip_lock, zip_tasks_state

    with zip_lock:
        existing = dict(zip_tasks_state["active_tasks"])
        zip_tasks_state["active_tasks"]["/media/album.zip"] = {
            "type": "zip",
            "status": "running",
            "percent": 42.5,
            "current": 3,
            "total": 7,
            "message": "packing",
            "started_at": 100.0,
        }
    try:
        payload = zip_status_payload()
        assert payload["is_active"] is True
        task = next(t for t in payload["tasks"] if t["path"] == "/media/album.zip")
        assert task["filename"] == "album.zip"
        assert task["percent"] == 42.5
        assert task["total"] == 7
        assert task["message"] == "packing"
    finally:
        with zip_lock:
            zip_tasks_state["active_tasks"].clear()
            zip_tasks_state["active_tasks"].update(existing)

    # Idle library: nothing is reported as running.
    with zip_lock:
        saved = dict(zip_tasks_state["active_tasks"])
        zip_tasks_state["active_tasks"].clear()
    try:
        assert zip_status_payload()["is_active"] is False
    finally:
        with zip_lock:
            zip_tasks_state["active_tasks"].update(saved)
