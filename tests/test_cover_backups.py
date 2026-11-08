"""Coverage tests for backups, ZIP routers, the batch runner and x265."""

from __future__ import annotations

import io
import os
import sqlite3
import tarfile
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from media_library import backups, writes, zip_batch, zip_ops
from media_library import config as v2_config
from media_library.app import create_app
from media_library.zip_batch import ZipBatchRunner


@pytest.fixture
def backup_env(tmp_path, monkeypatch):
    """Point every snapshot setting at a throwaway directory with a row."""
    library = tmp_path / "library"
    library.mkdir()
    thumbs = tmp_path / "thumbs"
    thumbs.mkdir()
    backup_dir = tmp_path / "backup"
    staging = tmp_path / "staging"
    staging.mkdir()
    monkeypatch.setenv("LIBRARY_DIR", str(library))
    monkeypatch.setenv("THUMBNAIL_DIR", str(thumbs))
    monkeypatch.setenv("LIBRARY_BACKUP_DIR", str(backup_dir))
    monkeypatch.setenv("BACKUP_STAGING_DIR", str(staging))
    monkeypatch.setenv("ALLOW_LIVE_RESTORE", "false")
    v2_config.reload_settings()

    db_path = Path(os.environ["DB_PATH"])
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO folders (path, name, parent_path, file_count, "
            "subfolder_count, last_scanned, is_zip, total_size) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (str(library), "library", None, 1, 0, 1.0, 0, 0),
        )
        conn.execute(
            "INSERT INTO files (path, name, folder_path, type, ext, size, "
            "size_formatted, mtime, needs_transcode, has_thumb) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                str(library / "clip.mp4"),
                "clip.mp4",
                str(library),
                "video",
                ".mp4",
                100,
                "100 B",
                1.0,
                0,
                1,
            ),
        )
    for index in range(2):
        (thumbs / f"thumb_{index}.jpg").write_bytes(f"poster{index}".encode())

    try:
        yield {
            "library": library,
            "thumbs": thumbs,
            "backup_dir": backup_dir,
            "staging": staging,
            "db_path": db_path,
        }
    finally:
        v2_config.reload_settings()


