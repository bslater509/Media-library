"""Folder and ZIP rename: disk, index subtree, favorites and artwork."""

from __future__ import annotations

import os
import sqlite3
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from media_library_v2 import cache
from media_library_v2 import config as v2_config
from media_library_v2.mount import build_v2_app


@pytest.fixture
def rename_env(tmp_path, monkeypatch):
    library = tmp_path / "library"
    thumbs = tmp_path / "thumbs"
    (thumbs / "animated").mkdir(parents=True)
    (library / "A" / "sub").mkdir(parents=True)
    (library / "B").mkdir()
    (library / "A" / "one.jpg").write_bytes(b"one" * 100)
    (library / "A" / "sub" / "deep.jpg").write_bytes(b"deep" * 50)
    (library / "B" / "two.jpg").write_bytes(b"two" * 20)
    with zipfile.ZipFile(library / "B" / "bundle.zip", "w") as bundle:
        bundle.writestr("inside.jpg", b"member")

    monkeypatch.setenv("LIBRARY_DIR", str(library))
    monkeypatch.setenv("V2_LIBRARY_DIR", str(library))
    monkeypatch.setenv("THUMBNAIL_DIR", str(thumbs))
    monkeypatch.setenv("V2_DB_READ_ONLY", "false")
    monkeypatch.setenv("V2_WRITES", "true")
    v2_config.reload_settings()

    db_path = Path(os.environ["DB_PATH"])
    folders = [library, library / "A", library / "A" / "sub", library / "B"]
    with sqlite3.connect(db_path) as conn:
        for folder in folders:
            conn.execute(
                "INSERT INTO folders (path, name, parent_path, file_count, "
                "subfolder_count, total_size, is_zip) VALUES (?,?,?,?,?,?,?)",
                (
                    str(folder),
                    folder.name,
                    str(folder.parent) if folder != library else None,
                    0,
                    0,
                    0,
                    0,
                ),
            )
        for path in (library / "A" / "one.jpg", library / "A" / "sub" / "deep.jpg"):
            stat = path.stat()
            conn.execute(
                "INSERT INTO files (path, name, folder_path, type, ext, size, "
                "size_formatted, mtime, needs_transcode, has_thumb) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    str(path),
                    path.name,
                    str(path.parent),
                    "image",
                    ".jpg",
                    stat.st_size,
                    f"{stat.st_size} B",
                    stat.st_mtime,
                    0,
                    1,
                ),
            )
        conn.execute(
            "INSERT INTO favorites (path, created_at) VALUES (?, ?)",
            (str(library / "A" / "sub" / "deep.jpg"), 1.0),
        )

    # A poster for the nested file, at the key the cache derives from its path.
    nested = library / "A" / "sub" / "deep.jpg"
    stat = nested.stat()
    poster = cache.thumbnail_path(str(nested), stat.st_mtime, stat.st_size)
    poster.write_bytes(b"poster")

    try:
        yield {
            "client": TestClient(build_v2_app()),
            "library": library,
            "thumbs": thumbs,
            "db_path": db_path,
            "nested": nested,
            "poster": poster,
        }
    finally:
        v2_config.reload_settings()


def test_rename_folder_rewrites_disk_and_index(rename_env):
    library = rename_env["library"]
    response = rename_env["client"].post(
        "/api/v2/manage/folders/rename",
        json={"path": str(library / "A"), "name": "Renamed A"},
    )
    assert response.status_code == 200
    assert response.json()["path"] == str(library / "Renamed A")

    assert (library / "Renamed A" / "one.jpg").is_file()
    assert (library / "Renamed A" / "sub" / "deep.jpg").is_file()
    assert not (library / "A").exists()

    with sqlite3.connect(rename_env["db_path"]) as conn:
        folders = {row[0] for row in conn.execute("SELECT path FROM folders")}
        files = {
            row[0]: row[1]
            for row in conn.execute("SELECT path, folder_path FROM files")
        }
        favorite = conn.execute("SELECT path FROM favorites").fetchone()[0]
        parent = conn.execute(
            "SELECT parent_path FROM folders WHERE path = ?",
            (str(library / "Renamed A" / "sub"),),
        ).fetchone()[0]
    assert str(library / "Renamed A") in folders
    assert str(library / "Renamed A" / "sub") in folders
    assert str(library / "A") not in folders
    assert str(library / "Renamed A" / "one.jpg") in files
    assert files[str(library / "Renamed A" / "sub" / "deep.jpg")] == str(
        library / "Renamed A" / "sub"
    )
    assert parent == str(library / "Renamed A")
    assert favorite == str(library / "Renamed A" / "sub" / "deep.jpg")


def test_rename_folder_moves_the_cached_poster(rename_env):
    library = rename_env["library"]
    old_poster = rename_env["poster"]
    assert old_poster.is_file()

    rename_env["client"].post(
        "/api/v2/manage/folders/rename",
        json={"path": str(library / "A"), "name": "Moved"},
    )

    renamed = library / "Moved" / "sub" / "deep.jpg"
    new_stat = renamed.stat()
    new_poster = cache.thumbnail_path(str(renamed), new_stat.st_mtime, new_stat.st_size)
    assert new_poster.is_file()
    assert not old_poster.is_file()


