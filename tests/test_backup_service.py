"""Tests for library backup snapshotting and restore."""

from media_library.core.state import state
from media_library.db.connection import get_db
from media_library.db.indexer import index_folder
from media_library.services.backup_service import (
    _create_library_backup_sync,
    _restore_library_backup_sync,
    get_existing_backup_meta,
    get_library_backup_dir,
)


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


def test_backup_service_error_handling(tmp_path, monkeypatch):
    from media_library.services.backup_service import (
        _create_library_backup_sync,
        _restore_library_backup_sync,
    )

    # 1. Restore when no backup exists returns error
    empty_bck = tmp_path / "empty_backup_dir"
    empty_bck.mkdir()
    monkeypatch.setattr("media_library.services.backup_service.LIBRARY_BACKUP_DIR", str(empty_bck))
    res_restore = _restore_library_backup_sync()
    assert res_restore["success"] is False
    assert "no valid database backup found" in res_restore["error"].lower()

    # 2. Backup when destination parent does not exist returns error
    monkeypatch.setattr(
        "media_library.services.backup_service.LIBRARY_BACKUP_DIR",
        str(tmp_path / "nonexistent_parent" / "sub" / "bck"),
    )
    res_create = _create_library_backup_sync()
    assert res_create["success"] is False
    assert "does not exist or is unmounted" in res_create["error"].lower()


def test_check_and_auto_restore_and_thumb_cache_sync(sandbox_env, sample_media):
    from media_library.core.config import THUMBNAIL_CACHE_DIR
    from media_library.db.connection import db_conn
    from media_library.services.backup_service import (
        _create_library_backup_sync,
        check_and_auto_restore_library_backup,
        sync_thumbnail_db_cache,
    )

    media_dir = sandbox_env["media_dir"]
    action_cat = sample_media["action_cat"]
    state["destination_dir"] = str(media_dir)

    index_folder(str(action_cat), base_dir=str(media_dir))
    res_bck = _create_library_backup_sync()
    assert res_bck["success"] is True

    # 1. sync_thumbnail_db_cache with matching files
    vid1 = sample_media["vid1"]
    stat = vid1.stat()
    import hashlib
    key = f"{str(vid1)}_{stat.st_mtime}_{stat.st_size}"
    h = hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
    thumb_file = THUMBNAIL_CACHE_DIR / f"thumb_{h}.jpg"
    thumb_file.write_bytes(b"\xff\xd8\xff\xe0" + b"X" * 100)

    sync_thumbnail_db_cache()
    with db_conn() as conn:
        row = conn.execute("SELECT has_thumb FROM files WHERE path = ?", (str(vid1),)).fetchone()
        assert row is not None
        assert row["has_thumb"] == 1

    # Populate 12 thumbnails so local_thumb_count >= 10
    for i in range(12):
        (THUMBNAIL_CACHE_DIR / f"thumb_{i}.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"T" * 20)

    # 2. check_and_auto_restore_library_backup when database is wiped
    db_file = sandbox_env["db_path"]
    if db_file.exists():
        db_file.unlink()

    restored = check_and_auto_restore_library_backup()
    assert restored is True

    # Call again when database is already populated and thumbnails >= 10
    second_check = check_and_auto_restore_library_backup()
    assert second_check is False

