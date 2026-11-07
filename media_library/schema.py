"""Schema creation and convergence for the index.

The DDL that used to live in the previous app's package lives here. It is
deliberately **not** a byte-for-byte port:

* every table and every column the application reads is created verbatim (a
  column is only dropped from this file when no query touches it, and all
  31 ``folders``/``files`` columns are touched);
* the indexes no query can use are not created — ``idx_files_name`` (search
  uses ``LIKE '%…%'``, which cannot seek), ``idx_files_webp_optimized`` (every
  predicate wraps the column in ``COALESCE``) and ``idx_ignored_match_type``
  (``match_type`` is only ever selected or inserted) — and ``DROP INDEX IF
  EXISTS`` removes them from an upgraded index so both shapes converge;
* the removed People tables, the one-shot face-artifact purge and the
  ``UPDATE files SET webp_optimized = 1 WHERE ext = '.webp'`` backfill that
  re-ran on every previous startup are gone. An index upgraded from a
  People-era build keeps those dead tables; they are inert.

Only ``CREATE … IF NOT EXISTS`` / ``DROP INDEX IF EXISTS`` and one seeded
``webp_stats`` row run here: no user data is ever read, rewritten or deleted,
so an existing library keeps every row.

Callers on the event loop must run this in a worker thread (AGENTS.md Rule 3):
``await asyncio.to_thread(init_schema)``.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

# Indexes the previous app created that no query can use. Dropped so an
# upgraded index and a freshly created one have the same shape.
OBSOLETE_INDEXES = (
    "idx_files_name",
    "idx_files_webp_optimized",
    "idx_ignored_match_type",
)

_TABLES = (
    """
    CREATE TABLE IF NOT EXISTS folders (
        path TEXT PRIMARY KEY,
        name TEXT,
        parent_path TEXT,
        file_count INTEGER DEFAULT 0,
        subfolder_count INTEGER DEFAULT 0,
        last_scanned REAL DEFAULT 0,
        is_zip INTEGER DEFAULT 0,
        total_size INTEGER DEFAULT 0,
        dir_mtime REAL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS files (
        path TEXT PRIMARY KEY,
        name TEXT,
        folder_path TEXT,
        type TEXT,
        ext TEXT,
        size INTEGER,
        size_formatted TEXT,
        mtime REAL,
        needs_transcode INTEGER DEFAULT 0,
        has_thumb INTEGER DEFAULT 0,
        width INTEGER,
        height INTEGER,
        duration REAL,
        video_codec TEXT,
        audio_codec TEXT,
        fps REAL,
        capture_time REAL,
        thumb_mtime REAL,
        has_animated INTEGER DEFAULT 0,
        quick_hash TEXT,
        thumb_hash TEXT,
        webp_optimized INTEGER DEFAULT 0
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS webp_stats (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        saved_bytes INTEGER DEFAULT 0,
        converted_count INTEGER DEFAULT 0,
        skipped_count INTEGER DEFAULT 0
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS ignored_duplicates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        path1 TEXT NOT NULL,
        path2 TEXT NOT NULL,
        match_type TEXT NOT NULL,
        created_at REAL NOT NULL,
        UNIQUE(path1, path2)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS favorites (
        path TEXT PRIMARY KEY,
        created_at REAL NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS visual_frames (
        path TEXT PRIMARY KEY,
        mtime REAL,
        size INTEGER,
        duration REAL,
        hashes TEXT NOT NULL,
        captured_at REAL NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS watch_history (
        path TEXT PRIMARY KEY,
        first_viewed_at REAL NOT NULL,
        last_viewed_at REAL NOT NULL,
        seconds_watched REAL NOT NULL,
        play_count INTEGER NOT NULL
    )
    """,
)

# Every index below backs a predicate or ordering in this package; see the
# module docstring for the three that were deliberately left out.
_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_files_folder ON files(folder_path)",
    "CREATE INDEX IF NOT EXISTS idx_files_folder_lower_name ON files(folder_path, LOWER(name))",
    "CREATE INDEX IF NOT EXISTS idx_files_type ON files(type)",
    "CREATE INDEX IF NOT EXISTS idx_files_has_thumb ON files(has_thumb)",
    "CREATE INDEX IF NOT EXISTS idx_files_has_animated ON files(has_animated)",
    "CREATE INDEX IF NOT EXISTS idx_files_size ON files(size)",
    "CREATE INDEX IF NOT EXISTS idx_files_quick_hash ON files(quick_hash)",
    "CREATE INDEX IF NOT EXISTS idx_files_thumb_hash ON files(thumb_hash)",
    "CREATE INDEX IF NOT EXISTS idx_files_width ON files(width)",
    "CREATE INDEX IF NOT EXISTS idx_folders_parent ON folders(parent_path)",
    "CREATE INDEX IF NOT EXISTS idx_folders_parent_lower_name ON folders(parent_path, LOWER(name))",
    "CREATE INDEX IF NOT EXISTS idx_ignored_p1_p2 ON ignored_duplicates(path1, path2)",
    "CREATE INDEX IF NOT EXISTS idx_ignored_created_at ON ignored_duplicates(created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_favorites_created_at ON favorites(created_at DESC)",
    (
        "CREATE INDEX IF NOT EXISTS idx_watch_history_last_viewed "
        "ON watch_history(last_viewed_at DESC)"
    ),
)

_SEED = (
    (
        "INSERT OR IGNORE INTO webp_stats "
        "(id, saved_bytes, converted_count, skipped_count) VALUES (1, 0, 0, 0)"
    ),
)


def init_schema(path: str | Path | None = None) -> dict[str, object]:
    """Create any missing tables/indexes and drop the obsolete indexes.

    Idempotent and non-destructive: safe to call on every start, on a fresh
    file and on an index written by the previous app. Returns a small report
    for the startup log.
    """
    from media_library.config import get_settings

    target = Path(path) if path is not None else get_settings().db_path
    target.parent.mkdir(parents=True, exist_ok=True)
    had_tables = _had_tables(target)

    conn = sqlite3.connect(str(target), timeout=30.0)
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        dropped: list[str] = []
        with conn:
            for statement in _TABLES:
                conn.execute(statement)
            for statement in _INDEXES:
                conn.execute(statement)
            for statement in _SEED:
                conn.execute(statement)
            for name in OBSOLETE_INDEXES:
                exists = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'index' AND name = ?",
                    (name,),
                ).fetchone()
                conn.execute(f"DROP INDEX IF EXISTS {name}")
                if exists:
                    dropped.append(name)
        return {
            "path": str(target),
            "created": not had_tables,
            "dropped_indexes": dropped,
        }
    finally:
        conn.close()


def _had_tables(path: Path) -> bool:
    """Best-effort check used only for the startup log."""
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2.0)
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table'"
            ).fetchone()
            return bool(row and row[0])
        finally:
            conn.close()
    except sqlite3.Error:
        return False


def table_columns(path: str | Path | None = None) -> dict[str, list[str]]:
    """Return ``{table: [column, …]}`` for the tables this schema owns."""
    from media_library.config import get_settings

    target = Path(path) if path is not None else get_settings().db_path
    conn = sqlite3.connect(f"file:{target}?mode=ro", uri=True, timeout=10.0)
    try:
        tables = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name IN ('folders', 'files', 'webp_stats', "
                "'ignored_duplicates', 'favorites', 'visual_frames', "
                "'watch_history') ORDER BY name"
            )
        ]
        return {
            table: [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
            for table in tables
        }
    finally:
        conn.close()
