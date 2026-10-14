"""Shared mutable indexer state and lock.

Kept in its own module so the indexer submodules can import it without
creating an import cycle with the ``db.indexer`` facade.
"""

import asyncio
from typing import Any

indexer_state: dict[str, Any] = {
    "status": "idle",  # "crawling" | "idle"
    "scanned_folders": 0,
    "skipped_folders": 0,
    "total_folders": 0,
    "scanned_files": 0,
    "total_files": 0,
    "current_folder": "",
    "last_synced": None,
    # Reconciled index health, refreshed at the end of every crawl/prune so the
    # status panel can tell "everything reachable is indexed" apart from
    # "indexed rows still point at folders that are gone from the mount".
    "stale_folders": 0,
    "stale_files": 0,
    "prune_aborted": False,
    "prune_reason": "",
    # Bumped whenever the folder/file row counts may have changed, so the
    # 30s count cache in services.status never serves a pre-crawl snapshot.
    "revision": 0,
}
indexer_lock = asyncio.Lock()
