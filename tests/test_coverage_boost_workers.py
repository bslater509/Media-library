"""Extra coverage for background worker run loops and queue helpers.

Targets the branches that the existing worker tests do not reach: the
LibraryOpsWorker move/delete/rename execution paths, the ThumbnailWorker
batch/pause/idle branches, the WebPOptimizerWorker memory guard and result
handling, and the TranscodeWorker enqueue/run-loop edge cases.
"""

import asyncio
import os
import threading
from unittest.mock import AsyncMock, MagicMock, patch

from media_library.core.state import state
from media_library.db.connection import db_conn
from media_library.workers.library_ops_worker import LibraryOpsWorker
from media_library.workers.thumbnail_worker import ThumbnailWorker
from media_library.workers.transcode_worker import TranscodeWorker
from media_library.workers.webp_worker import WebPOptimizerWorker

ORIG_SLEEP = asyncio.sleep


async def _fast_sleep(_seconds, *args, **kwargs):
    await ORIG_SLEEP(0.001)


async def _drive_until(worker, predicate, loops: int = 400) -> bool:
    """Runs a worker loop until ``predicate`` holds (or the loop budget runs out)."""
    task = asyncio.create_task(worker._run_loop())
    try:
        for _ in range(loops):
            await ORIG_SLEEP(0.01)
            if predicate():
                return True
    finally:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
    return False


# ==============================================================================
# LibraryOpsWorker
# ==============================================================================


def test_library_ops_worker_run_loop_executes_all_ops(sandbox_env):
    media = sandbox_env["media_dir"]
    state["destination_dir"] = str(media)
    worker = LibraryOpsWorker()

    src = media / "src"
    dst = media / "dst"
    src.mkdir()
    dst.mkdir()

    moving = src / "move_me.txt"
    moving.write_bytes(b"move")
    deleting = src / "delete_me.txt"
    deleting.write_bytes(b"delete")
    renaming = src / "old_name.txt"
    renaming.write_bytes(b"rename")

    worker.enqueue(
        {
            "id": "op_move",
            "op": "move",
            "source_path": str(moving),
            "target_path": str(dst / "move_me.txt"),
            "kind": "file",
        }
    )
    worker.enqueue({"id": "op_del", "op": "delete", "path": str(deleting), "kind": "file"})
    worker.enqueue(
        {"id": "op_rename", "op": "rename", "path": str(renaming), "new_name": "new_name.txt"}
    )
    worker.enqueue({"id": "op_cancel", "op": "delete", "path": str(deleting), "kind": "file"})
    worker.cancel("op_cancel")
    worker.enqueue(
        {
            "id": "op_fail",
            "op": "move",
            "source_path": str(src / "ghost.txt"),
            "target_path": str(dst / "ghost.txt"),
            "kind": "file",
        }
    )
    # Pre-fill history so the >500 truncation branch runs.
    worker.history = [{"id": f"h_{i}"} for i in range(502)]

    ran = asyncio.run(
        _drive_until(worker, lambda: worker.ops_queue.empty() and worker.active_op is None)
    )
    assert ran

    statuses = {h.get("id"): h.get("status") for h in worker.history}
    assert statuses["op_move"] == "completed"
    assert statuses["op_del"] == "completed"
    assert statuses["op_rename"] == "completed"
    # Pre-cancelled items are dropped without being executed.
    assert statuses["op_cancel"] == "cancelled"
    assert statuses["op_fail"] == "failed"
    failed = next(h for h in worker.history if h.get("id") == "op_fail")
    assert "Source not found" in failed["error"]

    assert (dst / "move_me.txt").exists()
    assert not deleting.exists()
    assert (src / "new_name.txt").exists()
    assert not renaming.exists()

    # Moves only are recorded on the undo stack.
    assert len(worker.undo_stack) == 1
    assert worker.undo_stack[0]["source_path"] == str(moving)

    # History is capped at 500 entries.
    assert len(worker.history) <= 500
    assert worker.get_status()["undo_count"] == 1


