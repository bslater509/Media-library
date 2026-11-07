"""The owned background runtime.

``OWN_WORKERS=true`` (the default) starts the background sweeps: periodic
crawler, metadata prober, poster sweep, loose-image WebP conversion, content and
visual duplicate hashing, and automatic snapshots.
"""

from __future__ import annotations

import gc
import logging
import threading

from media_library import backups, dedupe, growth, webp
from media_library.config import get_settings
from media_library.indexer import CrawlerThread
from media_library.probe import MetadataWorker
from media_library.thumbnail_worker import ThumbnailSweep

logger = logging.getLogger(__name__)

_crawler: CrawlerThread | None = None
_metadata: MetadataWorker | None = None
_thumbnails: ThumbnailSweep | None = None
_webp: _BatchSweep | None = None
_content_hashes: _BatchSweep | None = None
_visual_hashes: _BatchSweep | None = None
_backup_stop = threading.Event()
_backup_thread: threading.Thread | None = None
_growth = None
_started = False


class _BatchSweep:
    """Repeatedly runs one batch step until it stops finding work."""

    def __init__(self, name: str, step, idle_wait: float = 120.0) -> None:
        self.name = name
        self.step = step
        self.idle_wait = idle_wait
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name=self.name, daemon=True)
        self._thread.start()

    def _worked(self, result: dict | None) -> bool:
        if not result:
            return False
        return bool(result.get("converted") or result.get("hashed"))

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                result = self.step()
            except Exception as exc:
                logger.warning("[api] %s batch failed: %s", self.name, exc)
                result = None
            gc.collect()
            delay = 1.0 if self._worked(result) else self.idle_wait
            if self._stop.wait(delay):
                break

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)


class _BackupWatcher:
    def __init__(self, interval_hours: float, first_delay: float = 300.0) -> None:
        self.interval = max(0.1, interval_hours) * 3600
        self.first_delay = first_delay

    def start(self) -> None:
        global _backup_thread
        _backup_stop.clear()
        _backup_thread = threading.Thread(
            target=self._loop, name="backups", daemon=True
        )
        _backup_thread.start()

    def _loop(self) -> None:
        if _backup_stop.wait(self.first_delay):
            return
        while not _backup_stop.is_set():
            try:
                backups.create_snapshot()
            except Exception as exc:
                logger.warning("[api] Automatic snapshot failed: %s", exc)
            if _backup_stop.wait(self.interval):
                break

    def stop(self) -> None:
        _backup_stop.set()
        if _backup_thread:
            _backup_thread.join(timeout=5)


_backups = None


def start_runtime() -> bool:
    """Start the owned workers, or do nothing when they are disabled."""
    global _crawler, _metadata, _thumbnails, _backups, _started
    global _webp, _content_hashes, _visual_hashes, _growth
    settings = get_settings()
    if not settings.own_workers:
        logger.info("[api] Owned workers are disabled (OWN_WORKERS=false)")
        return False
    if _started:
        return True
    _crawler = CrawlerThread(interval_seconds=settings.crawl_interval_minutes * 60)
    _metadata = MetadataWorker()
    _thumbnails = ThumbnailSweep()
    _webp = _BatchSweep(
        "webp-sweep",
        lambda: webp.optimize_batch(20) if webp.pending_count() else None,
    )
    _content_hashes = _BatchSweep("content-hashes", lambda: dedupe.hash_content(50))
    _visual_hashes = _BatchSweep("visual-hashes", lambda: dedupe.hash_visual(50))
    _backups = _BackupWatcher(settings.backup_interval_hours)
    _growth = growth.GrowthWorker()
    _crawler.start()
    _metadata.start()
    _thumbnails.start()
    _webp.start()
    _content_hashes.start()
    _visual_hashes.start()
    _backups.start()
    growth.register_worker(_growth)
    _growth.start()
    _started = True
    logger.info("[api] Owned worker runtime started")
    return True


def stop_runtime() -> None:
    global _started
    for worker in (
        _crawler,
        _metadata,
        _thumbnails,
        _webp,
        _content_hashes,
        _visual_hashes,
        _backups,
        _growth,
    ):
        if worker is not None:
            try:
                worker.stop()
            except Exception:
                pass
    _started = False
    growth.register_worker(None)


def runtime_status() -> dict[str, object]:
    settings = get_settings()
    return {
        "owned": settings.own_workers,
        "started": _started,
        "crawler": bool(_crawler),
        "metadata": bool(_metadata),
        "thumbnails": bool(_thumbnails),
        "webp": bool(_webp),
        "content_hashes": bool(_content_hashes),
        "visual_hashes": bool(_visual_hashes),
        "backups": bool(_backups),
        "growth": bool(_growth),
    }
