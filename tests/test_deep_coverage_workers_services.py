"""Comprehensive deep coverage tests for workers, upload service, indexer deep clean, and media services."""

import asyncio
import io
import json
import os
import shutil
import time
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from PIL import Image

from media_library.app_factory import create_app
from media_library.core.config import UPLOAD_STAGING_DIR, ZIP_STAGING_DIR
from media_library.db.connection import db_conn
from media_library.db.indexer import (
    _index_zip_archive,
    _scan_folder_entries,
    deep_clean_database,
)
from media_library.services.ffmpeg_service import (
    convert_image_file_to_webp,
    convert_image_to_webp_bytes,
    probe_video_meta,
)
from media_library.services.upload_service import (
    _is_valid_upload_id,
    cancel_upload,
    finalize_upload,
    get_unique_filename,
    handle_upload_chunk,
)
from media_library.services.zip.compress import (
    _execute_sync_back_impl,
    compress_folder_to_zip,
)
from media_library.services.zip.core import get_zip_cache_dir
from media_library.workers.backup_watcher import (
    BackupWatcherWorker,
    library_backup_lock,
    run_library_backup_async,
    run_library_restore_async,
)
from media_library.workers.base import BaseWorker
from media_library.workers.transfer_worker import TransferWorker


# ==============================================================================
# 1. BaseWorker & TransferWorker Tests
# ==============================================================================


def test_base_worker_methods():
    worker = BaseWorker("custom")

    # _run_loop not implemented
    with pytest.raises(NotImplementedError):
        asyncio.run(worker._run_loop())

    async def _test_lifecycle():
        await worker.start()
        assert worker._task is not None
        # Starting again when already running is a no-op
        await worker.start()
        await worker.stop()
        assert worker._task is None

        # Test enqueue_nowait threadsafe and fallback
        q = asyncio.Queue()
        worker._loop = asyncio.get_running_loop()
        worker.enqueue_nowait(q, "item1")
        assert await q.get() == "item1"

        # Simulate RuntimeError in call_soon_threadsafe
        with patch.object(
            worker._loop, "call_soon_threadsafe", side_effect=RuntimeError("Loop closing")
        ):
            worker.enqueue_nowait(q, "item2")
            assert await q.get() == "item2"

    asyncio.run(_test_lifecycle())


def test_transfer_worker_run_loop_and_cleanup(tmp_path):
    worker = TransferWorker()

    src1 = tmp_path / "src1.mp4"
    src1.write_bytes(b"content1")
    dest1 = tmp_path / "dest1.mp4"

    item_done = {
        "id": "item1",
        "filename": "src1.mp4",
        "source_path": str(src1),
        "target_path": str(dest1),
        "status": "queued",
        "abort_event": None,
    }
    item_cancelled = {
        "id": "item2",
        "filename": "src2.mp4",
        "source_path": str(tmp_path / "src2.mp4"),
        "target_path": str(tmp_path / "dest2.mp4"),
        "status": "cancelled",
        "abort_event": None,
    }
    item_err = {
        "id": "item3",
        "filename": "src3.mp4",
        "source_path": str(tmp_path / "src3.mp4"),
        "target_path": str(tmp_path / "dest3.mp4"),
        "status": "queued",
        "abort_event": None,
    }

    worker.enqueue(item_done)
    worker.enqueue(item_cancelled)
    worker.enqueue(item_err)

    # Pre-populate history to test history truncation > 500
    worker.transfers_history = [{"id": f"h_{i}"} for i in range(502)]

    async def _run():
        def fake_transfer(item, tc_map):
            if item["id"] == "item3":
                raise RuntimeError("Transfer disk full")
            item["status"] = "completed"

        with patch("media_library.workers.transfer_worker.do_transfer", side_effect=fake_transfer):
            task = asyncio.create_task(worker._run_loop())
            for _ in range(50):
                await asyncio.sleep(0.01)
                if worker.transfer_queue.empty() and worker.active_transfer is None:
                    break
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    asyncio.run(_run())

    assert len(worker.transfers_history) <= 500
    assert any(h.get("id") == "item1" and h.get("status") == "completed" for h in worker.transfers_history)
    assert any(h.get("id") == "item3" and h.get("status") == "failed" for h in worker.transfers_history)


