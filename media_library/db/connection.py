"""Database connection factory."""

import asyncio
import logging
import os
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, TypeVar

from media_library.core.config import DB_PATH

logger = logging.getLogger(__name__)

T = TypeVar("T")


def get_current_db_path(custom_path: str | Path | None = None) -> Path:
    """Returns the resolved DB Path respecting os.environ['DB_PATH']."""
    if custom_path:
        return Path(custom_path)
    elif os.environ.get("DB_PATH"):
        return Path(os.environ["DB_PATH"]).resolve()
    return DB_PATH


def get_db(custom_path: str | Path | None = None) -> sqlite3.Connection:
    """Returns a SQLite connection configured with WAL mode, optimized pragmas, and row factory."""
    path = get_current_db_path(custom_path)
    conn = sqlite3.connect(path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA synchronous = NORMAL;")
        conn.execute("PRAGMA cache_size = -64000;")
        conn.execute("PRAGMA mmap_size = 268435456;")
        conn.execute("PRAGMA temp_store = MEMORY;")
    except sqlite3.DatabaseError as e:
        # A broken WAL/cache config must not silently pass: surface it, but keep
        # the connection usable so a transient failure doesn't kill the request.
        logger.warning("Failed to apply SQLite pragmas on %s: %s", path, e)
    return conn


@contextmanager
def db_conn(custom_path: str | Path | None = None) -> Iterator[sqlite3.Connection]:
    """Context manager yielding a connection that is always closed on exit.

    Use this for request/scoped work so an exception can never leak the
    connection (AGENTS.md Rule 3). Callers that need manual lifetime control
    (e.g. ``sqlite3_backup``) should keep using :func:`get_db`.
    """
    conn = get_db(custom_path)
    try:
        yield conn
    finally:
        conn.close()


async def run_db_write(fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """Run a synchronous, database-writing callable in a worker thread.

    SQLite admits one writer at a time, and a connection waits for that write
    lock for its whole ``timeout`` (30s here) before raising ``database is
    locked``. Awaiting such a write on the event loop therefore stalls *every*
    request for the duration: the server stops accepting, pages stop loading,
    and nothing is logged because the loop never gets back to the socket. That
    is exactly how a contended write used to take the whole site down.

    Always route worker-loop and request-handler mutations through this helper
    (or an equivalent ``asyncio.to_thread`` call) — reads are fine on the loop
    because WAL lets them proceed alongside a writer.
    """
    return await asyncio.to_thread(fn, *args, **kwargs)
