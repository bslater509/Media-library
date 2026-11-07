"""The v2 index schema: created here, and converging an old index.

Two shapes must behave identically: an index this package creates, and one the
previous app wrote (extra indexes, ``db_meta``, a legacy People table). These
tests pin both.
"""

from __future__ import annotations

import sqlite3

from fastapi.testclient import TestClient

from media_library_v2 import config as v2_config
from media_library_v2.mount import build_v2_app
from media_library_v2.schema import (
    OBSOLETE_INDEXES,
    init_schema,
    table_columns,
)

FOLDERS_COLUMNS = [
    "path",
    "name",
    "parent_path",
    "file_count",
    "subfolder_count",
    "last_scanned",
    "is_zip",
    "total_size",
    "dir_mtime",
]

FILES_COLUMNS = [
    "path",
    "name",
    "folder_path",
    "type",
    "ext",
    "size",
    "size_formatted",
    "mtime",
    "needs_transcode",
    "has_thumb",
    "width",
    "height",
    "duration",
    "video_codec",
    "audio_codec",
    "fps",
    "capture_time",
    "thumb_mtime",
    "has_animated",
    "quick_hash",
    "thumb_hash",
    "webp_optimized",
]

SLIM_INDEXES = {
    "idx_files_folder",
    "idx_files_folder_lower_name",
    "idx_files_type",
    "idx_files_has_thumb",
    "idx_files_has_animated",
    "idx_files_size",
    "idx_files_quick_hash",
    "idx_files_thumb_hash",
    "idx_files_width",
    "idx_folders_parent",
    "idx_folders_parent_lower_name",
    "idx_ignored_p1_p2",
    "idx_ignored_created_at",
    "idx_favorites_created_at",
}

# The shape the previous app left behind: every index it created (including the
# three v2 cannot use), its db_meta table and a table from a removed feature.
LEGACY_DDL = """
CREATE TABLE folders (
    path TEXT PRIMARY KEY, name TEXT, parent_path TEXT,
    file_count INTEGER DEFAULT 0, subfolder_count INTEGER DEFAULT 0,
    last_scanned REAL DEFAULT 0, is_zip INTEGER DEFAULT 0,
    total_size INTEGER DEFAULT 0, dir_mtime REAL
);
CREATE TABLE files (
    path TEXT PRIMARY KEY, name TEXT, folder_path TEXT, type TEXT, ext TEXT,
    size INTEGER, size_formatted TEXT, mtime REAL,
    needs_transcode INTEGER DEFAULT 0, has_thumb INTEGER DEFAULT 0,
    width INTEGER, height INTEGER, duration REAL, video_codec TEXT,
    audio_codec TEXT, fps REAL, capture_time REAL, thumb_mtime REAL,
    has_animated INTEGER DEFAULT 0, quick_hash TEXT, thumb_hash TEXT,
    webp_optimized INTEGER DEFAULT 0
);
CREATE TABLE webp_stats (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    saved_bytes INTEGER DEFAULT 0, converted_count INTEGER DEFAULT 0,
    skipped_count INTEGER DEFAULT 0
);
CREATE TABLE ignored_duplicates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path1 TEXT NOT NULL, path2 TEXT NOT NULL, match_type TEXT NOT NULL,
    created_at REAL NOT NULL, UNIQUE(path1, path2)
);
CREATE TABLE db_meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE favorites (path TEXT PRIMARY KEY, created_at REAL NOT NULL);
CREATE TABLE people (id INTEGER PRIMARY KEY, name TEXT);
CREATE INDEX idx_files_folder ON files(folder_path);
CREATE INDEX idx_files_folder_lower_name ON files(folder_path, LOWER(name));
CREATE INDEX idx_files_name ON files(name);
CREATE INDEX idx_files_type ON files(type);
CREATE INDEX idx_files_has_thumb ON files(has_thumb);
CREATE INDEX idx_files_has_animated ON files(has_animated);
CREATE INDEX idx_files_size ON files(size);
CREATE INDEX idx_files_quick_hash ON files(quick_hash);
CREATE INDEX idx_files_thumb_hash ON files(thumb_hash);
CREATE INDEX idx_files_webp_optimized ON files(webp_optimized);
CREATE INDEX idx_files_width ON files(width);
CREATE INDEX idx_folders_parent ON folders(parent_path);
CREATE INDEX idx_folders_parent_lower_name ON folders(parent_path, LOWER(name));
CREATE INDEX idx_ignored_p1_p2 ON ignored_duplicates(path1, path2);
CREATE INDEX idx_ignored_match_type ON ignored_duplicates(match_type);
CREATE INDEX idx_ignored_created_at ON ignored_duplicates(created_at DESC);
CREATE INDEX idx_favorites_created_at ON favorites(created_at DESC);
INSERT INTO webp_stats (id, saved_bytes, converted_count, skipped_count)
    VALUES (1, 4096, 7, 2);
INSERT INTO folders (path, name, parent_path, is_zip) VALUES
    ('/lib', 'lib', '/', 0), ('/lib/Trip', 'Trip', '/lib', 0);
INSERT INTO files (path, name, folder_path, type, ext, size)
    VALUES ('/lib/Trip/a.mp4', 'a.mp4', '/lib/Trip', 'video', '.mp4', 100),
           ('/lib/Trip/b.jpg', 'b.jpg', '/lib/Trip', 'image', '.jpg', 50);
INSERT INTO favorites (path, created_at) VALUES ('/lib/Trip/a.mp4', 123.0);
INSERT INTO ignored_duplicates (path1, path2, match_type, created_at)
    VALUES ('/lib/Trip/a.mp4', '/lib/Trip/b.jpg', 'content', 5.0);
INSERT INTO db_meta (key, value) VALUES ('index_version', '1');
INSERT INTO people (id, name) VALUES (1, 'someone');
"""


