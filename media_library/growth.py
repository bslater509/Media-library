"""Library growth history: one rollup row per UTC calendar day.

``growth_history`` records how large the indexed library was on each day —
file, video and image counts plus total bytes — so the analytics dashboard can
chart growth over time. A same-day recompute upserts over the row.

The owned worker (started by :mod:`media_library.runtime` only when
``OWN_WORKERS=true``) records today's row on start and then wakes up on a
coarse cadence, upserting again whenever the UTC date rolls over (and, at most
once per cadence, when it has not). All numbers are index-only: no directory
walk, no ``os.stat`` (AGENTS.md Rule 2); the write is synchronous on the
worker thread, never on the event loop (Rule 3).
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any

from media_library import activity
from media_library.db import DatabaseReadOnlyError, reader, writer

logger = logging.getLogger(__name__)

# Coarse cadence: the worker notices day rollover (and refreshes today's row)
# within half an hour at the latest, without churning an idle index.
WAKE_SECONDS = 25 * 60.0


def current_day(now: float | None = None) -> str:
    """The UTC calendar day of ``now``, formatted ``YYYY-MM-DD``."""
    ts = time.time() if now is None else now
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def sample_counts(conn) -> dict[str, int]:
    """Index-only rollup of the whole ``files`` table.

    Mirrors the dashboard's totals (``SELECT COUNT(*)`` / ``SUM(type = …)`` /
    ``SUM(size)``); unlike the overview it is never cached — one statement, no
    remote I/O.
    """
    row = conn.execute(
        "SELECT COUNT(*) AS files, "
        "COALESCE(SUM(type = 'video'), 0) AS videos, "
        "COALESCE(SUM(type = 'image'), 0) AS images, "
        "COALESCE(SUM(size), 0) AS bytes "
        "FROM files"
    ).fetchone()
    return {
        "files_count": int(row["files"] or 0),
        "video_count": int(row["videos"] or 0),
        "image_count": int(row["images"] or 0),
        "bytes_total": int(row["bytes"] or 0),
    }


def live_counts() -> dict[str, int]:
    """Sample the index through a read-only connection, always closed."""
    with reader() as conn:
        return sample_counts(conn)


@activity.tracked("growth")
def record_today(now: float | None = None) -> dict[str, Any]:
    """Compute today's rollup and upsert the day's row.

    Synchronous: the caller runs it off the event loop (AGENTS.md Rule 3).
    Raises :class:`DatabaseReadOnlyError` when the index is read-only — the
    refresh route turns that into 503 and the owned worker skips it without
    crash-looping. Frames the day from ``now`` so tests can inject time.
    """
    ts = time.time() if now is None else now
    day = current_day(ts)
    counts = live_counts()
    with writer() as conn, conn:
        conn.execute(
            """
            INSERT INTO growth_history
                (day, files_count, video_count, image_count,
                 bytes_total, recorded_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(day) DO UPDATE SET
                files_count = excluded.files_count,
                video_count = excluded.video_count,
                image_count = excluded.image_count,
                bytes_total = excluded.bytes_total,
                recorded_at = excluded.recorded_at
            """,
            (
                day,
                counts["files_count"],
                counts["video_count"],
                counts["image_count"],
                counts["bytes_total"],
                ts,
            ),
        )
    return {
        "day": day,
        **counts,
        "recorded_at": ts,
        "live": False,
    }


def read_history(days: int = 30) -> dict[str, Any]:
    """Recorded days capped to ``days`` (oldest first) plus today's live counts.

    A recorded row for today is folded away in favour of the live counts, so
    a chart's final point is fresh; the recorded history is untouched.
    """
    today = current_day()
    with reader() as conn:
        rows = conn.execute(
            "SELECT day, files_count, video_count, image_count, bytes_total, "
            "recorded_at FROM growth_history WHERE day < ? ORDER BY day ASC",
            (today,),
        ).fetchall()
        counts = sample_counts(conn)
    history = [
        {
            "day": row["day"],
            "files_count": int(row["files_count"]),
            "video_count": int(row["video_count"]),
            "image_count": int(row["image_count"]),
            "bytes_total": int(row["bytes_total"]),
            "recorded_at": float(row["recorded_at"]),
            "live": False,
        }
        for row in rows[-max(1, days) :]
    ]
    return {
        "days": days,
        "history": history,
        "today": {"day": today, **counts, "recorded_at": None, "live": True},
    }


def should_record(
    last_recorded_day: str | None,
    last_success_at: float | None,
    now: float | None = None,
    min_interval: float = WAKE_SECONDS,
) -> bool:
    """Whether the worker should record now.

    Pure and time-injectable: never recorded → yes; first record of a new UTC
    day → yes; otherwise at most once per ``min_interval``, so a same-day
    upsert keeps today's row honest without grinding on an idle index.
    """
    ts = time.time() if now is None else now
    if last_recorded_day != current_day(ts):
        return True
    if last_success_at is None:
        return True
    return ts - last_success_at >= min_interval


class GrowthWorker:
    """Owned daily-rollup thread: record today, then keep the day current."""

    def __init__(self, wake_seconds: float = WAKE_SECONDS) -> None:
        self.wake_seconds = max(1.0, wake_seconds)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.recorded_day: str | None = None
        self.last_result: dict[str, Any] | None = None
        self.last_success_at: float | None = None
        self.last_error: str | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="growth-history", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def record(self, now: float | None = None) -> dict[str, Any]:
        """Record today's row and note it in the worker state."""
        result = record_today(now)
        ts = time.time() if now is None else now
        self.recorded_day = result["day"]
        self.last_result = result
        self.last_success_at = ts
        self.last_error = None
        return result

    def tick(self, now: float | None = None) -> bool:
        """Record today when a new day is seen / the cadence elapsed.

        Time-injectable so a test can walk the worker across a rollover
        without a single real sleep. True when a row was written.
        """
        if not should_record(
            self.recorded_day, self.last_success_at, now, self.wake_seconds
        ):
            return False
        self.record(now)
        return True

    def safe_tick(self, now: float | None = None) -> bool:
        """A ``tick`` that never raises: read-only and transient DB failures
        are logged and remembered, not crash-looped (like the backup thread)."""
        try:
            return self.tick(now)
        except DatabaseReadOnlyError as exc:
            self.last_error = str(exc)
            logger.info("[api] growth rollup skipped: %s", exc)
            return False
        except Exception as exc:
            self.last_error = str(exc)
            logger.warning("[api] growth rollup failed: %s", exc)
            return False

    def _loop(self) -> None:
        # Compute and upsert today's row right away, then keep waking up.
        self.safe_tick()
        while not self._stop.wait(self.wake_seconds):
            self.safe_tick()

    def status(self) -> dict[str, Any]:
        """Small worker snapshot for its feature status route."""
        return {
            "started": bool(self._thread and self._thread.is_alive()),
            "recorded_day": self.recorded_day,
            "last_success_at": self.last_success_at,
            "last_error": self.last_error,
        }


_worker: GrowthWorker | None = None


def register_worker(worker: GrowthWorker | None) -> None:
    """The runtime registers (or clears) the process-wide worker instance."""
    global _worker
    _worker = worker


def worker_status() -> dict[str, Any] | None:
    """Snapshot of the owned worker while it runs, ``None`` otherwise."""
    if _worker is None:
        return None
    return _worker.status()