def _wait(predicate, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


class _CorruptCheckConnection:
    """Wraps a real connection; integrity probes always answer "corrupt"."""

    def __init__(self, real: sqlite3.Connection) -> None:
        self._real = real

    def execute(self, sql: str, *args, **kwargs):
        if "integrity_check" in sql:

            class _Row:
                def fetchone(self) -> tuple[str]:
                    return ("corrupt",)

            return _Row()
        return self._real.execute(sql, *args, **kwargs)

    def close(self) -> None:
        self._real.close()


# -- media_library/backups.py -------------------------------------------------


def test_counts_tolerate_a_broken_connection():
    conn = sqlite3.connect(":memory:")
    try:
        assert backups._counts(conn) == (0, 0)
    finally:
        conn.close()


def test_index_health_tolerates_a_failed_connection(tmp_path, monkeypatch):
    skeleton = tmp_path / "skeleton.db"
    skeleton.write_bytes(b"SQLite format 3\x00")

    def exploding_connect(*args, **kwargs):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(sqlite3, "connect", exploding_connect)
    assert backups._index_health(skeleton) == (False, 0, 0)


def test_index_health_tolerates_a_failed_census(tmp_path, monkeypatch):
    healthy = tmp_path / "healthy.db"
    sqlite3.connect(healthy).close()

    def raising_counts(conn):
        raise sqlite3.OperationalError("swept away")

    monkeypatch.setattr(backups, "_counts", raising_counts)
    assert backups._index_health(healthy) == (False, 0, 0)


def test_status_ignores_an_unreadable_meta(backup_env):
    basic = backups.status()
    assert basic["exists"] is False
    assert basic["db_bytes"] == 0

    backup_env["backup_dir"].mkdir()
    (backup_env["backup_dir"] / "meta.json").write_bytes(b"{not json")
    broken = backups.status()
    assert broken["exists"] is False
    assert broken["created_at"] is None
    assert broken["version"] is None


def test_full_snapshot_verify_and_restore_roundtrip(backup_env, tmp_path):
    result = backups.create_snapshot()
    assert result["success"] is True
    assert result["files"] == 1
    assert result["folders"] == 1
    assert result["thumbnails"] == 2
    root = backup_env["backup_dir"]
    assert (root / "library.db").is_file()
    assert (root / "thumbnails.tar").is_file()
    assert (root / "meta.json").is_file()

    status = backups.status()
    assert status["exists"] is True
    assert status["files"] == 1
    assert status["db_bytes"] > 0
    assert status["created_at_str"]

    verified = backups.verify_snapshot()
    assert verified["snapshot_integrity"] == "ok"
    assert verified["live_integrity"] == "ok"
    assert verified["files"] == 1
    assert verified["thumbnails"] == 2
    assert verified["meta_present"] is True

    target_db = tmp_path / "restored" / "library.db"
    target_thumbs = tmp_path / "restored_thumbs"
    restored = backups.restore_snapshot(
        target_db=target_db, target_thumbnails=target_thumbs, allow_live=True
    )
    assert restored["restored"] is True
    assert restored["restart_required"] is True
    assert (target_thumbs / "thumb_0.jpg").is_file()

    with sqlite3.connect(target_db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 1


def test_restore_snapshot_refuses_to_touch_the_live_index(backup_env):
    backups.create_snapshot()
    with pytest.raises(writes.WriteError) as caught:
        backups.restore_snapshot()
    assert caught.value.status == 409
    assert "Live restore is disabled" in caught.value.message


def test_auto_restore_covers_every_outcome(backup_env):
    # A usable index is never touched.
    assert backups.auto_restore_if_missing() == {
        "restored": False,
        "reason": "index present",
        "files": 1,
    }

    # A lost index and emptied poster cache recover from the snapshot.
    backups.create_snapshot()
    backup_env["db_path"].unlink()
    for poster in backup_env["thumbs"].glob("thumb_*.jpg"):
        poster.unlink()
    recovered = backups.auto_restore_if_missing()
    assert recovered["restored"] is True
    assert recovered["source"] == "snapshot"
    assert recovered["files"] == 1
    assert len(list(backup_env["thumbs"].glob("thumb_*.jpg"))) == 2

    # Neither index nor snapshot: nothing to do.
    backup_env["db_path"].unlink()
    for path in backup_env["backup_dir"].iterdir():
        path.unlink()
    assert backups.auto_restore_if_missing() == {
        "restored": False,
        "reason": "no usable snapshot",
    }


def test_create_snapshot_without_thumbnails(backup_env, tmp_path, monkeypatch):
    monkeypatch.setenv("THUMBNAIL_DIR", str(tmp_path / "empty_thumbs"))
    v2_config.reload_settings()
    result = backups.create_snapshot()
    v2_config.reload_settings()
    assert result["success"] is True
    assert result["thumbnails"] == 0
    with tarfile.open(backup_env["backup_dir"] / "thumbnails.tar") as archive:
        assert archive.getmembers() == []


def test_create_snapshot_refuses_to_overlap(backup_env):
    assert backups._lock.acquire() is True
    try:
        with pytest.raises(writes.WriteError) as caught:
            backups.create_snapshot()
        assert caught.value.status == 409
        assert "already being created" in caught.value.message
    finally:
        backups._lock.release()


def test_create_snapshot_reports_a_failed_integrity_check(backup_env, monkeypatch):
    real_connect = sqlite3.connect
    staged_seen = []

    def fake_connect(*args, **kwargs):
        target = str(args[0]) if args else "unknown"
        real = real_connect(*args, **kwargs)
        if target.endswith(".tmp"):
            if staged_seen:
                return _CorruptCheckConnection(real)
            staged_seen.append(True)
        return real

    monkeypatch.setattr(sqlite3, "connect", fake_connect)
    with pytest.raises(writes.WriteError) as caught:
        backups.create_snapshot()
    assert caught.value.status == 500
    assert "failed its check" in caught.value.message


def test_create_snapshot_skips_unreadable_thumbnails(backup_env, monkeypatch):
    ghost = backup_env["thumbs"] / "thumb_ghost.jpg"
    monkeypatch.setattr(backups, "_thumbnail_files", lambda: [ghost])
    result = backups.create_snapshot()
    assert result["success"] is True
    assert result["thumbnails"] == 1
    with tarfile.open(backup_env["backup_dir"] / "thumbnails.tar") as archive:
        assert archive.getmembers() == []


def test_create_snapshot_aborts_on_a_truncated_copy(backup_env, monkeypatch):
    def fake_copyfile(src, dst, **kwargs):
        with open(src, "rb") as fin, open(dst, "wb") as fout:
            fout.write(fin.read(4))

    monkeypatch.setattr("shutil.copyfile", fake_copyfile)
    with pytest.raises(writes.WriteError) as caught:
        backups.create_snapshot()
    assert caught.value.status == 500
    assert "truncated" in caught.value.message
    assert not (backup_env["backup_dir"] / "library.db").is_file()


def test_create_snapshot_tolerates_undeletable_staging_litter(backup_env):
    junk = backup_env["staging"] / "junk.tmp"
    junk.mkdir()
    result = backups.create_snapshot()
    assert result["success"] is True
    assert junk.is_dir()


def test_verify_snapshot_requires_a_snapshot(backup_env):
    with pytest.raises(writes.WriteError) as caught:
        backups.verify_snapshot()
    assert caught.value.status == 404
    assert "No snapshot found" in caught.value.message


def test_restore_from_refuses_missing_sources(backup_env, tmp_path):
    ghost_db = tmp_path / "missing.db"
    ghost_tar = tmp_path / "missing.tar"
    with pytest.raises(writes.WriteError) as caught:
        backups._restore_from(ghost_db, ghost_tar, tmp_path / "out.db", tmp_path / "t")
    assert caught.value.status == 404


def _make_snapshot_pair(tmp_path: Path) -> tuple[Path, Path]:
    db_source = tmp_path / "snap.db"
    with sqlite3.connect(db_source) as conn:
        conn.execute("CREATE TABLE marker (value TEXT)")
        conn.execute("INSERT INTO marker VALUES ('ok')")
    tar_source = tmp_path / "snap.tar"
    return db_source, tar_source


def test_restore_from_rejects_a_corrupt_snapshot(backup_env, tmp_path, monkeypatch):
    db_source, tar_source = _make_snapshot_pair(tmp_path)
    real_connect = sqlite3.connect
    monkeypatch.setattr(
        sqlite3,
        "connect",
        lambda *args, **kwargs: _CorruptCheckConnection(real_connect(*args, **kwargs)),
    )
    tar_source.write_bytes(b"x")
    with pytest.raises(writes.WriteError) as caught:
        backups._restore_from(
            db_source, tar_source, tmp_path / "out.db", tmp_path / "thumbs"
        )
    assert caught.value.status == 400
    assert "corrupt" in caught.value.message


def test_restore_from_skips_reserved_tar_members(backup_env, tmp_path):
    db_source, tar_source = _make_snapshot_pair(tmp_path)
    extract_to = tmp_path / "extracted"
    nested = tmp_path / "sub"
    nested.mkdir()
    (nested / "thumb_1.jpg").write_bytes(b"one")
    non_thumb = tmp_path / "leaf.webp"
    non_thumb.write_bytes(b"webp")
    empty_dir = tmp_path / "fromdisk"
    empty_dir.mkdir()

    with tarfile.open(tar_source, "a") as archive:
        archive.add(empty_dir, arcname="sub")
        archive.add(non_thumb, arcname="anim_x.webp")
        archive.add(nested / "thumb_1.jpg", arcname="sub/thumb_1.jpg")
        archive.add(non_thumb.parent / "leaf.webp", arcname="thumb_0.jpg")

    result = backups._restore_from(
        db_source, tar_source, tmp_path / "out.db", extract_to
    )
    assert result["restored"] is True
    assert result["thumbnails"] == 1
    assert (extract_to / "thumb_0.jpg").is_file()
    assert not (extract_to / "thumb_1.jpg").exists()


def test_restore_from_skips_unextractable_members(backup_env, tmp_path, monkeypatch):
    db_source, tar_source = _make_snapshot_pair(tmp_path)
    extract_to = tmp_path / "extracted"
    payload = tmp_path / "payload.jpg"
    payload.write_bytes(b"poster")
    with tarfile.open(tar_source, "a") as archive:
        archive.add(payload, arcname="thumb_solo.jpg")
    monkeypatch.setattr(tarfile.TarFile, "extractfile", lambda self, member: None)

    result = backups._restore_from(
        db_source, tar_source, tmp_path / "out.db", extract_to
    )
    assert result["restored"] is True
    assert result["thumbnails"] == 0
    assert not (extract_to / "thumb_solo.jpg").exists()


def test_restore_snapshot_reports_a_missing_snapshot(backup_env, tmp_path):
    with pytest.raises(writes.WriteError) as caught:
        backups.restore_snapshot(
            target_db=tmp_path / "out.db",
            target_thumbnails=tmp_path / "thumbs",
            allow_live=True,
        )
    assert caught.value.status == 404


# -- media_library/routers/backups.py -----------------------------------------


def test_router_snapshot_status_and_restore(backup_env, monkeypatch):
    monkeypatch.setenv("ALLOW_LIVE_RESTORE", "true")
    v2_config.reload_settings()
    backups.create_snapshot()
    client = TestClient(create_app())

    status = client.get("/api/backups/status")
    assert status.status_code == 200, status.text
    assert status.json()["exists"] is True
    assert status.json()["thumbnails"] == 2

    restored = client.post("/api/backups/restore")
    assert restored.status_code == 200, restored.text
    assert restored.json()["restored"] is True


def test_router_backup_error_paths(backup_env):
    client = TestClient(create_app())

    missing = client.get("/api/backups/verify")
    assert missing.status_code == 404
    assert missing.json()["detail"] == "No snapshot found"

    refused = client.post("/api/backups/restore")
    assert refused.status_code == 409
    assert refused.json()["detail"].startswith("Live restore is disabled")

    backups._lock.acquire()
    try:
        conflict = client.post("/api/backups/snapshot")
        assert conflict.status_code == 409
        assert conflict.json()["detail"] == "A snapshot is already being created"
    finally:
        backups._lock.release()


def test_router_download_variants(backup_env):
    backup_env["backup_dir"].mkdir()
    tar_path = backup_env["backup_dir"] / "thumbnails.tar"
    payload = io.BytesIO(b"poster")
    info = tarfile.TarInfo("thumb_0.jpg")
    info.size = payload.getbuffer().nbytes
    with tarfile.open(tar_path, "w") as archive:
        archive.addfile(info, payload)
    client = TestClient(create_app())

    thumbs = client.get("/api/backups/download", params={"kind": "thumbs"})
    assert thumbs.status_code == 200, thumbs.text
    assert thumbs.headers["cache-control"] == "no-store"

    assert (
        client.get("/api/backups/download", params={"kind": "tar"}).status_code == 200
    )
    assert (
        client.get("/api/backups/download", params={"kind": "zip"}).status_code == 400
    )
    assert client.get("/api/backups/download", params={"kind": "db"}).status_code == 404


# -- media_library/routers/zip.py ---------------------------------------------


def test_zip_router_translates_errors(monkeypatch):
    def boom(path):
        raise RuntimeError("boom")

    monkeypatch.setattr(zip_ops, "unpack_archive", boom)
    client = TestClient(create_app())

    assert client.post("/api/zip/unpack", json={"path": "/x"}).status_code == 500


def test_zip_router_lists_the_overview(monkeypatch):
    archive = [
        {
            "path": "/a.zip",
            "name": "a.zip",
            "file_count": 3,
            "size_formatted": "3 KB",
        }
    ]
    monkeypatch.setattr(zip_ops, "archives", lambda: archive)
    monkeypatch.setattr(zip_ops, "cache_info", lambda: {"bytes": 1})
    client = TestClient(create_app())

    overview = client.get("/api/zip/overview")
    assert overview.status_code == 200, overview.text
    payload = overview.json()
    assert payload["archives"][0]["name"] == "a.zip"
    assert "folders" not in payload


# -- media_library/routers/zip_batch.py and media_library/zip_batch.py --------


def test_zip_batch_router_full_control(monkeypatch):
    fresh = ZipBatchRunner()
    monkeypatch.setattr(zip_batch, "runner", fresh)
    blocker = threading.Event()

    def blocking(path):
        blocker.wait(3)
        return {"folder": path}

    monkeypatch.setattr(zip_ops, "unpack_archive", blocking)
    client = TestClient(create_app())

    started = client.post(
        "/api/zip/batch/start", json={"mode": "unpack", "paths": ["/a.zip"]}
    )
    assert started.status_code == 200, started.text
    assert started.json()["status"] == "running"

    status = client.get("/api/zip/batch/status")
    assert status.status_code == 200, status.text
    assert status.json()["total"] == 1

    paused = client.post("/api/zip/batch/pause")
    assert paused.status_code == 200, paused.text
    assert paused.json()["status"] == "paused"

    resumed = client.post("/api/zip/batch/resume")
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["status"] == "running"

    blocker.set()
    assert _wait(lambda: fresh.status()["status"] == "completed")

    after = client.get("/api/zip/batch/status")
    assert after.json()["succeeded"] == 1

    assert client.post("/api/zip/batch/pause").status_code == 409
    assert client.post("/api/zip/batch/cancel").status_code == 409


def test_zip_batch_router_refusals(monkeypatch):
    fresh = ZipBatchRunner()
    monkeypatch.setattr(zip_batch, "runner", fresh)
    client = TestClient(create_app())

    resumed = client.post("/api/zip/batch/resume")
    assert resumed.status_code == 409
    assert resumed.json()["detail"] == "No paused ZIP batch"

    cancelled = client.post("/api/zip/batch/cancel")
    assert cancelled.status_code == 409
    assert cancelled.json()["detail"] == "No ZIP batch to cancel"

    assert (
        client.post(
            "/api/zip/batch/start", json={"mode": "zap", "paths": []}
        ).status_code
        == 400
    )


def test_batch_rejects_bad_requests(monkeypatch):
    runner = ZipBatchRunner()

    with pytest.raises(writes.WriteError) as caught:
        runner.start("zap", ["/a.zip"])
    assert caught.value.status == 400

    monkeypatch.setattr(zip_ops, "archives", list)
    with pytest.raises(writes.WriteError) as empty:
        runner.start("unpack")
    assert empty.value.status == 400


def test_batch_default_queue_for_unpack(monkeypatch):
    runner = ZipBatchRunner()
    seen: list[str] = []
    monkeypatch.setattr(
        zip_ops, "archives", lambda: [{"path": "/a.zip"}, {"path": "/b.zip"}]
    )

    def fake(path):
        seen.append(path)
        return {"archive": path}

    monkeypatch.setattr(zip_ops, "unpack_archive", fake)
    status = runner.start("unpack")
    assert status["status"] == "running"
    assert status["total"] == 2
    assert _wait(lambda: runner.status()["status"] == "completed")
    assert seen == ["/a.zip", "/b.zip"]


def test_batch_double_start_is_refused(monkeypatch):
    runner = ZipBatchRunner()
    blocker = threading.Event()

    def blocking(path):
        blocker.wait(3)
        return {"archive": path}

    monkeypatch.setattr(zip_ops, "unpack_archive", blocking)
    runner.start("unpack", ["/one.zip"])
    assert _wait(lambda: runner.status()["status"] == "running")
    with pytest.raises(writes.WriteError) as caught:
        runner.start("unpack", ["/two.zip"])
    assert caught.value.status == 409
    runner.cancel()
    blocker.set()


def test_batch_error_log_is_capped_at_twenty_entries(monkeypatch):
    runner = ZipBatchRunner()
    paths = [f"/f{index}" for index in range(21)]

    def fail(path):
        raise RuntimeError("cannot unpack")

    monkeypatch.setattr(zip_ops, "unpack_archive", fail)
    runner.start("unpack", paths)
    assert _wait(lambda: runner.status()["status"] == "completed")
    final = runner.status()
    assert final["failed"] == 21
    assert len(final["errors"]) == 20
    assert [entry["path"] for entry in final["errors"]] == paths[1:]
    assert final["percent"] == 100.0


def test_batch_pauses_between_items_and_resumes(monkeypatch):
    runner = ZipBatchRunner()

    def slow(path):
        time.sleep(0.08)
        return {"folder": path}

    monkeypatch.setattr(zip_ops, "unpack_archive", slow)
    runner.start("unpack", ["/1.zip", "/2.zip", "/3.zip", "/4.zip"])
    assert _wait(lambda: runner.status()["processed"] >= 1)
    paused = runner.pause()
    assert paused["status"] == "paused"
    paused_at = runner.status()["processed"]
    time.sleep(0.2)
    assert runner.status()["processed"] <= paused_at + 1
    resumed = runner.resume()
    assert resumed["status"] == "running"
    assert _wait(lambda: runner.status()["status"] == "completed")
    assert runner.status()["succeeded"] == 4


def test_batch_cancelled_while_paused_skips_the_queue(monkeypatch):
    runner = ZipBatchRunner()
    blocker = threading.Event()

    def blocking(path):
        blocker.wait(3)
        return {"folder": path}

    monkeypatch.setattr(zip_ops, "unpack_archive", blocking)
    runner.start("unpack", ["/1.zip", "/2.zip", "/3.zip"])
    assert _wait(lambda: runner.status()["status"] == "running")
    runner.pause()
    cancelled = runner.cancel()
    assert cancelled["status"] == "cancelled"
    blocker.set()
    assert _wait(lambda: runner.status()["finished_at"] is not None)
    final = runner.status()
    # Only the archive already in flight may have completed.
    assert final["processed"] <= 1
    assert final["processed"] < 3
    assert final["succeeded"] == final["processed"]
    assert final["status"] == "cancelled"


# -- media_library/routers/x265.py --------------------------------------------


def test_x265_cancel_router_translates_errors(monkeypatch):
    from media_library.routers import x265 as x265_router

    def boom(job_id):
        raise RuntimeError("boom")

    monkeypatch.setattr(x265_router.manager, "cancel", boom)
    client = TestClient(create_app())
    response = client.post("/api/transcode/x265/cancel", json={"job_id": "gone"})
    assert response.status_code == 500
    assert response.json()["detail"] == "Internal error"


def test_x265_start_router_translates_errors(monkeypatch):
    from media_library.routers import x265 as x265_router

    async def boom(path):
        raise RuntimeError("boom")

    monkeypatch.setattr(x265_router.manager, "enqueue", boom)
    client = TestClient(create_app())
    response = client.post("/api/transcode/x265", json={"path": "/clip.mkv"})
    assert response.status_code == 500
    assert response.json()["detail"] == "Internal error"


def test_x265_router_starts_and_reports_a_job(monkeypatch):
    from media_library.routers import x265 as x265_router

    async def fake_enqueue(path):
        return {
            "id": "job-1",
            "source_path": path,
            "output_path": None,
            "status": "queued",
            "percent": 0.0,
            "fps": "0",
            "speed": "0",
            "eta": "unknown",
            "error": "",
            "duration": 0.0,
            "audio_mode": "copy",
            "created_at": 0.0,
        }

    monkeypatch.setattr(x265_router.manager, "enqueue", fake_enqueue)
    client = TestClient(create_app())

    status = client.get("/api/transcode/x265/status")
    assert status.status_code == 200, status.text
    assert status.json()["active_count"] == 0
    assert status.json()["queued_count"] == 0

    started = client.post("/api/transcode/x265", json={"path": "/clip.mkv"})
    assert started.status_code == 200, started.text
    assert started.json()["id"] == "job-1"
    assert started.json()["status"] == "queued"
