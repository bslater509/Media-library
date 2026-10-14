"""Tests for database initialization, folder indexing, and pruning."""

import asyncio
import os
import shutil

from media_library.core.state import state
from media_library.db.connection import get_db
from media_library.db.indexer import (
    add_file_to_index,
    index_folder,
    indexer_state,
    prune_stale_index_entries,
    remove_file_from_index,
    run_background_crawler,
)


def test_db_schema_tables(sandbox_env):
    conn = get_db(sandbox_env["db_path"])
    tables = [
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    ]
    assert "folders" in tables
    assert "files" in tables
    assert "ignored_duplicates" in tables
    assert "webp_stats" in tables
    assert "db_meta" in tables
    conn.close()


def test_index_folder_and_files(sandbox_env, sample_media):
    action_cat = sample_media["action_cat"]
    media_dir = sandbox_env["media_dir"]

    # Index action category
    result = index_folder(str(action_cat), base_dir=str(media_dir))
    assert result["file_count"] == 3  # 2 videos, 1 image

    conn = get_db(sandbox_env["db_path"])
    rows = conn.execute(
        "SELECT name, type, size FROM files WHERE folder_path = ?", (str(action_cat),)
    ).fetchall()
    conn.close()

    names = {r["name"] for r in rows}
    assert "clip1.mp4" in names
    assert "clip2.mkv" in names
    assert "poster1.jpg" in names


def test_add_and_remove_file_index(sandbox_env):
    media_dir = sandbox_env["media_dir"]
    conn = get_db(sandbox_env["db_path"])

    new_file = media_dir / "new_video.mp4"
    new_file.write_bytes(b"content" * 100)

    add_file_to_index(str(new_file))
    cur = conn.execute("SELECT name, size FROM files WHERE path = ?", (str(new_file),))
    row = cur.fetchone()
    assert row is not None
    assert row["name"] == "new_video.mp4"

    remove_file_from_index(str(new_file))
    cur = conn.execute("SELECT name FROM files WHERE path = ?", (str(new_file),))
    assert cur.fetchone() is None
    conn.close()


def test_prune_stale_folder_and_entries(sandbox_env, sample_media):
    media_dir = sandbox_env["media_dir"]
    comedy_cat = sample_media["comedy_cat"]

    # Index comedy category
    index_folder(str(comedy_cat), base_dir=str(media_dir))

    conn = get_db(sandbox_env["db_path"])
    cur = conn.execute(
        "SELECT name FROM files WHERE folder_path = ?", (str(comedy_cat),)
    )
    assert cur.fetchone() is not None
    conn.close()

    # Remove the entire comedy folder from disk
    shutil.rmtree(str(comedy_cat))

    # Run prune
    prune_res = prune_stale_index_entries(
        base_dir=str(media_dir), clean_orphaned_thumbnails=False
    )
    assert prune_res["pruned_folders"] >= 1

    conn = get_db(sandbox_env["db_path"])
    cur = conn.execute(
        "SELECT name FROM files WHERE folder_path = ?", (str(comedy_cat),)
    )
    assert cur.fetchone() is None
    cur_folder = conn.execute(
        "SELECT path FROM folders WHERE path = ?", (str(comedy_cat),)
    )
    assert cur_folder.fetchone() is None
    conn.close()


def test_prune_aborts_when_base_dir_missing(sandbox_env, sample_media):
    """A missing/unmounted base dir must never wipe the index."""
    media_dir = sandbox_env["media_dir"]
    index_folder(str(sample_media["action_cat"]), base_dir=str(media_dir))

    res = prune_stale_index_entries(
        base_dir=str(media_dir / "missing_root"), clean_orphaned_thumbnails=False
    )
    assert res.get("aborted") is True
    assert res["pruned_folders"] == 0

    conn = get_db(sandbox_env["db_path"])
    count = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
    conn.close()
    assert count == 3


def test_prune_aborts_on_mass_deletion(sandbox_env):
    """A transient mount failure that makes most folders look missing must abort,
    not delete the index (this is the regression that wiped ~48k files)."""
    media_dir = sandbox_env["media_dir"]
    folders = []
    for i in range(40):
        d = media_dir / f"mass_{i:02d}"
        d.mkdir()
        (d / "clip.mp4").write_bytes(b"\x00" * 16)
        index_folder(str(d), base_dir=str(media_dir))
        folders.append(d)

    # 30 of 40 folders vanish at once -> implausible for a healthy mount.
    for d in folders[:30]:
        shutil.rmtree(str(d))

    res = prune_stale_index_entries(
        base_dir=str(media_dir), clean_orphaned_thumbnails=False
    )
    assert res.get("aborted") is True
    assert res["pruned_folders"] == 0

    conn = get_db(sandbox_env["db_path"])
    remaining = conn.execute("SELECT COUNT(*) FROM folders").fetchone()[0]
    conn.close()
    assert remaining == 40


