"""Background file transfer worker managing sequential moves and copies."""

import asyncio
import logging
import time
from typing import Any

from media_library.services.media_service import do_transfer
from media_library.workers.base import BaseWorker

logger = logging.getLogger(__name__)


class TransferWorker(BaseWorker):
    """Worker managing queued file transfers with chunking and cancellation support."""

    def __init__(self):
        super().__init__("transfer_worker")
        self.transfer_queue: asyncio.Queue = asyncio.Queue()
        self.active_transfer: dict[str, Any] | None = None
        self.transfers_history: list[dict[str, Any]] = []
        self.transfer_items_map: dict[str, dict[str, Any]] = {}

    def enqueue(self, item: dict[str, Any]):
        if len(self.transfer_items_map) > 1000:
            active_ids = {h.get("id") for h in self.transfers_history[-100:]}
            if self.active_transfer:
                active_ids.add(self.active_transfer.get("id"))
            for k in list(self.transfer_items_map.keys()):
                if k not in active_ids and self.transfer_items_map[k].get("status") in (
                    "completed",
                    "failed",
                    "cancelled",
                ):
                    del self.transfer_items_map[k]
        self.transfer_items_map[item["id"]] = item
        self.enqueue_nowait(self.transfer_queue, item)

    def cancel(self, task_id: str) -> bool:
        if task_id in self.transfer_items_map:
            item = self.transfer_items_map[task_id]
            item["status"] = "cancelled"
            if item.get("abort_event"):
                item["abort_event"].set()
            return True
        return False

    def get_status(self) -> dict[str, Any]:
        return {
            "active": self.active_transfer,
            "queue_length": self.transfer_queue.qsize(),
            "history": self.transfers_history[-20:],
        }

    async def _run_loop(self):
        while True:
            item = await self.transfer_queue.get()
            try:
                if item.get("status") == "cancelled":
                    self.transfer_queue.task_done()
                    continue

                item["status"] = "transferring"
                item["started_at"] = time.time()
                self.active_transfer = item
                self._status = "transferring"

                # Import transcode items map if available
                from media_library.workers.transcode_worker import (
                    transcode_worker_instance,
                )

                transcode_map = (
                    transcode_worker_instance.transcode_items_map
                    if transcode_worker_instance
                    else None
                )

                await asyncio.to_thread(do_transfer, item, transcode_map)
                self.transfers_history.append(item)
                if len(self.transfers_history) > 500:
                    self.transfers_history = self.transfers_history[-500:]
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in transfer worker: {e}")
                item["status"] = "failed"
                item["error"] = str(e)
                self.transfers_history.append(item)
                if len(self.transfers_history) > 500:
                    self.transfers_history = self.transfers_history[-500:]
            finally:
                self.active_transfer = None
                self._status = "idle"
                self.transfer_queue.task_done()


transfer_worker_instance = TransferWorker()