def test_rename_zip_updates_member_paths(rename_env):
    library = rename_env["library"]
    # Index the archive and its member the way the crawler does.
    with sqlite3.connect(rename_env["db_path"]) as conn:
        conn.execute(
            "INSERT INTO folders (path, name, parent_path, file_count, "
            "subfolder_count, total_size, is_zip) VALUES (?,?,?,?,?,?,?)",
            (
                str(library / "B" / "bundle.zip"),
                "bundle.zip",
                str(library / "B"),
                1,
                0,
                6,
                1,
            ),
        )
        conn.execute(
            "INSERT INTO files (path, name, folder_path, type, ext, size, "
            "size_formatted, mtime, needs_transcode, has_thumb) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                f"{library}/B/bundle.zip/inside.jpg",
                "inside.jpg",
                f"{library}/B/bundle.zip",
                "image",
                ".jpg",
                6,
                "6 B",
                1.0,
                0,
                0,
            ),
        )

    response = rename_env["client"].post(
        "/api/v2/manage/folders/rename",
        json={"path": str(library / "B" / "bundle.zip"), "name": "renamed.zip"},
    )
    assert response.status_code == 200
    assert (library / "B" / "renamed.zip").is_file()

    with sqlite3.connect(rename_env["db_path"]) as conn:
        member = conn.execute(
            "SELECT path, folder_path FROM files WHERE path LIKE ?",
            ("%renamed.zip/inside.jpg",),
        ).fetchone()
        archive = conn.execute(
            "SELECT is_zip FROM folders WHERE path = ?",
            (str(library / "B" / "renamed.zip"),),
        ).fetchone()
    assert member is not None
    assert member[1] == str(library / "B" / "renamed.zip")
    assert archive is not None and archive[0] == 1


def test_rename_folder_refuses_collisions_and_bad_names(rename_env):
    library = rename_env["library"]
    client = rename_env["client"]
    collision = client.post(
        "/api/v2/manage/folders/rename",
        json={"path": str(library / "A"), "name": "B"},
    )
    assert collision.status_code == 409
    bad = client.post(
        "/api/v2/manage/folders/rename",
        json={"path": str(library / "A"), "name": "../escape"},
    )
    assert bad.status_code == 400
    not_a_folder = client.post(
        "/api/v2/manage/folders/rename",
        json={"path": str(library / "A" / "one.jpg"), "name": "x.jpg"},
    )
    assert not_a_folder.status_code == 400


def test_move_folder_to_another_parent(rename_env):
    library = rename_env["library"]
    response = rename_env["client"].post(
        "/api/v2/manage/move",
        json={
            "paths": [str(library / "A")],
            "destination": str(library / "B"),
            "conflict": "auto_rename",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["failed"] == []
    target = library / "B" / "A"
    assert target.is_dir()
    assert (target / "sub" / "deep.jpg").is_file()

    with sqlite3.connect(rename_env["db_path"]) as conn:
        favorite = conn.execute("SELECT path FROM favorites").fetchone()[0]
        folder = conn.execute(
            "SELECT parent_path FROM folders WHERE path = ?", (str(target / "sub"),)
        ).fetchone()
    assert favorite == str(target / "sub" / "deep.jpg")
    assert folder is not None and folder[0] == str(target)

    poster = cache.thumbnail_path(
        str(target / "sub" / "deep.jpg"),
        (target / "sub" / "deep.jpg").stat().st_mtime,
        (target / "sub" / "deep.jpg").stat().st_size,
    )
    assert poster.is_file()


def test_cannot_move_a_folder_into_itself(rename_env):
    library = rename_env["library"]
    response = rename_env["client"].post(
        "/api/v2/manage/move",
        json={
            "paths": [str(library / "A")],
            "destination": str(library / "A" / "sub"),
            "conflict": "auto_rename",
        },
    )
    assert response.status_code == 200
    assert response.json()["failed"][0]["error"] == "Cannot move a folder into itself"
    assert (library / "A" / "sub").is_dir()


def test_rename_does_not_rewrite_a_sibling_matching_the_like_pattern(rename_env):
    """Prefix edits are exact: ``_``/``%`` in one name must not touch another."""
    library = rename_env["library"]
    source = library / "my_folder"
    sibling = library / "myXfolder"
    (source / "sub").mkdir(parents=True)
    sibling.mkdir()
    (source / "sub" / "keep.jpg").write_bytes(b"k" * 10)
    (sibling / "other.jpg").write_bytes(b"o" * 10)

    with sqlite3.connect(rename_env["db_path"]) as conn:
        for folder in (source, source / "sub", sibling):
            conn.execute(
                "INSERT INTO folders (path, name, parent_path, file_count, "
                "subfolder_count, total_size, is_zip) VALUES (?,?,?,?,?,?,?)",
                (
                    str(folder),
                    folder.name,
                    str(folder.parent),
                    0,
                    0,
                    0,
                    0,
                ),
            )
        for media in (source / "sub" / "keep.jpg", sibling / "other.jpg"):
            stat = media.stat()
            conn.execute(
                "INSERT INTO files (path, name, folder_path, type, ext, size, "
                "size_formatted, mtime, needs_transcode, has_thumb) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    str(media),
                    media.name,
                    str(media.parent),
                    "image",
                    ".jpg",
                    stat.st_size,
                    "10 B",
                    stat.st_mtime,
                    0,
                    1,
                ),
            )

    response = rename_env["client"].post(
        "/api/v2/manage/folders/rename",
        json={"path": str(source), "name": "renamed"},
    )
    assert response.status_code == 200, response.text

    with sqlite3.connect(rename_env["db_path"]) as conn:
        files = {row[0] for row in conn.execute("SELECT path FROM files")}
        folders = {row[0] for row in conn.execute("SELECT path FROM folders")}
    # The renamed subtree moved...
    assert str(library / "renamed" / "sub" / "keep.jpg") in files
    assert str(library / "renamed" / "sub") in folders
    # ...and the one-character-different sibling is untouched.
    assert str(sibling / "other.jpg") in files
    assert str(library / "renamedXfolder") not in folders
    assert str(library / "renamedXfolder" / "other.jpg") not in files
