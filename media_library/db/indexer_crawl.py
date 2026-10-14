"""The background crawler loop."""

import asyncio
import hashlib
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from media_library.core.config import (
    DEFAULT_DESTINATION_DIR,
    THUMBNAIL_CACHE_DIR,
)
from media_library.core.constants import (
    IMAGE_EXTENSIONS,
    NATIVE_EXTENSIONS,
    VIDEO_EXTENSIONS,
)
from media_library.core.state import (
    format_size,
    get_thumbnail_cache_path,
    state,
)
from media_library.db.connection import db_conn

logger = logging.getLogger(__name__)

from media_library.db.indexer_state import indexer_lock, indexer_state  # noqa: F401

from media_library.db.indexer_prune import prune_stale_index_entries
from media_library.db.indexer_scan import _scan_root_dirs, index_folder


async def run_background_crawler(force_full: bool = False):
    async with indexer_lock:
        indexer_state["status"] = "crawling"
        indexer_state["scanned_folders"] = 0
        indexer_state["skipped_folders"] = 0
        indexer_state["scanned_files"] = 0
        base_dir = os.path.realpath(
            state.get("destination_dir") or DEFAULT_DESTINATION_DIR
        )
        if not os.path.exists(base_dir):
            indexer_state["status"] = "idle"
            return

        try:
            root_dirs = await asyncio.to_thread(_scan_root_dirs, base_dir)
            # The FUSE mount may still be settling right after boot (rclone/NFS).
            # An empty listing for an existing, non-empty library is almost always
            # a not-yet-ready mount, so retry briefly before giving up.
            attempts = 0
            while not root_dirs and attempts < 5 and os.path.isdir(base_dir):
                attempts += 1
                logger.warning(
                    f"[Crawler] No entries found under {base_dir}; waiting for mount (attempt {attempts}/5)..."
                )
                await asyncio.sleep(2.0)
                root_dirs = await asyncio.to_thread(_scan_root_dirs, base_dir)
            indexer_state["total_folders"] = len(root_dirs)

            # Snapshot the previously recorded per-folder mtimes and the known
            # parent -> children map. A folder whose directory mtime still matches
            # what we stored last scan is unchanged, so it can be skipped without
            # re-stat-ing/rewriting its files — while still recursing into its
            # known children so deep changes (and pruning) stay correct.
            known_dir_mtimes: dict[str, float | None] = {}
            known_children: dict[str, list[str]] = {}
            with db_conn() as conn:
                for r in conn.execute(
                    "SELECT path, parent_path, dir_mtime FROM folders"
                ):
                    known_dir_mtimes[r["path"]] = r["dir_mtime"]
                    if r["parent_path"]:
                        known_children.setdefault(r["parent_path"], []).append(
                            r["path"]
                        )

            dirs_queue = list(root_dirs)
            visited_folders: set[str] = set()
            crawler_sem = asyncio.Semaphore(4)

            async def _crawl_single(folder_path: str) -> dict[str, Any]:
                async with crawler_sem:
                    indexer_state["current_folder"] = os.path.basename(folder_path)
                    try:
                        st = await asyncio.to_thread(os.stat, folder_path)
                    except FileNotFoundError:
                        # Really gone: don't protect it, let the prune remove it.
                        return {"gone": True, "subfolders": [], "file_count": 0}
                    except OSError:
                        # Transient mount error: keep the indexed subtree intact
                        # rather than risk the prune deleting it.
                        return {
                            "subfolders": [
                                {"path": c, "name": os.path.basename(c)}
                                for c in known_children.get(folder_path, [])
                            ],
                            "file_count": 0,
                            "skipped": True,
                        }

                    prev_mtime = known_dir_mtimes.get(folder_path)
                    is_zip_file = folder_path.lower().endswith(".zip")
                    unchanged = (
                        not force_full
                        and prev_mtime is not None
                        and abs(st.st_mtime - prev_mtime) < 1e-6
                    )
                    if unchanged:
                        # Directory entries are unchanged: reuse the index and
                        # recurse into the known children only.
                        indexer_state["skipped_folders"] += 1
                        indexer_state["scanned_folders"] += 1
                        children = (
                            [] if is_zip_file else known_children.get(folder_path, [])
                        )
                        return {
                            "subfolders": [
                                {"path": c, "name": os.path.basename(c)}
                                for c in children
                            ],
                            "file_count": 0,
                            "skipped": True,
                        }

                    res = await asyncio.to_thread(index_folder, folder_path, base_dir)
                    indexer_state["scanned_folders"] += 1
                    if isinstance(res, dict):
                        indexer_state["scanned_files"] += res.get("file_count", 0)
                        return res
                    return {"file_count": 0, "subfolders": []}

            while dirs_queue:
                batch = []
                while dirs_queue and len(batch) < 8:
                    batch.append(dirs_queue.pop(0))

                results = await asyncio.gather(
                    *[_crawl_single(f) for f in batch], return_exceptions=True
                )
                for f, res in zip(batch, results):
                    if not isinstance(res, dict):
                        # Unexpected error: protect this folder from pruning.
                        visited_folders.add(os.path.realpath(f))
                        continue
                    if res.get("gone"):
                        continue
                    visited_folders.add(os.path.realpath(f))
                    for sub in res.get("subfolders") or []:
                        dirs_queue.append(sub["path"])
                        indexer_state["total_folders"] += 1

                await asyncio.sleep(0.04)

            # Auto-prune stale entries, deleted folders & orphaned thumbnails.
            # Only prune when the crawl actually enumerated the tree; a partial
            # crawl (e.g. the FUSE mount was still coming up at boot) must never
            # be allowed to delete index rows.
            if visited_folders:
                indexer_state["current_folder"] = "Pruning deleted folders & cache..."
                await asyncio.to_thread(
                    prune_stale_index_entries, base_dir, visited_folders, True
                )
            else:
                logger.warning(
                    "[Crawler] No folders were visited; skipping prune to protect the index."
                )
                indexer_state["prune_aborted"] = True
                indexer_state["prune_reason"] = (
                    "Crawl visited no folders (mount not ready?)"
                )

            with db_conn() as conn:
                cur = conn.execute("SELECT COUNT(*) FROM files")
                indexer_state["total_files"] = cur.fetchone()[0]
                cur_folders = conn.execute("SELECT COUNT(*) FROM folders")
                indexer_state["total_folders"] = cur_folders.fetchone()[0]

            indexer_state["revision"] = indexer_state.get("revision", 0) + 1
            indexer_state["last_synced"] = time.time()
        except Exception as e:
            logger.error(f"Error in background crawler: {e}")
        finally:
            indexer_state["status"] = "idle"
            indexer_state["current_folder"] = ""