def test_transfer_worker_map_pruning_and_cancel_event():
    worker = TransferWorker()
    import threading

    abort = threading.Event()
    worker.transfer_items_map = {
        f"old_{i}": {"id": f"old_{i}", "status": "completed"} for i in range(1005)
    }
    worker.active_transfer = {"id": "active_1"}
    worker.transfers_history = [{"id": "hist_1"}]

    new_item = {
        "id": "new_1",
        "abort_event": abort,
        "status": "queued",
    }
    worker.enqueue(new_item)
    # Map was pruned
    assert len(worker.transfer_items_map) < 1000

    # Cancel with abort event
    assert worker.cancel("new_1") is True
    assert abort.is_set()
    assert worker.cancel("non_existent") is False


# ==============================================================================
# 2. BackupWatcherWorker Tests
# ==============================================================================


def test_backup_watcher_async_operations(monkeypatch):
    from media_library.services.backup_service import library_backup_state

    # 1. Test locked lock early returns
    async def _test_locked():
        async with library_backup_lock:
            await run_library_backup_async()
            await run_library_restore_async()

    asyncio.run(_test_locked())

    # 2. Test run_library_backup_async error return
    async def _test_backup_failure():
        with patch(
            "media_library.workers.backup_watcher._create_library_backup_sync",
            return_value={"success": False, "error": "Disk failure"},
        ):
            await run_library_backup_async()
            assert library_backup_state["status"] == "error"
            assert "Disk failure" in library_backup_state["error_message"]

        # Test unhandled exception in backup
        with patch(
            "media_library.workers.backup_watcher._create_library_backup_sync",
            side_effect=RuntimeError("Fatal backup boom"),
        ):
            await run_library_backup_async()
            assert library_backup_state["status"] == "error"
            assert "Fatal backup boom" in library_backup_state["error_message"]

    asyncio.run(_test_backup_failure())

    # 3. Test run_library_restore_async error return and exception
    async def _test_restore_failure():
        with patch(
            "media_library.workers.backup_watcher._restore_library_backup_sync",
            return_value={"success": False, "error": "Snapshot missing"},
        ):
            await run_library_restore_async()
            assert library_backup_state["status"] == "error"
            assert "Snapshot missing" in library_backup_state["error_message"]

        with patch(
            "media_library.workers.backup_watcher._restore_library_backup_sync",
            side_effect=RuntimeError("Restore corrupted"),
        ):
            await run_library_restore_async()
            assert library_backup_state["status"] == "error"
            assert "Restore corrupted" in library_backup_state["error_message"]

    asyncio.run(_test_restore_failure())

    # 4. Test get_status
    worker = BackupWatcherWorker()
    assert worker.get_status() == library_backup_state