def test_library_ops_worker_status_with_active_and_queued(sandbox_env):
    worker = LibraryOpsWorker()
    worker.active_op = {
        "id": "active_1",
        "op": "move",
        "source_path": "/a/src.txt",
        "target_path": "/a/dst.txt",
        "kind": "file",
    }
    worker.enqueue_nowait(worker.ops_queue, {"id": "q1", "op": "delete", "status": "queued"})
    worker.items_map["q1"] = {"id": "q1", "op": "delete", "status": "queued"}

    st = worker.get_status()
    assert st["active"]["id"] == "active_1"
    assert st["active"]["op"] == "move"
    assert {"id": "q1", "op": "delete"} in st["queued"]
    assert st["queue_length"] == 1


# ==============================================================================
# ThumbnailWorker
# ==============================================================================


def test_thumbnail_worker_batch_pause_idle_and_failures(sandbox_env):
    media = sandbox_env["media_dir"]
    state["destination_dir"] = str(media)
    worker = ThumbnailWorker()

    names = ["ok.mp4", "bad.mp4", "boom.mp4"]
    for name in names:
        (media / name).write_bytes(b"\x00" * 64)
        with db_conn() as conn, conn:
            conn.execute(
                """INSERT INTO files (path, name, folder_path, type, ext, size,
                       size_formatted, mtime, has_thumb)
                   VALUES (?, ?, ?, 'video', '.mp4', 64, '64 B', 5.0, 0)""",
                (str(media / name), name, str(media)),
            )

    async def fake_generate(fpath, **kwargs):
        if "boom" in fpath:
            raise RuntimeError("ffmpeg exploded")
        return not fpath.endswith("bad.mp4")

    async def _test():
        with patch(
            "media_library.workers.thumbnail_worker.generate_thumbnail",
            side_effect=fake_generate,
        ), patch(
            "media_library.workers.thumbnail_worker.sync_thumbnail_db_cache"
        ), patch("asyncio.sleep", side_effect=_fast_sleep):
            worker.pause()
            assert await _drive_until(worker, lambda: worker.state["status"] == "paused")
            worker.resume()
            assert worker.state["status"] == "generating"
            # The batch runs and records successes/failures; the item whose
            # generator raised is logged and stays pending (retried next pass).
            done = await _drive_until(worker, lambda: worker.state["processed_count"] >= 1)
            assert done

            # With every item resolved the loop drains to the idle branch.
            with db_conn() as conn, conn:
                conn.execute("UPDATE files SET has_thumb = 2 WHERE name = 'boom.mp4'")
            done = await _drive_until(
                worker,
                lambda: worker.state["pending_count"] == 0
                and worker.state["status"] == "idle",
            )
            assert done

    asyncio.run(_test())

    with db_conn() as conn:
        rows = {
            r["name"]: r["has_thumb"]
            for r in conn.execute("SELECT name, has_thumb FROM files").fetchall()
        }
    assert rows["ok.mp4"] == 1
    assert rows["bad.mp4"] == 2
    assert rows["boom.mp4"] == 2
    assert worker.state["processed_count"] >= 1
    assert worker.state["current_file"] == ""
    assert worker.state["percent"] == 100.0


