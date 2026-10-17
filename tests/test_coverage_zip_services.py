import os
import time
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

from media_library.db.connection import db_conn
from media_library.services.zip.core import (
    get_zip_cache_dir,
    index_zip_archive,
    manage_lru_cache,
    resolve_zip_file_path,
    split_zip_path,
    zip_lock,
    zip_tasks_state,
)
from media_library.services.zip.extract import (
    extract_zip_to_cache,
    unpack_zip_to_folder,
)


def test_split_and_resolve_zip_file_path(sandbox_env):
    # Split zip path test
    zpath, inner = split_zip_path("/media/archive.zip/photos/pic.jpg")
    assert zpath == "/media/archive.zip"
    assert inner == "photos/pic.jpg"

    none_z, none_in = split_zip_path("/media/regular_folder/pic.jpg")
    assert none_z is None and none_in == ""

    # resolve_zip_file_path non-zip returns None
    assert resolve_zip_file_path("/media/regular_folder/pic.jpg") is None

    # resolve_zip_file_path non-existent cache returns None
    assert resolve_zip_file_path("/media/nonexistent.zip/pic.jpg") is None

    # Set up a real cache directory for an archive
    archive_path = str(sandbox_env["media_dir"] / "test_archive.zip")
    cache_dir = get_zip_cache_dir(archive_path)
    cache_dir.mkdir(parents=True, exist_ok=True)
    marker = cache_dir / ".extracted_done"
    marker.write_text("done")

    # 1. Direct path check
    direct_file = cache_dir / "folder" / "direct.jpg"
    direct_file.parent.mkdir(parents=True, exist_ok=True)
    direct_file.write_bytes(b"direct_img")

    resolved = resolve_zip_file_path(f"{archive_path}/folder/direct.jpg")
    assert resolved == str(direct_file)

    # 2. Immediate subdirectory match check (common root stripped/preserved)
    sub_root = cache_dir / "common_root"
    sub_root.mkdir(parents=True, exist_ok=True)
    sub_file = sub_root / "nested.jpg"
    sub_file.write_bytes(b"nested_img")

    resolved_sub = resolve_zip_file_path(f"{archive_path}/nested.jpg")
    assert resolved_sub == str(sub_file)

    # 3. Fallback: match by basename
    deep_dir = cache_dir / "deep" / "nested" / "path"
    deep_dir.mkdir(parents=True, exist_ok=True)
    deep_file = deep_dir / "unique_deep.jpg"
    deep_file.write_bytes(b"deep_img")

    resolved_deep = resolve_zip_file_path(f"{archive_path}/anywhere/unique_deep.jpg")
    assert resolved_deep == str(deep_file)


def test_index_zip_archive_and_missing(sandbox_env):
    # Non existent zip returns early
    index_zip_archive("/nonexistent/file.zip")

    media_dir = sandbox_env["media_dir"]
    zip_path = media_dir / "gallery.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("photo1.jpg", b"image1")
        zf.writestr("video1.mp4", b"video1")
        zf.writestr("doc.txt", b"text")
        zf.writestr(".hidden.jpg", b"hidden")
        zf.writestr("__MACOSX/._photo1.jpg", b"meta")

    index_zip_archive(str(zip_path))

    with db_conn() as conn:
        f_row = conn.execute(
            "SELECT * FROM folders WHERE path = ?", (str(zip_path),)
        ).fetchone()
        assert f_row is not None
        assert f_row["is_zip"] == 1
        assert (
            f_row["file_count"] == 3
        )  # photo1, video1, doc.txt (excludes hidden & macosx)

        files = conn.execute(
            "SELECT * FROM files WHERE folder_path = ? ORDER BY name", (str(zip_path),)
        ).fetchall()
        assert len(files) == 3
        names = [f["name"] for f in files]
        assert "photo1.jpg" in names
        assert "video1.mp4" in names
        assert "doc.txt" in names