def test_backup_watcher_run_loop(sandbox_env, monkeypatch):
    from media_library.services.backup_service import library_backup_state
    from media_library.db.indexer import indexer_state
    from media_library.workers.thumbnail_worker import thumbnail_worker_instance
    from media_library.workers.duplicate_hash_worker import (
        duplicate_hash_worker_instance,
        thumb_hash_worker_instance,
    )
    from media_library.workers.webp_worker import webp_worker_instance
    from media_library.workers.transfer_worker import transfer_worker_instance
    from media_library.workers.transcode_worker import transcode_worker_instance
    from media_library.workers.library_ops_worker import library_ops_worker_instance
    from media_library.workers.metadata_worker import metadata_worker_instance
    from media_library.services.zip import zip_tasks_state

    indexer_state["status"] = "idle"
    thumbnail_worker_instance.state["status"] = "idle"
    duplicate_hash_worker_instance.state["status"] = "idle"
    thumb_hash_worker_instance.state["status"] = "idle"
    webp_worker_instance.state["status"] = "idle"
    metadata_worker_instance.state["status"] = "idle"
    library_backup_state["status"] = "idle"
    transfer_worker_instance.active_transfer = None
    transcode_worker_instance.active_transcode = None
    library_ops_worker_instance.active_op = None
    zip_tasks_state["active_tasks"] = {}

    while not transfer_worker_instance.transfer_queue.empty():
        try:
            transfer_worker_instance.transfer_queue.get_nowait()
        except Exception:
            break
    while not transcode_worker_instance.transcode_queue.empty():
        try:
            transcode_worker_instance.transcode_queue.get_nowait()
        except Exception:
            break
    while not library_ops_worker_instance.ops_queue.empty():
        try:
            library_ops_worker_instance.ops_queue.get_nowait()
        except Exception:
            break

    worker = BackupWatcherWorker()

    # Insert a file in database to ensure cur_files > 0
    with db_conn() as conn, conn:
        conn.execute(
            "INSERT INTO files (path, name, folder_path, type, ext, size, size_formatted, mtime, has_thumb) VALUES (?, ?, ?, 'video', '.mp4', 500, '500 B', 200.0, 1)",
            ("/fake/vid.mp4", "vid.mp4", "/fake"),
        )

    orig_sleep = asyncio.sleep

    async def fast_sleep(s):
        if s > 0.05:
            await orig_sleep(0.01)
        else:
            await orig_sleep(s)

    triggered_backup = False
    fake_clock = [1000.0]

    def fake_time():
        val = fake_clock[0]
        fake_clock[0] += 50.0
        return val

    async def fake_backup():
        nonlocal triggered_backup
        triggered_backup = True

    async def _test_loop():
        with patch("asyncio.sleep", side_effect=fast_sleep), patch(
            "time.time", side_effect=fake_time
        ), patch(
            "media_library.workers.backup_watcher.run_library_backup_async", side_effect=fake_backup
        ), patch(
            "media_library.workers.backup_watcher.get_existing_backup_meta",
            return_value={"file_count": 0, "thumb_count": 0, "max_mtime": 10.0, "total_size": 10, "timestamp": 10.0},
        ), patch(
            "media_library.workers.backup_watcher.LIBRARY_BACKUP_AUTO_ENABLED", True
        ), patch(
            "media_library.workers.backup_watcher.LIBRARY_BACKUP_AUTO_INTERVAL_HOURS", 0.0
        ):
            task = asyncio.create_task(worker._run_loop())
            for _ in range(60):
                await orig_sleep(0.01)
                if triggered_backup:
                    break
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    asyncio.run(_test_loop())
    assert triggered_backup is True


# ==============================================================================
# 3. Upload Service Tests
# ==============================================================================


def test_upload_service_validations_and_chunks(sandbox_env, tmp_path):
    import media_library.services.upload_service as upload_svc

    # ID validation
    assert _is_valid_upload_id("valid-upload_123") is True
    assert _is_valid_upload_id("../invalid") is False
    assert _is_valid_upload_id("") is False
    assert _is_valid_upload_id("a" * 65) is False

    # Chunk handling
    async def _test_chunks():
        upload_id = "test_chunk_id"
        staging_part = upload_svc.UPLOAD_STAGING_DIR / f"{upload_id}.part"
        staging_part.unlink(missing_ok=True)

        # Invalid upload_id
        with pytest.raises(HTTPException) as exc:
            await handle_upload_chunk("invalid/id", 0, 2, b"hello")
        assert exc.value.status_code == 400

        # Chunk 0 (wb)
        res0 = await handle_upload_chunk(upload_id, 0, 2, b"part1_")
        assert res0["status"] == "ok"
        assert staging_part.read_bytes() == b"part1_"

        # Chunk 1 (ab)
        res1 = await handle_upload_chunk(upload_id, 1, 2, b"part2")
        assert res1["status"] == "ok"
        assert staging_part.read_bytes() == b"part1_part2"

        # Size limit exceeded check
        with patch("media_library.services.upload_service.MAX_UPLOAD_FILE_SIZE_GB", 0.000001):
            with pytest.raises(HTTPException) as exc2:
                await handle_upload_chunk(upload_id, 2, 3, b"x" * 2000)
            assert exc2.value.status_code == 413

        staging_part.unlink(missing_ok=True)

    asyncio.run(_test_chunks())