def test_thumbnail_worker_priority_queue_and_error_paths(sandbox_env):
    media = sandbox_env["media_dir"]
    state["destination_dir"] = str(media)
    worker = ThumbnailWorker()

    clip = media / "priority.mp4"
    clip.write_bytes(b"\x00" * 32)
    with db_conn() as conn, conn:
        conn.execute(
            """INSERT INTO files (path, name, folder_path, type, ext, size,
                   size_formatted, mtime, has_thumb)
               VALUES (?, ?, ?, 'video', '.mp4', 32, '32 B', 7.0, 0)""",
            (str(clip), "priority.mp4", str(media)),
        )

    async def fake_generate(fpath, **kwargs):
        return True

    async def _test():
        with patch(
            "media_library.workers.thumbnail_worker.generate_thumbnail",
            side_effect=fake_generate,
        ), patch(
            "media_library.workers.thumbnail_worker.sync_thumbnail_db_cache"
        ), patch("asyncio.sleep", side_effect=_fast_sleep):
            worker.enqueue_folder(str(media))
            assert worker.priority_queue.qsize() == 1
            done = await _drive_until(
                worker, lambda: worker.priority_queue.empty() and worker.state["processed_count"] >= 1
            )
            assert done

    asyncio.run(_test())

    # DB failure while enqueuing / refreshing stats must be swallowed.
    broken = ThumbnailWorker()
    with patch(
        "media_library.workers.thumbnail_worker.db_conn",
        side_effect=RuntimeError("db is down"),
    ):
        broken.enqueue_folder(str(media))
        broken._refresh_stats()
    assert broken.state["pending_count"] == 0


# ==============================================================================
# WebPOptimizerWorker
# ==============================================================================


def test_webp_worker_enqueue_helpers_and_throttling(sandbox_env):
    from PIL import Image

    media = sandbox_env["media_dir"]
    state["destination_dir"] = str(media)
    worker = WebPOptimizerWorker()

    img = media / "photo.jpg"
    Image.new("RGB", (16, 16), color="red").save(img, "JPEG")
    (media / "note.txt").write_text("not an image")
    (media / "native.webp").write_bytes(b"webp")

    # Only the loose JPEG is a valid candidate.
    queued = worker.enqueue_files(
        [str(img), str(media / "note.txt"), str(media / "ghost.jpg"), str(media / "native.webp")]
    )
    assert queued == 1
    assert worker.priority_queue.qsize() == 1
    while not worker.priority_queue.empty():
        worker.priority_queue.get_nowait()

    # Loose folder: one queue item per convertible image.
    loose = media / "loose"
    loose.mkdir()
    with db_conn() as conn, conn:
        conn.execute(
            """INSERT INTO folders (path, name, parent_path, file_count,
                   subfolder_count, last_scanned, is_zip)
               VALUES (?, ?, ?, 2, 0, 0.0, 0)""",
            (str(loose), "loose", str(media)),
        )
    for i in range(2):
        Image.new("RGB", (12, 12), color="blue").save(loose / f"l{i}.jpg", "JPEG")
        with db_conn() as conn, conn:
            conn.execute(
                """INSERT INTO files (path, name, folder_path, type, ext, size,
                       size_formatted, mtime, webp_optimized)
                   VALUES (?, ?, ?, 'image', '.jpg', 10, '10 B', 1.0, 0)""",
                (str(loose / f"l{i}.jpg"), f"l{i}.jpg", str(loose)),
            )
    assert worker.enqueue_folder(str(loose)) == 2
    while not worker.priority_queue.empty():
        worker.priority_queue.get_nowait()

    # ZIP folders are enqueued once as a single archive unit and throttled.
    zip_path = media / "album.zip"
    zip_path.write_bytes(b"PK\x05\x06" + b"\x00" * 18)
    with db_conn() as conn, conn:
        conn.execute(
            """INSERT INTO folders (path, name, parent_path, file_count,
                   subfolder_count, last_scanned, is_zip)
               VALUES (?, ?, ?, 2, 0, 0.0, 1)""",
            (str(zip_path), "album.zip", str(media)),
        )
        for i in range(2):
            conn.execute(
                """INSERT INTO files (path, name, folder_path, type, ext, size,
                       size_formatted, mtime, webp_optimized)
                   VALUES (?, ?, ?, 'image', '.jpg', 10, '10 B', 1.0, 0)""",
                (f"{zip_path}/inner{i}.jpg", f"inner{i}.jpg", str(zip_path)),
            )
    assert worker.enqueue_folder(str(zip_path)) == 2
    assert worker.priority_queue.qsize() == 1
    # Second enqueue inside the throttle window is dropped.
    assert worker.enqueue_folder(str(zip_path)) == 0
    assert worker.priority_queue.qsize() == 1
    while not worker.priority_queue.empty():
        worker.priority_queue.get_nowait()

    # DB error while enqueuing is swallowed.
    with patch(
        "media_library.workers.webp_worker.db_conn", side_effect=RuntimeError("db down")
    ):
        assert worker.enqueue_folder(str(loose)) == 0


