"""Sequential ZIP batch jobs.

One runner at a time, on a daemon thread: each archive operation is the same
validated compress/unpack used by the single-item endpoints, so batch mode adds
scheduling, not a second implementation. Pause takes effect between items (an
archive is never left half-written), and cancel clears the queue.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

from media_library import writes, zip_ops


class ZipBatchRunner:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._pause = threading.Event()
        self._cancel = threading.Event()
        self._state: dict[str, Any] = {
            "mode": None,
            "status": "idle",
            "total": 0,
            "processed": 0,
            "succeeded": 0,
            "failed": 0,
            "percent": 0.0,
            "current_path": None,
            "errors": [],
            "started_at": None,
            "finished_at": None,
        }

    # -- control -------------------------------------------------------

    def start(self, mode: str, paths: list[str] | None = None) -> dict[str, Any]:
        if mode not in ("compress", "unpack"):
            raise writes.WriteError("Unknown batch mode", 400)
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                raise writes.WriteError("A ZIP batch is already running", 409)
            queue = list(paths) if paths else self._default_queue(mode)
            if not queue:
                raise writes.WriteError("No eligible items to process", 400)
            self._pause.clear()
            self._cancel.clear()
            self._state = {
                "mode": mode,
                "status": "running",
                "total": len(queue),
                "processed": 0,
                "succeeded": 0,
                "failed": 0,
                "percent": 0.0,
                "current_path": None,
                "errors": [],
                "started_at": time.time(),
                "finished_at": None,
            }
            operation = (
                zip_ops.compress_folder
                if mode == "compress"
                else zip_ops.unpack_archive
            )
            self._thread = threading.Thread(
                target=self._run,
                args=(queue, operation),
                name="zip-batch",
                daemon=True,
            )
            self._thread.start()
            return self.status()

    @staticmethod
    def _default_queue(mode: str) -> list[str]:
        if mode == "compress":
            return [item["path"] for item in zip_ops.archive_candidates("")]
        return [item["path"] for item in zip_ops.archives()]

    def _run(
        self, queue: list[str], operation: Callable[[str], dict[str, Any]]
    ) -> None:
        for path in queue:
            while self._pause.is_set() and not self._cancel.is_set():
                time.sleep(0.2)
            if self._cancel.is_set():
                break
            with self._lock:
                self._state["current_path"] = path
            try:
                operation(path)
                with self._lock:
                    self._state["succeeded"] += 1
            except Exception as exc:
                with self._lock:
                    self._state["failed"] += 1
                    errors = self._state["errors"]
                    errors.append({"path": path, "error": str(exc)})
                    if len(errors) > 20:
                        del errors[:-20]
            with self._lock:
                self._state["processed"] += 1
                total = self._state["total"] or 1
                self._state["percent"] = round(
                    self._state["processed"] / total * 100, 1
                )
        with self._lock:
            self._state["current_path"] = None
            self._state["finished_at"] = time.time()
            self._state["status"] = (
                "cancelled" if self._cancel.is_set() else "completed"
            )

    def pause(self) -> dict[str, Any]:
        if self._state["status"] != "running":
            raise writes.WriteError("No running ZIP batch", 409)
        self._pause.set()
        with self._lock:
            self._state["status"] = "paused"
        return self.status()

    def resume(self) -> dict[str, Any]:
        if self._state["status"] != "paused":
            raise writes.WriteError("No paused ZIP batch", 409)
        self._pause.clear()
        with self._lock:
            self._state["status"] = "running"
        return self.status()

    def cancel(self) -> dict[str, Any]:
        if self._state["status"] not in ("running", "paused"):
            raise writes.WriteError("No ZIP batch to cancel", 409)
        self._cancel.set()
        self._pause.clear()
        with self._lock:
            self._state["status"] = "cancelled"
        return self.status()

    def status(self) -> dict[str, Any]:
        with self._lock:
            payload = dict(self._state)
            payload["errors"] = list(self._state["errors"])
            return payload


runner = ZipBatchRunner()