def test_upload_unique_filename(tmp_path):
    f = tmp_path / "photo.jpg"
    assert get_unique_filename(tmp_path, "photo.jpg") == f

    f.write_bytes(b"dummy")
    f1 = get_unique_filename(tmp_path, "photo.jpg")
    assert f1.name == "photo (1).jpg"

    f1.write_bytes(b"dummy1")
    f2 = get_unique_filename(tmp_path, "photo.jpg")
    assert f2.name == "photo (2).jpg"


def test_upload_finalize_and_cancel(sandbox_env, tmp_path):
    import media_library.services.upload_service as upload_svc
    from media_library.core.state import state

    state.set_destination_dir(str(sandbox_env["media_dir"]))
    dest_dir = sandbox_env["media_dir"] / "uploads_test"
    dest_dir.mkdir(parents=True, exist_ok=True)

    async def _test():
        upload_id = "fin_upload_1"
        staging_part = upload_svc.UPLOAD_STAGING_DIR / f"{upload_id}.part"
        staging_part.write_bytes(b"image data content")

        # Invalid ID
        with pytest.raises(HTTPException) as exc:
            await finalize_upload("../bad_id", "file.jpg", "file.jpg", str(dest_dir), "auto-rename")
        assert exc.value.status_code == 400

        # Missing staging file
        with pytest.raises(HTTPException) as exc:
            await finalize_upload("missing_id", "file.jpg", "file.jpg", str(dest_dir), "auto-rename")
        assert exc.value.status_code == 404

        # Non-directory destination
        dest_file = dest_dir / "plain.txt"
        dest_file.write_text("not a dir")
        staging_part.write_bytes(b"image data content")
        with pytest.raises(HTTPException) as exc:
            await finalize_upload(upload_id, "file.jpg", "file.jpg", str(dest_file), "auto-rename")
        assert exc.value.status_code == 400

        # Disallowed extension
        staging_part.write_bytes(b"image data content")
        with pytest.raises(HTTPException) as exc:
            await finalize_upload(upload_id, "script.sh", "script.sh", str(dest_dir), "auto-rename")
        assert exc.value.status_code == 400

        # Path traversal in relative path
        staging_part.write_bytes(b"image data content")
        with pytest.raises(HTTPException) as exc:
            await finalize_upload(upload_id, "file.jpg", "../traverse.jpg", str(dest_dir), "auto-rename")
        assert exc.value.status_code == 400

        # Successful finalize with mtime
        staging_part.write_bytes(b"image data content")
        target_mtime = 1600000000000
        res = await finalize_upload(
            upload_id, "photo.jpg", "sub/photo.jpg", str(dest_dir), "auto-rename", mtime=target_mtime
        )
        assert res["status"] == "ok"
        final_p = Path(res["path"])
        assert final_p.exists()
        assert final_p.read_bytes() == b"image data content"

        # Collision policies: "skip", "overwrite", "auto-rename"
        staging_part.write_bytes(b"new collision data")
        res_skip = await finalize_upload(
            upload_id, "photo.jpg", "sub/photo.jpg", str(dest_dir), "skip"
        )
        assert res_skip["status"] == "skipped"

        staging_part.write_bytes(b"overwrite data")
        res_ow = await finalize_upload(
            upload_id, "photo.jpg", "sub/photo.jpg", str(dest_dir), "overwrite"
        )
        assert res_ow["status"] == "ok"
        assert final_p.read_bytes() == b"overwrite data"

        staging_part.write_bytes(b"auto rename data")
        res_ar = await finalize_upload(
            upload_id, "photo.jpg", "sub/photo.jpg", str(dest_dir), "auto-rename"
        )
        assert res_ar["status"] == "ok"
        assert "(1)" in res_ar["path"]

        # Cancel upload
        cancel_id = "to_cancel_123"
        part_cancel = upload_svc.UPLOAD_STAGING_DIR / f"{cancel_id}.part"
        part_cancel.write_bytes(b"cancel me")
        res_canc = await cancel_upload(cancel_id)
        assert res_canc["status"] == "cancelled"
        assert not part_cancel.exists()

        # Cancel missing
        assert (await cancel_upload("non_existent_part"))["status"] == "not_found"

        # Cancel invalid
        with pytest.raises(HTTPException):
            await cancel_upload("../../bad")

    asyncio.run(_test())