def test_webp_worker_next_auto_items_respects_auto_optimize(sandbox_env):
    from PIL import Image

    media = sandbox_env["media_dir"]
    state["destination_dir"] = str(media)
    worker = WebPOptimizerWorker()
    Image.new("RGB", (16, 16), color="green").save(media / "auto.jpg", "JPEG")
    with db_conn() as conn, conn:
        conn.execute(
            """INSERT INTO files (path, name, folder_path, type, ext, size,
                   size_formatted, mtime, webp_optimized)
               VALUES (?, ?, ?, 'image', '.jpg', 10, '10 B', 1.0, 0)""",
            (str(media / "auto.jpg"), "auto.jpg", str(media)),
        )

    with db_conn() as conn:
        assert len(worker._next_auto_items(conn)) == 1
        with patch("media_library.workers.webp_worker.WEBP_AUTO_OPTIMIZE", False):
            assert worker._next_auto_items(conn) == []


def test_webp_worker_run_loop_memory_guard_and_results(sandbox_env):
    from PIL import Image

    media = sandbox_env["media_dir"]
    state["destination_dir"] = str(media)
    worker = WebPOptimizerWorker()

    # Ordered by size DESC in the auto sweep: p2 (largest) is picked last so the
    # sweep is exercised across several batches.
    for i, size in enumerate((10, 20, 30)):
        Image.new("RGB", (24, 24), color="purple").save(media / f"p{i}.jpg", "JPEG")
        with db_conn() as conn, conn:
            conn.execute(
                """INSERT INTO files (path, name, folder_path, type, ext, size,
                       size_formatted, mtime, webp_optimized)
                   VALUES (?, ?, ?, 'image', '.jpg', ?, ?, 1.0, 0)""",
                (str(media / f"p{i}.jpg"), f"p{i}.jpg", str(media), size, f"{size} B"),
            )

    zip_path = media / "dupe.zip"
    zip_path.write_bytes(b"PK\x05\x06" + b"\x00" * 18)

    converted: list[str] = []
    zip_calls: list[str] = []

    def fake_convert(path, **kwargs):
        name = os.path.basename(path)
        converted.append(name)
        # Mark the row done so the automatic sweep advances to the next image.
        with db_conn() as conn, conn:
            conn.execute("UPDATE files SET webp_optimized = 1 WHERE path = ?", (path,))
        if name == "p0.jpg":
            return {"status": "converted", "converted_count": 1}
        if name == "p1.jpg":
            return {"status": "error", "error": "decode failed"}
        raise RuntimeError("p2 exploded")

    def fake_zip(path, quality=None):
        zip_calls.append(path)
        return {"status": "already_webp", "converted": 0}

    async def _test():
        with patch(
            "media_library.workers.webp_worker.convert_image_file_to_webp",
            side_effect=fake_convert,
        ), patch(
            "media_library.workers.webp_worker.optimize_zip_images_to_webp",
            side_effect=fake_zip,
        ), patch(
            # Force the memory back-off loop to run before converting.
            "media_library.workers.webp_worker._available_memory_mb",
            return_value=1.0,
        ), patch("asyncio.sleep", side_effect=_fast_sleep):
            # Duplicate ZIP entries in the priority queue are deduplicated.
            for _ in range(2):
                worker.enqueue_nowait(
                    worker.priority_queue,
                    {
                        "path": str(zip_path),
                        "name": "dupe.zip",
                        "folder_path": str(zip_path),
                        "is_zip": 1,
                    },
                )
            worker.pause()
            assert await _drive_until(worker, lambda: worker.state["is_paused"])
            worker.resume()
            done = await _drive_until(
                worker,
                lambda: {"p0.jpg", "p1.jpg", "p2.jpg"} <= set(converted)
                and len(zip_calls) >= 1,
            )
            assert done

    asyncio.run(_test())

    # The duplicate ZIP entry was processed exactly once.
    assert len(zip_calls) == 1
    assert {"p0.jpg", "p1.jpg", "p2.jpg"} <= set(converted)
    assert worker.state["current_file"] != ""


