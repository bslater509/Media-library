"""Supplementary coverage for indexer, library reads, paths, cleanup and cache.

The existing suites exercise the happy paths end to end; this module targets the
remaining branches: the crawler's defensive guards on flaky mounts (unreadable
folders, stale archives, confirmed-gone retries), the index-only read helpers'
clamping and empty-query returns, path validation fallbacks, empty-folder
cleanup's junk/OSError handling, and the artwork sweep's removal failures.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import threading
import time
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from media_library import cache, cleanup, indexer, library, paths, writes
from media_library import config as v2_config
from media_library import db as v2_db

REAL_STAT = os.stat


def _reset_indexer_state() -> None:
    indexer._update(
        status="idle",
        scanned_folders=0,
        skipped_folders=0,
        total_folders=0,
        current_folder="",
        last_synced=0.0,
        revision=0,
        prune_aborted=0,
        stale_folders=0,
        stale_files=0,
        error="",
    )


def _deny_stat_for(target: Path):
    """An ``os.stat`` stand-in that rejects exactly one path (non-ENOENT)."""

    def deny(path, **kwargs):
        if str(path) == str(target):
            raise PermissionError(13, "permission denied", str(path))
        return REAL_STAT(path, **kwargs)

    return deny


def _insert_index_row(db_path: Path) -> None:
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """
            INSERT INTO files
                (path, name, folder_path, type, ext, size, size_formatted, mtime,
                 needs_transcode, has_thumb)
            VALUES ('lib/x.jpg', 'x.jpg', 'lib', 'image', '.jpg', 4, '4 B',
                    1.5, 0, 1)
            """
        )


@pytest.fixture
def library_env(tmp_path, monkeypatch):
    """A small library tree plus the crawler settings reset fixture."""
    lib = tmp_path / "media"
    (lib / "A" / "sub").mkdir(parents=True)
    (lib / "B").mkdir()
    (lib / "A" / "one.jpg").write_bytes(b"one" * 100)
    (lib / "A" / "note.txt").write_bytes(b"plain text")
    (lib / "A" / "sub" / "deep.jpg").write_bytes(b"deep" * 20)
    (lib / "B" / "two.jpg").write_bytes(b"two" * 40)
    with zipfile.ZipFile(lib / "B" / "bundle.zip", "w") as bundle:
        bundle.writestr("inside.txt", "payload")
        bundle.writestr("still.jpg", b"still")

    monkeypatch.setenv("LIBRARY_DIR", str(lib))
    monkeypatch.setenv("DB_READ_ONLY", "false")
    monkeypatch.setenv("WRITES", "true")
    monkeypatch.setenv("OWN_WORKERS", "false")
    v2_config.reload_settings()
    _reset_indexer_state()
    try:
        yield {"library": lib, "db_path": Path(os.environ["DB_PATH"])}
    finally:
        v2_config.reload_settings()


# ---------------------------------------------------------------------------
# indexer: standalone helpers
# ---------------------------------------------------------------------------


def test_confirm_gone_outcomes(tmp_path, monkeypatch):
    live = tmp_path / "live.jpg"
    live.write_bytes(b"x")
    assert indexer._confirm_gone(str(live)) is False

    vanished = tmp_path / "vanished-child"
    monkeypatch.setattr(os, "stat", _deny_stat_for(vanished))
    assert indexer._confirm_gone(str(vanished)) is False


def test_upsert_marks_unknown_extension_as_other(library_env):
    result = indexer.crawl()
    assert result["status"] == "idle"
    with sqlite3.connect(str(library_env["db_path"])) as conn:
        row = conn.execute("SELECT type FROM files WHERE name = 'note.txt'").fetchone()
    assert row[0] == "other"


def test_index_zip_and_garbage_archive(library_env):
    from media_library.db import writer

    lib = library_env["library"]
    bundle = lib / "B" / "bundle.zip"
    garbage = lib / "B" / "broken.zip"
    garbage.write_bytes(b"this is not a zip archive")

    with writer() as conn, conn:
        assert indexer._index_zip(conn, str(bundle), os.stat(str(bundle))) == 2
    assert indexer._index_zip(None, str(garbage), os.stat(str(garbage))) == 0
    assert indexer._read_zip_infos(str(garbage)) is None


# ---------------------------------------------------------------------------
# indexer: _index_zip_path guards
# ---------------------------------------------------------------------------


def test_reindex_archive_row_already_fresh(library_env):
    bundle = library_env["library"] / "B" / "bundle.zip"
    indexer.crawl()
    # The index row already matches this stat: nothing is re-indexed.
    indexer._index_zip_path(str(bundle), os.stat(str(bundle)))


def test_reindex_archive_rest_failure(library_env, monkeypatch):
    bundle = library_env["library"] / "B" / "bundle.zip"
    indexer.crawl()
    stat = os.stat(str(bundle))
    monkeypatch.setattr(indexer.os, "stat", _deny_stat_for(bundle))
    # The central directory reads, but the write-phase re-stat fails.
    indexer._index_zip_path(str(bundle), stat)


def test_reindex_archive_stale_stat_argument(library_env):
    lib = library_env["library"]
    bundle = lib / "B" / "bundle.zip"
    indexer.crawl()
    old_stat = os.stat(str(bundle))
    newer = old_stat.st_mtime + 60
    os.utime(str(bundle), (newer, newer))
    # The passed stat no longer matches the file: skip and let the crawl retry.
    indexer._index_zip_path(str(bundle), old_stat)


def test_crawl_retries_unreadable_archive(library_env):
    bundle = library_env["library"] / "B" / "bundle.zip"
    indexer.crawl()
    bundle.write_bytes(b"corrupted beyond repair")
    result = indexer.crawl()
    assert result["status"] == "idle"
    with sqlite3.connect(str(library_env["db_path"])) as conn:
        members = conn.execute(
            "SELECT COUNT(*) FROM files WHERE folder_path = ?", (str(bundle),)
        ).fetchone()[0]
    assert members == 2  # kept until the archive reads cleanly again


# ---------------------------------------------------------------------------
# indexer: _scan_folder branches
# ---------------------------------------------------------------------------


def test_scan_folder_read_and_fresh_archives(library_env):
    lib = library_env["library"]
    folder_b = lib / "B"

    stat = os.stat(str(folder_b))
    indexer._scan_folder(str(folder_b), stat)
    # The second scan finds the archive row matching: nothing is rebuilt, the
    # parent-row update in the fresh-archive branch still runs.
    assert indexer._scan_folder(str(folder_b), stat) == []

    with pytest.raises(writes.WriteError):
        indexer._scan_folder(str(lib / "missing-dir"), os.stat(str(lib)))


def test_scan_folder_entry_stat_failure(library_env, monkeypatch):
    lib = library_env["library"]
    folder_b = lib / "B"

    class BrokenEntry:
        name = "weird.bin"
        path = str(lib / "B" / "weird.bin")

        def is_dir(self, follow_symlinks=False):
            return False

        def is_file(self, follow_symlinks=False):
            raise OSError(9, "bad descriptor")

    monkeypatch.setattr(indexer.os, "scandir", lambda folder: [BrokenEntry()])
    assert indexer._scan_folder(str(folder_b), os.stat(str(folder_b))) == []
    with sqlite3.connect(str(library_env["db_path"])) as conn:
        row = conn.execute(
            "SELECT file_count, subfolder_count FROM folders WHERE path = ?",
            (str(folder_b),),
        ).fetchone()
    assert row == (0, 0)


def test_scan_folder_stale_child_is_kept(library_env, monkeypatch):
    lib = library_env["library"]
    folder_a = lib / "A"
    sub = folder_a / "sub"

    indexer.crawl()
    shutil.rmtree(sub)
    monkeypatch.setattr(indexer.os, "stat", _deny_stat_for(sub))
    indexer._scan_folder(str(folder_a), os.stat(str(folder_a)))
    monkeypatch.setattr(indexer.os, "stat", REAL_STAT)

    state = indexer.status()
    assert state["stale_folders"] == 1
    assert state["stale_files"] == 1
    with sqlite3.connect(str(library_env["db_path"])) as conn:
        remaining = conn.execute(
            "SELECT COUNT(*) FROM folders WHERE path = ?", (str(sub),)
        ).fetchone()[0]
    assert remaining == 1  # never confirmed gone, so the row stays


def test_scan_folder_zip_write_phase_guards(library_env, monkeypatch):
    lib = library_env["library"]
    folder_b = lib / "B"
    bundle = folder_b / "bundle.zip"

    indexer.crawl()
    newer = time.time() + 90
    os.utime(str(bundle), (newer, newer))
    folder_stat = os.stat(str(folder_b))

    # (a) the write-phase re-stat fails: members are left untouched
    monkeypatch.setattr(indexer.os, "stat", _deny_stat_for(bundle))
    indexer._scan_folder(str(folder_b), folder_stat)
    monkeypatch.setattr(indexer.os, "stat", REAL_STAT)

    # (b) the re-stat disagrees with the stat used for the read: skipped
    def shifted(path, **kwargs):
        st = REAL_STAT(path, **kwargs)
        if str(path) == str(bundle):
            return SimpleNamespace(st_mtime=st.st_mtime + 5, st_size=st.st_size)
        return st

    monkeypatch.setattr(indexer.os, "stat", shifted)
    indexer._scan_folder(str(folder_b), folder_stat)
    monkeypatch.setattr(indexer.os, "stat", REAL_STAT)


# ---------------------------------------------------------------------------
# indexer: crawl loop branches
# ---------------------------------------------------------------------------


def test_start_crawl_failure_releases_lock(library_env, monkeypatch):
    def explode(*args, **kwargs):
        raise RuntimeError("thread machinery unavailable")

    monkeypatch.setattr(indexer.threading, "Thread", explode)
    with pytest.raises(RuntimeError):
        indexer.start_crawl()
    # The crawl lock is released: a direct crawl runs afterwards.
    assert indexer.crawl()["status"] == "idle"


def test_start_crawl_runs_in_background(library_env):
    assert indexer.start_crawl() is True
    deadline = time.time() + 10
    while time.time() < deadline:
        state = indexer.status()
        if state["status"] == "idle" and state["last_synced"] > 0:
            break
        time.sleep(0.05)
    assert indexer.status()["status"] == "idle"
    assert indexer.status()["last_synced"] > 0


def test_crawl_refuses_when_already_running(library_env):
    lock = indexer._crawl_lock
    assert lock.acquire(blocking=False)
    try:
        with pytest.raises(writes.WriteError) as exc:
            indexer.crawl()
        assert exc.value.status == 409
    finally:
        lock.release()


def test_crawl_root_unavailable_updates_error_state(library_env, tmp_path, monkeypatch):
    monkeypatch.setenv("LIBRARY_DIR", str(tmp_path / "gone-root"))
    v2_config.reload_settings()
    with pytest.raises(writes.WriteError):
        indexer.crawl()
    state = indexer.status()
    assert state["status"] == "error"
    assert "not available" in state["error"]


def test_crawl_seen_folder_is_skipped(library_env, monkeypatch):
    root = str(indexer.get_settings().library_dir)
    scanned = []

    def fake_scan(folder, stat):
        scanned.append(folder)
        return [folder]

    monkeypatch.setattr(indexer, "_scan_folder", fake_scan)
    result = indexer.crawl()
    assert scanned == [root]
    assert result["status"] == "idle"


def test_crawl_skips_child_whose_stat_fails(library_env, monkeypatch):
    def fake_scan(folder, stat):
        return [os.path.join(folder, "vanished-child")]

    monkeypatch.setattr(indexer, "_scan_folder", fake_scan)
    result = indexer.crawl()
    assert result["status"] == "idle"


def test_crawl_tolerates_reader_failure_on_row_check(library_env, monkeypatch):
    real_reader = indexer.reader
    attempts = {"n": 0}

    def flaky_reader():
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("index temporarily unavailable")
        return real_reader()

    monkeypatch.setattr(indexer, "reader", flaky_reader)
    result = indexer.crawl()
    assert result["status"] == "idle"
    assert attempts["n"] >= 3


def test_crawl_skip_branch_handles_archived_stat(library_env, monkeypatch):
    bundle = library_env["library"] / "B" / "bundle.zip"
    indexer.crawl()
    monkeypatch.setattr(indexer.os, "stat", _deny_stat_for(bundle))
    result = indexer.crawl()
    assert result["status"] == "idle"


def test_crawl_survives_unreadable_folder(library_env, monkeypatch):
    def explode(folder, stat):
        raise writes.WriteError("unmount storm", 500)

    monkeypatch.setattr(indexer, "_scan_folder", explode)
    result = indexer.crawl()
    assert result["status"] == "idle"
    state = indexer.status()
    assert state["prune_aborted"] == 1
    assert state["stale_folders"] == 1


def test_crawl_swallows_artwork_sweep_failure(library_env, tmp_path, monkeypatch):
    thumbs = tmp_path / "thumbs"
    thumbs.mkdir()
    monkeypatch.setenv("THUMBNAIL_DIR", str(thumbs))
    monkeypatch.setenv("OWN_WORKERS", "true")
    v2_config.reload_settings()

    def explode():
        raise RuntimeError("sweep boom")

    monkeypatch.setattr(indexer.cache, "sweep_orphaned_artwork", explode)
    result = indexer.crawl()
    assert result["status"] == "idle"
    assert result["artwork_swept"] == {}


# ---------------------------------------------------------------------------
# indexer: CrawlerThread
# ---------------------------------------------------------------------------


def test_crawler_thread_loop_breaks_on_stop(library_env, monkeypatch):
    marker = {"entered": False}

    def fake_wait(self, timeout=None):
        time.sleep(0.02)
        return self.is_set()

    monkeypatch.setattr(threading.Event, "wait", fake_wait)

    def explode(*args, **kwargs):
        marker["entered"] = True
        raise RuntimeError("crawl unavailable")

    monkeypatch.setattr(indexer, "crawl", explode)

    crawler = indexer.CrawlerThread(interval_seconds=0.05)
    crawler.start()
    deadline = time.time() + 5
    while time.time() < deadline and not marker["entered"]:
        time.sleep(0.01)
    assert marker["entered"]  # the loop body ran inside the worker thread
    crawler.start()  # a live thread: the second start returns immediately
    crawler.stop()
    assert not crawler._thread.is_alive()


# ---------------------------------------------------------------------------
# library: clamping and empty queries
# ---------------------------------------------------------------------------


def test_browse_clamps_missing_leaf(library_env):
    lib = library_env["library"]
    result = library.browse(str(lib / "ghost.mp3"))
    assert result["path"] == str(lib)
    assert result["indexed"] is False


def test_clamp_falls_back_to_root_when_root_missing(tmp_path, monkeypatch):
    missing_root = tmp_path / "nowhere"
    monkeypatch.setenv("LIBRARY_DIR", str(missing_root))
    v2_config.reload_settings()
    result = library.browse(None)
    assert result["path"] == str(missing_root)
    assert result["folders"] == []
    assert result["indexed"] is False


def test_list_folders_clamps_missing_leaf(library_env):
    lib = library_env["library"]
    result = library.list_folders(str(lib / "ghost.mp3"))
    assert result["path"] == str(lib)


def test_browse_clamp_hits_target_that_reappears(library_env, monkeypatch):
    """A directory that vanishes between the browse probe and the clamp check."""
    lib = library_env["library"]
    ghost = lib / "ghost.mp3"
    real_isdir = os.path.isdir
    probes = {"n": 0}

    def flaky_isdir(path):
        probes["n"] += 1
        if probes["n"] == 2:
            return True  # the target reappears by the time the clamp checks
        return real_isdir(path)

    monkeypatch.setattr(os.path, "isdir", flaky_isdir)
    result = library.browse(str(ghost))
    assert result["path"] == str(ghost)
    assert result["indexed"] is False
    assert result["files"] == []


def test_empty_queries_return_no_results(library_env):
    assert library.search_folders("   ") == {"query": "   ", "results": []}
    empty = library.search("   ")
    assert empty["total"] == 0
    assert empty["truncated"] is False
    assert empty["results"] == []


# ---------------------------------------------------------------------------
# paths
# ---------------------------------------------------------------------------


def test_resolve_existing_file_guards(library_env):
    lib = library_env["library"]
    real = lib / "A" / "one.jpg"

    with pytest.raises(HTTPException) as exc:
        paths.resolve_existing_file("   ")
    assert exc.value.status_code == 400

    with pytest.raises(HTTPException) as exc:
        paths.resolve_existing_file(str(lib / "ghost.mp3"))
    assert exc.value.status_code == 404

    assert paths.resolve_existing_file(str(real)) == str(real)


def test_resolve_library_path_commonpath_failure(library_env, monkeypatch):
    def broken(*args, **kwargs):
        raise ValueError("mixed absolute/relative paths")

    monkeypatch.setattr(os.path, "commonpath", broken)
    with pytest.raises(HTTPException) as exc:
        paths.resolve_library_path(str(library_env["library"] / "A"))
    assert exc.value.status_code == 403


def test_relative_to_root_error_and_zero_sizes(library_env, monkeypatch):
    def broken(path, start):
        raise ValueError("different drives")

    monkeypatch.setattr(os.path, "relpath", broken)
    assert paths.relative_to_root(str(library_env["library"] / "A")) == ""
    assert paths.format_size(None) == "0 B"
    assert paths.format_size(0) == "0 B"


def test_is_zip_member_survives_index_failure(library_env, monkeypatch):
    def broken_reader():
        raise RuntimeError("index unavailable")

    monkeypatch.setattr(v2_db, "reader", broken_reader)
    member = str(library_env["library"] / "B" / "bundle.zip" / "inside.txt")
    # The string-shaped contract decides when the index cannot be read.
    assert paths.is_zip_member(member) is True


# ---------------------------------------------------------------------------
# cleanup
# ---------------------------------------------------------------------------


def test_delete_empty_input_guards():
    with pytest.raises(writes.WriteError):
        cleanup.delete_empty([])
    with pytest.raises(writes.WriteError):
        cleanup.delete_empty([f"plain/{n}.jpg" for n in range(1001)])


def test_delete_empty_junk_and_oserror(library_env):
    lib = library_env["library"]

    blocked = lib / "blocked"
    (blocked / ".DS_Store").mkdir(parents=True)  # junk that cannot be removed
    result = cleanup.delete_empty([str(blocked)])
    assert result["deleted"] == []
    assert len(result["failed"]) == 1
    assert result["failed"][0]["path"] == str(blocked)
    assert blocked.exists()

    removable = lib / "removable"
    removable.mkdir()
    (removable / "Thumbs.db").write_bytes(b"junk")
    result = cleanup.delete_empty([str(removable)])
    assert result["deleted"] == [str(removable)]
    assert not removable.exists()


# ---------------------------------------------------------------------------
# cache
# ---------------------------------------------------------------------------


def test_sweep_returns_skipped_without_thumbs_dir(library_env, tmp_path, monkeypatch):
    thumbnails = Path(os.environ["DATA_DIR"]) / "thumbs"  # deliberately absent
    monkeypatch.setenv("THUMBNAIL_DIR", str(thumbnails))
    v2_config.reload_settings()
    _insert_index_row(library_env["db_path"])

    result = cache.sweep_orphaned_artwork()

    assert result == {"thumbnails": 0, "animated": 0, "frames": 0, "skipped": 1}


def test_sweep_handles_unremovable_entries(library_env, tmp_path, monkeypatch):
    thumbs = tmp_path / "thumbs"
    animated = thumbs / "animated"
    frames = thumbs / "frames"
    animated.mkdir(parents=True)
    frames.mkdir()

    orphan = thumbs / f"thumb_{'0' * 24}.jpg"
    orphan.write_bytes(b"poster")
    orphan_anim = animated / f"anim_{'2' * 24}.webp"
    orphan_anim.write_bytes(b"anim")
    (thumbs / f"thumb_{'e' * 24}.jpg").mkdir()  # unlink fails: it is a directory
    (animated / f"anim_{'f' * 24}.webp").mkdir()

    with_frames = frames / ("6" * 24)
    with_frames.mkdir()
    (with_frames / "cand_1.jpg").write_bytes(b"frame")
    other_dir = tmp_path / "elsewhere"
    other_dir.mkdir()
    (frames / ("5" * 24)).symlink_to(other_dir)  # rmtree fails on a symlink

    monkeypatch.setenv("THUMBNAIL_DIR", str(thumbs))
    v2_config.reload_settings()
    _insert_index_row(library_env["db_path"])

    result = cache.sweep_orphaned_artwork()

    assert result == {"thumbnails": 1, "animated": 1, "frames": 1, "skipped": 0}
