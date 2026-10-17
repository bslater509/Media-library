import json
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from media_library.db.connection import db_conn
from media_library.services.backup_service import (
    CONFIG_FILE,
    _create_library_backup_sync,
    _restore_library_backup_sync,
    check_and_auto_restore_library_backup,
    get_existing_backup_meta,
    get_library_backup_dir,
)
from media_library.services.library_manage_service import (
    _apply_rename_rule,
    delete_library_path,
    find_empty_folders,
    move_library_path,
    rename_library_path,
)


def test_move_library_path_abort_and_missing(sandbox_env):
    abort_ev = threading.Event()
    abort_ev.set()

    # Move cancelled by user
    with pytest.raises(InterruptedError):
        move_library_path("/any/path", "/any/target", "file", abort_event=abort_ev)

    # Missing source file
    with pytest.raises(FileNotFoundError):
        move_library_path("/missing/source.mp4", "/any/target.mp4", "file")


def test_move_library_path_zip_and_file(sandbox_env):
    media_dir = sandbox_env["media_dir"]
    sub1 = media_dir / "sub1"
    sub2 = media_dir / "sub2"
    sub1.mkdir()
    sub2.mkdir()

    # 1. Test move zip
    zip_src = sub1 / "archive.zip"
    zip_src.write_bytes(b"PK00zip")
    zip_dst = sub2 / "archive.zip"

    res_zip = move_library_path(str(zip_src), str(zip_dst), "zip")
    assert res_zip["target_path"] == str(zip_dst)
    assert zip_dst.exists()
    assert not zip_src.exists()

    # 2. Test move file
    f_src = sub1 / "photo.jpg"
    f_src.write_bytes(b"JPEGDATA")
    f_dst = sub2 / "photo.jpg"

    res_file = move_library_path(str(f_src), str(f_dst), "file")
    assert res_file["target_path"] == str(f_dst)
    assert f_dst.exists()
    assert not f_src.exists()


def test_delete_library_path_kinds_and_abort(sandbox_env):
    abort_ev = threading.Event()
    abort_ev.set()

    with pytest.raises(InterruptedError):
        delete_library_path("/any/file", "file", abort_event=abort_ev)

    media_dir = sandbox_env["media_dir"]

    # 1. Delete zip
    z = media_dir / "del.zip"
    z.write_bytes(b"PKdel")
    delete_library_path(str(z), "zip")
    assert not z.exists()

    # 2. Delete folder
    fold = media_dir / "del_folder"
    fold.mkdir()
    (fold / "inner.txt").write_text("hello")
    delete_library_path(str(fold), "folder")
    assert not fold.exists()

    # 3. Delete file
    f = media_dir / "del.txt"
    f.write_text("del")
    delete_library_path(str(f), "file")
    assert not f.exists()


def test_rename_library_path_validation_and_kinds(sandbox_env):
    media_dir = sandbox_env["media_dir"]
    sandbox_env["state"] = {"destination_dir": str(media_dir)}
    from media_library.core.state import state

    state["destination_dir"] = str(media_dir)

    f = media_dir / "rename_me.jpg"
    f.write_bytes(b"DATA")

    # Invalid names
    for bad in ["", "   ", "sub/name", "sub\\name", ".hidden"]:
        with pytest.raises(ValueError):
            rename_library_path(str(f), bad)

    # Identical name returns success early
    same_res = rename_library_path(str(f), "rename_me.jpg")
    assert same_res["success"] is True

    # Target already exists raises FileExistsError
    existing = media_dir / "existing.jpg"
    existing.write_bytes(b"EXIST")
    with pytest.raises(FileExistsError):
        rename_library_path(str(f), "existing.jpg")

    # Successful file rename
    res = rename_library_path(str(f), "new_name.jpg")
    assert res["success"] is True
    assert (media_dir / "new_name.jpg").exists()
    assert not f.exists()

    # Successful zip rename
    z = media_dir / "old.zip"
    z.write_bytes(b"PK")
    res_z = rename_library_path(str(z), "renamed.zip")
    assert res_z["success"] is True
    assert (media_dir / "renamed.zip").exists()


def test_apply_rename_rules():
    # Case mode
    assert _apply_rename_rule("MyFile", 0, 1, "case", {"case": "upper"}) == "MYFILE"
    assert _apply_rename_rule("MyFile", 0, 1, "case", {"case": "lower"}) == "myfile"
    assert _apply_rename_rule("my file", 0, 1, "case", {"case": "title"}) == "My File"

    # Find replace mode
    assert (
        _apply_rename_rule(
            "file_2020", 0, 1, "find_replace", {"find": "2020", "replace": "2026"}
        )
        == "file_2026"
    )
    with pytest.raises(ValueError):
        _apply_rename_rule("file", 0, 1, "find_replace", {"find": ""})

    # Numbering mode
    assert (
        _apply_rename_rule(
            "pic",
            2,
            5,
            "numbering",
            {"start": 1, "step": 1, "padding": 3, "position": "prefix"},
        )
        == "003pic"
    )
    assert (
        _apply_rename_rule(
            "pic",
            2,
            5,
            "numbering",
            {"start": 1, "step": 1, "padding": 0, "position": "suffix"},
        )
        == "pic3"
    )

    # Base counter mode
    assert (
        _apply_rename_rule(
            "old_name", 0, 10, "base_counter", {"base": "img", "start": 1, "padding": 2}
        )
        == "img01"
    )
    with pytest.raises(ValueError):
        _apply_rename_rule("old_name", 0, 10, "base_counter", {"base": ""})

    # Unknown mode
    with pytest.raises(ValueError):
        _apply_rename_rule("file", 0, 1, "unknown_mode", {})