def test_webp_worker_run_loop_idle_and_db_error(sandbox_env):
    worker = WebPOptimizerWorker()
    db_calls = []

    def broken_db_conn(*args, **kwargs):
        db_calls.append(1)
        raise RuntimeError("db down")

    async def _test():
        with patch("media_library.workers.webp_worker.WEBP_AUTO_OPTIMIZE", False), patch(
            "asyncio.sleep", side_effect=_fast_sleep
        ):
            done = await _drive_until(worker, lambda: worker.state["last_synced"] is not None)
            assert done
            assert worker.state["status"] == "idle"

        # A DB failure while querying candidates must not kill the loop.
        broken = WebPOptimizerWorker()
        with patch("media_library.workers.webp_worker.WEBP_AUTO_OPTIMIZE", False), patch(
            "media_library.workers.webp_worker.db_conn", side_effect=broken_db_conn
        ), patch("asyncio.sleep", side_effect=_fast_sleep):
            # The loop keeps retrying instead of dying on the failed query.
            await _drive_until(broken, lambda: len(db_calls) >= 2)
            assert len(db_calls) >= 2

    asyncio.run(_test())


# ==============================================================================
# TranscodeWorker
# ==============================================================================


def _insert_file(path, name, folder, ext, ftype="video"):
    with db_conn() as conn, conn:
        conn.execute(
            """INSERT INTO files (path, name, folder_path, type, ext, size,
                   size_formatted, mtime)
               VALUES (?, ?, ?, ?, ?, 10, '10 B', 1.0)""",
            (str(path), name, str(folder), ftype, ext),
        )


def test_transcode_worker_enqueue_edge_cases(sandbox_env):
    from media_library.core.state import state

    media = sandbox_env["media_dir"]
    state["destination_dir"] = str(media)
    worker = TranscodeWorker()

    # Missing single file is ignored.
    assert worker.enqueue_for_backup(file_path=str(media / "ghost.mkv")) == []

    # scandir failures are swallowed.
    with patch("os.scandir", side_effect=OSError("permission denied")):
        assert worker.enqueue_for_backup(folder_path=str(media)) == []

    # SQLite failures are swallowed.
    with patch("media_library.db.connection.db_conn", side_effect=RuntimeError("db down")):
        assert worker.enqueue_indexed_folder(str(media)) == []
        assert worker.enqueue_all_for_backup() == []

    mkv = media / "clip.mkv"
    mkv.write_bytes(b"\x1aE\xdf\xa3" + b"A" * 128)
    avi = media / "movie.avi"
    avi.write_bytes(b"RIFF" + b"B" * 128)
    _insert_file(mkv, "clip.mkv", media, ".mkv")
    _insert_file(avi, "movie.avi", media, ".avi")
    _insert_file(media / "sub.mp4", "sub.mp4", media, ".mp4")
    _insert_file(media / "doc.txt", "doc.txt", media, ".txt", ftype="other")
    _insert_file(media / "missing.mkv", "missing.mkv", media, ".mkv")

    # The bulk sweep skips indexed rows whose file vanished from disk.
    bulk_worker = TranscodeWorker()
    bulk = bulk_worker.enqueue_all_for_backup()
    assert [e["filename"] for e in bulk] == ["clip.mkv", "movie.avi"]

    enqueued = worker.enqueue_indexed_folder(str(media))
    # Videos enqueued in index order; .mp4 filtered by SQL, .txt by extension.
    assert [e["filename"] for e in enqueued] == ["clip.mkv", "movie.avi", "missing.mkv"]
    # A second call skips them all as existing tasks.
    assert worker.enqueue_indexed_folder(str(media)) == []
    assert worker.enqueue_all_for_backup() == []

    worker.cancel_all()
    assert worker.transcode_items_map[enqueued[0]["id"]]["status"] == "cancelled"
    worker.transcode_items_map["manual"] = {
        "id": "manual",
        "status": "queued",
        "abort_event": threading.Event(),
    }
    worker.cancel_all()
    assert worker.transcode_items_map["manual"]["status"] == "cancelled"
    assert worker.transcode_items_map["manual"]["abort_event"].is_set()

    assert worker.get_status()["paused"] is False
    worker.pause()
    assert worker.get_status()["paused"] is True
    worker.resume()
    assert worker.get_status()["paused"] is False


