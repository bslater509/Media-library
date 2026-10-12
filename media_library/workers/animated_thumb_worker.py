"""Background animated preview worker compiling looping WebP previews for videos."""

import asyncio
import logging
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from media_library.core.constants import VIDEO_EXTENSIONS
from media_library.db.connection import db_conn
from media_library.services.ffmpeg_service import (
    generate_animated_preview,
    get_animated_preview_path,
)
from media_library.workers.base import BaseWorker

logger = logging.getLogger(__name__)


def sync_animated_thumb_db_cache():
    """Fast check: cross-reference existing cached animated WebP files against files in SQLite."""
    try:
        from media_library.core.config import THUMBNAIL_CACHE_DIR
        import hashlib

        anim_dir = Path(THUMBNAIL_CACHE_DIR) / "animated"
        if not anim_dir.exists():
            return
        existing_anims = {
            p.name for p in anim_dir.glob("anim_*.webp") if p.stat().st_size > 0
        }
        if not existing_anims:
            return

        with db_conn() as conn:
            cur = conn.execute(
                "SELECT path, mtime, size FROM files WHERE type = 'video' AND (has_animated IS NULL OR has_animated = 0)"
            )
            rows = cur.fetchall()
            matched = []
            for r in rows:
                key = str(r["path"])
                if r["mtime"] is not None and r["size"] is not None:
                    key = f"{r['path']}_{r['mtime']}_{r['size']}"
                h = hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
                if f"anim_{h}.webp" in existing_anims:
                    matched.append((r["path"],))
            if matched:
                with conn:
                    conn.executemany(
                        "UPDATE files SET has_animated = 1 WHERE path = ?", matched
                    )
                logger.info(
                    f"[AnimatedThumbWorker] Synced {len(matched)} existing animated WebP previews with SQLite index."
                )
    except Exception as e:
        logger.error(f"Error syncing animated thumbnail cache: {e}")


