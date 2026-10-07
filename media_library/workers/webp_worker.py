"""Background WebP image optimization worker."""

import os
import time
import asyncio
from typing import Dict, Any

from media_library.workers.base import BaseWorker
from media_library.core.config import WEBP_WORKER_CONCURRENCY, WEBP_AUTO_OPTIMIZE
from media_library.core.state import format_size
from media_library.db.connection import get_db
from media_library.services.ffmpeg_service import convert_image_file_to_webp

class WebPOptimizerWorker(BaseWorker):
    """Worker converting existing library images to WebP format in background."""

    def __init__(self):
        super().__init__("webp_worker")
        self.priority_queue: asyncio.Queue = asyncio.Queue()
        self.state: Dict[str, Any] = {
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

    def enqueue_folder(self, folder_path: str) -> int:
        enqueued = 0
        try:
            conn = get_db()
            folder_row = conn.execute("SELECT is_zip FROM folders WHERE path = ?", (folder_path,)).fetchone()
            is_zip = bool(folder_row and folder_row[0] == 1) or folder_path.lower().endswith(".zip")
            cur = conn.execute(
                "SELECT path, name, folder_path FROM files WHERE folder_path = ? AND type = 'image' AND ext != '.webp' AND webp_optimized = 0",
                (folder_path,)
            )
            rows = cur.fetchall()
            conn.close()
            for r in rows:
                self.priority_queue.put_nowait({
                    "path": r["path"],
                    "name": r["name"],
                    "folder_path": r["folder_path"],
                    "is_zip": 1 if is_zip else 0,
                })
                enqueued += 1
        except Exception as e:
            print(f"Error enqueuing folder for WebP optimization: {e}")
        return enqueued

    def get_status(self) -> Dict[str, Any]:
        return self.state

    async def _run_loop(self):
        if not WEBP_AUTO_OPTIMIZE:
            self.state["status"] = "disabled"
            return

        await asyncio.sleep(5.0)
        concurrency_sem = asyncio.Semaphore(WEBP_WORKER_CONCURRENCY)

        async def optimize_item(item: dict) -> dict:
            async with concurrency_sem:
                if not self._pause_event.is_set():
                    await self._pause_event.wait()
                fpath = item["path"]
                fname = item["name"]
                is_zip = (item.get("is_zip") == 1) or (".zip/" in fpath) or (".zip\\" in fpath) or fpath.lower().endswith(".zip")

                if is_zip:
                    from media_library.services.zip_service import split_zip_path, optimize_zip_images_to_webp
                    zip_p, _ = split_zip_path(fpath)
                    target_zip = zip_p or item.get("folder_path") or fpath
                    self.state["current_file"] = f"{os.path.basename(target_zip)}: {fname}"
                    self.state["current_folder"] = os.path.basename(os.path.dirname(target_zip))
                    return await asyncio.to_thread(optimize_zip_images_to_webp, target_zip)
                else:
                    self.state["current_file"] = fname
                    self.state["current_folder"] = os.path.basename(os.path.dirname(fpath))
                    return await asyncio.to_thread(convert_image_file_to_webp, fpath)

        while True:
            try:
                if not self._pause_event.is_set():
                    self.state["status"] = "paused"
                    self.state["is_paused"] = True
                    await self._pause_event.wait()
                    self.state["status"] = "converting"
                    self.state["is_paused"] = False

                conn = get_db()
                try:
                    tot_row = conn.execute("SELECT COUNT(*) FROM files WHERE type = 'image'").fetchone()
                    total_imgs = tot_row[0] if tot_row else 0

                    opt_row = conn.execute("SELECT COUNT(*) FROM files WHERE type = 'image' AND (webp_optimized != 0 OR ext = '.webp')").fetchone()
                    optimized_imgs = opt_row[0] if opt_row else 0

                    stats_row = conn.execute("SELECT saved_bytes, converted_count, skipped_count FROM webp_stats WHERE id = 1").fetchone()
                    if stats_row:
                        self.state["saved_bytes"] = stats_row[0]
                        self.state["saved_formatted"] = format_size(stats_row[0])
                        self.state["converted_count"] = stats_row[1]
                        self.state["skipped_count"] = stats_row[2]

                    priority_items = []
                    batch_limit = max(1, WEBP_WORKER_CONCURRENCY * 2)
                    while not self.priority_queue.empty() and len(priority_items) < batch_limit:
                        try:
                            item = self.priority_queue.get_nowait()
                            priority_items.append(item)
                        except asyncio.QueueEmpty:
                            break

                    if priority_items:
                        pending_rows = priority_items
                    else:
                        rows = conn.execute(f"""
                            SELECT f.path, f.name, f.folder_path, COALESCE(fo.is_zip, 0) as is_zip
                            FROM files f
                            LEFT JOIN folders fo ON f.folder_path = fo.path
                            WHERE f.type = 'image' AND f.ext != '.webp' AND f.webp_optimized = 0
                            ORDER BY f.size DESC
                            LIMIT {batch_limit}
                        """).fetchall()
                        pending_rows = [
                            {
                                "path": r["path"],
                                "name": r["name"],
                                "folder_path": r["folder_path"],
                                "is_zip": r["is_zip"],
                            }
                            for r in rows
                        ]

                except Exception as e:
                    print(f"Error querying webp images: {e}")
                    pending_rows = []
                finally:
                    conn.close()

                if total_imgs > 0:
                    self.state["total_images"] = total_imgs
                    self.state["optimized_count"] = optimized_imgs
                    self.state["pending_count"] = max(0, total_imgs - optimized_imgs)
                    self.state["percent"] = round((optimized_imgs / total_imgs * 100.0), 1)

                if not pending_rows:
                    self.state["status"] = "idle"
                    self.state["current_file"] = ""
                    self.state["current_folder"] = ""
                    self.state["last_synced"] = time.time()
                    await asyncio.sleep(5.0)
                    continue

                self.state["status"] = "converting"
                results = await asyncio.gather(*[optimize_item(r) for r in pending_rows], return_exceptions=True)

                has_progress = False
                for res in results:
                    if isinstance(res, dict) and res.get("status") == "converted":
                        has_progress = True
                        self.state["saved_bytes"] += res.get("saved_bytes", 0)
                        self.state["saved_formatted"] = format_size(self.state["saved_bytes"])
                        self.state["converted_count"] += res.get("converted_count", 1)
                        if self.state["pending_count"] > 0:
                            self.state["pending_count"] = max(0, self.state["pending_count"] - res.get("converted_count", 1))
                    elif isinstance(res, dict) and res.get("status") in ("skipped", "already_webp"):
                        has_progress = True
                        self.state["skipped_count"] = self.state.get("skipped_count", 0) + 1
                        if self.state["pending_count"] > 0:
                            self.state["pending_count"] = max(0, self.state["pending_count"] - 1)
                    elif isinstance(res, dict) and res.get("status") in ("error", "unsupported"):
                        has_progress = True
                        if self.state["pending_count"] > 0:
                            self.state["pending_count"] = max(0, self.state["pending_count"] - 1)
                    elif isinstance(res, Exception):
                        has_progress = True

                if not has_progress:
                    await asyncio.sleep(2.0)
                else:
                    await asyncio.sleep(0.1)

            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"Error in webp_converter_worker: {e}")
                await asyncio.sleep(5.0)

webp_worker_instance = WebPOptimizerWorker()