def test_transcode_worker_enqueue_prunes_finished_items():
    worker = TranscodeWorker()
    worker.transcode_items_map = {
        f"old_{i}": {"id": f"old_{i}", "status": "completed"} for i in range(1005)
    }
    worker.transcode_history = [{"id": "old_7"}]
    # The in-flight task and recently recorded history entries survive pruning.
    worker.active_transcode = {"id": "old_500"}

    worker.enqueue({"id": "fresh", "source_path": "/tmp/x.mkv", "status": "queued"})
    assert len(worker.transcode_items_map) < 1000
    assert "fresh" in worker.transcode_items_map
    assert "old_500" in worker.transcode_items_map
    assert "old_7" in worker.transcode_items_map


def test_transcode_worker_run_loop_records_failures_and_outer_errors():
    worker = TranscodeWorker()

    async def _test():
        worker.enqueue(
            {
                "id": "boom",
                "source_path": __file__,
                "status": "queued",
                "abort_event": threading.Event(),
            }
        )
        with patch.object(
            worker, "_run_transcode_task", side_effect=RuntimeError("transcode exploded")
        ), patch("asyncio.sleep", side_effect=_fast_sleep):
            done = await _drive_until(
                worker, lambda: worker.transcode_items_map["boom"]["status"] == "failed"
            )
            assert done

        # task_done() failures and fatal queue errors must not kill the loop.
        worker.transcode_queue.task_done = MagicMock(side_effect=ValueError("too many"))
        worker.transcode_queue.get = MagicMock(side_effect=RuntimeError("queue exploded"))
        await _drive_until(worker, lambda: False, loops=5)
        worker.transcode_queue.get = asyncio.Queue().get

    asyncio.run(_test())

    item = worker.transcode_items_map["boom"]
    assert item["error"] == "transcode exploded"
    assert item["status"] == "failed"


