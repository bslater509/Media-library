"""Extra coverage for the defensive branches of ``media_library/writes.py``.

Complements ``tests/test_v2_writes.py`` with the rollback, throttling and
error-reporting paths the main suite does not reach. Write functions run
directly (no HTTP layer, no subprocesses, no network); failures are staged
with ``monkeypatch`` so nothing is touched outside the throwaway index.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from media_library import config as v2_config
from media_library import writes
from media_library.paths import stable_id
from media_library.writes import WriteError


@pytest.fixture
def writes_env(tmp_path, monkeypatch):
    """Library, thumbs and staging roots for direct write-path calls."""
    library = tmp_path / "library"
    (library / "Trip").mkdir(parents=True)
    inbox = library / "Inbox"
    inbox.mkdir()
    thumbs = tmp_path / "thumbs"
    (thumbs / "animated").mkdir(parents=True)
    staging = tmp_path / "upload_staging"
    staging.mkdir()

    monkeypatch.setenv("LIBRARY_DIR", str(library))
    monkeypatch.setenv("THUMBNAIL_DIR", str(thumbs))
    monkeypatch.setenv("UPLOAD_STAGING_DIR", str(staging))
    monkeypatch.setenv("DB_READ_ONLY", "false")
    monkeypatch.setenv("WRITES", "true")
    v2_config.reload_settings()

    yield SimpleNamespace(
        library=library,
        trip=library / "Trip",
        inbox=inbox,
        thumbs=thumbs,
        staging=staging,
    )
    v2_config.reload_settings()


def _db() -> sqlite3.Connection:
    return sqlite3.connect(os.environ["DB_PATH"])


def _add_folder(conn, path, *, is_zip=0) -> None:
    path = str(path)
    conn.execute(
        "INSERT INTO folders (path, name, parent_path, file_count, "
        "subfolder_count, last_scanned, is_zip, total_size) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (path, Path(path).name, str(Path(path).parent), 0, 0, 1.0, is_zip, 0),
    )


def _add_file(conn, path, *, kind="video", ext=None) -> None:
    stat = os.stat(path)
    conn.execute(
        "INSERT INTO files (path, name, folder_path, type, ext, size, "
        "size_formatted, mtime, needs_transcode, has_thumb, width, height) "
        "VALUES (?,?,?,?,?,?,?,?,?,0,1920,1080)",
        (
            str(path),
            os.path.basename(path),
            str(Path(path).parent),
            kind,
            ext or os.path.splitext(path)[1],
            stat.st_size,
            "1 KB",
            stat.st_mtime,
            0,
        ),
    )


# ---------------------------------------------------------------------------
# Resolve / favorites / view history
# ---------------------------------------------------------------------------


def test_resolve_dir_refuses_a_missing_destination(writes_env):
    ghost_dest = str(writes_env.library / "Nope")
    with pytest.raises(WriteError) as excinfo:
        writes.move_files([str(writes_env.inbox)], ghost_dest)
    assert excinfo.value.status == 404
    assert "Destination folder not found" in excinfo.value.message


def test_rename_keeps_going_when_the_poster_move_fails(writes_env, monkeypatch):
    clip = writes_env.trip / "clip.mp4"
    clip.write_bytes(b"x" * 32)
    stat = clip.stat()
    thumb, _anim = writes._thumb_paths(str(clip), stat.st_mtime, stat.st_size)
    thumb.write_bytes(b"poster")

    def broken(src, dst):
        raise OSError("read-only cache")

    monkeypatch.setattr(os, "replace", broken)
    result = writes.rename_file(str(clip), "renamed.mp4")
    assert Path(result["path"]).is_file()
    assert not clip.exists()


def test_delete_survives_a_failing_poster_unlink(writes_env, monkeypatch):
    clip = writes_env.trip / "clip.mp4"
    clip.write_bytes(b"x" * 8)
    with _db() as conn:
        _add_folder(conn, writes_env.trip)
        _add_file(conn, clip)

    def broken(self, missing_ok=False):
        raise OSError("device busy")

    monkeypatch.setattr(Path, "unlink", broken)
    result = writes.delete_files([str(clip)])
    assert result["deleted"] == [str(clip)]
    assert result["failed"] == []


def test_rename_to_an_unknown_extension_types_other(writes_env):
    clip = writes_env.trip / "clip.mp4"
    clip.write_bytes(b"x" * 16)
    with _db() as conn:
        _add_folder(conn, writes_env.trip)
        _add_file(conn, clip)

    result = writes.rename_file(str(clip), "notes.txt")
    renamed = Path(result["path"])
    with _db() as conn:
        row = conn.execute(
            "SELECT type, needs_transcode FROM files WHERE path = ?",
            (str(renamed),),
        ).fetchone()
    assert row == ("other", 0)


def test_rename_keeps_the_five_frame_check(writes_env):
    clip = writes_env.trip / "clip.mp4"
    clip.write_bytes(b"x" * 16)
    with _db() as conn:
        _add_folder(conn, writes_env.trip)
        _add_file(conn, clip)
        conn.execute(
            "INSERT INTO visual_frames "
            "(path, mtime, size, duration, hashes, captured_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (str(clip), 1.0, 16, 100.0, '["aaaaaaaaaaaaaaaa"]', 1.0),
        )

    result = writes.rename_file(str(clip), "renamed.mp4")
    renamed = result["path"]
    with _db() as conn:
        row = conn.execute(
            "SELECT path, hashes FROM visual_frames WHERE path = ?", (renamed,)
        ).fetchone()
        stale = conn.execute(
            "SELECT COUNT(*) FROM visual_frames WHERE path = ?", (str(clip),)
        ).fetchone()[0]
    assert row == (renamed, '["aaaaaaaaaaaaaaaa"]')
    assert stale == 0


def test_toggle_favorite_refuses_an_unindexed_path(writes_env):
    with pytest.raises(WriteError) as excinfo:
        writes.toggle_favorite(str(writes_env.trip / "ghost.mp4"))
    assert excinfo.value.status == 404
    assert excinfo.value.message == "Not indexed"


def test_set_favorites_rejects_an_unknown_action(writes_env):
    with pytest.raises(WriteError) as excinfo:
        writes.set_favorites([], action="nope")
    assert excinfo.value.status == 400
    assert excinfo.value.message == "Unknown favorite action"


def test_set_favorites_reports_unresolvable_paths(writes_env):
    result = writes.set_favorites(["/etc/passwd"])
    assert result["updated"] == 0
    assert result["failed"][0]["path"] == "/etc/passwd"
    assert "outside the library" in result["failed"][0]["error"]


def test_record_view_rejects_invalid_seconds(writes_env):
    target = str(writes_env.trip / "clip.mp4")
    for bad in (float("nan"), float("inf"), -1.0):
        with pytest.raises(WriteError) as excinfo:
            writes.record_view(target, seconds=bad)
        assert excinfo.value.status == 400
        assert excinfo.value.message == "Invalid seconds value"


# ---------------------------------------------------------------------------
# Rename and delete
# ---------------------------------------------------------------------------


def test_rename_to_the_same_name_is_a_no_op(writes_env):
    clip = writes_env.trip / "clip.mp4"
    clip.write_bytes(b"x" * 8)
    assert writes.rename_file(str(clip), "clip.mp4") == {"path": str(clip)}
    assert clip.is_file()


def test_rename_rolls_back_when_the_index_write_fails(writes_env, monkeypatch):
    clip = writes_env.trip / "clip.mp4"
    clip.write_bytes(b"x" * 8)

    def boom(*args, **kwargs):
        raise RuntimeError("index down")

    monkeypatch.setattr(writes, "_apply_file_move", boom)
    with pytest.raises(RuntimeError):
        writes.rename_file(str(clip), "renamed.mp4")
    assert clip.is_file()
    assert not (writes_env.trip / "renamed.mp4").exists()


def test_delete_rejects_empty_and_oversized_batches(writes_env):
    with pytest.raises(WriteError) as empty:
        writes.delete_files([])
    assert empty.value.message == "No files selected"
    with pytest.raises(WriteError) as oversized:
        writes.delete_files(["x"] * 501)
    assert "max 500" in oversized.value.message


def test_delete_reports_a_failing_remove(writes_env, monkeypatch):
    clip = writes_env.trip / "clip.mp4"
    clip.write_bytes(b"x" * 8)
    with _db() as conn:
        _add_folder(conn, writes_env.trip)
        _add_file(conn, clip)

    def locked(path):
        raise OSError("device busy")

    monkeypatch.setattr(os, "remove", locked)
    result = writes.delete_files([str(clip)])
    assert result["deleted"] == []
    assert "device busy" in result["failed"][0]["error"]
    assert clip.is_file()


def test_zip_rename_survives_a_failing_cache_purge(writes_env, monkeypatch):
    archive = writes_env.trip / "bundle.zip"
    archive.write_bytes(b"PK\x05\x06" + b"\x00" * 18)

    def boom(source):
        raise OSError("cache busy")

    monkeypatch.setattr("media_library.zipfs.purge_cache", boom)
    result = writes.rename_folder(str(archive), "renamed.zip")
    assert result == {
        "path": str(writes_env.trip / "renamed.zip"),
        "name": "renamed.zip",
    }
    assert not archive.exists()
    assert (writes_env.trip / "renamed.zip").is_file()


def test_rename_folder_to_the_same_name_is_a_no_op(writes_env):
    assert writes.rename_folder(str(writes_env.trip), "Trip") == {
        "path": str(writes_env.trip),
        "name": "Trip",
    }


def test_rename_folder_rolls_back_when_the_index_write_fails(writes_env, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("index down")

    monkeypatch.setattr(writes, "_relocate_entry", boom)
    with pytest.raises(RuntimeError):
        writes.rename_folder(str(writes_env.trip), "Renamed")
    assert writes_env.trip.is_dir()
    assert not (writes_env.library / "Renamed").exists()


def test_create_folder_survives_a_failing_stat(writes_env, monkeypatch):
    def boom(path):
        raise OSError("no stat")

    monkeypatch.setattr(writes, "_file_stats", boom)
    created = writes.create_folder(str(writes_env.inbox), "Day 1")
    assert Path(created["path"]).is_dir()
    with _db() as conn:
        row = conn.execute(
            "SELECT name, parent_path, file_count FROM folders WHERE path = ?",
            (created["path"],),
        ).fetchone()
    assert row == ("Day 1", str(writes_env.inbox), 0)


# ---------------------------------------------------------------------------
# Moves
# ---------------------------------------------------------------------------


def test_unique_target_wrapper_avoids_occupied_names(writes_env):
    (writes_env.inbox / "a (1).mp4").write_bytes(b"x")
    by_disk = writes.unique_target(str(writes_env.inbox), "a.mp4", set())
    assert by_disk == str(writes_env.inbox / "a (2).mp4")

    by_occupied = writes.unique_target(
        str(writes_env.inbox), "c.mp4", {str(writes_env.inbox / "c (1).mp4")}
    )
    assert by_occupied == str(writes_env.inbox / "c (2).mp4")

    plain = writes.unique_target(str(writes_env.inbox), "b.mp4", None)
    assert plain == str(writes_env.inbox / "b (1).mp4")


def test_move_validates_policy_and_batch(writes_env):
    dest = str(writes_env.inbox)
    with pytest.raises(WriteError) as policy:
        writes.move_files([str(writes_env.trip)], dest, conflict="nope")
    assert policy.value.message == "Unknown conflict policy"
    with pytest.raises(WriteError) as empty:
        writes.move_files([], dest)
    assert empty.value.message == "No items selected"
    with pytest.raises(WriteError) as oversized:
        writes.move_files(["x"] * 501, dest)
    assert "max 500" in oversized.value.message


def test_move_reports_a_missing_source(writes_env):
    ghost = str(writes_env.trip / "ghost.mp4")
    result = writes.move_files([ghost], str(writes_env.inbox))
    assert result["moved"] == []
    assert result["failed"] == [{"path": ghost, "error": "File or folder not found"}]


def test_move_skips_a_file_already_in_its_folder(writes_env):
    clip = writes_env.inbox / "clip.mp4"
    clip.write_bytes(b"x" * 8)
    result = writes.move_files([str(clip)], str(writes_env.inbox))
    assert result["skipped"] == [
        {"path": str(clip), "reason": "Already in this folder"}
    ]
    assert result["moved"] == []
    assert clip.is_file()


def test_move_reports_a_failing_rename(writes_env, monkeypatch):
    clip = writes_env.trip / "clip.mp4"
    clip.write_bytes(b"x" * 8)

    def broken(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(shutil, "move", broken)
    result = writes.move_files([str(clip)], str(writes_env.inbox))
    assert result["moved"] == []
    assert "disk full" in result["failed"][0]["error"]
    assert clip.is_file()


def test_move_folder_rolls_back_and_reports_when_indexing_fails(
    writes_env, monkeypatch
):
    sub = writes_env.trip / "Sub"
    sub.mkdir()
    real_move = shutil.move
    calls = []

    def flaky(src, dst):
        calls.append(src)
        if len(calls) == 1:
            return real_move(src, dst)
        raise OSError("cannot restore")

    def boom(*args, **kwargs):
        raise RuntimeError("index down")

    monkeypatch.setattr(shutil, "move", flaky)
    monkeypatch.setattr(writes, "_relocate_entry", boom)
    result = writes.move_files([str(sub)], str(writes_env.inbox))
    assert result["moved"] == []
    assert result["failed"] == [{"path": str(sub), "error": "Index update failed"}]
    # The restore move itself failed, so the folder stays at the destination.
    assert not sub.exists()
    assert (writes_env.inbox / "Sub").is_dir()


def test_move_file_rolls_back_and_reports_when_indexing_fails(writes_env, monkeypatch):
    clip = writes_env.trip / "clip.mp4"
    clip.write_bytes(b"x" * 8)
    real_move = shutil.move
    calls = []

    def flaky(src, dst):
        calls.append(src)
        if len(calls) == 1:
            return real_move(src, dst)
        raise OSError("cannot restore")

    def boom(*args, **kwargs):
        raise RuntimeError("index down")

    monkeypatch.setattr(shutil, "move", flaky)
    monkeypatch.setattr(writes, "_apply_file_move", boom)
    result = writes.move_files([str(clip)], str(writes_env.inbox))
    assert result["moved"] == []
    assert result["failed"] == [{"path": str(clip), "error": "Index update failed"}]
    assert not clip.exists()
    assert (writes_env.inbox / "clip.mp4").is_file()


# ---------------------------------------------------------------------------
# Staging GC
# ---------------------------------------------------------------------------


def test_staging_gc_skips_when_the_sweep_lock_is_held(writes_env, monkeypatch):
    monkeypatch.setattr(writes, "_gc_checked_at", None)
    with writes._gc_lock:
        status = writes.staged_size("gclock12")
    assert status == {
        "upload_id": "gclock12",
        "exists": False,
        "received_bytes": 0,
    }


def test_staging_gc_rechecks_after_a_won_race(writes_env, monkeypatch):
    class OtherThreadWon:
        def acquire(self, blocking: bool = True) -> bool:
            writes._gc_checked_at = writes.time.monotonic()
            return True

        def release(self) -> None:
            pass

    monkeypatch.setattr(writes, "_gc_lock", OtherThreadWon())
    monkeypatch.setattr(writes, "_gc_checked_at", None)
    status = writes.staged_size("gcrace12")
    assert status["exists"] is False


def test_staging_gc_returns_when_staging_is_missing(writes_env, tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOAD_STAGING_DIR", str(tmp_path / "missing"))
    v2_config.reload_settings()
    monkeypatch.setattr(writes, "_gc_checked_at", None)
    writes._gc_stale_staging()
    assert not (tmp_path / "missing").exists()


def test_staging_gc_survives_a_failing_stat(writes_env, monkeypatch):
    stale = writes_env.staging / "gcstat12.part"
    stale.write_bytes(b"old")
    monkeypatch.setattr(writes, "_gc_checked_at", None)

    real_stat = Path.stat

    def busy(self, *args, **kwargs):
        if self.name.endswith(".part"):
            raise OSError("device busy")
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", busy)
    writes._gc_stale_staging()
    assert os.path.isfile(stale)


# ---------------------------------------------------------------------------
# Uploads
# ---------------------------------------------------------------------------


def test_invalid_upload_ids_are_refused(writes_env):
    for bad in ("ab", "has spaces!!", "bad/slash"):
        with pytest.raises(WriteError) as excinfo:
            writes.staged_size(bad)
        assert excinfo.value.status == 400
        assert excinfo.value.message == "Invalid upload id"


def test_stage_chunk_rewrites_an_overlapping_retry(writes_env):
    """A retried chunk with a new offset truncates and appends (sync code)."""
    writes_env.staging.mkdir(exist_ok=True)
    first = writes.stage_chunk("overlap12", 0, b"0123456789")
    assert first == {"received_bytes": 10, "duplicate": False}

    again = writes.stage_chunk("overlap12", 4, b"XXXXXXXX")
    assert again == {"received_bytes": 12, "duplicate": False}
    assert (writes_env.staging / "overlap12.part").read_bytes() == b"0123XXXXXXXX"

    with pytest.raises(WriteError) as gap:
        writes.stage_chunk("overlap12", 99, b"z")
    assert gap.value.status == 409
    assert "past the staged data" in gap.value.message

    dup = writes.stage_chunk("overlap12", 0, b"01")
    assert dup == {"received_bytes": 12, "duplicate": True}


def test_finalize_indexes_a_non_media_file_as_other(writes_env):
    staging_path = writes_env.staging / "plainfile1.part"
    staging_path.write_bytes(b"text-bytes" * 4)

    result = writes.finalize_upload("plainfile1", "notes.txt", str(writes_env.inbox))
    final = Path(result["path"])
    assert result["skipped"] is False
    assert final.read_bytes() == b"text-bytes" * 4
    with _db() as conn:
        row = conn.execute(
            "SELECT type, ext, name FROM files WHERE path = ?", (str(final),)
        ).fetchone()
    assert row == ("other", ".txt", "notes.txt")
    assert not staging_path.exists()


def test_finalize_rejects_an_unknown_conflict_policy(writes_env):
    with pytest.raises(WriteError) as excinfo:
        writes.finalize_upload(
            "polcheck1", "a.mp4", str(writes_env.inbox), conflict="nope"
        )
    assert excinfo.value.status == 400
    assert excinfo.value.message == "Unknown conflict policy"


def test_finalize_rejects_an_empty_upload(writes_env):
    staging_path = writes._staging_file("emptyupl1")
    staging_path.write_bytes(b"")
    with pytest.raises(WriteError) as excinfo:
        writes.finalize_upload("emptyupl1", "a.mp4", str(writes_env.inbox))
    assert excinfo.value.status == 400
    assert excinfo.value.message == "Upload is empty"


def test_finalize_conflicts_with_an_existing_file(writes_env):
    data = b"payload-bytes"
    staging_path = writes_env.staging / "conflic1.part"
    staging_path.write_bytes(data)
    existing = writes_env.inbox / "same.mp4"
    existing.write_bytes(b"already-there")

    with pytest.raises(WriteError) as excinfo:
        writes.finalize_upload(
            "conflic1",
            "same.mp4",
            str(writes_env.inbox),
            total_size=len(data),
            conflict="error",
        )
    assert excinfo.value.status == 409
    assert existing.read_bytes() == b"already-there"

    result = writes.finalize_upload(
        "conflic1", "same.mp4", str(writes_env.inbox), total_size=len(data)
    )
    final = Path(result["path"])
    assert final.name == "same (1).mp4"
    assert final.read_bytes() == data
    assert existing.read_bytes() == b"already-there"
    assert not staging_path.exists()


def test_finalize_reports_a_failing_copy(writes_env, monkeypatch):
    staging_path = writes_env.staging / "copyfail1.part"
    staging_path.write_bytes(b"staged-data")

    def broken(src, dst):
        raise OSError("no space left on device")

    monkeypatch.setattr(shutil, "copyfile", broken)
    with pytest.raises(WriteError) as excinfo:
        writes.finalize_upload("copyfail1", "a.mp4", str(writes_env.inbox))
    assert excinfo.value.status == 500
    assert "no space left" in excinfo.value.message
    assert os.path.isfile(staging_path)


# ---------------------------------------------------------------------------
# External imports
# ---------------------------------------------------------------------------


def test_import_validates_policy_and_source(writes_env, tmp_path):
    with pytest.raises(WriteError) as policy:
        writes.import_file(
            str(tmp_path / "a.mp4"), str(writes_env.inbox), conflict="nope"
        )
    assert policy.value.message == "Unknown conflict policy"
    with pytest.raises(WriteError) as missing:
        writes.import_file(str(tmp_path / "ghost.mp4"), str(writes_env.inbox))
    assert missing.value.status == 404
    assert missing.value.message == "Source file not found"


def test_import_conflicts_with_an_existing_target(writes_env, tmp_path):
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"fresh")
    target = writes_env.inbox / "clip.mp4"
    target.write_bytes(b"existing")

    skipped = writes.import_file(str(source), str(writes_env.inbox), conflict="skip")
    assert skipped["skipped"] is True
    assert skipped["source"] == str(source)
    assert skipped["target"] == str(target)
    assert target.read_bytes() == b"existing"
    assert source.is_file()

    with pytest.raises(WriteError) as excinfo:
        writes.import_file(str(source), str(writes_env.inbox), conflict="error")
    assert excinfo.value.status == 409
    assert source.is_file()
    assert target.read_bytes() == b"existing"


def test_import_reports_a_failing_move(writes_env, tmp_path, monkeypatch):
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"fresh")

    def broken(src, dst):
        raise OSError("read-only mount")

    monkeypatch.setattr(shutil, "move", broken)
    with pytest.raises(WriteError) as excinfo:
        writes.import_file(str(source), str(writes_env.inbox))
    assert excinfo.value.status == 500
    assert "read-only mount" in excinfo.value.message
    assert source.is_file()


def test_import_rolls_back_when_indexing_fails(writes_env, tmp_path, monkeypatch):
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"fresh")
    real_move = shutil.move
    calls = []

    def flaky(src, dst):
        calls.append(src)
        if len(calls) == 1:
            return real_move(src, dst)
        raise OSError("cannot restore")

    def boom(target_path):
        raise RuntimeError("index down")

    monkeypatch.setattr(shutil, "move", flaky)
    monkeypatch.setattr(writes, "index_external_file", boom)
    with pytest.raises(RuntimeError):
        writes.import_file(str(source), str(writes_env.inbox))
    # The restore move itself failed, so the file stays at the destination.
    assert not source.exists()
    assert (writes_env.inbox / "clip.mp4").is_file()


def test_file_id_is_a_stable_short_hash():
    expected = stable_id("anything")
    assert writes.file_id("anything") == expected
    assert len(expected) == 16
