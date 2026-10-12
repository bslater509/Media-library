"""Extra coverage for the library snapshot backup/restore service.

Exercises the branches the existing backup tests do not reach: thumbnail cache
sync, corrupt metadata handling, the VACUUM INTO fallback, mid-operation file
disappearances, thumbnail tar failures, restore config warnings, and the
auto-restore decision matrix.
"""

import hashlib
import os
import sqlite3
import tarfile
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest

from media_library.db.connection import db_conn
from media_library.services import backup_service as bs


@pytest.fixture
def backup_dir(tmp_path, monkeypatch):
    """Point the backup service at a sandboxed backup directory."""
    bck = tmp_path / "bck"
    bck.mkdir()
    monkeypatch.setattr(bs, "LIBRARY_BACKUP_DIR", str(bck))
    monkeypatch.setattr(bs, "_backup_meta_cache", None)
    monkeypatch.setattr(bs, "_backup_meta_cache_time", 0.0)
    return bck


def _insert_file_row(path, name, folder, *, mtime=10.0, size=32, has_thumb=0):
    with db_conn() as conn, conn:
        conn.execute(
            """INSERT INTO files (path, name, folder_path, type, ext, size,
                   size_formatted, mtime, has_thumb)
               VALUES (?, ?, ?, 'video', '.mp4', ?, ?, ?, ?)""",
            (str(path), name, str(folder), size, f"{size} B", mtime, has_thumb),
        )


def _thumb_key(path, mtime, size):
    key = f"{path}_{mtime}_{size}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]


# ==============================================================================
# sync_thumbnail_db_cache
# ==============================================================================


def test_sync_thumbnail_db_cache_matches_files(sandbox_env, monkeypatch):
    from media_library.services import backup_service as svc

    media = sandbox_env["media_dir"]
    thumb_dir = sandbox_env["thumb_dir"]

    matched = media / "matched.mp4"
    matched.write_bytes(b"\x00" * 32)
    unmatched = media / "unmatched.mp4"
    unmatched.write_bytes(b"\x00" * 32)
    _insert_file_row(matched, "matched.mp4", media, mtime=10.0, size=32)
    _insert_file_row(unmatched, "unmatched.mp4", media, mtime=11.0, size=32)

    (thumb_dir / f"thumb_{_thumb_key(str(matched), 10.0, 32)}.jpg").write_bytes(
        b"\xff\xd8\xff"
    )
    svc.sync_thumbnail_db_cache()

    with db_conn() as conn:
        rows = {
            r["name"]: r["has_thumb"]
            for r in conn.execute("SELECT name, has_thumb FROM files").fetchall()
        }
    assert rows["matched.mp4"] == 1
    assert rows["unmatched.mp4"] == 0

    # No thumbnail directory yet: the sync bails out without touching the DB.
    monkeypatch.setattr(svc, "THUMBNAIL_CACHE_DIR", thumb_dir / "missing_dir")
    svc.sync_thumbnail_db_cache()

    # A database failure is swallowed rather than raised.
    with patch("media_library.services.backup_service.db_conn", side_effect=RuntimeError("db down")):
        svc.sync_thumbnail_db_cache()


# ==============================================================================
# get_existing_backup_meta
# ==============================================================================


def test_get_existing_backup_meta_handles_corrupt_json(sandbox_env, backup_dir):
    (backup_dir / "backup_meta.json").write_text("{not valid json", encoding="utf-8")
    assert bs.get_existing_backup_meta(force_refresh=True) is None
    # A failed read clears any previously cached metadata.
    assert bs._backup_meta_cache is None


def test_get_existing_backup_meta_caches_result(sandbox_env, backup_dir):
    import json

    payload = {"timestamp": 123.0, "file_count": 7}
    (backup_dir / "backup_meta.json").write_text(json.dumps(payload), encoding="utf-8")

    assert bs.get_existing_backup_meta(force_refresh=True) == payload
    # Cached: the file is removed but the cached copy is still returned.
    (backup_dir / "backup_meta.json").unlink()
    assert bs.get_existing_backup_meta() == payload


def test_get_existing_backup_meta_missing_file(sandbox_env, backup_dir):
    assert bs.get_existing_backup_meta(force_refresh=True) is None


