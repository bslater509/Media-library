"""Empty-folder cleanup tests for v2."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

from media_library_v2 import cleanup
from media_library_v2 import config as v2_config


@pytest.fixture
def empty_env(tmp_path, monkeypatch):
    library = tmp_path / "library"
    (library / "Parent" / "Empty").mkdir(parents=True)
    (library / "Parent" / "Junk").mkdir()
    (library / "Parent" / "Full").mkdir()
    (library / "Parent" / "WithChild" / "Child").mkdir(parents=True)
    (library / "Parent" / "Empty" / ".DS_Store").write_bytes(b"junk")
    (library / "Parent" / "Junk" / "Thumbs.db").write_bytes(b"junk")
    (library / "Parent" / "Full" / "clip.mp4").write_bytes(b"video")

    monkeypatch.setenv("LIBRARY_DIR", str(library))
    monkeypatch.setenv("V2_LIBRARY_DIR", str(library))
    monkeypatch.setenv("V2_DB_READ_ONLY", "false")
    monkeypatch.setenv("V2_WRITES", "true")
    v2_config.reload_settings()

    db_path = Path(os.environ["DB_PATH"])
    with sqlite3.connect(db_path) as conn:
        for folder in (
            library,
            library / "Parent",
            library / "Parent" / "Empty",
            library / "Parent" / "Junk",
            library / "Parent" / "Full",
            library / "Parent" / "WithChild",
            library / "Parent" / "WithChild" / "Child",
        ):
            conn.execute(
                "INSERT INTO folders (path, name, parent_path, file_count, "
                "subfolder_count, last_scanned, is_zip, total_size) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (
                    str(folder),
                    folder.name,
                    str(folder.parent) if folder != library else None,
                    0,
                    0,
                    1.0,
                    0,
                    0,
                ),
            )
        conn.execute(
            "INSERT INTO files (path, name, folder_path, type, ext, size, "
            "size_formatted, mtime, needs_transcode, has_thumb) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                str(library / "Parent" / "Full" / "clip.mp4"),
                "clip.mp4",
                str(library / "Parent" / "Full"),
                "video",
                ".mp4",
                5,
                "5 B",
                1.0,
                0,
                0,
            ),
        )
        conn.execute(
            "UPDATE folders SET file_count = 1 WHERE path = ?",
            (str(library / "Parent" / "Full"),),
        )
        conn.execute(
            "UPDATE folders SET subfolder_count = 4 WHERE path = ?",
            (str(library / "Parent"),),
        )

    try:
        yield {"library": library, "db_path": db_path}
    finally:
        v2_config.reload_settings()


def test_candidates_are_leaf_folders_with_no_files(empty_env):
    result = cleanup.candidates()
    paths = {folder["path"] for folder in result["folders"]}
    assert str(empty_env["library"] / "Parent" / "Empty") in paths
    assert str(empty_env["library"] / "Parent" / "Junk") in paths
    assert str(empty_env["library"] / "Parent" / "Full") not in paths
    assert str(empty_env["library"] / "Parent" / "WithChild") not in paths
    assert str(empty_env["library"] / "Parent" / "WithChild" / "Child") in paths


def test_delete_removes_junk_and_the_folder(empty_env):
    empty = empty_env["library"] / "Parent" / "Empty"
    result = cleanup.delete_empty([str(empty)])
    assert result["deleted"] == [str(empty)]
    assert not empty.exists()

    with sqlite3.connect(empty_env["db_path"]) as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM folders WHERE path = ?", (str(empty),)
            ).fetchone()[0]
            == 0
        )
        parent = conn.execute(
            "SELECT subfolder_count FROM folders WHERE path = ?",
            (str(empty_env["library"] / "Parent"),),
        ).fetchone()[0]
    assert parent == 3


def test_non_empty_and_root_are_refused(empty_env):
    full = empty_env["library"] / "Parent" / "Full"
    result = cleanup.delete_empty([str(full), str(empty_env["library"])])
    assert result["deleted"] == []
    errors = {item["path"]: item["error"] for item in result["failed"]}
    assert errors[str(full)] == "Folder is not empty"
    assert "library root" in errors[str(empty_env["library"])]


def test_missing_folder_is_reported(empty_env):
    missing = str(empty_env["library"] / "Parent" / "Gone")
    result = cleanup.delete_empty([missing])
    assert result["failed"][0]["path"] == missing