class AnimatedThumbWorker(BaseWorker):
    """Worker managing video animated WebP previews queue and batch compilation."""

    def __init__(self):
        super().__init__("animated_thumb_worker")
        self.priority_queue: asyncio.Queue = asyncio.Queue()
        self.batch_queue: asyncio.Queue = asyncio.Queue()
        self.items_map: dict[str, dict[str, Any]] = {}
        self.active_task: dict[str, Any] | None = None
        self.history: list[dict[str, Any]] = []
        self.recent_completed: list[str] = []
        self.processed_count: int = 0
        self.failed_count: int = 0

    def pause(self):
        super().pause()
        self._status = "paused"

    def resume(self):
        super().resume()
        self._status = "generating" if self.active_task else "idle"

    def cancel_all(self) -> int:
        """Cancels all queued animated preview requests and aborts active task."""
        cancelled = 0
        while not self.priority_queue.empty():
            try:
                it = self.priority_queue.get_nowait()
                it["status"] = "cancelled"
                cancelled += 1
                self.priority_queue.task_done()
            except Exception:
                break

        while not self.batch_queue.empty():
            try:
                it = self.batch_queue.get_nowait()
                it["status"] = "cancelled"
                cancelled += 1
                self.batch_queue.task_done()
            except Exception:
                break

        for it in list(self.items_map.values()):
            if it.get("status") == "queued":
                it["status"] = "cancelled"
                cancelled += 1

        if self.active_task and self.active_task.get("abort_event"):
            self.active_task["abort_event"].set()

        self.items_map.clear()
        return cancelled

    def enqueue(self, file_path: str, priority: bool = False) -> bool:
        """Enqueues a video for animated WebP compilation."""
        try:
            from media_library.services.media_service import validate_backup_path
            real_path = validate_backup_path(file_path)
        except Exception:
            real_path = file_path

        # If already cached on disk, mark DB and return False
        anim_path = get_animated_preview_path(real_path)
        if anim_path.exists() and anim_path.stat().st_size > 0:
            if real_path not in self.recent_completed:
                self.recent_completed.append(real_path)
            try:
                with db_conn() as conn, conn:
                    conn.execute(
                        "UPDATE files SET has_animated = 1 WHERE path = ?", (real_path,)
                    )
            except Exception:
                pass
            return False

        existing = self.items_map.get(real_path)
        if existing:
            if existing.get("status") in ("generating", "completed"):
                return False
            if priority and not existing.get("priority"):
                existing["priority"] = True
                self.enqueue_nowait(self.priority_queue, existing)
                return True
            return False

        item = {
            "id": uuid.uuid4().hex[:10],
            "path": real_path,
            "name": os.path.basename(real_path),
            "status": "queued",
            "priority": priority,
            "created_at": time.time(),
            "started_at": None,
            "percent": 0.0,
            "abort_event": threading.Event(),
        }
        self.items_map[real_path] = item
        if priority:
            self.enqueue_nowait(self.priority_queue, item)
        else:
            self.enqueue_nowait(self.batch_queue, item)
        return True

    def enqueue_batch(self, file_paths: list[str]) -> int:
        """Enqueues multiple video paths for batch processing."""
        count = 0
        for p in file_paths:
            if self.enqueue(p, priority=False):
                count += 1
        return count

    def enqueue_all(self, force: bool = False) -> int:
        """Enqueues all videos across the library that do not yet have animated previews."""
        videos: list[str] = []
        try:
            with db_conn() as conn:
                if force:
                    cur = conn.execute("SELECT path FROM files WHERE type = 'video'")
                else:
                    cur = conn.execute(
                        "SELECT path FROM files WHERE type = 'video' AND (has_animated IS NULL OR has_animated = 0)"
                    )
                videos = [r["path"] for r in cur.fetchall()]
        except Exception as e:
            logger.error(f"Error querying all videos for animated previews: {e}")

        return self.enqueue_batch(videos)

    def enqueue_folder(self, folder_path: str, force: bool = False) -> int:
        """Enqueues all videos in a folder that do not yet have animated previews."""
        from media_library.services.media_service import validate_backup_path

        real_folder = validate_backup_path(folder_path)
        videos: list[str] = []
        try:
            with db_conn() as conn:
                if force:
                    cur = conn.execute(
                        "SELECT path FROM files WHERE folder_path = ? AND type = 'video'",
                        (real_folder,),
                    )
                else:
                    cur = conn.execute(
                        "SELECT path FROM files WHERE folder_path = ? AND type = 'video' AND (has_animated IS NULL OR has_animated = 0)",
                        (real_folder,),
                    )
                videos = [r["path"] for r in cur.fetchall()]
        except Exception as e:
            logger.error(f"Error querying folder for animated previews: {e}")

        return self.enqueue_batch(videos)

    def get_status(self) -> dict[str, Any]:
        """Returns the current worker status and progress dictionary for system status."""
        queued_items = [
            {"id": it["id"], "name": it["name"], "path": it["path"]}
            for it in self.items_map.values()
            if it.get("status") == "queued"
        ]
        is_paused = self.is_paused()
        is_active = bool(self.active_task)
        status_str = (
            "paused" if is_paused else ("generating" if is_active else "idle")
        )
        total_q = len(queued_items) + (1 if is_active else 0)

        done = self.processed_count
        total_work = done + total_q
        pct = (
            round((done / total_work * 100.0), 1)
            if total_work > 0
            else (100.0 if done > 0 else 0.0)
        )

        return {
            "status": status_str,
            "is_paused": is_paused,
            "active": (
                {
                    "name": self.active_task["name"],
                    "path": self.active_task["path"],
                    "started_at": self.active_task.get("started_at"),
                    "percent": self.active_task.get("percent", 0.0),
                }
                if self.active_task
                else None
            ),
            "current_file": self.active_task["name"] if self.active_task else "",
            "queue_length": len(queued_items),
            "total_queued": total_q,
            "processed_count": self.processed_count,
            "failed_count": self.failed_count,
            "percent": pct,
            "queued": queued_items[:20],
            "history": self.history[-20:],
            "recent_completed": list(self.recent_completed)[-50:],
        }

    async def _run_loop(self):
        """Main execution loop for animated thumbnail generation."""
        await asyncio.to_thread(sync_animated_thumb_db_cache)

        while True:
            try:
                await self._pause_event.wait()

                item = None
                try:
                    item = self.priority_queue.get_nowait()
                except asyncio.QueueEmpty:
                    try:
                        item = self.batch_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        self.active_task = None
                        if not self.is_paused():
                            self._status = "idle"
                        await asyncio.sleep(0.5)
                        continue

                if not item or item.get("status") == "cancelled":
                    continue

                item["status"] = "generating"
                item["started_at"] = time.time()
                self.active_task = item
                self._status = "generating"

                file_path = item["path"]
                try:
                    anim = await generate_animated_preview(file_path)
                    if anim and anim.exists() and anim.stat().st_size > 0:
                        item["status"] = "completed"
                        item["completed_at"] = time.time()
                        self.processed_count += 1
                        if file_path not in self.recent_completed:
                            self.recent_completed.append(file_path)
                        if len(self.recent_completed) > 100:
                            self.recent_completed.pop(0)

                        self.history.append(
                            {
                                "name": item["name"],
                                "path": file_path,
                                "status": "completed",
                                "completed_at": time.time(),
                            }
                        )
                        if len(self.history) > 50:
                            self.history.pop(0)

                        try:
                            with db_conn() as conn, conn:
                                conn.execute(
                                    "UPDATE files SET has_animated = 1 WHERE path = ?",
                                    (file_path,),
                                )
                        except Exception as dbe:
                            logger.warning(
                                f"Failed to update has_animated in DB for {file_path}: {dbe}"
                            )
                    else:
                        item["status"] = "failed"
                        self.failed_count += 1
                        self.history.append(
                            {
                                "name": item["name"],
                                "path": file_path,
                                "status": "failed",
                                "completed_at": time.time(),
                            }
                        )
                        if len(self.history) > 50:
                            self.history.pop(0)
                except asyncio.CancelledError:
                    break
                except Exception as ex:
                    logger.warning(
                        f"Exception generating animated preview for {file_path}: {ex}"
                    )
                    item["status"] = "failed"
                    self.failed_count += 1
                finally:
                    self.active_task = None
                    if item.get("path") in self.items_map:
                        del self.items_map[item["path"]]

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in AnimatedThumbWorker loop: {e}")
                await asyncio.sleep(1.0)


animated_thumb_worker_instance = AnimatedThumbWorker()
