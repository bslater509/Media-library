"""Database schema creation and migrations."""

import sqlite3
from pathlib import Path

from media_library.db.connection import db_conn


def init_backup_db(custom_path: str | Path | None = None):
    """Initializes SQLite schema, tables, and indexes for media indexing and metadata."""
    with db_conn(custom_path) as conn:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("""
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
        )""")
        try:
            conn.execute("ALTER TABLE folders ADD COLUMN is_zip INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE folders ADD COLUMN total_size INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        try:
            # Directory/ZIP mtime recorded at scan time. The startup crawler
            # skips a folder whose mtime still matches this, so restarts do not
            # re-stat and rewrite an unchanged (often remote FUSE) tree.
            conn.execute("ALTER TABLE folders ADD COLUMN dir_mtime REAL")
        except sqlite3.OperationalError:
            pass
        conn.execute("""
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
            has_animated INTEGER DEFAULT 0
        )""")
        try:
            conn.execute("ALTER TABLE files ADD COLUMN has_animated INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE files ADD COLUMN has_thumb INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE files ADD COLUMN width INTEGER")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE files ADD COLUMN height INTEGER")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE files ADD COLUMN duration REAL")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE files ADD COLUMN video_codec TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE files ADD COLUMN audio_codec TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE files ADD COLUMN fps REAL")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE files ADD COLUMN capture_time REAL")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE files ADD COLUMN quick_hash TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE files ADD COLUMN thumb_hash TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE files ADD COLUMN thumb_mtime REAL")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute(
                "ALTER TABLE files ADD COLUMN webp_optimized INTEGER DEFAULT 0"
            )
        except sqlite3.OperationalError:
            pass

        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_files_folder ON files(folder_path)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_files_folder_lower_name ON files(folder_path, LOWER(name))"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_files_name ON files(name)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_files_type ON files(type)")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_files_has_thumb ON files(has_thumb)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_files_has_animated ON files(has_animated)"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_files_size ON files(size)")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_files_quick_hash ON files(quick_hash)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_files_thumb_hash ON files(thumb_hash)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_files_webp_optimized ON files(webp_optimized)"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_files_width ON files(width)")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_folders_parent ON folders(parent_path)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_folders_parent_lower_name ON folders(parent_path, LOWER(name))"
        )

        conn.execute("""
        CREATE TABLE IF NOT EXISTS webp_stats (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            saved_bytes INTEGER DEFAULT 0,
            converted_count INTEGER DEFAULT 0,
            skipped_count INTEGER DEFAULT 0
        )""")
        conn.execute(
            "INSERT OR IGNORE INTO webp_stats (id, saved_bytes, converted_count, skipped_count) VALUES (1, 0, 0, 0)"
        )
        conn.execute(
            "UPDATE files SET webp_optimized = 1 WHERE ext = '.webp' AND webp_optimized = 0"
        )

        conn.execute("""
        CREATE TABLE IF NOT EXISTS ignored_duplicates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            path1 TEXT NOT NULL,
            path2 TEXT NOT NULL,
            match_type TEXT NOT NULL,
            created_at REAL NOT NULL,
            UNIQUE(path1, path2)
        )""")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ignored_p1_p2 ON ignored_duplicates(path1, path2)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ignored_match_type ON ignored_duplicates(match_type)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ignored_created_at ON ignored_duplicates(created_at DESC)"
        )

        conn.execute("""
        CREATE TABLE IF NOT EXISTS db_meta (
            key TEXT PRIMARY KEY,
            value TEXT
        )""")

        conn.execute("""
        CREATE TABLE IF NOT EXISTS favorites (
            path TEXT PRIMARY KEY,
            created_at REAL NOT NULL
        )""")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_favorites_created_at ON favorites(created_at DESC)"
        )

        # ---- Face recognition ("People") -------------------------------------
        # A cluster of visually-similar faces the user can name. Counts are never
        # stored: they are derived with GROUP BY so a move/delete/re-scan cannot
        # leave a stale counter behind.
        conn.execute("""
        CREATE TABLE IF NOT EXISTS people (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            created_at REAL NOT NULL,
            updated_at REAL
        )""")
        # One detected face. ``frame_time`` is -1.0 for images and the seconds
        # offset for a video hit (never NULL, so the UNIQUE key below is exact:
        # SQLite treats NULLs as distinct and would accept duplicates).
        # ``embedding`` is 128 little-endian float32 (512 bytes), L2-normalised.
        conn.execute("""
        CREATE TABLE IF NOT EXISTS faces (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_path TEXT NOT NULL,
            frame_time REAL NOT NULL DEFAULT -1.0,
            x INTEGER, y INTEGER, w INTEGER, h INTEGER,
            score REAL,
            embedding BLOB,
            person_id INTEGER,
            distance REAL,
            crop TEXT,
            created_at REAL NOT NULL,
            UNIQUE(file_path, frame_time, x, y)
        )""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_faces_person ON faces(person_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_faces_file ON faces(file_path)")
        # Which files have already been scanned (including ones with no faces at
        # all, which have no rows in ``faces`` and would otherwise be re-scanned
        # forever). Keyed on path + the mtime/size the result is valid for.
        conn.execute("""
        CREATE TABLE IF NOT EXISTS face_scans (
            file_path TEXT PRIMARY KEY,
            mtime REAL,
            size INTEGER,
            faces_found INTEGER DEFAULT 0,
            scanned_at REAL NOT NULL
        )""")
        # Faces the user rejected as "not a person": kept as a rejection so a
        # later re-scan of the same file does not re-cluster them. The box is
        # what identifies the detection, and the embedding is what survives a
        # change in how frames are sampled (a denser grid moves the timestamp
        # and box of the representative detection of the same face).
        conn.execute("""
        CREATE TABLE IF NOT EXISTS dismissed_faces (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_path TEXT NOT NULL,
            frame_time REAL NOT NULL DEFAULT -1.0,
            x INTEGER, y INTEGER,
            embedding BLOB,
            created_at REAL NOT NULL,
            UNIQUE(file_path, frame_time, x, y)
        )""")
        try:
            conn.execute("ALTER TABLE dismissed_faces ADD COLUMN embedding BLOB")
        except sqlite3.OperationalError:
            pass
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_dismissed_file ON dismissed_faces(file_path)"
        )

        conn.commit()
