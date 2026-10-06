"""Workers module exports."""

from media_library.workers.base import BaseWorker
from media_library.workers.transfer_worker import TransferWorker, transfer_worker_instance
from media_library.workers.transcode_worker import (
    TranscodeWorker,
    transcode_worker_instance,
    enqueue_pretranscode_for_source,
)
from media_library.workers.thumbnail_worker import ThumbnailWorker, thumbnail_worker_instance
from media_library.workers.duplicate_hash_worker import (
    DuplicateHashWorker,
    ThumbHashWorker,
    duplicate_hash_worker_instance,
    thumb_hash_worker_instance,
)
from media_library.workers.webp_worker import WebPOptimizerWorker, webp_worker_instance
from media_library.workers.backup_watcher import (
    BackupWatcherWorker,
    backup_watcher_instance,
    run_library_backup_async,
    run_library_restore_async,
)

__all__ = [
    "BaseWorker",
    "TransferWorker",
    "transfer_worker_instance",
    "TranscodeWorker",
    "transcode_worker_instance",
    "enqueue_pretranscode_for_source",
    "ThumbnailWorker",
    "thumbnail_worker_instance",
    "DuplicateHashWorker",
    "ThumbHashWorker",
    "duplicate_hash_worker_instance",
    "thumb_hash_worker_instance",
    "WebPOptimizerWorker",
    "webp_worker_instance",
    "BackupWatcherWorker",
    "backup_watcher_instance",
    "run_library_backup_async",
    "run_library_restore_async",
]