def _indexes(path) -> set[str]:
    with sqlite3.connect(path) as conn:
        return {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index' "
                "AND name NOT LIKE 'sqlite_%'"
            )
        }


def _scalar(path, sql, params=()):
    with sqlite3.connect(path) as conn:
        return conn.execute(sql, params).fetchone()[0]


def test_fresh_schema_has_every_column_and_index(tmp_path):
    db = tmp_path / "fresh.db"
    report = init_schema(db)

    assert report["created"] is True
    assert report["dropped_indexes"] == []
    columns = table_columns(db)
    assert columns["folders"] == FOLDERS_COLUMNS
    assert columns["files"] == FILES_COLUMNS
    assert columns["favorites"] == ["path", "created_at"]
    assert set(columns) == {
        "favorites",
        "files",
        "folders",
        "ignored_duplicates",
        "webp_stats",
    }
    assert SLIM_INDEXES == _indexes(db)
    assert not (SLIM_INDEXES & set(OBSOLETE_INDEXES))
    assert _scalar(db, "SELECT COUNT(*) FROM webp_stats") == 1


def test_init_is_idempotent(tmp_path):
    db = tmp_path / "fresh.db"
    init_schema(db)
    before = _indexes(db)
    report = init_schema(db)

    assert report["created"] is False
    assert report["dropped_indexes"] == []
    assert _indexes(db) == before


def test_old_index_converges_without_losing_rows(tmp_path):
    db = tmp_path / "legacy.db"
    with sqlite3.connect(db) as conn:
        conn.executescript(LEGACY_DDL)

    report = init_schema(db)

    assert report["created"] is False
    assert set(report["dropped_indexes"]) == set(OBSOLETE_INDEXES)
    indexes = _indexes(db)
    assert SLIM_INDEXES <= indexes
    assert not (set(OBSOLETE_INDEXES) & indexes)

    # No row was touched, no column removed, and the app's tables are intact.
    assert _scalar(db, "SELECT COUNT(*) FROM files") == 2
    assert _scalar(db, "SELECT COUNT(*) FROM folders") == 2
    assert _scalar(db, "SELECT COUNT(*) FROM favorites") == 1
    assert _scalar(db, "SELECT COUNT(*) FROM ignored_duplicates") == 1
    assert _scalar(db, "SELECT saved_bytes FROM webp_stats WHERE id = 1") == 4096
    # The previous app's own tables are left alone rather than migrated.
    assert _scalar(db, "SELECT COUNT(*) FROM db_meta") == 1
    assert _scalar(db, "SELECT COUNT(*) FROM people") == 1
    columns = table_columns(db)
    assert columns["files"] == FILES_COLUMNS


