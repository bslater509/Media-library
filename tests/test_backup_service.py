"""Tests for library backup snapshotting and restore."""

import os
from media_library.core.state import state
from media_library.services.backup_service import (
    _create_library_backup_sync,
    _restore_library_backup_sync,
    get_library_backup_dir,
    get_existing_backup_meta,
)
from media_library.db.indexer import index_folder
from media_library.db.connection import get_db

def test_backup_and_restore_cycle(sandbox_env, sample_media):
    media_dir = sandbox_env["media_dir"]
    action_cat = sample_media["action_cat"]
    state["destination_dir"] = str(media_dir)

    # 1. Index some files
    index_folder(str(action_cat), base_dir=str(media_dir))

    # 2. Run backup snapshot
    res = _create_library_backup_sync()
    assert res["success"] is True

    bck_dir = get_library_backup_dir()
    assert (bck_dir / "backup_index.db").exists()
    assert (bck_dir / "backup_meta.json").exists()

    meta = get_existing_backup_meta()
    assert meta is not None
    assert meta["file_count"] >= 3
    assert "thumb_count" in meta
    assert "disk_thumb_count" in meta
    assert meta["thumb_count"] >= 0

    # Test cache refresh
    refreshed_meta = get_existing_backup_meta(force_refresh=True)
    assert refreshed_meta["timestamp"] == meta["timestamp"]

    # 3. Simulate database wipe
    db_file = sandbox_env["db_path"]
    if db_file.exists():
        db_file.unlink()

    # 4. Restore from backup
    restore_res = _restore_library_backup_sync()
    assert restore_res["success"] is True

    # Check that database exists and has files again
    conn = get_db(sandbox_env["db_path"])
    cnt = conn.execute("SELECT count(*) FROM files").fetchone()[0]
    assert cnt >= 3
    conn.close()

