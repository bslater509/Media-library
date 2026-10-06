"""Base background worker manager defining standard lifecycle methods."""

import asyncio
from typing import Dict, Any, Optional

class BaseWorker:
    """Abstract base worker encapsulating lifecycle, pause/resume, and status reporting."""

    def __init__(self, name: str):
        self.name = name
        self._task: Optional[asyncio.Task] = None
        self._pause_event = asyncio.Event()
        self._pause_event.set()
        self._status: str = "idle"

    async def start(self):
        """Starts the background worker task if not already running."""
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run_loop())

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

    def get_status(self) -> Dict[str, Any]:
        """Returns the current worker status and progress dictionary."""
        return {"status": self._status}

    async def _run_loop(self):
        """Main execution loop to be implemented by child worker classes."""
        raise NotImplementedError
