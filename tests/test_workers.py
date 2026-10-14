"""Tests for background worker managers and lifecycles."""

import asyncio
from unittest.mock import patch

from media_library.workers.backup_watcher import BackupWatcherWorker
from media_library.workers.base import BaseWorker
from media_library.workers.thumbnail_worker import ThumbnailWorker
from media_library.workers.transcode_worker import TranscodeWorker
from media_library.workers.transfer_worker import TransferWorker
from media_library.workers.webp_worker import WebPOptimizerWorker


def test_base_worker_lifecycle():
    class DummyWorker(BaseWorker):
        async def _run_loop(self):
            await asyncio.sleep(0.01)

    worker = DummyWorker("dummy")
    assert not worker.is_paused()
    worker.pause()
    assert worker.is_paused()
    assert worker.get_status()["status"] == "paused"
    worker.resume()
    assert not worker.is_paused()


def test_transfer_worker_queue_and_cancel():
    worker = TransferWorker()
    item = {
        "id": "t1",
        "filename": "video.mp4",
        "source_path": "/fake/video.mp4",
        "target_path": "/fake/dest.mp4",
        "total_bytes": 1000,
        "bytes_copied": 0,
        "percent": 0.0,
        "status": "queued",
        "abort_event": None,
    }
    worker.enqueue(item)
    assert worker.get_status()["queue_length"] == 1
    cancelled = worker.cancel("t1")
    assert cancelled is True
    assert item["status"] == "cancelled"


def test_transcode_worker_pause_and_cancel():
    worker = TranscodeWorker()
    item = {
        "id": "tc1",
        "filename": "clip.mkv",
        "source_path": "/fake/clip.mkv",
        "status": "queued",
        "abort_event": None,
    }
    worker.enqueue(item)
    assert worker.get_status()["queue_length"] == 1
    worker.pause()
    assert worker.get_status()["paused"] is True
    worker.resume()
    assert worker.get_status()["paused"] is False
    worker.cancel("tc1")
    assert item["status"] == "cancelled"


def test_thumbnail_worker_status():
    worker = ThumbnailWorker()
    status = worker.get_status()
    assert "status" in status
    assert "total_files" in status
    assert "percent" in status
    # The unified /api/system/status payload is this dict, so the card needs the
    # pause flag here (the dedicated status route used to add it separately).
    assert status["is_paused"] is False


def test_thumbnail_worker_pause_flag_round_trips():
    worker = ThumbnailWorker()
    worker.pause()
    assert worker.get_status()["is_paused"] is True
    assert worker.get_status()["status"] == "paused"
    worker.resume()
    assert worker.get_status()["is_paused"] is False


def test_webp_optimizer_worker_status():
    worker = WebPOptimizerWorker()
    status = worker.get_status()
    assert "status" in status
    assert "total_images" in status


def test_webp_enqueue_folder_zip_single_item(sandbox_env):
    """enqueue_folder on a ZIP must queue a single archive-level item, not one per member."""
    import io
    import zipfile

    from PIL import Image

    from media_library.services.zip import index_zip_archive

    media_dir = sandbox_env["media_dir"]
    zip_path = media_dir / "album.zip"

    img1 = io.BytesIO()
    Image.new("RGB", (8, 8), color="red").save(img1, format="JPEG")
    img2 = io.BytesIO()
    Image.new("RGB", (8, 8), color="blue").save(img2, format="JPEG")
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("one.jpg", img1.getvalue())
        zf.writestr("two.jpg", img2.getvalue())

    index_zip_archive(str(zip_path))

    worker = WebPOptimizerWorker()
    count = worker.enqueue_folder(str(zip_path))
    assert count == 2  # reports pending member count

    assert worker.priority_queue.qsize() == 1
    item = worker.priority_queue.get_nowait()
    assert item["is_zip"] == 1
    assert item["path"] == str(zip_path)


