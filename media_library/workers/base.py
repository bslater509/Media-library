"""Base background worker manager defining standard lifecycle methods."""

import asyncio
from typing import Any


class BaseWorker:
    """Abstract base worker encapsulating lifecycle, pause/resume, and status reporting."""

    def __init__(self, name: str):
        self.name = name
        self._task: asyncio.Task | None = None
        self._pause_event = asyncio.Event()
        self._pause_event.set()
        self._status: str = "idle"
        self._loop: asyncio.AbstractEventLoop | None = None

    async def start(self):
        """Starts the background worker task if not already running."""
        if self._task is None or self._task.done():
            self._loop = asyncio.get_running_loop()
            self._task = asyncio.create_task(self._run_loop())

    def enqueue_nowait(self, queue: asyncio.Queue, item: Any):
        """Thread-safely push an item onto a worker's asyncio.Queue.

        Enqueue methods are frequently called from sync route handlers running on
        AnyIO's threadpool. ``asyncio.Queue.put_nowait`` wakes the consumer via
        ``Future._loop.call_soon``, which does not signal the event loop's
        self-pipe, so an item could sit unprocessed until the loop wakes for an
        unrelated reason. ``call_soon_threadsafe`` signals the loop correctly.
        """
        loop = self._loop
        if loop is not None and loop.is_running():
            try:
                loop.call_soon_threadsafe(queue.put_nowait, item)
                return
            except RuntimeError:
                pass
        queue.put_nowait(item)

    async def stop(self):
        """Gracefully stops and cancels the worker task."""
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    def pause(self):
        """Pauses the background processing loop."""
        self._pause_event.clear()
        self._status = "paused"

    def resume(self):
        """Resumes the background processing loop."""
        self._pause_event.set()
        self._status = "running"

    def is_paused(self) -> bool:
        """Returns True if the worker is currently paused."""
        return not self._pause_event.is_set()

    def get_status(self) -> dict[str, Any]:
        """Returns the current worker status and progress dictionary."""
        return {"status": self._status}

    async def _run_loop(self):
        """Main execution loop to be implemented by child worker classes."""
        raise NotImplementedError