def test_startup_creates_the_schema_for_a_missing_index(tmp_path, monkeypatch):
    """A fresh deployment has no index; the app makes one instead of failing."""
    library = tmp_path / "library"
    thumbs = tmp_path / "thumbs"
    library.mkdir()
    thumbs.mkdir()
    db = tmp_path / "absent" / "index.db"

    monkeypatch.setenv("LIBRARY_DIR", str(library))
    monkeypatch.setenv("V2_LIBRARY_DIR", str(library))
    monkeypatch.setenv("THUMBNAIL_DIR", str(thumbs))
    monkeypatch.setenv("DB_PATH", str(db))
    monkeypatch.setenv("V2_DB_READ_ONLY", "false")
    monkeypatch.setenv("V2_WRITES", "true")
    v2_config.reload_settings()

    from media_library_v2.standalone import build_standalone_app

    with TestClient(build_standalone_app()) as client:
        assert client.get("/api/v2/health").status_code == 200
        assert db.is_file()

    assert "files" in table_columns(db)


def test_api_serves_a_freshly_created_index(tmp_path, monkeypatch):
    """The whole point of the port: v2 must run without the old app's DDL."""
    library = tmp_path / "library"
    thumbs = tmp_path / "thumbs"
    (library / "Trip").mkdir(parents=True)
    thumbs.mkdir()
    (library / "Trip" / "aurora.mp4").write_bytes(b"0123456789" * 100)

    db = tmp_path / "index.db"
    monkeypatch.setenv("LIBRARY_DIR", str(library))
    monkeypatch.setenv("V2_LIBRARY_DIR", str(library))
    monkeypatch.setenv("THUMBNAIL_DIR", str(thumbs))
    monkeypatch.setenv("DB_PATH", str(db))
    monkeypatch.setenv("V2_DB_READ_ONLY", "false")
    monkeypatch.setenv("V2_WRITES", "true")
    v2_config.reload_settings()

    init_schema(db)
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO folders (path, name, parent_path, file_count, "
            "subfolder_count, total_size, is_zip) VALUES (?,?,?,?,?,?,?)",
            (str(library), "library", str(library.parent), 1, 1, 0, 0),
        )
        conn.execute(
            "INSERT INTO folders (path, name, parent_path, file_count, "
            "subfolder_count, total_size, is_zip) VALUES (?,?,?,?,?,?,?)",
            (str(library / "Trip"), "Trip", str(library), 1, 0, 0, 0),
        )
        conn.execute(
            "INSERT INTO files (path, name, folder_path, type, ext, size, "
            "size_formatted, mtime, needs_transcode, has_thumb) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                str(library / "Trip" / "aurora.mp4"),
                "aurora.mp4",
                str(library / "Trip"),
                "video",
                ".mp4",
                1000,
                "1000 B",
                1.0,
                0,
                0,
            ),
        )

    client = TestClient(build_v2_app())
    body = client.get("/api/v2/library/browse", params={"path": str(library)}).json()

    assert [f["name"] for f in body["folders"]] == ["Trip"]
    assert body["total"] == 0  # the root holds no files of its own
    sub = client.get(
        "/api/v2/library/browse", params={"path": str(library / "Trip")}
    ).json()
    assert [f["name"] for f in sub["files"]] == ["aurora.mp4"]
    assert sub["indexed"] is True