# ==============================================================================
# _create_library_backup_sync
# ==============================================================================


class _NoVacuumConn:
    """Connection proxy that makes ``VACUUM INTO`` fail so the fallback runs."""

    def __init__(self, real):
        self._real = real

    def execute(self, sql, *args):
        if "VACUUM INTO" in sql.upper():
            raise sqlite3.OperationalError("simulated VACUUM failure")
        return self._real.execute(sql, *args)

    def backup(self, target):
        return self._real.backup(target)

    def close(self):
        return self._real.close()


@contextmanager
def _proxy_db_conn():
    from media_library.db.connection import db_conn as real_db_conn

    with real_db_conn() as conn:
        yield _NoVacuumConn(conn)


def test_create_backup_success_with_vacuum_fallback(sandbox_env, backup_dir):
    media = sandbox_env["media_dir"]
    thumb_dir = sandbox_env["thumb_dir"]

    clip = media / "clip.mp4"
    clip.write_bytes(b"\x00" * 64)
    _insert_file_row(clip, "clip.mp4", media, mtime=5.0, size=64, has_thumb=1)
    with db_conn() as conn, conn:
        conn.execute(
            """INSERT INTO folders (path, name, parent_path, file_count,
                   subfolder_count, last_scanned)
               VALUES (?, ?, ?, 1, 0, 0.0)""",
            (str(media), "media", "",),
        )
    for i in range(2):
        (thumb_dir / f"thumb_existing_{i}.jpg").write_bytes(b"\xff\xd8" * 8)

    messages = []
    with patch("media_library.services.backup_service.db_conn", _proxy_db_conn):
        result = bs._create_library_backup_sync(
            progress_cb=lambda msg, pct: messages.append((msg, pct))
        )

    assert result["success"] is True
    meta = result["meta"]
    assert meta["file_count"] == 1
    assert meta["folder_count"] == 1
    assert meta["disk_thumb_count"] == 2
    assert meta["total_size_bytes"] > 0

    assert (backup_dir / "backup_index.db").exists()
    assert (backup_dir / "thumbnails.tar").exists()
    assert (backup_dir / "backup_meta.json").exists()
    assert any("VACUUM" not in m for m, _ in messages)
    assert messages[0][0] == "Creating SQLite snapshot..."
    assert messages[-1] == ("Backup complete", 100.0)

    # The snapshot database really is a usable copy of the index.
    with sqlite3.connect(str(backup_dir / "backup_index.db")) as snap:
        assert snap.execute("SELECT count(*) FROM files").fetchone()[0] == 1

    assert bs.library_backup_state["backup_exists"] is True
    assert bs.library_backup_state["file_count"] == 1


def test_create_backup_survives_disappearing_thumbs(sandbox_env, backup_dir):
    """A thumb file that vanishes mid-archive (or fails to add) is skipped."""
    thumb_dir = sandbox_env["thumb_dir"]
    vanishing = thumb_dir / "thumb_vanish.jpg"
    vanishing.write_bytes(b"\xff\xd8" * 4)
    failing = thumb_dir / "thumb_fail.jpg"
    failing.write_bytes(b"\xff\xd8" * 4)

    real_open = tarfile.open
    calls = {"add": 0}

    class _FlakyTar:
        def __init__(self, real):
            self._real = real

        def add(self, name, arcname=None):
            calls["add"] += 1
            if calls["add"] == 1:
                # Simulate a file vanishing between glob and add.
                vanishing.unlink()
            if str(name).endswith("thumb_fail.jpg"):
                raise OSError("FUSE read error")
            return self._real.add(name, arcname=arcname)

        def __enter__(self):
            self._real.__enter__()
            return self

        def __exit__(self, *exc):
            return self._real.__exit__(*exc)

    def flaky_open(*args, **kwargs):
        return _FlakyTar(real_open(*args, **kwargs))

    with patch("media_library.services.backup_service.tarfile.open", flaky_open):
        result = bs._create_library_backup_sync()

    assert result["success"] is True
    assert result["meta"]["disk_thumb_count"] == 2


