"""Background WebP image optimization worker."""

import asyncio
import gc
import logging
import os
import time
from typing import Any

from media_library.core.config import WEBP_AUTO_OPTIMIZE, WEBP_WORKER_CONCURRENCY
from media_library.core.constants import IMAGE_EXTENSIONS
from media_library.core.state import format_size
from media_library.db.connection import db_conn
from media_library.services.ffmpeg_service import convert_image_file_to_webp
from media_library.services.zip_service import (
    optimize_zip_images_to_webp,
    split_zip_path,
)
from media_library.workers.base import BaseWorker

logger = logging.getLogger(__name__)

# Back off from processing while the app has less than this much usable memory
# free, so photo decoding / archive rewrites cannot push the machine into OOM.
# Measured against the tighter of host RAM and the service's cgroup cap; kept
# generous so transient decode/encode spikes cannot reach memory.high.
MEMORY_GUARD_MB = 1536.0


def _host_available_memory_mb() -> float:
    try:
        with open("/proc/meminfo", "r", encoding="latin-1") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return float(line.split()[1]) / 1024.0
    except OSError:
        pass
    return float("inf")


def _cgroup_headroom_mb() -> float:
    """Free memory before this service's own cgroup limit is reached.

    The host's ``MemAvailable`` says nothing about the tighter systemd cgroup cap
    (``memory.high`` / ``memory.max``). Once memory.high is exceeded the kernel
    starts throttling allocations and sockets, which is what makes the whole
    server stop answering HTTP. Returning the smaller of host-available and
    cgroup-headroom lets the worker back off in time.
    """
    try:
        rel = "/"
        with open("/proc/self/cgroup", "r", encoding="latin-1") as f:
            for line in f:
                parts = line.strip().split(":", 2)
                if len(parts) == 3 and parts[0] == "0":  # cgroup v2 unified
                    rel = parts[2] or "/"
                    break
        base = "/sys/fs/cgroup" + ("" if rel == "/" else rel)

        def _read(name: str):
            try:
                with open(f"{base}/{name}", "r", encoding="latin-1") as fh:
                    val = fh.read().strip()
            except OSError:
                return None
            return None if val == "max" else int(val)

        current = _read("memory.current")
        if current is None:
            return float("inf")
        limit = _read("memory.high")
        if limit is None:
            limit = _read("memory.max")
        if limit is None:
            return float("inf")
        return max(0.0, (limit - current) / (1024.0 * 1024.0))
    except OSError:
        return float("inf")


def _available_memory_mb() -> float:
    """Effective headroom: the tighter of host RAM and the app's cgroup cap."""
    return min(_host_available_memory_mb(), _cgroup_headroom_mb())


def _release_memory() -> None:
    """Return freed heap pages to the OS after image work.

    Pillow decodes/encodes large buffers per image; glibc can retain those freed
    arenas, so RSS drifts upward even though the Python objects are gone. Trim it
    back down so the memory guard can recover instead of sitting at the cap.
    """
    gc.collect()
    try:
        import ctypes

        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except Exception:
        pass


