"""Background worker for queued library file/folder management operations.

Moves, recursive deletes, and bulk renames are executed sequentially off the
request thread so a large operation over a remote FUSE mount cannot block the
event loop or time out an HTTP request. Moves are recorded on an undo stack so
they can be reversed one at a time (``POST /api/backup/undo``).

Each queued item is a dict with a stable shape; see ``enqueue`` for the fields.
"""

import asyncio
import logging
import threading
import time
from typing import Any

from media_library.services.library_manage_service import (
    delete_library_path,
    move_library_path,
    rename_library_path,
)
from media_library.workers.base import BaseWorker

logger = logging.getLogger(__name__)


class LibraryOpsWorker(BaseWorker):
    """Sequential executor for library file-management operations with undo."""

    def __init__(self):
        super().__init__("library_ops_worker")
        self.ops_queue: asyncio.Queue = asyncio.Queue()
        self.active_op: dict[str, Any] | None = None
        self.history: list[dict[str, Any]] = []
        self.items_map: dict[str, dict[str, Any]] = {}
        self.undo_stack: list[dict[str, Any]] = []

    def enqueue(self, item: dict[str, Any]):
        item.setdefault("abort_event", threading.Event())
        item.setdefault("status", "queued")
        item.setdefault("error", None)
        item.setdefault("created_at", time.time())
        self.items_map[item["id"]] = item
        self.enqueue_nowait(self.ops_queue, item)

    def cancel(self, task_id: str) -> bool:
        item = self.items_map.get(task_id)
        if not item:
            return False
        item["status"] = "cancelled"
        if item.get("abort_event"):
            item["abort_event"].set()
        return True

    def get_status(self) -> dict[str, Any]:
        active = None
        if self.active_op:
            a = self.active_op
            active = {
                "id": a.get("id"),
                "op": a.get("op"),
                "source_path": a.get("source_path") or a.get("path"),
                "target_path": a.get("target_path"),
                "kind": a.get("kind"),
            }
        queued = []
        for item in self.items_map.values():
            if item.get("status") == "queued":
                queued.append({"id": item.get("id"), "op": item.get("op")})
        return {
            "status": self._status,
            "active": active,
            "queue_length": self.ops_queue.qsize(),
            "queued": queued,
            "history": [self._history_entry(h) for h in self.history[-20:]],
            "undo_count": len(self.undo_stack),
        }

    @staticmethod
    def _history_entry(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": item.get("id"),
            "op": item.get("op"),
            "source_path": item.get("source_path") or item.get("path"),
            "target_path": item.get("target_path"),
            "status": item.get("status"),
            "error": item.get("error"),
        }

    async def _run_loop(self):
        while True:
            item = await self.ops_queue.get()
            try:
                if item.get("status") == "cancelled":
                    self.ops_queue.task_done()
                    continue

                item["status"] = "transferring"
                item["started_at"] = time.time()
                self.active_op = item
                self._status = "transferring"

                op = item.get("op")
                if op == "move":
                    await asyncio.to_thread(
                        move_library_path,
                        item["source_path"],
                        item["target_path"],
                        item["kind"],
                        item.get("abort_event"),
                    )
                    # Record for undo (moves only).
                    self.undo_stack.append(
                        {
                            "source_path": item["source_path"],
                            "target_path": item["target_path"],
                            "kind": item["kind"],
                        }
                    )
                elif op == "delete":
                    await asyncio.to_thread(
                        delete_library_path,
                        item["path"],
                        item["kind"],
                        item.get("abort_event"),
                    )
                elif op == "rename":
                    await asyncio.to_thread(
                        rename_library_path,
                        item["path"],
                        item["new_name"],
                    )

                item["status"] = "completed"
                item["completed_at"] = time.time()
            except asyncio.CancelledError:
                break
            except Exception as e:
                item["status"] = "failed"
                item["error"] = str(e)
                logger.error(f"Library op failed ({item.get('op')}): {e}")
            finally:
                self.active_op = None
                self._status = "idle"
                self.history.append(item)
                self.ops_queue.task_done()

    def pop_undo(self) -> dict[str, Any] | None:
        if not self.undo_stack:
            return None
        return self.undo_stack.pop()

    def push_undo(self, record: dict[str, Any]):
        self.undo_stack.append(record)


library_ops_worker_instance = LibraryOpsWorker()
