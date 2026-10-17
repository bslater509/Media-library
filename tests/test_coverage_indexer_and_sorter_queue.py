from unittest.mock import patch

import pytest
from fastapi import HTTPException

from media_library.core.state import state
from media_library.db.connection import db_conn
from media_library.db.indexer import (
    _clean_orphaned_thumbnails,
    _path_confirmed_gone,
    delete_path_from_index,
    move_path_in_index,
    prune_stale_index_entries,
)
from media_library.routers.sorter_queue import (
    CreateFolderRequest,
    _resolve_directory_tolerant,
    browse_directory,
    create_destination_subfolder,
    get_quick_nav,
    list_destinations,
)


def test_indexer_move_and_delete_kinds(sandbox_env):
    media_dir = sandbox_env["media_dir"]
    sub1 = media_dir / "sub1"
    sub2 = media_dir / "sub2"
    sub1.mkdir()
    sub2.mkdir()

    # 1. Test move_path_in_index for 'zip'
    old_zip = str(sub1 / "archive.zip")
    new_zip = str(sub2 / "archive.zip")
    with db_conn() as conn, conn:
        conn.execute(
            "INSERT INTO folders (path, name, parent_path, is_zip) VALUES (?, 'archive.zip', ?, 1)",
            (old_zip, str(sub1)),
        )
        conn.execute(
            "INSERT INTO files (path, name, folder_path) VALUES (?, 'inner.jpg', ?)",
            (f"{old_zip}/inner.jpg", old_zip),
        )

    move_path_in_index(old_zip, new_zip, "zip")
    with db_conn() as conn:
        f_row = conn.execute(
            "SELECT path, parent_path FROM folders WHERE path = ?", (new_zip,)
        ).fetchone()
        assert f_row is not None
        assert f_row["parent_path"] == str(sub2)
        file_row = conn.execute(
            "SELECT path, folder_path FROM files WHERE folder_path = ?", (new_zip,)
        ).fetchone()
        assert file_row is not None
        assert file_row["path"] == f"{new_zip}/inner.jpg"

    # Test delete_path_from_index for 'zip'
    delete_path_from_index(new_zip, "zip")
    with db_conn() as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM folders WHERE path = ?", (new_zip,)
            ).fetchone()[0]
            == 0
        )
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM files WHERE folder_path = ?", (new_zip,)
            ).fetchone()[0]
            == 0
        )

    # 2. Test move_path_in_index for 'folder' with descendants
    f_old = str(sub1 / "parent_fold")
    f_new = str(sub2 / "parent_fold")
    f_descendant = str(sub1 / "parent_fold" / "child_fold")
    with db_conn() as conn, conn:
        conn.execute(
            "INSERT INTO folders (path, name, parent_path) VALUES (?, 'parent_fold', ?)",
            (f_old, str(sub1)),
        )
        conn.execute(
            "INSERT INTO folders (path, name, parent_path) VALUES (?, 'child_fold', ?)",
            (f_descendant, f_old),
        )
        conn.execute(
            "INSERT INTO files (path, name, folder_path) VALUES (?, 'child.mp4', ?)",
            (f"{f_descendant}/child.mp4", f_descendant),
        )

    move_path_in_index(f_old, f_new, "folder")
    with db_conn() as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM folders WHERE path = ?", (f_new,)
            ).fetchone()[0]
            == 1
        )
        expected_desc = str(sub2 / "parent_fold" / "child_fold")
        child_row = conn.execute(
            "SELECT path, parent_path FROM folders WHERE path = ?", (expected_desc,)
        ).fetchone()
        assert child_row is not None
        assert child_row["parent_path"] == f_new

    # Test delete_path_from_index for 'folder'
    delete_path_from_index(f_new, "folder")
    with db_conn() as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM folders WHERE path LIKE ?", (f"{f_new}%",)
            ).fetchone()[0]
            == 0
        )
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM files WHERE folder_path LIKE ?", (f"{f_new}%",)
            ).fetchone()[0]
            == 0
        )

    # 3. Test move_path_in_index for 'file'
    p1 = str(sub1 / "video.mp4")
    p2 = str(sub2 / "video.mp4")
    with db_conn() as conn, conn:
        conn.execute(
            "INSERT INTO files (path, name, folder_path, width, height, duration) VALUES (?, 'video.mp4', ?, 1920, 1080, 12.5)",
            (p1, str(sub1)),
        )

    move_path_in_index(p1, p2, "file")
    with db_conn() as conn:
        row = conn.execute("SELECT * FROM files WHERE path = ?", (p2,)).fetchone()
        assert row is not None
        assert row["width"] == 1920
        assert row["height"] == 1080
        assert row["duration"] == 12.5
        assert (
            conn.execute("SELECT COUNT(*) FROM files WHERE path = ?", (p1,)).fetchone()[
                0
            ]
            == 0
        )

    # Test delete_path_from_index for 'file'
    import time

    with db_conn() as conn, conn:
        conn.execute(
            "INSERT OR REPLACE INTO folders (path, name, file_count, total_size) VALUES (?, ?, 1, 100)",
            (str(sub2), "sub2"),
        )
        conn.execute(
            "INSERT INTO ignored_duplicates (path1, path2, match_type, created_at) VALUES (?, '/other', 'quick', ?)",
            (p2, time.time()),
        )

    delete_path_from_index(p2, "file")
    with db_conn() as conn:
        assert (
            conn.execute("SELECT COUNT(*) FROM files WHERE path = ?", (p2,)).fetchone()[
                0
            ]
            == 0
        )
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM ignored_duplicates WHERE path1 = ?", (p2,)
            ).fetchone()[0]
            == 0
        )
        f_info = conn.execute(
            "SELECT file_count FROM folders WHERE path = ?", (str(sub2),)
        ).fetchone()
        assert f_info["file_count"] == 0


