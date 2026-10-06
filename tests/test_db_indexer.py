"""Tests for database initialization, folder indexing, and pruning."""

import os
import shutil
from media_library.db.connection import get_db
from media_library.db.indexer import (
    index_folder,
    add_file_to_index,
    remove_file_from_index,
    update_file_in_index,
    prune_stale_index_entries,
)

def test_db_schema_tables(sandbox_env):
    conn = get_db(sandbox_env["db_path"])
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
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
    rows = conn.execute("SELECT name, type, size FROM files WHERE folder_path = ?", (str(action_cat),)).fetchall()
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
    cur = conn.execute("SELECT name FROM files WHERE folder_path = ?", (str(comedy_cat),))
    assert cur.fetchone() is not None
    conn.close()

    # Remove the entire comedy folder from disk
    shutil.rmtree(str(comedy_cat))

    # Run prune
    prune_res = prune_stale_index_entries(base_dir=str(media_dir), clean_orphaned_thumbnails=False)
    assert prune_res["pruned_folders"] >= 1

    conn = get_db(sandbox_env["db_path"])
    cur = conn.execute("SELECT name FROM files WHERE folder_path = ?", (str(comedy_cat),))
    assert cur.fetchone() is None
    cur_folder = conn.execute("SELECT path FROM folders WHERE path = ?", (str(comedy_cat),))
    assert cur_folder.fetchone() is None
    conn.close()
