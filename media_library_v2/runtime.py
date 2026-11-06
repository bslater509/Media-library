"""Optional v2-owned background runtime.

While v1 runs it owns the crawler, metadata probes, posters, WebP sweeps,
duplicate hashing and snapshots; v2 deliberately starts nothing. Setting
``V2_OWN_WORKERS=true`` (after the cutover) starts the v2 equivalents: periodic
crawler, metadata prober, poster sweep, loose-image WebP conversion, content and
visual duplicate hashing, and automatic snapshots.
"""

from __future__ import annotations

import gc
import logging
import threading

from media_library_v2 import backups, dedupe, webp
from media_library_v2.config import get_settings
from media_library_v2.indexer import CrawlerThread
from media_library_v2.probe import MetadataWorker
from media_library_v2.thumbnail_worker import ThumbnailSweep

logger = logging.getLogger(__name__)

_crawler: CrawlerThread | None = None
_metadata: MetadataWorker | None = None
_thumbnails: ThumbnailSweep | None = None
_webp: _BatchSweep | None = None
_content_hashes: _BatchSweep | None = None
_visual_hashes: _BatchSweep | None = None
_backup_stop = threading.Event()
_backup_thread: threading.Thread | None = None
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
                logger.warning("[v2] %s batch failed: %s", self.name, exc)
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
            target=self._loop, name="v2-backups", daemon=True
        )
        _backup_thread.start()

    def _loop(self) -> None:
        if _backup_stop.wait(self.first_delay):
            return
        while not _backup_stop.is_set():
            try:
                backups.create_snapshot()
            except Exception as exc:
                logger.warning("[v2] Automatic snapshot failed: %s", exc)
            if _backup_stop.wait(self.interval):
                break

    def stop(self) -> None:
        _backup_stop.set()
        if _backup_thread:
            _backup_thread.join(timeout=5)


_backups = None


def start_runtime() -> bool:
    """Start v2's workers when it owns them; a no-op alongside v1."""
    global _crawler, _metadata, _thumbnails, _backups, _started
    global _webp, _content_hashes, _visual_hashes
    settings = get_settings()
    if not settings.own_workers:
        logger.info("[v2] Workers belong to v1 (V2_OWN_WORKERS=false)")
        return False
    if _started:
        return True
    _crawler = CrawlerThread(interval_seconds=settings.crawl_interval_minutes * 60)
    _metadata = MetadataWorker()
    _thumbnails = ThumbnailSweep()
    _webp = _BatchSweep(
        "v2-webp",
        lambda: webp.optimize_batch(20) if webp.pending_count() else None,
    )
    _content_hashes = _BatchSweep("v2-content-hashes", lambda: dedupe.hash_content(50))
    _visual_hashes = _BatchSweep("v2-visual-hashes", lambda: dedupe.hash_visual(50))
    _backups = _BackupWatcher(settings.backup_interval_hours)
    _crawler.start()
    _metadata.start()
    _thumbnails.start()
    _webp.start()
    _content_hashes.start()
    _visual_hashes.start()
    _backups.start()
    _started = True
    logger.info("[v2] Owned worker runtime started")
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
    ):
        if worker is not None:
            try:
                worker.stop()
            except Exception:
                pass
    _started = False


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
    }
