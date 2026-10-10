"""Background workers pre-verifying file quick hashes and 64-bit DCT pHash for duplicate detection."""

import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from media_library.core.state import get_thumbnail_cache_path
from media_library.db.connection import db_conn
from media_library.services.deduplication_service import (
    compute_quick_hash,
    compute_thumbnail_phash,
    invalidate_visual_cache,
)
from media_library.workers.base import BaseWorker

logger = logging.getLogger(__name__)


class DuplicateHashWorker(BaseWorker):
    """Worker pre-verifying fast 128KB SHA-256 chunk hashes for duplicate candidate files."""

    def __init__(self):
        super().__init__("duplicate_hash_worker")
        self.state: dict[str, Any] = {
            "status": "idle",  # "idle" | "hashing" | "paused"
            "total_candidates": 0,
            "hashed_count": 0,
            "pending_count": 0,
            "percent": 100.0,
            "current_file": "",
            "processed_count": 0,
            "is_paused": False,
            "last_synced": None,
        }

    def pause(self):
        super().pause()
        self.state["status"] = "paused"
        self.state["is_paused"] = True

    def resume(self):
        super().resume()
        self.state["status"] = "hashing"
        self.state["is_paused"] = False

    def get_status(self) -> dict[str, Any]:
        return self.state

    async def _run_loop(self):
        await asyncio.sleep(3.0)
        concurrency_sem = asyncio.Semaphore(2)

        async def hash_single_candidate(row) -> tuple:
            async with concurrency_sem:
                if not self._pause_event.is_set():
                    await self._pause_event.wait()
                fpath = row["path"]
                self.state["current_file"] = row["name"]
                q_hash = await asyncio.to_thread(compute_quick_hash, fpath)
                return fpath, q_hash

        while True:
            try:
                if not self._pause_event.is_set():
                    self.state["status"] = "paused"
                    self.state["is_paused"] = True
                    await self._pause_event.wait()
                    self.state["status"] = "hashing"
                    self.state["is_paused"] = False

                try:
                    with db_conn() as conn:
                        c_tot_row = conn.execute("""
                            SELECT COUNT(*) FROM files 
                            WHERE size > 0 AND size IN (SELECT size FROM files WHERE size > 0 GROUP BY size HAVING COUNT(*) > 1)
                        """).fetchone()
                        c_tot = c_tot_row[0] if c_tot_row else 0

                        c_hashed_row = conn.execute("""
                            SELECT COUNT(*) FROM files 
                            WHERE quick_hash IS NOT NULL AND size > 0 AND size IN (SELECT size FROM files WHERE size > 0 GROUP BY size HAVING COUNT(*) > 1)
                        """).fetchone()
                        c_hashed = c_hashed_row[0] if c_hashed_row else 0

                        pending_rows = conn.execute("""
                            SELECT path, name FROM files 
                            WHERE quick_hash IS NULL AND size > 0 AND size IN (SELECT size FROM files WHERE size > 0 GROUP BY size HAVING COUNT(*) > 1)
                            ORDER BY size DESC
                            LIMIT 50
                        """).fetchall()
                except Exception as e:
                    logger.error(f"Error querying duplicate candidates: {e}")
                    c_tot, c_hashed, pending_rows = 0, 0, []

                self.state["total_candidates"] = c_tot
                self.state["hashed_count"] = c_hashed
                self.state["pending_count"] = max(0, c_tot - c_hashed)
                self.state["percent"] = (
                    round((c_hashed / c_tot * 100.0), 1) if c_tot > 0 else 100.0
                )

                if not pending_rows:
                    self.state["status"] = "idle"
                    self.state["current_file"] = ""
                    self.state["last_synced"] = time.time()
                    await asyncio.sleep(30.0)
                    continue

                self.state["status"] = "hashing"
                batch = list(pending_rows)
                results = await asyncio.gather(
                    *[hash_single_candidate(r) for r in batch], return_exceptions=True
                )

                updates = []
                for res in results:
                    if isinstance(res, tuple):
                        fpath, q_hash = res
                        val = q_hash if q_hash else "unhashable"
                        updates.append((val, fpath))

                if updates:
                    try:
                        with db_conn() as conn, conn:
                            conn.executemany(
                                "UPDATE files SET quick_hash = ? WHERE path = ?",
                                updates,
                            )
                    except Exception as e:
                        logger.error(f"Error updating candidate hashes in DB: {e}")

                    self.state["processed_count"] += len(updates)
                    self.state["hashed_count"] += len(updates)
                    self.state["pending_count"] = max(
                        0, self.state["total_candidates"] - self.state["hashed_count"]
                    )
                    tot = self.state["total_candidates"]
                    if tot > 0:
                        self.state["percent"] = round(
                            (self.state["hashed_count"] / tot * 100.0), 1
                        )

                await asyncio.sleep(0.1)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in duplicate hash worker: {e}")
                await asyncio.sleep(5.0)