def test_manage_lru_cache_stale_cleaning_and_eviction(sandbox_env):
    import media_library.services.zip.core as zip_core_mod

    # Non existent cache dir returns early
    orig_cache = zip_core_mod.ZIP_CACHE_DIR
    try:
        zip_core_mod.ZIP_CACHE_DIR = Path("/nonexistent/zip_cache")
        manage_lru_cache()
    finally:
        zip_core_mod.ZIP_CACHE_DIR = orig_cache

    cache_dir = zip_core_mod.ZIP_CACHE_DIR
    cache_dir.mkdir(parents=True, exist_ok=True)
    staging_dir = zip_core_mod.ZIP_STAGING_DIR
    staging_dir.mkdir(parents=True, exist_ok=True)

    # Create stale staging dir (>600s old)
    old_time = time.time() - 1000
    stale_staging = cache_dir / "test_staging_abc123"
    stale_staging.mkdir(parents=True, exist_ok=True)
    (stale_staging / "temp.txt").write_text("old")
    os.utime(stale_staging, (old_time, old_time))

    # Create fresh staging dir
    fresh_staging = cache_dir / "fresh_staging_xyz789"
    fresh_staging.mkdir(parents=True, exist_ok=True)

    # Create stale staging file in ZIP_STAGING_DIR
    stale_tmp_file = staging_dir / ".tmp_old_file"
    stale_tmp_file.write_text("old_tmp")
    os.utime(stale_tmp_file, (old_time, old_time))

    # Create normal cache entry
    c1 = cache_dir / "c1"
    c1.mkdir(parents=True, exist_ok=True)
    (c1 / "file1.dat").write_bytes(b"X" * 1024)
    (c1 / ".extracted_done").touch()
    os.utime(c1, (old_time, old_time))

    # Populate active_tasks with > 100 completed items to test active_tasks pruning
    with zip_lock:
        for i in range(110):
            zip_tasks_state["active_tasks"][f"/dummy/task_{i}.zip"] = {
                "type": "extract",
                "status": "completed",
            }
        # Mark c1's path as NOT active
        zip_tasks_state["active_tasks"]["/active/task.zip"] = {
            "type": "extract",
            "status": "running",
        }

    # Set cache max size small (e.g. 0.0000001 GB ~ 100 bytes) so c1 exceeds quota
    with patch("media_library.services.zip.core.ZIP_CACHE_MAX_SIZE_GB", 0.0000001):
        manage_lru_cache()

    assert not stale_staging.exists()
    assert fresh_staging.exists()
    assert not stale_tmp_file.exists()
    # c1 should be evicted because it exceeds the tiny quota
    assert not c1.exists()

    # Verify active tasks pruned down
    assert len(zip_tasks_state["active_tasks"]) <= 60


def test_extract_zip_to_cache_done_marker(sandbox_env):
    media_dir = sandbox_env["media_dir"]
    zip_path = media_dir / "quick.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("test.txt", b"hello")

    cache_dir = get_zip_cache_dir(str(zip_path))
    cache_dir.mkdir(parents=True, exist_ok=True)
    marker = cache_dir / ".extracted_done"
    marker.write_text("done")

    res = extract_zip_to_cache(str(zip_path))
    assert res == cache_dir


def test_extract_zip_to_cache_missing_zip(sandbox_env):
    missing_zip = str(sandbox_env["media_dir"] / "gone.zip")
    with zip_lock:
        zip_tasks_state["active_tasks"][missing_zip] = {
            "type": "extract",
            "status": "running",
        }

    res = extract_zip_to_cache(missing_zip)
    assert res is None
    with zip_lock:
        assert missing_zip not in zip_tasks_state["active_tasks"]