class WebPOptimizerWorker(BaseWorker):
    """Worker converting existing library images to WebP format in background."""

    def __init__(self):
        super().__init__("webp_worker")
        self.priority_queue: asyncio.Queue = asyncio.Queue()
        self._zip_enqueue_recent: dict[str, float] = {}
        self.state: dict[str, Any] = {
            "status": "idle",  # "idle" | "converting" | "paused" | "disabled"
            "total_images": 0,
            "optimized_count": 0,
            "converted_count": 0,
            "skipped_count": 0,
            "pending_count": 0,
            "percent": 100.0,
            "saved_bytes": 0,
            "saved_formatted": "0 B",
            "current_file": "",
            "current_folder": "",
            "is_paused": False,
            "concurrency": WEBP_WORKER_CONCURRENCY,
            "last_synced": None,
        }

    def pause(self):
        super().pause()
        self.state["status"] = "paused"
        self.state["is_paused"] = True

    def resume(self):
        super().resume()
        self.state["status"] = "converting"
        self.state["is_paused"] = False

    def enqueue_files(self, paths: list[str]) -> int:
        """Enqueues individual loose image files for WebP conversion.

        Returns the number of items queued. Non-image or already-WebP files are
        skipped (the caller may report them as such).
        """
        enqueued = 0
        for p in paths:
            if not os.path.exists(p) or not os.path.isfile(p):
                continue
            ext = os.path.splitext(p)[1].lower()
            if ext not in IMAGE_EXTENSIONS or ext == ".webp":
                continue
            self.enqueue_nowait(
                self.priority_queue,
                {
                    "path": p,
                    "name": os.path.basename(p),
                    "folder_path": os.path.dirname(p),
                    "is_zip": 0,
                },
            )
            enqueued += 1
        return enqueued

    def enqueue_folder(self, folder_path: str) -> int:
        enqueued = 0
        try:
            with db_conn() as conn:
                folder_row = conn.execute(
                    "SELECT is_zip FROM folders WHERE path = ?", (folder_path,)
                ).fetchone()
                is_zip = bool(
                    folder_row and folder_row[0] == 1
                ) or folder_path.lower().endswith(".zip")
                cur = conn.execute(
                    "SELECT path, name, folder_path FROM files WHERE folder_path = ? AND type = 'image' AND ext != '.webp' AND webp_optimized = 0",
                    (folder_path,),
                )
                rows = cur.fetchall()

            if is_zip:
                if rows:
                    # Throttle: a ZIP that was enqueued very recently (e.g. from a
                    # browse, background-index, and the Optimize button in quick
                    # succession) must not be queued multiple times.
                    now = time.time()
                    if now - self._zip_enqueue_recent.get(folder_path, 0.0) >= 30.0:
                        # Process the whole archive as a single unit rather than one item per member.
                        self.enqueue_nowait(
                            self.priority_queue,
                            {
                                "path": folder_path,
                                "name": os.path.basename(folder_path),
                                "folder_path": folder_path,
                                "is_zip": 1,
                            },
                        )
                        self._zip_enqueue_recent[folder_path] = now
                        enqueued = len(rows)
            else:
                for r in rows:
                    self.enqueue_nowait(
                        self.priority_queue,
                        {
                            "path": r["path"],
                            "name": r["name"],
                            "folder_path": r["folder_path"],
                            "is_zip": 0,
                        },
                    )
                    enqueued += 1
        except Exception as e:
            logger.error(f"Error enqueuing folder for WebP optimization: {e}")
        return enqueued

    def get_status(self) -> dict[str, Any]:
        return self.state

    def _next_auto_items(self, conn) -> list[dict[str, Any]]:
        """Items the worker picks up on its own: loose (non-ZIP) images only.

        Images inside ZIP archives are intentionally excluded from the automatic
        sweep — rewriting a remote archive is a heavy, one-at-a-time operation,
        so archives are optimized on demand (when browsed, or via the Optimize
        WebP button). ``WEBP_AUTO_OPTIMIZE=false`` disables even the loose-image
        sweep; explicitly queued items always run.
        """
        if not WEBP_AUTO_OPTIMIZE:
            return []
        batch_limit = max(1, WEBP_WORKER_CONCURRENCY * 2)
        rows = conn.execute(
            """
            SELECT f.path, f.name, f.folder_path, 0 AS is_zip
            FROM files f
            LEFT JOIN folders fo ON f.folder_path = fo.path
            WHERE f.type = 'image' AND f.ext != '.webp' AND f.webp_optimized = 0
              AND (fo.is_zip IS NULL OR fo.is_zip = 0)
            ORDER BY f.size DESC
            LIMIT ?
        """,
            (batch_limit,),
        ).fetchall()
        return [
            {
                "path": r["path"],
                "name": r["name"],
                "folder_path": r["folder_path"],
                "is_zip": 0,
            }
            for r in rows
        ]

    async def _run_loop(self):
        await asyncio.sleep(5.0)
        concurrency_sem = asyncio.Semaphore(WEBP_WORKER_CONCURRENCY)

        async def optimize_item(item: dict) -> dict:
            async with concurrency_sem:
                if not self._pause_event.is_set():
                    await self._pause_event.wait()
                fpath = item["path"]
                fname = item["name"]
                is_zip = (
                    (item.get("is_zip") == 1)
                    or (".zip/" in fpath)
                    or (".zip\\" in fpath)
                    or fpath.lower().endswith(".zip")
                )

                if is_zip:
                    zip_p, _ = split_zip_path(fpath)
                    target_zip = zip_p or item.get("folder_path") or fpath
                    self.state["current_file"] = os.path.basename(target_zip)
                    self.state["current_folder"] = os.path.basename(
                        os.path.dirname(target_zip)
                    )
                    res = await asyncio.to_thread(
                        optimize_zip_images_to_webp, target_zip
                    )
                    # Releasing retained arenas after a big archive rewrite keeps
                    # RSS from drifting upward run after run.
                    _release_memory()
                    return res
                else:
                    self.state["current_file"] = fname
                    self.state["current_folder"] = os.path.basename(
                        os.path.dirname(fpath)
                    )
                    res = await asyncio.to_thread(convert_image_file_to_webp, fpath)
                    # Release decoded-image heap between conversions so a long
                    # sweep cannot accumulate RSS up to the cgroup cap.
                    _release_memory()
                    return res

        while True:
            try:
                if not self._pause_event.is_set():
                    self.state["status"] = "paused"
                    self.state["is_paused"] = True
                    await self._pause_event.wait()
                    self.state["status"] = "converting"
                    self.state["is_paused"] = False

                try:
                    with db_conn() as conn:
                        tot_row = conn.execute(
                            "SELECT COUNT(*) FROM files WHERE type = 'image'"
                        ).fetchone()
                        total_imgs = tot_row[0] if tot_row else 0

                        opt_row = conn.execute(
                            "SELECT COUNT(*) FROM files WHERE type = 'image' AND (webp_optimized != 0 OR ext = '.webp')"
                        ).fetchone()
                        optimized_imgs = opt_row[0] if opt_row else 0

                        stats_row = conn.execute(
                            "SELECT saved_bytes, converted_count, skipped_count FROM webp_stats WHERE id = 1"
                        ).fetchone()
                        if stats_row:
                            self.state["saved_bytes"] = stats_row[0]
                            self.state["saved_formatted"] = format_size(stats_row[0])
                            self.state["converted_count"] = stats_row[1]
                            self.state["skipped_count"] = stats_row[2]

                        priority_items = []
                        batch_limit = max(1, WEBP_WORKER_CONCURRENCY * 2)
                        while (
                            not self.priority_queue.empty()
                            and len(priority_items) < batch_limit
                        ):
                            try:
                                item = self.priority_queue.get_nowait()
                                priority_items.append(item)
                            except asyncio.QueueEmpty:
                                break

                        if priority_items:
                            pending_rows = priority_items
                        else:
                            # Automatic background work is deliberately limited to loose
                            # (non-ZIP) images. Images inside ZIP archives are only ever
                            # optimized on demand: when the archive is browsed or via the
                            # Optimize WebP button.
                            pending_rows = self._next_auto_items(conn)

                except Exception as e:
                    logger.error(f"Error querying webp images: {e}")
                    pending_rows = []

                # Deduplicate ZIP archive items so each archive is optimized at most once
                # per batch (protects against duplicate priority-queue entries).
                seen_zips = set()
                deduped = []
                for r in pending_rows:
                    is_zip_item = (
                        (r.get("is_zip") == 1)
                        or (".zip/" in r["path"])
                        or (".zip\\" in r["path"])
                        or r["path"].lower().endswith(".zip")
                    )
                    if is_zip_item:
                        zip_p, _ = split_zip_path(r["path"])
                        key = zip_p or r.get("folder_path") or r["path"]
                        if key in seen_zips:
                            continue
                        seen_zips.add(key)
                    deduped.append(r)
                pending_rows = deduped

                if total_imgs > 0:
                    self.state["total_images"] = total_imgs
                    self.state["optimized_count"] = optimized_imgs
                    self.state["pending_count"] = max(0, total_imgs - optimized_imgs)
                    self.state["percent"] = round(
                        (optimized_imgs / total_imgs * 100.0), 1
                    )

                if not pending_rows:
                    self.state["status"] = "idle"
                    self.state["current_file"] = ""
                    self.state["current_folder"] = ""
                    self.state["last_synced"] = time.time()
                    await asyncio.sleep(5.0)
                    continue

                # Memory back-off: photo decoding and archive rewrites are
                # memory-hungry. If the app is close to its cgroup limit, hold off
                # (trimming freed heap) instead of pushing the box into throttle
                # or an OOM-kill.
                wait_loops = 0
                while _available_memory_mb() < MEMORY_GUARD_MB and wait_loops < 60:
                    _release_memory()
                    await asyncio.sleep(5.0)
                    wait_loops += 1

                self.state["status"] = "converting"
                results = await asyncio.gather(
                    *[optimize_item(r) for r in pending_rows], return_exceptions=True
                )

                has_progress = False
                for res in results:
                    if isinstance(res, dict) and res.get("status") == "converted":
                        has_progress = True
                        if self.state["pending_count"] > 0:
                            self.state["pending_count"] = max(
                                0,
                                self.state["pending_count"]
                                - res.get("converted_count", 1),
                            )
                    elif (
                        isinstance(res, dict)
                        and res.get("status") in ("skipped", "already_webp")
                        or isinstance(res, dict)
                        and res.get("status") in ("error", "unsupported")
                    ):
                        has_progress = True
                        if self.state["pending_count"] > 0:
                            self.state["pending_count"] = max(
                                0, self.state["pending_count"] - 1
                            )
                    elif isinstance(res, Exception):
                        has_progress = True

                # Refresh persisted stats from the database rather than double-counting:
                # convert_* already wrote saved_bytes/converted_count/skipped_count to webp_stats.
                with db_conn() as conn:
                    stats_row = conn.execute(
                        "SELECT saved_bytes, converted_count, skipped_count FROM webp_stats WHERE id = 1"
                    ).fetchone()
                    if stats_row:
                        self.state["saved_bytes"] = stats_row[0]
                        self.state["saved_formatted"] = format_size(stats_row[0])
                        self.state["converted_count"] = stats_row[1]
                        self.state["skipped_count"] = stats_row[2]

                if not has_progress:
                    await asyncio.sleep(2.0)
                else:
                    await asyncio.sleep(0.1)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in webp_converter_worker: {e}")
                await asyncio.sleep(5.0)


webp_worker_instance = WebPOptimizerWorker()