class ThumbHashWorker(BaseWorker):
    """Worker calculating 64-bit DCT pHash for cached thumbnails."""

    def __init__(self):
        super().__init__("thumb_hash_worker")
        self.state: dict[str, Any] = {
            "status": "idle",  # "idle" | "hashing" | "migrating"
            "total_thumbnails": 0,
            "hashed_count": 0,
            "pending_count": 0,
            "percent": 100.0,
            "current_file": "",
            "processed_count": 0,
        }

    def get_status(self) -> dict[str, Any]:
        return self.state

    async def _run_loop(self):
        await asyncio.sleep(4.0)
        concurrency_sem = asyncio.Semaphore(4)

        # One-time pHash migration check
        try:
            with db_conn() as conn:
                meta_row = conn.execute(
                    "SELECT value FROM db_meta WHERE key = 'phash_migration_complete'"
                ).fetchone()
                is_migrated = bool(meta_row and meta_row[0] == "1")

            if not is_migrated:
                self.state["status"] = "migrating"
                with db_conn() as conn:
                    rows = conn.execute(
                        "SELECT path, name, mtime, size FROM files WHERE has_thumb = 1"
                    ).fetchall()

                total_m = len(rows)
                self.state["total_thumbnails"] = total_m
                self.state["hashed_count"] = 0
                self.state["pending_count"] = total_m
                self.state["percent"] = 0.0

                def hash_task(r):
                    tpath = get_thumbnail_cache_path(
                        r["path"], mtime=r["mtime"], size=r["size"]
                    )
                    ph = compute_thumbnail_phash(tpath)
                    return (ph if ph else "no_thumb", r["path"])

                chunk_size = 500
                for start_idx in range(0, total_m, chunk_size):
                    chunk = rows[start_idx : start_idx + chunk_size]
                    loop = asyncio.get_running_loop()
                    with ThreadPoolExecutor(max_workers=8) as pool:
                        updates = await loop.run_in_executor(
                            pool, lambda chunk=chunk: list(map(hash_task, chunk))
                        )

                    with db_conn() as conn, conn:
                        conn.executemany(
                            "UPDATE files SET thumb_hash = ? WHERE path = ?", updates
                        )

                    self.state["hashed_count"] = min(total_m, start_idx + len(chunk))
                    self.state["pending_count"] = max(
                        0, total_m - self.state["hashed_count"]
                    )
                    self.state["percent"] = (
                        round((self.state["hashed_count"] / total_m * 100.0), 1)
                        if total_m > 0
                        else 100.0
                    )
                    await asyncio.sleep(0.01)

                with db_conn() as conn, conn:
                    conn.execute(
                        "INSERT OR REPLACE INTO db_meta (key, value) VALUES ('phash_migration_complete', '1')"
                    )

                invalidate_visual_cache()
                self.state["status"] = "idle"
                self.state["percent"] = 100.0
        except asyncio.CancelledError:
            return
        except Exception as e:
            logger.error(f"Error during pHash migration in thumb_hash_worker: {e}")

        async def hash_single_thumb(row) -> tuple:
            async with concurrency_sem:
                fpath = row["path"]
                self.state["current_file"] = row["name"]
                tpath = get_thumbnail_cache_path(
                    fpath, mtime=row["mtime"], size=row["size"]
                )
                dh = await asyncio.to_thread(compute_thumbnail_phash, tpath)
                return fpath, dh

        while True:
            try:
                try:
                    with db_conn() as conn:
                        tot_row = conn.execute(
                            "SELECT COUNT(*) FROM files WHERE type = 'video' AND has_thumb = 1"
                        ).fetchone()
                        tot_thumbs = tot_row[0] if tot_row else 0

                        hashed_row = conn.execute(
                            "SELECT COUNT(*) FROM files WHERE type = 'video' AND has_thumb = 1 AND thumb_hash IS NOT NULL AND thumb_hash != ''"
                        ).fetchone()
                        hashed_thumbs = hashed_row[0] if hashed_row else 0

                        pending_rows = conn.execute("""
                            SELECT path, name, mtime, size FROM files 
                            WHERE type = 'video' AND has_thumb = 1 AND (thumb_hash IS NULL OR thumb_hash = '')
                            ORDER BY size DESC
                            LIMIT 100
                        """).fetchall()
                except Exception as e:
                    logger.error(f"Error querying thumbnail hash candidates: {e}")
                    tot_thumbs, hashed_thumbs, pending_rows = 0, 0, []

                self.state["total_thumbnails"] = tot_thumbs
                self.state["hashed_count"] = hashed_thumbs
                self.state["pending_count"] = max(0, tot_thumbs - hashed_thumbs)
                self.state["percent"] = (
                    round((hashed_thumbs / tot_thumbs * 100.0), 1)
                    if tot_thumbs > 0
                    else 100.0
                )

                if not pending_rows:
                    self.state["status"] = "idle"
                    self.state["current_file"] = ""
                    await asyncio.sleep(30.0)
                    continue

                self.state["status"] = "hashing"
                batch = list(pending_rows)
                results = await asyncio.gather(
                    *[hash_single_thumb(r) for r in batch], return_exceptions=True
                )

                updates = []
                for res in results:
                    if isinstance(res, tuple):
                        fpath, dh = res
                        val = dh if dh else "no_thumb"
                        updates.append((val, fpath))

                if updates:
                    with db_conn() as conn, conn:
                        conn.executemany(
                            "UPDATE files SET thumb_hash = ? WHERE path = ?", updates
                        )

                    self.state["processed_count"] += len(updates)
                    self.state["hashed_count"] += len(updates)
                    self.state["pending_count"] = max(
                        0, self.state["total_thumbnails"] - self.state["hashed_count"]
                    )
                    tot = self.state["total_thumbnails"]
                    if tot > 0:
                        self.state["percent"] = round(
                            (self.state["hashed_count"] / tot * 100.0), 1
                        )

                await asyncio.sleep(0.05)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in thumb_hash_worker: {e}")
                await asyncio.sleep(5.0)


duplicate_hash_worker_instance = DuplicateHashWorker()
thumb_hash_worker_instance = ThumbHashWorker()