def test_path_confirmed_gone(tmp_path):
    f = tmp_path / "test.txt"
    f.write_text("exists")
    assert _path_confirmed_gone(str(f), attempts=2, delay=0.01) is False

    f.unlink()
    assert _path_confirmed_gone(str(f), attempts=2, delay=0.01) is True

    # OSError simulation
    with (
        patch("os.path.isdir", side_effect=OSError("Network failure")),
        patch("os.path.isfile", side_effect=OSError("Network failure")),
    ):
        assert _path_confirmed_gone("/mock/fuse/path", attempts=2, delay=0.01) is True


def test_prune_stale_index_entries_inaccessible_and_safety_limit(sandbox_env):
    media_dir = sandbox_env["media_dir"]
    state["destination_dir"] = str(media_dir)

    # 1. Base dir inaccessible
    res = prune_stale_index_entries(base_dir="/nonexistent/unmounted/library")
    assert res["aborted"] is True
    assert "Base directory unmounted or missing" in res["reason"]

    # 2. Safety limit exceeded: add 30 folders to DB that don't exist on disk
    with db_conn() as conn, conn:
        for i in range(30):
            conn.execute(
                "INSERT OR REPLACE INTO folders (path, name, parent_path) VALUES (?, ?, ?)",
                (f"{media_dir}/missing_{i}", f"missing_{i}", str(media_dir)),
            )

    # Without force, should abort because > 25 missing folders
    res_safe = prune_stale_index_entries(base_dir=str(media_dir), force=False)
    assert res_safe["aborted"] is True
    assert "Too many folders reported missing" in res_safe["reason"]

    # With force=True, should prune them
    res_forced = prune_stale_index_entries(base_dir=str(media_dir), force=True)
    assert res_forced.get("aborted") is not True
    assert res_forced["pruned_folders"] >= 25


def test_clean_orphaned_thumbnails(sandbox_env):
    thumb_dir = sandbox_env["thumb_dir"]
    media_dir = sandbox_env["media_dir"]
    f = media_dir / "valid.mp4"
    f.write_text("v")

    with db_conn() as conn, conn:
        conn.execute(
            "INSERT INTO files (path, name, folder_path, mtime, size) VALUES (?, 'valid.mp4', ?, 100.0, 10)",
            (str(f), str(media_dir)),
        )

    import hashlib

    k = f"{f}_100.0_10"
    h = hashlib.sha256(k.encode("utf-8")).hexdigest()[:24]
    valid_thumb = thumb_dir / f"thumb_{h}.jpg"
    valid_thumb.write_text("valid")

    orphan_thumb = thumb_dir / "thumb_000000000000000000000000.jpg"
    orphan_thumb.write_text("orphan")

    with db_conn() as conn:
        pruned = _clean_orphaned_thumbnails(conn, thumb_dir)
        assert pruned == 1
        assert not orphan_thumb.exists()
        assert valid_thumb.exists()


