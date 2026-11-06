"""Optional v2-owned background runtime.

While v1 runs it owns the crawler, metadata probes, posters and snapshots; v2
deliberately starts nothing. Setting ``V2_OWN_WORKERS=true`` (after the
cutover) starts the v2 equivalents: periodic crawler, metadata prober, poster
sweep and automatic snapshots.
"""

from __future__ import annotations

import logging
import threading

from media_library_v2 import backups
from media_library_v2.config import get_settings
from media_library_v2.indexer import CrawlerThread
from media_library_v2.probe import MetadataWorker
from media_library_v2.thumbnail_worker import ThumbnailSweep

logger = logging.getLogger(__name__)

_crawler: CrawlerThread | None = None
_metadata: MetadataWorker | None = None
_thumbnails: ThumbnailSweep | None = None
_backup_stop = threading.Event()
_backup_thread: threading.Thread | None = None
_started = False


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
    settings = get_settings()
    if not settings.own_workers:
        logger.info("[v2] Workers belong to v1 (V2_OWN_WORKERS=false)")
        return False
    if _started:
        return True
    _crawler = CrawlerThread(interval_seconds=settings.crawl_interval_minutes * 60)
    _metadata = MetadataWorker()
    _thumbnails = ThumbnailSweep()
    _backups = _BackupWatcher(settings.backup_interval_hours)
    _crawler.start()
    _metadata.start()
    _thumbnails.start()
    _backups.start()
    _started = True
    logger.info("[v2] Owned worker runtime started")
    return True


def stop_runtime() -> None:
    global _started
    for worker in (_crawler, _metadata, _thumbnails, _backups):
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
        "backups": bool(_backups),
    }