# ==============================================================================
# 4. Deep Clean Database & Indexer Scan Tests
# ==============================================================================


def test_indexer_deep_clean(sandbox_env):
    media_dir = sandbox_env["media_dir"]
    f_keep = media_dir / "keep.mp4"
    f_keep.write_bytes(b"keep this file")
    f_gone = media_dir / "deleted_file.mp4"
    # Do not write f_gone to disk

    with db_conn() as conn, conn:
        conn.execute(
            "INSERT INTO files (path, name, folder_path, type, ext, size, size_formatted, mtime) VALUES (?, ?, ?, 'video', '.mp4', 10, '10 B', 1.0)",
            (str(f_keep), "keep.mp4", str(media_dir)),
        )
        conn.execute(
            "INSERT INTO files (path, name, folder_path, type, ext, size, size_formatted, mtime) VALUES (?, ?, ?, 'video', '.mp4', 10, '10 B', 1.0)",
            (str(f_gone), "deleted_file.mp4", str(media_dir)),
        )

    deep_clean_database()

    with db_conn() as conn:
        paths = [r["path"] for r in conn.execute("SELECT path FROM files").fetchall()]
        assert str(f_keep) in paths
        assert str(f_gone) not in paths


def test_indexer_zip_and_nested_entries(sandbox_env, tmp_path):
    sub = tmp_path / "nested_dir"
    sub.mkdir()
    z_file = sub / "test.zip"
    with zipfile.ZipFile(z_file, "w") as zf:
        zf.writestr("inside.jpg", b"dummy")

    # Test _scan_folder_entries
    subfolders, files = _scan_folder_entries(str(sub))
    assert any(sf["path"] == str(z_file) for sf in subfolders)

    # Test _index_zip_archive
    res = _index_zip_archive(str(z_file), str(tmp_path))
    assert res["file_count"] == 1

    # Corrupt zip handling
    bad_zip = tmp_path / "corrupt.zip"
    bad_zip.write_bytes(b"not a zip file at all")
    res_bad = _index_zip_archive(str(bad_zip), str(tmp_path))
    assert res_bad["file_count"] == 0


# ==============================================================================
# 5. FFmpeg & WebP Services Deep Coverage
# ==============================================================================


def test_probe_video_meta_parsing():
    mock_payload = {
        "format": {"duration": "42.5"},
        "streams": [
            {"codec_type": "video", "codec_name": "h264"},
            {"codec_type": "audio", "codec_name": "aac"},
        ],
    }

    async def _test():
        proc_mock = MagicMock()
        proc_mock.communicate = MagicMock(
            return_value=asyncio.sleep(0.001, result=(json.dumps(mock_payload).encode(), b""))
        )
        proc_mock.returncode = 0

        with patch("asyncio.create_subprocess_exec", return_value=proc_mock):
            meta = await probe_video_meta("/fake/video.mp4")
            assert meta["duration"] == 42.5
            assert meta["video_codec"] == "h264"
            assert meta["audio_codec"] == "aac"

        # Test failure/exception path
        with patch("asyncio.create_subprocess_exec", side_effect=OSError("ffprobe missing")):
            meta_err = await probe_video_meta("/fake/video.mp4")
            assert meta_err["duration"] == 0.0

    asyncio.run(_test())


