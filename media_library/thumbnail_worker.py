"""Background poster sweep (part of the owned-worker runtime)."""

from __future__ import annotations

import threading
from typing import Any

from media_library import thumbnails
from media_library.db import reader, writer
from media_library.paths import is_zip_member
from media_library.thumbnails import generate_thumbnail


class ThumbnailSweep:
    def __init__(self, batch: int = 10) -> None:
        self.batch = batch
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def run_once(self) -> dict[str, int]:
        with reader() as conn:
            rows = conn.execute(
                "SELECT path, type, mtime, size FROM files "
                "WHERE has_thumb = 0 AND type IN ('video', 'image') "
                "ORDER BY path LIMIT ?",
                (self.batch,),
            ).fetchall()
        generated = 0
        failed = 0
        for row in rows:
            path = row["path"]
            if is_zip_member(path):
                with writer() as conn, conn:
                    conn.execute(
                        "UPDATE files SET has_thumb = -1 WHERE path = ?", (path,)
                    )
                failed += 1
                continue
            try:
                generate_thumbnail(
                    path, row["type"], float(row["mtime"] or 0), int(row["size"] or 0)
                )
                generated += 1
            except Exception:
                failed += 1
                # Mark the failure against the mtime/size this attempt was for.
                # A file that changed underneath gets its fresh row retried.
                thumbnails.mark_has_thumb(
                    path, float(row["mtime"] or 0), int(row["size"] or 0), -1
                )
        with reader() as conn:
            remaining = conn.execute(
                "SELECT COUNT(*) AS n FROM files WHERE has_thumb = 0 "
                "AND type IN ('video', 'image')"
            ).fetchone()["n"]
        return {
            "generated": generated,
            "failed": failed,
            "remaining": int(remaining or 0),
        }

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="thumbnails", daemon=True
        )
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                result = self.run_once()
            except Exception:
                result = {"remaining": 0}
            if not result.get("remaining"):
                if self._stop.wait(5):
                    break
            elif self._stop.wait(0.2):
                break

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)


def status(worker: ThumbnailSweep | None) -> dict[str, Any]:
    return {"running": bool(worker and worker._thread and worker._thread.is_alive())}