def test_find_empty_library_folders(sandbox_env):
    media_dir = sandbox_env["media_dir"]

    # 1. Truly empty folder
    empty_f = media_dir / "empty_dir"
    empty_f.mkdir()

    # 2. Folder containing benign files (.DS_Store, Thumbs.db)
    benign_f = media_dir / "benign_dir"
    benign_f.mkdir()
    (benign_f / ".DS_Store").write_text("junk")
    (benign_f / "Thumbs.db").write_text("junk")

    # 3. Folder containing a real file
    real_f = media_dir / "real_dir"
    real_f.mkdir()
    (real_f / "video.mp4").write_text("video")

    # Index folders in DB
    with db_conn() as conn, conn:
        conn.execute(
            "INSERT OR REPLACE INTO folders (path, name, parent_path, file_count, subfolder_count) VALUES (?, ?, ?, 0, 0)",
            (str(empty_f), "empty_dir", str(media_dir)),
        )
        conn.execute(
            "INSERT OR REPLACE INTO folders (path, name, parent_path, file_count, subfolder_count) VALUES (?, ?, ?, 0, 0)",
            (str(benign_f), "benign_dir", str(media_dir)),
        )
        conn.execute(
            "INSERT OR REPLACE INTO folders (path, name, parent_path, file_count, subfolder_count) VALUES (?, ?, ?, 1, 0)",
            (str(real_f), "real_dir", str(media_dir)),
        )
        conn.execute(
            "INSERT OR REPLACE INTO files (path, name, folder_path) VALUES (?, ?, ?)",
            (str(real_f / "video.mp4"), "video.mp4", str(real_f)),
        )

    res = find_empty_folders(str(media_dir))
    candidates = [c["path"] for c in res["candidates"]]
    assert str(empty_f) in candidates
    assert str(benign_f) in candidates
    assert str(real_f) not in candidates


def test_backup_service_get_meta_and_corrupt(sandbox_env):
    bck_dir = get_library_backup_dir()
    bck_dir.mkdir(parents=True, exist_ok=True)
    meta_path = bck_dir / "backup_meta.json"

    # Valid meta
    sample_data = {"timestamp": 12345678, "file_count": 42}
    meta_path.write_text(json.dumps(sample_data))

    meta = get_existing_backup_meta(force_refresh=True)
    assert meta is not None
    assert meta["file_count"] == 42

    # Corrupt meta
    meta_path.write_text("corrupted json content")
    bad_meta = get_existing_backup_meta(force_refresh=True)
    assert bad_meta is None


def test_backup_service_create_and_restore_cycle(sandbox_env):
    media_dir = sandbox_env["media_dir"]
    from media_library.core.state import state

    state["destination_dir"] = str(media_dir)

    # Populate some files and thumbnails (>=10 thumbnails so healthy check passes)
    f1 = media_dir / "vid1.mp4"
    f1.write_bytes(b"12345")
    thumb_dir = sandbox_env["thumb_dir"]
    for i in range(12):
        (thumb_dir / f"thumb_{i}.jpg").write_bytes(b"THUMB")

    with db_conn() as conn, conn:
        conn.execute(
            "INSERT INTO folders (path, name) VALUES (?, ?)", (str(media_dir), "media")
        )
        conn.execute(
            "INSERT INTO files (path, name, folder_path, size, has_thumb) VALUES (?, ?, ?, 5, 1)",
            (str(f1), "vid1.mp4", str(media_dir)),
        )

    # Save a config file
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(
        json.dumps({"source_dir": str(media_dir), "recent_destinations": ["Action"]})
    )

    progress_create = []
    res_create = _create_library_backup_sync(
        progress_cb=lambda msg, pct: progress_create.append(pct)
    )
    assert res_create["success"] is True
    assert len(progress_create) > 0

    # Test auto restore check returns False when database and thumbnails are healthy
    assert check_and_auto_restore_library_backup() is False

    # Simulate database loss
    db_p = sandbox_env["db_path"]
    db_p.unlink()

    # Now auto restore check should detect missing db and restore!
    restored = check_and_auto_restore_library_backup()
    assert restored is True
    assert db_p.exists()

    with db_conn() as conn:
        row = conn.execute("SELECT count(*) FROM files").fetchone()
        assert row[0] >= 1


def test_backup_service_create_missing_parent(sandbox_env):
    with patch(
        "media_library.services.backup_service.get_library_backup_dir",
        return_value=Path("/nonexistent_drive/unmounted/bck"),
    ):
        res = _create_library_backup_sync()
        assert res["success"] is False
        assert "does not exist or is unmounted" in res["error"]


def test_backup_service_restore_no_valid_backup(sandbox_env):
    with patch(
        "media_library.services.backup_service.get_library_backup_dir",
        return_value=Path("/empty/backup/dir"),
    ):
        res = _restore_library_backup_sync()
        assert res["success"] is False
        assert "No valid database backup found" in res["error"]
