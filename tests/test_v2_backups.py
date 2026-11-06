"""Backup snapshot/restore tests for v2."""

from __future__ import annotations

import os
import sqlite3
import tarfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from media_library_v2 import backups, writes
from media_library_v2 import config as v2_config
from media_library_v2.mount import build_v2_app


@pytest.fixture
def backup_env(tmp_path, monkeypatch):
    library = tmp_path / "library"
    library.mkdir()
    thumbs = tmp_path / "thumbs"
    (thumbs / "animated").mkdir(parents=True)
    (thumbs / "candidates").mkdir()
    backup_dir = tmp_path / "backup"
    staging = tmp_path / "staging"
    staging.mkdir()
    monkeypatch.setenv("LIBRARY_DIR", str(library))
    monkeypatch.setenv("V2_LIBRARY_DIR", str(library))
    monkeypatch.setenv("THUMBNAIL_DIR", str(thumbs))
    monkeypatch.setenv("LIBRARY_BACKUP_DIR", str(backup_dir))
    monkeypatch.setenv("V2_BACKUP_STAGING_DIR", str(staging))
    monkeypatch.setenv("V2_ALLOW_LIVE_RESTORE", "false")
    v2_config.reload_settings()

    db_path = Path(os.environ["DB_PATH"])
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO folders (path, name, parent_path, file_count, "
            "subfolder_count, last_scanned, is_zip, total_size) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (str(library), "library", None, 2, 0, 1.0, 0, 0),
        )
        for index in range(2):
            conn.execute(
                "INSERT INTO files (path, name, folder_path, type, ext, size, "
                "size_formatted, mtime, needs_transcode, has_thumb) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    str(library / f"clip{index}.mp4"),
                    f"clip{index}.mp4",
                    str(library),
                    "video",
                    ".mp4",
                    100 + index,
                    "100 B",
                    1.0,
                    0,
                    1,
                ),
            )

    for index in range(3):
        Image.new("RGB", (8, 8), (index * 40, 20, 30)).save(
            thumbs / f"thumb_{index}.jpg", "JPEG"
        )
    (thumbs / "animated" / "anim_x.webp").write_bytes(b"anim")
    (thumbs / "candidates" / "cand_1.jpg").write_bytes(b"cand")

    try:
        yield {
            "library": library,
            "thumbs": thumbs,
            "backup_dir": backup_dir,
            "db_path": db_path,
            "staging": staging,
        }
    finally:
        v2_config.reload_settings()


def test_create_snapshot_writes_db_tar_and_meta(backup_env):
    result = backups.create_snapshot()
    assert result["success"] is True
    assert result["files"] == 2
    assert result["folders"] == 1
    assert result["thumbnails"] == 3

    root = backup_env["backup_dir"] / "v2"
    assert (root / "library.db").is_file()
    assert (root / "thumbnails.tar").is_file()
    assert (root / "meta.json").is_file()

    with sqlite3.connect(root / "library.db") as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 2

    with tarfile.open(root / "thumbnails.tar") as archive:
        names = {member.name for member in archive.getmembers() if member.isfile()}
    assert names == {"thumb_0.jpg", "thumb_1.jpg", "thumb_2.jpg"}
    assert not any("anim" in name or "cand" in name for name in names)


def test_status_and_verify_report_the_snapshot(backup_env):
    backups.create_snapshot()
    status = backups.status()
    assert status["exists"] is True
    assert status["files"] == 2
    assert status["thumbnails"] == 3
    assert status["db_bytes"] > 0
    assert status["tar_bytes"] > 0

    verified = backups.verify_snapshot()
    assert verified["snapshot_integrity"] == "ok"
    assert verified["live_integrity"] == "ok"
    assert verified["files"] == 2
    assert verified["thumbnails"] == 3
    assert verified["meta_present"] is True


def test_restore_into_explicit_targets(backup_env, tmp_path):
    backups.create_snapshot()
    target_db = tmp_path / "restored" / "library.db"
    target_thumbs = tmp_path / "restored_thumbs"

    result = backups.restore_snapshot(
        target_db=target_db, target_thumbnails=target_thumbs, allow_live=True
    )
    assert result["restored"] is True
    assert result["files"] == 2
    assert result["thumbnails"] == 3
    assert result["restart_required"] is True

    with sqlite3.connect(target_db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 2
    assert (target_thumbs / "thumb_0.jpg").is_file()
    # The live database and thumbnail dir were not touched.
    with sqlite3.connect(backup_env["db_path"]) as conn:
        assert conn.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 2
    assert (backup_env["thumbs"] / "thumb_0.jpg").is_file()


def test_live_restore_is_refused_by_default(backup_env):
    backups.create_snapshot()
    with pytest.raises(writes.WriteError) as caught:
        backups.restore_snapshot()
    assert caught.value.status == 409
    assert "Live restore is disabled" in caught.value.message


def test_router_snapshot_status_verify_and_download(backup_env):
    client = TestClient(build_v2_app())

    before = client.get("/api/v2/backups/status").json()
    assert before["exists"] is False

    created = client.post("/api/v2/backups/snapshot")
    assert created.status_code == 200, created.text
    assert created.json()["files"] == 2

    status = client.get("/api/v2/backups/status").json()
    assert status["exists"] is True
    assert status["thumbnails"] == 3

    verified = client.get("/api/v2/backups/verify").json()
    assert verified["snapshot_integrity"] == "ok"

    download = client.get("/api/v2/backups/download", params={"kind": "db"})
    assert download.status_code == 200
    assert download.content.startswith(b"SQLite format 3")

    refused = client.post("/api/v2/backups/restore")
    assert refused.status_code == 409