def test_webp_conversion_comprehensive(sandbox_env, tmp_path):
    # 1. Missing file
    res_missing = convert_image_file_to_webp(str(tmp_path / "missing.jpg"))
    assert res_missing["status"] == "error"

    # 2. Already webp
    f_webp = tmp_path / "image.webp"
    f_webp.write_bytes(b"webp data")
    assert convert_image_file_to_webp(str(f_webp))["status"] == "already_webp"

    # 3. Unsupported extension
    f_txt = tmp_path / "doc.txt"
    f_txt.write_text("hello")
    assert convert_image_file_to_webp(str(f_txt))["status"] == "unsupported"

    # 4. Successful PNG conversion
    f_png = tmp_path / "graphic.png"
    Image.new("RGBA", (32, 32), color=(255, 0, 0, 128)).save(f_png, "PNG")
    res_png = convert_image_file_to_webp(str(f_png))
    assert res_png["status"] == "converted"
    assert Path(res_png["new_path"]).exists()

    # 5. Skip when larger
    f_small_jpg = tmp_path / "tiny.jpg"
    Image.new("RGB", (8, 8), color="white").save(f_small_jpg, "JPEG")
    orig_stat = Path.stat

    def fake_stat(self, *args, **kwargs):
        if str(self).endswith(".webp"):
            return MagicMock(st_size=999999)
        return orig_stat(self, *args, **kwargs)

    with patch("media_library.services.ffmpeg_service.WEBP_SKIP_LARGER", True), patch.object(
        Path, "stat", fake_stat
    ):
        res_skip = convert_image_file_to_webp(str(f_small_jpg))
        assert res_skip["status"] == "skipped"

    # 6. CMYK image conversion
    f_cmyk = tmp_path / "print.jpg"
    Image.new("CMYK", (32, 32), color=(0, 255, 0, 0)).save(f_cmyk, "JPEG")
    res_cmyk = convert_image_file_to_webp(str(f_cmyk))
    assert res_cmyk["status"] == "converted"

    # 7. convert_image_to_webp_bytes with raw bytes
    bio = io.BytesIO()
    Image.new("RGB", (20, 20), color="blue").save(bio, "JPEG")
    raw_webp = convert_image_to_webp_bytes(bio.getvalue())
    assert len(raw_webp) > 0


# ==============================================================================
# 6. ZIP Compress Sync-Back & App Factory Lifespan
# ==============================================================================


def test_zip_compress_sync_back(sandbox_env, tmp_path):
    z_path = tmp_path / "sync_test.zip"
    with zipfile.ZipFile(z_path, "w") as zf:
        zf.writestr("test.txt", "initial text")

    cache_dir = get_zip_cache_dir(str(z_path))
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "test.txt").write_text("updated text content")
    (cache_dir / ".extracted_done").touch()

    # Run sync back
    _execute_sync_back_impl(str(z_path))

    with zipfile.ZipFile(z_path, "r") as zf:
        assert zf.read("test.txt").decode() == "updated text content"

    # Test error handling when cache directory causes failure
    with patch("zipfile.ZipFile", side_effect=RuntimeError("Corrupt zip write")):
        _execute_sync_back_impl(str(z_path))
        from media_library.services.zip import zip_tasks_state
        task = zip_tasks_state["active_tasks"].get(str(z_path))
        assert task["status"] == "failed"


def test_app_factory_lifespan(sandbox_env):
    async def _test():
        with patch("media_library.workers.transfer_worker.transfer_worker_instance.start"), patch(
            "media_library.workers.transcode_worker.transcode_worker_instance.start"
        ), patch("media_library.workers.thumbnail_worker.thumbnail_worker_instance.start"), patch(
            "media_library.workers.metadata_worker.metadata_worker_instance.start"
        ), patch(
            "media_library.workers.duplicate_hash_worker.duplicate_hash_worker_instance.start"
        ), patch(
            "media_library.workers.duplicate_hash_worker.thumb_hash_worker_instance.start"
        ), patch("media_library.workers.webp_worker.webp_worker_instance.start"), patch(
            "media_library.workers.backup_watcher.backup_watcher_instance.start"
        ), patch(
            "media_library.workers.library_ops_worker.library_ops_worker_instance.start"
        ), patch(
            "media_library.app_factory.run_background_crawler", return_value=asyncio.sleep(0.01)
        ), patch(
            "media_library.app_factory.check_and_auto_restore_library_backup", return_value=True
        ), patch(
            "media_library.app_factory._create_library_backup_sync", return_value={"success": True}
        ):
            app = create_app(run_startup_workers=True)
            async with app.router.lifespan_context(app):
                pass

    asyncio.run(_test())