def test_transcode_task_target_collision_and_commit_paths(tmp_path):
    """Covers converted-name collisions, remux abort cleanup, and commit failures."""
    from media_library.workers.transcode_worker import TRANSCODE_TEMP_DIR
    from media_library.core.state import state

    state["destination_dir"] = str(tmp_path)

    async def _test():
        worker = TranscodeWorker()

        # 1. Target name collision: clip.mp4 and clip_converted_1.mp4 already exist.
        src = tmp_path / "clip.mkv"
        src.write_bytes(b"\x00" * 4096)
        (tmp_path / "clip.mp4").write_bytes(b"existing mp4")
        (tmp_path / "clip_converted_1.mp4").write_bytes(b"existing converted")
        item = {
            "id": "collide",
            "source_path": str(src),
            "status": "queued",
            "abort_event": threading.Event(),
        }
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.wait = AsyncMock(return_value=0)

        async def fake_proc(*args, **kwargs):
            # The temp name always uses the *original* target name, even when a
            # collision renames the final target to _converted_N.
            temp_p = TRANSCODE_TEMP_DIR / f"temp_{item['id']}_clip.mp4"
            temp_p.write_bytes(b"MP4" * 600)
            return mock_proc

        with patch(
            "media_library.workers.transcode_worker.probe_video_meta",
            return_value={"duration": 5.0, "video_codec": "h264", "audio_codec": "aac"},
        ), patch("asyncio.create_subprocess_exec", side_effect=fake_proc):
            await worker._run_transcode_task(item)
        assert item["status"] == "completed"
        assert item["target_path"].endswith("clip_converted_2.mp4")

        # 2. Remux abort with a leftover temp file: temp is removed.
        src2 = tmp_path / "abortme.mkv"
        src2.write_bytes(b"\x00" * 2048)
        abort_ev = threading.Event()
        abort_ev.set()
        item2 = {
            "id": "remux_abort",
            "source_path": str(src2),
            "status": "queued",
            "abort_event": abort_ev,
        }
        temp2 = TRANSCODE_TEMP_DIR / f"temp_{item2['id']}_abortme.mp4"
        temp2.write_bytes(b"leftover")
        abort_proc = AsyncMock()
        abort_proc.returncode = None
        abort_proc.terminate = MagicMock()

        async def abort_wait():
            raise asyncio.TimeoutError()

        abort_proc.wait = abort_wait
        with patch(
            "media_library.workers.transcode_worker.probe_video_meta",
            return_value={"duration": 5.0, "video_codec": "h264", "audio_codec": "aac"},
        ), patch("asyncio.create_subprocess_exec", return_value=abort_proc):
            await worker._run_transcode_task(item2)
        assert item2["status"] == "cancelled"
        assert not temp2.exists()

        # 3. Remux + re-encode + fallback all fail: temp cleaned, item failed.
        src3 = tmp_path / "hopeless.avi"
        src3.write_bytes(b"\x00" * 2048)
        item3 = {
            "id": "hopeless",
            "source_path": str(src3),
            "status": "queued",
            "abort_event": threading.Event(),
        }
        temp3 = TRANSCODE_TEMP_DIR / f"temp_{item3['id']}_hopeless.mp4"
        temp3.write_bytes(b"stale temp")
        fail_proc = AsyncMock()
        fail_proc.returncode = 1
        fail_proc.wait = AsyncMock(return_value=1)

        class _EmptyStdout:
            async def readline(self):
                return b""

        fail_proc.stdout = _EmptyStdout()
        worker.transcode_history = [{"id": f"h_{i}"} for i in range(502)]
        with patch(
            "media_library.workers.transcode_worker.probe_video_meta",
            return_value={"duration": 5.0, "video_codec": "mpeg4", "audio_codec": "mp3"},
        ), patch("asyncio.create_subprocess_exec", return_value=fail_proc):
            await worker._run_transcode_task(item3)
        assert item3["status"] == "failed"
        assert item3["method"] == "fallback_reencode"
        assert not temp3.exists()
        assert len(worker.transcode_history) <= 500

    asyncio.run(_test())