def test_create_backup_reports_missing_parent(sandbox_env, monkeypatch):
    monkeypatch.setattr(
        bs, "LIBRARY_BACKUP_DIR", str(Path(sandbox_env["base_dir"]) / "gone" / "bck")
    )
    result = bs._create_library_backup_sync()
    assert result["success"] is False
    assert "does not exist" in result["error"]


def test_create_backup_failure_cleans_staging(sandbox_env, backup_dir):
    """A copy failure mid-backup is reported and staging files are removed."""

    def broken_copyfile(src, dst, *args, **kwargs):
        if str(dst).startswith(str(backup_dir)):
            raise OSError("remote mount went away")
        return real_copyfile(src, dst, *args, **kwargs)

    real_copyfile = bs.shutil.copyfile
    with patch("media_library.services.backup_service.shutil.copyfile", broken_copyfile):
        result = bs._create_library_backup_sync()

    assert result["success"] is False
    assert "remote mount went away" in result["error"]
    leftovers = [
        p.name
        for p in Path(os.environ["BACKUP_STAGING_DIR"]).iterdir()
        if p.name.startswith(("temp_db_", "temp_thumbs_"))
    ]
    assert leftovers == []


# ==============================================================================
# _restore_library_backup_sync
# ==============================================================================


def _make_backup():
    result = bs._create_library_backup_sync()
    assert result["success"] is True, result


def test_restore_no_backup_returns_error(sandbox_env, backup_dir):
    result = bs._restore_library_backup_sync()
    assert result["success"] is False
    assert "No valid database backup" in result["error"]


def test_restore_round_trip_with_progress(sandbox_env, backup_dir, monkeypatch):
    media = sandbox_env["media_dir"]
    thumb_dir = sandbox_env["thumb_dir"]
    cfg_tmp = sandbox_env["base_dir"] / "restored_config.json"
    cfg_tmp.write_text('{"recent_destinations": [], "source_dir": ""}', encoding="utf-8")
    monkeypatch.setattr(bs, "CONFIG_FILE", cfg_tmp)
    monkeypatch.setattr("media_library.core.config.CONFIG_FILE", cfg_tmp)

    clip = media / "clip.mp4"
    clip.write_bytes(b"\x00" * 64)
    _insert_file_row(clip, "clip.mp4", media, mtime=5.0, size=64, has_thumb=1)
    (thumb_dir / "thumb_restored.jpg").write_bytes(b"\xff\xd8" * 8)
    _make_backup()

    # Wipe the live index and thumbnails to simulate data loss.
    with db_conn() as conn, conn:
        conn.execute("DELETE FROM files")
    for p in thumb_dir.glob("thumb_*.jpg"):
        p.unlink()

    messages = []
    result = bs._restore_library_backup_sync(
        progress_cb=lambda msg, pct: messages.append((msg, pct))
    )
    assert result["success"] is True
    assert result["restored_files"] == 1
    assert (thumb_dir / "thumb_restored.jpg").exists()

    with db_conn() as conn:
        assert conn.execute("SELECT count(*) FROM files").fetchone()[0] == 1
    assert messages[0][0] == "Restoring SQLite index database..."
    assert messages[-1] == ("Restore complete", 100.0)
    assert any("Extracting thumbnails" in m for m, _ in messages)


def test_restore_survives_config_and_wal_failures(sandbox_env, backup_dir, monkeypatch):
    media = sandbox_env["media_dir"]
    cfg_tmp = sandbox_env["base_dir"] / "cfg_target.json"
    monkeypatch.setattr(bs, "CONFIG_FILE", cfg_tmp)

    clip = media / "clip.mp4"
    clip.write_bytes(b"\x00" * 64)
    _insert_file_row(clip, "clip.mp4", media)
    _make_backup()

    # A stale WAL file whose removal fails must not abort the restore.
    db_path = Path(os.environ["DB_PATH"])
    wal_path = Path(str(db_path) + "-wal")
    wal_path.write_bytes(b"stale wal")
    real_unlink = Path.unlink

    def flaky_unlink(self, *args, **kwargs):
        if str(self).endswith("-wal"):
            raise OSError("read-only mount")
        return real_unlink(self, *args, **kwargs)

    # Old Python: no tarfile.data_filter, so extractall runs without a filter.
    monkeypatch.delattr(tarfile, "data_filter", raising=False)

    real_copyfile = bs.shutil.copyfile

    def broken_copyfile(src, dst, *args, **kwargs):
        if str(dst) == str(cfg_tmp):
            raise OSError("config is locked")
        return real_copyfile(src, dst, *args, **kwargs)

    with patch.object(Path, "unlink", flaky_unlink), patch(
        "media_library.services.backup_service.shutil.copyfile", broken_copyfile
    ):
        result = bs._restore_library_backup_sync()

    assert result["success"] is True
    assert result["restored_files"] == 1
    # The stale WAL's unlink raised OSError inside the restore; SQLite itself
    # reclaims the file when it re-opens the replaced database.


