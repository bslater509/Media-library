"""SQLite access: one reader and one guarded writer.

The index is a single WAL database. Readers open it read-only so a stray query
can never mutate it, and every mutation goes through :func:`writer`, which
refuses when the app is configured read-only. Callers must run a write off the
event loop (``await asyncio.to_thread(...)``) — see AGENTS.md Rule 3.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from media_library.config import get_settings


def connect(
    path: Path | None = None, *, read_only: bool | None = None
) -> sqlite3.Connection:
    """Open the index with WAL-friendly, low-latency pragmas."""
    settings = get_settings()
    target = Path(path) if path is not None else settings.db_path
    ro = settings.db_read_only if read_only is None else read_only
    if ro:
        conn = sqlite3.connect(
            f"file:{target}?mode=ro",
            uri=True,
            timeout=10.0,
            check_same_thread=False,
        )
        conn.execute("PRAGMA query_only = ON;")
    else:
        conn = sqlite3.connect(target, timeout=30.0, check_same_thread=False)
        conn.execute("PRAGMA synchronous = NORMAL;")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000;")
    conn.execute("PRAGMA temp_store = MEMORY;")
    return conn


@contextmanager
def reader() -> Iterator[sqlite3.Connection]:
    """Yield a read-only connection, always closed on exit."""
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


class DatabaseReadOnlyError(RuntimeError):
    """Raised when a mutation is attempted while the index is read-only."""


def writes_available() -> bool:
    settings = get_settings()
    return settings.writes_enabled and not settings.db_read_only


@contextmanager
def writer() -> Iterator[sqlite3.Connection]:
    """Yield a writable connection, or raise when writes are disabled.

    Callers run the whole transaction off the event loop (AGENTS.md Rule 3):
    every ``writes.py`` function is synchronous and invoked via
    ``asyncio.to_thread`` from the router.
    """
    if not writes_available():
        raise DatabaseReadOnlyError("Writes are disabled (V2_WRITES / V2_DB_READ_ONLY)")
    conn = connect(read_only=False)
    try:
        yield conn
    finally:
        conn.close()


def is_available() -> bool:
    """True when the index file exists and answers a trivial query."""
    settings = get_settings()
    if not settings.db_path.exists():
        return False
    try:
        with reader() as conn:
            conn.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone()
        return True
    except sqlite3.DatabaseError:
        return False