def test_extract_zip_to_cache_full_and_slip_protection(sandbox_env):
    media_dir = sandbox_env["media_dir"]
    zip_path = media_dir / "common_root.zip"

    # Zip with common root and normal files + zip slip member inside common root
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("my_folder/pic1.jpg", b"pic1_content")
        zf.writestr("my_folder/pic2.jpg", b"pic2_content")
        zf.writestr("my_folder/.hidden", b"hidden")
        zf.writestr("my_folder/__MACOSX/._pic1.jpg", b"meta")
        # Attempt zip-slip traversal
        zf.writestr("my_folder/../../etc/passwd", b"malicious")

    progress_calls = []

    def on_progress(cur, total, name):
        progress_calls.append((cur, total, name))

    res_dir = extract_zip_to_cache(str(zip_path), progress_cb=on_progress)
    assert res_dir.exists()
    assert (res_dir / ".extracted_done").exists()

    # Common prefix 'my_folder/' should have been stripped
    assert (res_dir / "pic1.jpg").exists()
    assert (res_dir / "pic2.jpg").exists()
    assert not (res_dir / "../../etc/passwd").exists()
    assert len(progress_calls) > 0

    with zip_lock:
        task = zip_tasks_state["active_tasks"].get(str(zip_path))
        assert task is not None
        assert task["status"] == "completed"
        assert task["percent"] == 100.0


def test_extract_zip_to_cache_failure_cleanup(sandbox_env):
    media_dir = sandbox_env["media_dir"]
    corrupt_zip = media_dir / "corrupted.zip"
    corrupt_zip.write_bytes(b"not a valid zip file")

    with pytest.raises(zipfile.BadZipFile):
        extract_zip_to_cache(str(corrupt_zip))

    with zip_lock:
        task = zip_tasks_state["active_tasks"].get(str(corrupt_zip))
        assert task is not None
        assert task["status"] == "failed"


def test_unpack_zip_to_folder_success_and_collision(sandbox_env):
    media_dir = sandbox_env["media_dir"]
    zip_path = media_dir / "holiday.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("beach.jpg", b"beach_bytes")
        zf.writestr("sunset.jpg", b"sunset_bytes")

    # First, index the zip in DB
    index_zip_archive(str(zip_path))

    # Also create a folder "holiday" so collision renaming triggers "holiday_1"
    existing_folder = media_dir / "holiday"
    existing_folder.mkdir()
    (existing_folder / "existing.txt").write_text("old")

    progress_events = []
    res = unpack_zip_to_folder(
        str(zip_path), progress_cb=lambda c, t, n: progress_events.append((c, t))
    )

    assert res["success"] is True
    # Name collided, so holiday_1 created
    assert res["folder_name"] == "holiday_1"
    unpacked_dir = Path(res["folder_path"])
    assert unpacked_dir.exists()
    assert (unpacked_dir / "beach.jpg").read_bytes() == b"beach_bytes"
    assert (unpacked_dir / "sunset.jpg").read_bytes() == b"sunset_bytes"

    # Original zip must be deleted
    assert not zip_path.exists()

    # Index updated: old zip path removed, new folder indexed
    with db_conn() as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM folders WHERE path = ?", (str(zip_path),)
            ).fetchone()[0]
            == 0
        )
        new_f = conn.execute(
            "SELECT COUNT(*) FROM folders WHERE path = ?", (str(unpacked_dir),)
        ).fetchone()[0]
        assert new_f == 1


def test_unpack_zip_to_folder_validation_and_errors(sandbox_env):
    # Non-zip extension raises ValueError
    with pytest.raises(ValueError):
        unpack_zip_to_folder("/media/not_a_zip.tar")

    # Non-existent zip raises ValueError
    with pytest.raises(ValueError):
        unpack_zip_to_folder("/media/missing.zip")

    # Corrupt zip raises BadZipFile and updates task failure
    media_dir = sandbox_env["media_dir"]
    corrupt_zip = media_dir / "broken.zip"
    corrupt_zip.write_bytes(b"garbagetext")

    with pytest.raises(zipfile.BadZipFile):
        unpack_zip_to_folder(str(corrupt_zip))

    with zip_lock:
        task = zip_tasks_state["active_tasks"].get(str(corrupt_zip))
        assert task is not None
        assert task["status"] == "failed"