def test_prune_still_removes_small_missing_set(sandbox_env):
    """A genuinely deleted folder (within the safety limit) is still pruned."""
    media_dir = sandbox_env["media_dir"]
    folders = []
    for i in range(5):
        d = media_dir / f"small_{i}"
        d.mkdir()
        (d / "clip.mp4").write_bytes(b"\x00" * 16)
        index_folder(str(d), base_dir=str(media_dir))
        folders.append(d)

    shutil.rmtree(str(folders[0]))

    res = prune_stale_index_entries(
        base_dir=str(media_dir), clean_orphaned_thumbnails=False
    )
    assert res.get("aborted") is not True
    assert res["pruned_folders"] == 1


def test_folders_have_dir_mtime_column(sandbox_env):
    conn = get_db(sandbox_env["db_path"])
    cols = [r[1] for r in conn.execute("PRAGMA table_info(folders)").fetchall()]
    conn.close()
    assert "dir_mtime" in cols


def test_incremental_crawl_skips_unchanged_folders(sandbox_env):
    """A second crawl must reuse the index when directory mtimes are unchanged."""
    media_dir = sandbox_env["media_dir"]
    state["destination_dir"] = str(media_dir)
    cat = media_dir / "cat"
    cat.mkdir()
    (cat / "clip.mp4").write_bytes(b"\x00" * 32)

    async def scenario():
        await run_background_crawler()
        first_scanned = indexer_state["scanned_folders"]
        # Nothing changed on disk -> the second crawl should skip the folder.
        await run_background_crawler()
        return first_scanned

    first_scanned = asyncio.run(scenario())
    assert first_scanned >= 1
    assert indexer_state["skipped_folders"] >= 1

    # The row (and its file) survive the skip.
    conn = get_db(sandbox_env["db_path"])
    row = conn.execute(
        "SELECT dir_mtime FROM folders WHERE path = ?", (str(cat),)
    ).fetchone()
    files = conn.execute(
        "SELECT COUNT(*) FROM files WHERE folder_path = ?", (str(cat),)
    ).fetchone()[0]
    conn.close()
    assert row is not None and row["dir_mtime"] is not None
    assert files == 1


def test_incremental_crawl_indexes_new_file(sandbox_env):
    """Adding a file changes the folder mtime, so the folder is re-indexed."""
    media_dir = sandbox_env["media_dir"]
    state["destination_dir"] = str(media_dir)
    cat = media_dir / "cat"
    cat.mkdir()
    (cat / "a.mp4").write_bytes(b"\x00" * 32)

    async def scenario():
        await run_background_crawler()
        (cat / "b.mp4").write_bytes(b"\x00" * 16)
        os.utime(cat, None)  # ensure the directory mtime moves forward
        await run_background_crawler()

    asyncio.run(scenario())

    conn = get_db(sandbox_env["db_path"])
    names = {
        r["name"]
        for r in conn.execute(
            "SELECT name FROM files WHERE folder_path = ?", (str(cat),)
        )
    }
    conn.close()
    assert {"a.mp4", "b.mp4"} <= names


def test_incremental_crawl_prunes_deleted_subfolder(sandbox_env):
    """Deleting a nested folder changes its parent mtime, so the parent is
    re-scanned and the now-missing child is removed by the prune."""
    media_dir = sandbox_env["media_dir"]
    state["destination_dir"] = str(media_dir)
    cat = media_dir / "cat"
    sub = cat / "sub"
    sub.mkdir(parents=True)
    (sub / "clip.mp4").write_bytes(b"\x00" * 16)

    async def scenario():
        await run_background_crawler()
        shutil.rmtree(str(sub))
        os.utime(cat, None)
        await run_background_crawler()

    asyncio.run(scenario())

    conn = get_db(sandbox_env["db_path"])
    gone_folder = conn.execute(
        "SELECT 1 FROM folders WHERE path = ?", (str(sub),)
    ).fetchone()
    gone_files = conn.execute(
        "SELECT COUNT(*) FROM files WHERE folder_path = ?", (str(sub),)
    ).fetchone()[0]
    conn.close()
    assert gone_folder is None
    assert gone_files == 0


