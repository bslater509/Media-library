import asyncio
from unittest.mock import patch, MagicMock
import pytest

from media_library.db.connection import db_conn
from media_library.workers.duplicate_hash_worker import DuplicateHashWorker, ThumbHashWorker


def test_duplicate_hash_worker_methods(sandbox_env):
    worker = DuplicateHashWorker()
    worker.pause()
    assert worker.state["status"] == "paused"
    assert worker.state["is_paused"] is True
    assert worker.is_paused() is True

    worker.resume()
    assert worker.state["status"] == "hashing"
    assert worker.state["is_paused"] is False
    assert worker.is_paused() is False

    st = worker.get_status()
    assert st["status"] == "hashing"


def test_duplicate_hash_worker_run_loop_success(sandbox_env):
    worker = DuplicateHashWorker()
    media_dir = sandbox_env["media_dir"]
    f1 = media_dir / "dup1.mp4"
    f2 = media_dir / "dup2.mp4"
    f1.write_bytes(b"A" * 1024)
    f2.write_bytes(b"A" * 1024)

    with db_conn() as conn, conn:
        conn.execute(
            "INSERT INTO files (path, name, folder_path, size, quick_hash) VALUES (?, ?, ?, ?, NULL)",
            (str(f1), "dup1.mp4", str(media_dir), 1024),
        )
        conn.execute(
            "INSERT INTO files (path, name, folder_path, size, quick_hash) VALUES (?, ?, ?, ?, NULL)",
            (str(f2), "dup2.mp4", str(media_dir), 1024),
        )

    real_sleep = asyncio.sleep
    iteration = 0

    async def _test():
        nonlocal iteration

        async def fast_sleep(sec):
            nonlocal iteration
            iteration += 1
            if iteration >= 4:
                raise asyncio.CancelledError()
            await real_sleep(0)

        with patch("asyncio.sleep", side_effect=fast_sleep):
            try:
                await worker._run_loop()
            except asyncio.CancelledError:
                pass

    asyncio.run(_test())

    with db_conn() as conn:
        rows = conn.execute("SELECT path, quick_hash FROM files WHERE size = 1024").fetchall()
        assert len(rows) == 2
        for r in rows:
            assert r["quick_hash"] is not None
            assert r["quick_hash"] != "unhashable"


def test_duplicate_hash_worker_unhashable_and_idle(sandbox_env):
    worker = DuplicateHashWorker()
    media_dir = sandbox_env["media_dir"]
    missing_file = media_dir / "ghost.mp4"

    with db_conn() as conn, conn:
        conn.execute(
            "INSERT INTO files (path, name, folder_path, size, quick_hash) VALUES (?, ?, ?, ?, NULL)",
            (str(missing_file), "ghost.mp4", str(media_dir), 2048),
        )
        conn.execute(
            "INSERT INTO files (path, name, folder_path, size, quick_hash) VALUES (?, ?, ?, ?, NULL)",
            (str(missing_file) + "_other", "ghost2.mp4", str(media_dir), 2048),
        )

    real_sleep = asyncio.sleep
    iteration = 0

    async def _test():
        nonlocal iteration

        async def fast_sleep(sec):
            nonlocal iteration
            iteration += 1
            if iteration >= 4:
                raise asyncio.CancelledError()
            await real_sleep(0)

        with patch("asyncio.sleep", side_effect=fast_sleep):
            try:
                await worker._run_loop()
            except asyncio.CancelledError:
                pass

    asyncio.run(_test())

    with db_conn() as conn:
        rows = conn.execute("SELECT quick_hash FROM files WHERE size = 2048").fetchall()
        assert any(r["quick_hash"] == "unhashable" for r in rows)


def test_duplicate_hash_worker_db_query_error(sandbox_env):
    worker = DuplicateHashWorker()
    iteration = 0

    async def _test():
        nonlocal iteration

        async def fast_sleep(sec):
            nonlocal iteration
            iteration += 1
            if iteration >= 2:
                raise asyncio.CancelledError()
            await asyncio.sleep(0)

        mock_conn = MagicMock()
        mock_conn.execute.side_effect = RuntimeError("DB Query Failure")
        mock_cm = MagicMock()
        mock_cm.__enter__.return_value = mock_conn
        mock_cm.__exit__.return_value = False

        with patch("asyncio.sleep", side_effect=fast_sleep), patch("media_library.workers.duplicate_hash_worker.db_conn", return_value=mock_cm):
            try:
                await worker._run_loop()
            except asyncio.CancelledError:
                pass

    asyncio.run(_test())
    assert worker.state["total_candidates"] == 0


def test_thumb_hash_worker_methods(sandbox_env):
    worker = ThumbHashWorker()
    st = worker.get_status()
    assert "status" in st
    assert st["status"] == "idle"