def test_restore_failure_is_reported(sandbox_env, backup_dir):
    media = sandbox_env["media_dir"]
    clip = media / "clip.mp4"
    clip.write_bytes(b"\x00" * 64)
    _insert_file_row(clip, "clip.mp4", media)
    _make_backup()

    with patch(
        "media_library.services.backup_service.init_backup_db",
        side_effect=RuntimeError("schema migration exploded"),
    ):
        result = bs._restore_library_backup_sync()
    assert result["success"] is False
    assert "schema migration exploded" in result["error"]


# ==============================================================================
# check_and_auto_restore_library_backup
# ==============================================================================


def test_auto_restore_no_backup(sandbox_env, backup_dir):
    assert bs.check_and_auto_restore_library_backup() is False


def test_auto_restore_restores_empty_database(sandbox_env, backup_dir):
    media = sandbox_env["media_dir"]
    clip = media / "clip.mp4"
    clip.write_bytes(b"\x00" * 64)
    _insert_file_row(clip, "clip.mp4", media)
    _make_backup()

    with db_conn() as conn, conn:
        conn.execute("DELETE FROM files")

    assert bs.check_and_auto_restore_library_backup() is True
    with db_conn() as conn:
        assert conn.execute("SELECT count(*) FROM files").fetchone()[0] == 1


def test_auto_restore_restores_missing_thumbnails(sandbox_env, backup_dir, monkeypatch):
    media = sandbox_env["media_dir"]
    thumb_dir = sandbox_env["thumb_dir"]
    clip = media / "clip.mp4"
    clip.write_bytes(b"\x00" * 64)
    _insert_file_row(clip, "clip.mp4", media, has_thumb=1)
    (thumb_dir / "thumb_keep.jpg").write_bytes(b"\xff\xd8" * 8)
    _make_backup()

    # Live DB is populated but the thumbnail cache directory is gone.
    monkeypatch.setattr(bs, "THUMBNAIL_CACHE_DIR", thumb_dir / "deleted")
    monkeypatch.setenv("THUMBNAIL_DIR", str(thumb_dir / "deleted"))

    assert bs.check_and_auto_restore_library_backup() is True
    assert (thumb_dir / "deleted" / "thumb_keep.jpg").exists()


def test_auto_restore_reports_failure(sandbox_env, backup_dir):
    media = sandbox_env["media_dir"]
    clip = media / "clip.mp4"
    clip.write_bytes(b"\x00" * 64)
    _insert_file_row(clip, "clip.mp4", media)
    _make_backup()

    with db_conn() as conn, conn:
        conn.execute("DELETE FROM files")
    with patch.object(
        bs, "_restore_library_backup_sync", return_value={"success": False, "error": "boom"}
    ):
        assert bs.check_and_auto_restore_library_backup() is False


def test_auto_restore_populates_state_from_meta(sandbox_env, backup_dir):
    """With healthy local data the check only refreshes the status snapshot."""
    media = sandbox_env["media_dir"]
    thumb_dir = sandbox_env["thumb_dir"]
    clip = media / "clip.mp4"
    clip.write_bytes(b"\x00" * 64)
    _insert_file_row(clip, "clip.mp4", media)
    for i in range(12):
        (thumb_dir / f"thumb_sweep_{i}.jpg").write_bytes(b"\xff\xd8" * 4)
    _make_backup()

    assert bs.check_and_auto_restore_library_backup() is False
    assert bs.library_backup_state["backup_exists"] is True
    assert bs.library_backup_state["file_count"] == 1