def test_force_full_rescan_reindexes_unchanged_folders(sandbox_env):
    """The manual sync path (force_full) must not skip anything."""
    media_dir = sandbox_env["media_dir"]
    state["destination_dir"] = str(media_dir)
    cat = media_dir / "cat"
    cat.mkdir()
    (cat / "clip.mp4").write_bytes(b"\x00" * 32)

    async def scenario():
        await run_background_crawler()
        await run_background_crawler(force_full=True)

    asyncio.run(scenario())
    assert indexer_state["skipped_folders"] == 0
    assert indexer_state["scanned_folders"] >= 1


def test_prune_abort_reports_stale_folders_in_status(sandbox_env):
    """A mass remote deletion must surface as a stale index, not as 100%."""
    from media_library.services.status import index_status_payload

    media_dir = sandbox_env["media_dir"]
    state["destination_dir"] = str(media_dir)
    cats = []
    for i in range(40):
        cat = media_dir / f"cat{i:02d}"
        cat.mkdir()
        (cat / "clip.mp4").write_bytes(b"\x00" * 16)
        cats.append(cat)

    asyncio.run(run_background_crawler())
    assert index_status_payload()["is_complete"] is True

    # Delete more than the mount-safety limit (10%, min 25) of the index and
    # re-crawl: the prune must refuse and the status must say so.
    for cat in cats[:30]:
        shutil.rmtree(str(cat))
    os.utime(media_dir, None)
    asyncio.run(run_background_crawler())

    assert indexer_state["prune_aborted"] is True
    assert indexer_state["stale_folders"] == 30

    payload = index_status_payload()
    assert payload["status"] == "idle"
    assert payload["total_folders"] == 40
    assert payload["scanned_folders"] == 10
    assert payload["stale_folders"] == 30
    assert payload["stale_files"] == 30
    assert payload["percent"] < 100
    assert payload["is_complete"] is False
    assert payload["prune_reason"]


def test_forced_prune_clears_stale_rows_and_repairs_status(sandbox_env):
    """The manual prune path removes the stale rows and restores 100%."""
    from media_library.services.status import index_status_payload

    media_dir = sandbox_env["media_dir"]
    state["destination_dir"] = str(media_dir)
    cats = []
    for i in range(40):
        cat = media_dir / f"cat{i:02d}"
        cat.mkdir()
        (cat / "clip.mp4").write_bytes(b"\x00" * 16)
        cats.append(cat)

    asyncio.run(run_background_crawler())
    for cat in cats[:30]:
        shutil.rmtree(str(cat))
    os.utime(media_dir, None)
    asyncio.run(run_background_crawler())
    assert index_status_payload()["is_complete"] is False

    res = prune_stale_index_entries(str(media_dir), None, True, force=True)
    assert res.get("aborted") is not True
    assert res["pruned_folders"] == 30
    assert res["pruned_files"] == 30

    payload = index_status_payload()
    assert payload["total_folders"] == 10
    assert payload["scanned_folders"] == 10
    assert payload["stale_folders"] == 0
    assert payload["percent"] == 100.0
    assert payload["is_complete"] is True
    assert indexer_state["prune_aborted"] is False


def test_prune_skips_folders_outside_the_library(sandbox_env, sample_media):
    """Rows outside the configured library are never considered stale."""
    media_dir = sandbox_env["media_dir"]
    state["destination_dir"] = str(media_dir)
    outsider = sandbox_env["base_dir"] / "elsewhere"
    outsider.mkdir()
    (outsider / "clip.mp4").write_bytes(b"\x00" * 16)
    index_folder(str(outsider), base_dir=str(media_dir))

    res = prune_stale_index_entries(str(media_dir), None, True, force=True)
    assert res.get("aborted") is not True
    assert res["pruned_folders"] == 0

    conn = get_db(sandbox_env["db_path"])
    still_there = conn.execute(
        "SELECT COUNT(*) FROM folders WHERE path = ?", (str(outsider),)
    ).fetchone()[0]
    conn.close()
    assert still_there == 1


def test_suite_never_writes_to_the_configured_database():
    """Guard the autouse DB_PATH isolation: a test must never reach the live DB."""
    from pathlib import Path

    from media_library.core.config import DB_PATH as configured_db_path
    from media_library.db.connection import get_current_db_path

    assert os.environ.get("DB_PATH")
    assert get_current_db_path() != Path(configured_db_path)