def test_transcode_task_commit_error_paths(tmp_path):
    """Covers cross-device copy verification failure and source-removal warnings."""
    from media_library.core.state import state

    state["destination_dir"] = str(tmp_path)

    async def _test():
        worker = TranscodeWorker()
        src = tmp_path / "commit.avi"
        src.write_bytes(b"\x00" * 4096)
        item = {
            "id": "commitfail",
            "source_path": str(src),
            "status": "queued",
            "abort_event": threading.Event(),
        }
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.wait = AsyncMock(return_value=0)

        async def fake_proc(*args, **kwargs):
            from media_library.workers.transcode_worker import TRANSCODE_TEMP_DIR

            temp_p = TRANSCODE_TEMP_DIR / f"temp_{item['id']}_commit.mp4"
            temp_p.write_bytes(b"MP4" * 600)
            return mock_proc

        # os.replace fails (cross-device) and the verification copy is truncated.
        with patch(
            "media_library.workers.transcode_worker.probe_video_meta",
            return_value={"duration": 5.0, "video_codec": "h264", "audio_codec": "aac"},
        ), patch("asyncio.create_subprocess_exec", side_effect=fake_proc), patch(
            "os.replace", side_effect=OSError(18, "Invalid cross-device link")
        ), patch("shutil.copy2", side_effect=OSError("copy truncated")):
            await worker._run_transcode_task(item)
        assert item["status"] == "failed"
        assert "Failed to commit transcoded file" in item["error"]

        # Successful commit where removing the source and the cached poster fail.
        src2 = tmp_path / "warn.avi"
        src2.write_bytes(b"\x00" * 4096)
        item2 = {
            "id": "commitwarn",
            "source_path": str(src2),
            "status": "queued",
            "abort_event": threading.Event(),
        }
        mock_proc2 = AsyncMock()
        mock_proc2.returncode = 0
        mock_proc2.wait = AsyncMock(return_value=0)

        async def fake_proc2(*args, **kwargs):
            from media_library.workers.transcode_worker import TRANSCODE_TEMP_DIR

            temp_p = TRANSCODE_TEMP_DIR / f"temp_{item2['id']}_warn.mp4"
            temp_p.write_bytes(b"MP4" * 600)
            return mock_proc2

        real_remove = os.remove

        def flaky_remove(path, *args, **kwargs):
            if str(path).endswith("warn.avi"):
                raise OSError("read-only mount")
            return real_remove(path, *args, **kwargs)

        with patch(
            "media_library.workers.transcode_worker.probe_video_meta",
            return_value={"duration": 5.0, "video_codec": "h264", "audio_codec": "aac"},
        ), patch("asyncio.create_subprocess_exec", side_effect=fake_proc2), patch(
            "os.remove", side_effect=flaky_remove
        ), patch(
            "media_library.workers.transcode_worker.get_thumbnail_cache_path",
            side_effect=OSError("thumb cache unavailable"),
        ):
            await worker._run_transcode_task(item2)
        assert item2["status"] == "completed"
        assert src2.exists()

    asyncio.run(_test())


def test_transcode_enqueue_pretranscode_skips_existing_and_bad_dirs(tmp_path):
    from media_library.workers.transcode_worker import (
        enqueue_pretranscode_for_source,
        transcode_worker_instance,
    )

    src = tmp_path / "incoming"
    src.mkdir()
    (src / "new_clip.mkv").write_bytes(b"\x1aE\xdf\xa3" + b"C" * 64)
    (src / "already_queued.avi").write_bytes(b"RIFF" + b"D" * 64)
    (src / "native.mp4").write_bytes(b"\x00\x00\x00 ftyp" + b"E" * 64)
    (src / ".hidden.mkv").write_bytes(b"hidden")

    transcode_worker_instance.transcode_items_map["existing"] = {
        "id": "existing",
        "filename": "already_queued.avi",
        "status": "queued",
    }
    try:
        enqueue_pretranscode_for_source(str(src))
        filenames = {
            t.get("filename")
            for t in transcode_worker_instance.transcode_items_map.values()
        }
        assert "new_clip.mkv" in filenames
        assert "native.mp4" not in filenames
        assert ".hidden.mkv" not in filenames
        assert (
            sum(
                1
                for t in transcode_worker_instance.transcode_items_map.values()
                if t.get("filename") == "already_queued.avi"
            )
            == 1
        )

        # Non-directory source dirs are ignored entirely.
        enqueue_pretranscode_for_source(str(tmp_path / "ghost_dir"))
    finally:
        transcode_worker_instance.transcode_items_map.pop("existing", None)
        for key in [
            k
            for k, v in transcode_worker_instance.transcode_items_map.items()
            if v.get("filename") == "new_clip.mkv"
        ]:
            transcode_worker_instance.transcode_items_map.pop(key, None)