def test_resolve_tolerant_dir_path(tmp_path):
    # Empty / none
    assert _resolve_directory_tolerant("") == "/"
    assert _resolve_directory_tolerant(None) == "/"

    # Real directory
    d = tmp_path / "normal_folder"
    d.mkdir()
    assert _resolve_directory_tolerant(str(d)) == str(d)

    # Folder with trailing whitespace
    space_dir = tmp_path / "Movies "
    space_dir.mkdir()
    # Requested without trailing space
    matched = _resolve_directory_tolerant(str(tmp_path / "Movies"))
    assert matched == str(space_dir)


def test_browse_folder_structure(sandbox_env):
    media_dir = sandbox_env["media_dir"]
    sub = media_dir / "browse_sub"
    sub.mkdir()
    (sub / "vid.mp4").write_bytes(b"VID")
    (sub / "clip.mkv").write_bytes(b"MKV")
    (sub / "subsub").mkdir()

    # 1. Normal directory browse
    res = browse_directory(str(sub))
    assert res["current_path"] == str(sub)
    assert res["video_count_here"] == 2
    assert len(res["folders"]) == 1
    assert res["folders"][0]["name"] == "subsub"

    # 2. Browse a file path: falls back to parent folder
    res_file = browse_directory(str(sub / "vid.mp4"))
    assert res_file["current_path"] == str(sub)

    # 3. Non-existent path raises 404
    with pytest.raises(HTTPException) as exc:
        browse_directory(str(media_dir / "missing_nonexistent"))
    assert exc.value.status_code == 404

    # 4. OSError raises 503
    with patch("os.stat", side_effect=OSError("FUSE timeout")):
        with pytest.raises(HTTPException) as exc:
            browse_directory(str(sub))
        assert exc.value.status_code == 503


def test_get_quick_nav_and_destinations(sandbox_env):
    media_dir = sandbox_env["media_dir"]
    state["destination_dir"] = str(media_dir)
    state["recent_destinations"] = ["Recent1", "Recent2"]

    # Quick nav
    q = get_quick_nav()
    assert "shortcuts" in q
    labels = [s["label"] for s in q["shortcuts"]]
    assert "Home" in labels

    # Destinations when dest_dir does not exist
    state["destination_dir"] = "/nonexistent/dest_dir"
    d_missing = list_destinations()
    assert d_missing["folders"] == []

    # Destinations with folders
    state["destination_dir"] = str(media_dir)
    (media_dir / "CategoryA").mkdir(exist_ok=True)
    (media_dir / "CategoryB").mkdir(exist_ok=True)

    with db_conn() as conn, conn:
        conn.execute(
            "INSERT OR REPLACE INTO folders (path, name, parent_path) VALUES (?, 'CategoryA', ?)",
            (str(media_dir / "CategoryA"), str(media_dir)),
        )
        conn.execute(
            "INSERT OR REPLACE INTO folders (path, name, parent_path) VALUES (?, 'CategoryB', ?)",
            (str(media_dir / "CategoryB"), str(media_dir)),
        )

    d_valid = list_destinations()
    assert "CategoryA" in d_valid["folders"]
    assert "CategoryB" in d_valid["folders"]


def test_create_destination_subfolder(sandbox_env):
    media_dir = sandbox_env["media_dir"]
    state["destination_dir"] = str(media_dir)
    state["recent_destinations"] = []

    # Invalid names raise 400
    with pytest.raises(HTTPException) as exc:
        create_destination_subfolder(CreateFolderRequest(folder_name=""))
    assert exc.value.status_code == 400

    with pytest.raises(HTTPException) as exc:
        create_destination_subfolder(CreateFolderRequest(folder_name=".hidden"))
    assert exc.value.status_code == 400

    # Valid creation
    res = create_destination_subfolder(
        CreateFolderRequest(folder_name="New Destination")
    )
    assert res["success"] is True
    assert res["folder_name"] == "New Destination"
    assert (media_dir / "New Destination").exists()

    # Verify added to recent
    assert "New Destination" in state["recent_destinations"]