def test_thumb_hash_worker_migration(sandbox_env):
    worker = ThumbHashWorker()
    media_dir = sandbox_env["media_dir"]
    vid = media_dir / "vid1.mp4"
    vid.write_bytes(b"viddata")

    with db_conn() as conn, conn:
        # Ensure migration flag is NOT set
        conn.execute("DELETE FROM db_meta WHERE key = 'phash_migration_complete'")
        conn.execute(
            "INSERT INTO files (path, name, folder_path, type, has_thumb, thumb_hash, mtime, size) VALUES (?, ?, ?, 'video', 1, NULL, 1234.0, 50)",
            (str(vid), "vid1.mp4", str(media_dir)),
        )

    real_sleep = asyncio.sleep
    iteration = 0

    async def _test():
        nonlocal iteration

        async def fast_sleep(sec):
            nonlocal iteration
            iteration += 1
            if iteration >= 5:
                raise asyncio.CancelledError()
            await real_sleep(0)

        with patch("asyncio.sleep", side_effect=fast_sleep), patch(
            "media_library.workers.duplicate_hash_worker.compute_thumbnail_phash",
            return_value="testphash123",
        ):
            try:
                await worker._run_loop()
            except asyncio.CancelledError:
                pass

    asyncio.run(_test())

    with db_conn() as conn:
        meta = conn.execute("SELECT value FROM db_meta WHERE key = 'phash_migration_complete'").fetchone()
        assert meta is not None and meta[0] == "1"
        row = conn.execute("SELECT thumb_hash FROM files WHERE path = ?", (str(vid),)).fetchone()
        assert row is not None
        assert row[0] == "testphash123"


def test_thumb_hash_worker_hashing_loop(sandbox_env):
    worker = ThumbHashWorker()
    media_dir = sandbox_env["media_dir"]
    vid = media_dir / "vid2.mp4"
    vid.write_bytes(b"viddata2")

    with db_conn() as conn, conn:
        # Mark migration complete so it jumps straight to hashing loop
        conn.execute("INSERT OR REPLACE INTO db_meta (key, value) VALUES ('phash_migration_complete', '1')")
        conn.execute(
            "INSERT INTO files (path, name, folder_path, type, has_thumb, thumb_hash, mtime, size) VALUES (?, ?, ?, 'video', 1, NULL, 1234.0, 60)",
            (str(vid), "vid2.mp4", str(media_dir)),
        )

    real_sleep = asyncio.sleep
    iteration = 0

    async def _test():
        nonlocal iteration

        async def fast_sleep(sec):
            nonlocal iteration
            iteration += 1
            if iteration >= 5:
                raise asyncio.CancelledError()
            await real_sleep(0)

        with patch("asyncio.sleep", side_effect=fast_sleep), patch(
            "media_library.workers.duplicate_hash_worker.compute_thumbnail_phash",
            return_value="phash999",
        ):
            try:
                await worker._run_loop()
            except asyncio.CancelledError:
                pass

    asyncio.run(_test())

    with db_conn() as conn:
        row = conn.execute("SELECT thumb_hash FROM files WHERE path = ?", (str(vid),)).fetchone()
        assert row is not None
        assert row[0] == "phash999"


def test_thumb_hash_worker_db_error_handling(sandbox_env):
    worker = ThumbHashWorker()
    real_sleep = asyncio.sleep
    iteration = 0

    async def _test():
        nonlocal iteration

        async def fast_sleep(sec):
            nonlocal iteration
            iteration += 1
            if iteration >= 3:
                raise asyncio.CancelledError()
            await real_sleep(0)

        mock_conn = MagicMock()
        # Allow migration check to return migrated
        mock_conn.execute.side_effect = [
            MagicMock(fetchone=lambda: ("1",)),  # db_meta check
            RuntimeError("Query error"),         # files count error
        ]
        mock_cm = MagicMock()
        mock_cm.__enter__.return_value = mock_conn
        mock_cm.__exit__.return_value = False

        with patch("asyncio.sleep", side_effect=fast_sleep), patch(
            "media_library.workers.duplicate_hash_worker.db_conn", return_value=mock_cm
        ):
            try:
                await worker._run_loop()
            except asyncio.CancelledError:
                pass

    asyncio.run(_test())
    assert worker.state["total_thumbnails"] == 0


def test_duplicate_hash_worker_paused_in_loop(sandbox_env):
    worker = DuplicateHashWorker()
    worker.pause()

    real_sleep = asyncio.sleep
    iteration = 0

    async def _test():
        nonlocal iteration

        async def fast_sleep(sec):
            nonlocal iteration
            iteration += 1
            if iteration >= 4:
                raise asyncio.CancelledError()
            await real_sleep(0)

        async def resume_later():
            await real_sleep(0.01)
            worker.resume()

        asyncio.create_task(resume_later())

        with patch("asyncio.sleep", side_effect=fast_sleep):
            try:
                await worker._run_loop()
            except asyncio.CancelledError:
                pass

    asyncio.run(_test())
    assert worker.state["is_paused"] is False


def test_thumb_hash_worker_paused_in_loop(sandbox_env):
    worker = ThumbHashWorker()
    with db_conn() as conn, conn:
        conn.execute("INSERT OR REPLACE INTO db_meta (key, value) VALUES ('phash_migration_complete', '1')")

    worker.pause()
    real_sleep = asyncio.sleep
    iteration = 0

    async def _test():
        nonlocal iteration

        async def fast_sleep(sec):
            nonlocal iteration
            iteration += 1
            if iteration >= 4:
                raise asyncio.CancelledError()
            await real_sleep(0)

        async def resume_later():
            await real_sleep(0.01)
            worker.resume()

        asyncio.create_task(resume_later())

        with patch("asyncio.sleep", side_effect=fast_sleep):
            try:
                await worker._run_loop()
            except asyncio.CancelledError:
                pass

    asyncio.run(_test())
    assert worker.state["is_paused"] is False

