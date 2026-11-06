"""v2 crawler tests: indexing, incremental skips, pruning, ZIP members."""

from __future__ import annotations

import os
import sqlite3
import zipfile
from pathlib import Path

import pytest

from media_library_v2 import config as v2_config
from media_library_v2 import indexer


@pytest.fixture
def crawl_env(tmp_path, monkeypatch):
    library = tmp_path / "library"
    (library / "A" / "sub").mkdir(parents=True)
    (library / "B").mkdir()
    (library / ".media_library_backup").mkdir()
    (library / "A" / "one.jpg").write_bytes(b"one" * 100)
    (library / "A" / "two.mp4").write_bytes(b"two" * 50)
    (library / "A" / "sub" / "deep.jpg").write_bytes(b"deep" * 20)
    (library / "B" / "three.jpg").write_bytes(b"three" * 10)
    (library / ".media_library_backup" / "hidden.jpg").write_bytes(b"x")

    with zipfile.ZipFile(library / "B" / "bundle.zip", "w") as bundle:
        bundle.writestr("inside.txt", "text")
        bundle.writestr("img.jpg", b"image-bytes")

    monkeypatch.setenv("LIBRARY_DIR", str(library))
    monkeypatch.setenv("V2_LIBRARY_DIR", str(library))
    monkeypatch.setenv("V2_DB_READ_ONLY", "false")
    monkeypatch.setenv("V2_WRITES", "true")
    monkeypatch.setenv("V2_OWN_WORKERS", "false")
    v2_config.reload_settings()
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

    try:
        yield {"library": library, "db_path": Path(os.environ["DB_PATH"])}
    finally:
        v2_config.reload_settings()


def _db(env):
    return sqlite3.connect(env["db_path"])


def test_crawl_indexes_folders_files_and_zips(crawl_env):
    result = indexer.crawl()
    assert result["status"] == "idle"
    assert result["revision"] == 1
    library = crawl_env["library"]

    with _db(crawl_env) as conn:
        folders = {
            row[0]: row[1:]
            for row in conn.execute(
                "SELECT path, is_zip, file_count FROM folders"
            ).fetchall()
        }
        files = {
            row[0]: (row[1], row[2])
            for row in conn.execute("SELECT path, type, size FROM files").fetchall()
        }
    assert str(library) in folders
    assert str(library / "A") in folders
    assert str(library / "A" / "sub") in folders
    assert str(library / "B") in folders
    assert str(library / ".media_library_backup") not in folders
    assert folders[str(library / "B" / "bundle.zip")][0] == 1
    assert folders[str(library / "A")][1] == 2
    assert files[str(library / "A" / "one.jpg")][0] == "image"
    assert files[str(library / "A" / "two.mp4")][0] == "video"
    assert files[str(library / "A" / "sub" / "deep.jpg")][0] == "image"
    assert files[str(library / "B" / "three.jpg")][0] == "image"
    assert f"{library}/B/bundle.zip/img.jpg" in files
    assert f"{library}/B/bundle.zip/inside.txt" in files
    assert str(library / ".media_library_backup" / "hidden.jpg") not in files


def test_second_crawl_skips_unchanged_folders(crawl_env):
    indexer.crawl()
    first = indexer.status()
    second = indexer.crawl()
    assert second["revision"] == 2
    assert second["scanned_folders"] == 0
    assert second["skipped_folders"] >= 4
    assert second["skipped_folders"] == second["total_folders"]
    assert first["last_synced"] > 0


def test_new_and_deleted_files_are_reconciled(crawl_env):
    indexer.crawl()
    library = crawl_env["library"]
    (library / "A" / "new.jpg").write_bytes(b"new")
    indexer.crawl()
    with _db(crawl_env) as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM files WHERE path = ?",
                (str(library / "A" / "new.jpg"),),
            ).fetchone()[0]
            == 1
        )

    (library / "A" / "one.jpg").unlink()
    indexer.crawl()
    with _db(crawl_env) as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM files WHERE path = ?",
                (str(library / "A" / "one.jpg"),),
            ).fetchone()[0]
            == 0
        )


def test_deleted_subtree_is_pruned(crawl_env):
    indexer.crawl()
    library = crawl_env["library"]
    import shutil

    shutil.rmtree(library / "A" / "sub")
    indexer.crawl()
    with _db(crawl_env) as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM folders WHERE path = ?",
                (str(library / "A" / "sub"),),
            ).fetchone()[0]
            == 0
        )
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM files WHERE folder_path = ?",
                (str(library / "A" / "sub"),),
            ).fetchone()[0]
            == 0
        )


def test_changed_zip_is_reindexed(crawl_env):
    indexer.crawl()
    library = crawl_env["library"]
    with zipfile.ZipFile(library / "B" / "bundle.zip", "a") as bundle:
        bundle.writestr("added.jpg", b"added")
    indexer.crawl()
    with _db(crawl_env) as conn:
        members = {
            row[0]
            for row in conn.execute(
                "SELECT path FROM files WHERE folder_path = ?",
                (str(library / "B" / "bundle.zip"),),
            ).fetchall()
        }
    assert f"{library}/B/bundle.zip/added.jpg" in members
    assert f"{library}/B/bundle.zip/img.jpg" in members


def test_mass_disappearance_aborts_pruning(crawl_env):
    library = crawl_env["library"]
    mass = library / "M"
    mass.mkdir()
    for index in range(6):
        child = mass / f"c{index}"
        child.mkdir()
        (child / "pic.jpg").write_bytes(b"p")
    indexer.crawl()
    import shutil

    for index in range(4):
        shutil.rmtree(mass / f"c{index}")

    result = indexer.crawl()
    assert result["prune_aborted"] >= 1
    assert result["stale_folders"] >= 4
    with _db(crawl_env) as conn:
        remaining = conn.execute(
            "SELECT COUNT(*) FROM folders WHERE parent_path = ?", (str(mass),)
        ).fetchone()[0]
    assert remaining == 6


def test_sync_endpoint_requires_worker_ownership(crawl_env):
    from fastapi.testclient import TestClient

    from media_library_v2.mount import build_v2_app

    client = TestClient(build_v2_app())
    status = client.get("/api/v2/system/index/status").json()
    assert status["owned"] is False
    assert client.post("/api/v2/system/index/sync").status_code == 409


def test_owned_crawl_sweeps_orphaned_artwork(crawl_env, tmp_path, monkeypatch):
    thumbs = tmp_path / "thumbs"
    (thumbs / "animated").mkdir(parents=True)
    orphan = thumbs / f"thumb_{'d' * 24}.jpg"
    orphan.write_bytes(b"poster")
    monkeypatch.setenv("THUMBNAIL_DIR", str(thumbs))
    monkeypatch.setenv("V2_OWN_WORKERS", "true")
    v2_config.reload_settings()

    result = indexer.crawl()

    assert result["artwork_swept"]["thumbnails"] == 1
    assert not orphan.exists()