def test_webp_auto_items_only_loose_images(sandbox_env, monkeypatch):
    """The background sweep must never pick up images inside ZIP archives."""
    import io
    import zipfile

    from PIL import Image

    from media_library.db.connection import get_db
    from media_library.db.indexer import index_folder
    from media_library.services.zip import index_zip_archive

    media_dir = sandbox_env["media_dir"]

    # Loose (non-ZIP) image that should be auto-converted.
    loose = media_dir / "loose"
    loose.mkdir()
    (loose / "photo.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"X" * 512)
    index_folder(str(loose), base_dir=str(media_dir))

    # ZIP containing a JPG that must NOT be auto-picked.
    zip_path = media_dir / "album.zip"
    img = io.BytesIO()
    Image.new("RGB", (8, 8), color="red").save(img, format="JPEG")
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("one.jpg", img.getvalue())
    index_zip_archive(str(zip_path), parent_path=str(media_dir))

    worker = WebPOptimizerWorker()
    conn = get_db()
    try:
        auto = worker._next_auto_items(conn)
    finally:
        conn.close()

    paths = {r["path"] for r in auto}
    assert str(loose / "photo.jpg") in paths
    assert not any("album.zip" in p for p in paths), (
        "zip members must be excluded from the auto sweep"
    )

    # AUTO=false must disable the sweep entirely, but queued items still work.
    monkeypatch.setattr("media_library.workers.webp_worker.WEBP_AUTO_OPTIMIZE", False)
    conn = get_db()
    try:
        assert worker._next_auto_items(conn) == []
    finally:
        conn.close()
    assert worker.priority_queue.qsize() == 0
    count = worker.enqueue_folder(str(zip_path))
    assert count == 1
    assert worker.priority_queue.qsize() == 1


def test_webp_enqueue_folder_zip_throttle(sandbox_env):
    """Repeated enqueues of the same ZIP within the throttle window queue once."""
    import io
    import zipfile

    from PIL import Image

    from media_library.services.zip import index_zip_archive

    media_dir = sandbox_env["media_dir"]
    zip_path = media_dir / "throttle.zip"
    img = io.BytesIO()
    Image.new("RGB", (8, 8), color="blue").save(img, format="JPEG")
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("a.jpg", img.getvalue())
    index_zip_archive(str(zip_path))

    worker = WebPOptimizerWorker()
    first = worker.enqueue_folder(str(zip_path))
    second = worker.enqueue_folder(str(zip_path))
    assert first == 1
    assert second == 0
    assert worker.priority_queue.qsize() == 1


def test_thumbnail_worker_skips_deferred_zip_images(sandbox_env):
    """Thumbnail worker must skip non-WebP images inside ZIPs (deferred until WebP conversion)."""
    import io
    import zipfile

    from PIL import Image

    from media_library.services.zip import index_zip_archive

    media_dir = sandbox_env["media_dir"]
    zip_path = media_dir / "mixed.zip"

    jpg = io.BytesIO()
    Image.new("RGB", (8, 8), color="red").save(jpg, format="JPEG")
    webp = io.BytesIO()
    Image.new("RGB", (8, 8), color="blue").save(webp, format="WEBP")

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("photo.jpg", jpg.getvalue())  # deferred (non-WebP image)
        zf.writestr("photo.webp", webp.getvalue())  # already WebP -> thumbnail
        zf.writestr("clip.mp4", b"\x00\x00\x00 ftyp" + b"X" * 100)  # video -> thumbnail

    index_zip_archive(str(zip_path))

    worker = ThumbnailWorker()
    worker.enqueue_folder(str(zip_path))

    queued_paths = []
    while not worker.priority_queue.empty():
        queued_paths.append(worker.priority_queue.get_nowait()["path"])

    assert str(zip_path / "photo.jpg") not in queued_paths
    assert str(zip_path / "photo.webp") in queued_paths
    assert str(zip_path / "clip.mp4") in queued_paths


def test_backup_watcher_worker_status():
    worker = BackupWatcherWorker()
    status = worker.get_status()
    assert "status" in status
    assert "progress_percent" in status
    assert "current_step" in status


def test_thumbnail_worker_run_loop(sandbox_env):
    from media_library.db.connection import db_conn
    from PIL import Image

    media = sandbox_env["media_dir"]
    sub = media / "thumb_loop_test"
    sub.mkdir()
    img = sub / "pic.jpg"
    Image.new("RGB", (64, 64), color="blue").save(img, format="JPEG")

    with db_conn() as conn, conn:
        conn.execute(
            "INSERT INTO files (path, name, folder_path, type, ext, size, size_formatted, mtime, has_thumb) VALUES (?, ?, ?, 'image', '.jpg', 100, '100 B', 100.0, 0)",
            (str(img), "pic.jpg", str(sub)),
        )

    worker = ThumbnailWorker()
    worker.enqueue_folder(str(sub))
    assert worker.priority_queue.qsize() == 1

    orig_sleep = asyncio.sleep

    async def fast_sleep(s):
        if s > 0.05:
            await orig_sleep(0.01)
        else:
            await orig_sleep(s)

    async def _run():
        with patch("asyncio.sleep", side_effect=fast_sleep):
            task = asyncio.create_task(worker._run_loop())
            for _ in range(50):
                await orig_sleep(0.05)
                if worker.state["cached_count"] >= 1:
                    break
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    asyncio.run(_run())
    assert worker.state["cached_count"] >= 1


def test_webp_worker_run_loop(sandbox_env):
    from media_library.db.connection import db_conn
    from PIL import Image

    media = sandbox_env["media_dir"]
    sub = media / "webp_loop_test"
    sub.mkdir()
    img = sub / "photo_to_conv.jpg"
    Image.new("RGB", (64, 64), color="red").save(img, format="JPEG")

    with db_conn() as conn, conn:
        conn.execute(
            "INSERT INTO files (path, name, folder_path, type, ext, size, size_formatted, mtime, webp_optimized) VALUES (?, ?, ?, 'image', '.jpg', 100, '100 B', 100.0, 0)",
            (str(img), "photo_to_conv.jpg", str(sub)),
        )

    worker = WebPOptimizerWorker()
    worker.enqueue_folder(str(sub))

    orig_sleep = asyncio.sleep

    async def fast_sleep(s):
        if s > 0.05:
            await orig_sleep(0.01)
        else:
            await orig_sleep(s)

    async def _run():
        with patch("asyncio.sleep", side_effect=fast_sleep):
            task = asyncio.create_task(worker._run_loop())
            for _ in range(50):
                await orig_sleep(0.05)
                if worker.state["converted_count"] >= 1 or not worker.priority_queue.empty():
                    break
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    asyncio.run(_run())

