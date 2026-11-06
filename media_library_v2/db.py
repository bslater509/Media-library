"""Read-only SQLite access for v2.

The v2 slice reads the index that v1 maintains. WAL lets both processes read
and write concurrently; v2 opens the file in ``mode=ro`` so it can never
mutate the live database by accident. When the v2 indexer lands, flipping
``V2_DB_READ_ONLY=false`` gives it a writable connection on its own database.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from media_library_v2.config import get_settings


def connect(
    path: Path | None = None, *, read_only: bool | None = None
) -> sqlite3.Connection:
    """Open the v2 database with WAL-friendly, low-latency pragmas."""
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
