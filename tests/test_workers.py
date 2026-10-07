"""Tests for background worker managers and lifecycles."""

import pytest
import asyncio
from media_library.workers.base import BaseWorker
from media_library.workers.transfer_worker import TransferWorker
from media_library.workers.transcode_worker import TranscodeWorker
from media_library.workers.thumbnail_worker import ThumbnailWorker
from media_library.workers.webp_worker import WebPOptimizerWorker
from media_library.workers.backup_watcher import BackupWatcherWorker

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

def test_webp_optimizer_worker_status():
    worker = WebPOptimizerWorker()
    status = worker.get_status()
    assert "status" in status
    assert "total_images" in status

def test_backup_watcher_worker_status():
    worker = BackupWatcherWorker()
    status = worker.get_status()
    assert "status" in status
    assert "progress_percent" in status
    assert "current_step" in status

