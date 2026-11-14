"""Explicit poster regeneration scopes and failure safety."""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from media_library import poster_batch, thumbnails
from media_library.app import create_app
from media_library.config import get_settings
from media_library.db import reader, writer
from media_library.writes import WriteError


@pytest.fixture
def video():
    path = str(get_settings().library_dir / "clip.mp4")
    with writer() as conn, conn:
        conn.execute(
            "INSERT INTO files(path, name, folder_path, type, mtime, size, duration, thumb_hash) "
            "VALUES (?, 'clip.mp4', ?, 'video', 1, 10, 100, 'old')",
            (path, str(get_settings().library_dir)),
        )
    return path


@pytest.fixture(autouse=True)
def reset_queue(monkeypatch):
    # Run deterministically; never leave background threads using a test database.
    monkeypatch.setattr(
        poster_batch,
        "threading",
        SimpleNamespace(
            Thread=lambda **kwargs: SimpleNamespace(
                start=lambda: None, is_alive=lambda: True
            ),
        ),
    )
    poster_batch._queue.clear()
    poster_batch._seen.clear()
    poster_batch._thread = None
    poster_batch._state.update(
        status="idle", active=None, queued=0, completed=0, failed=0, error=None
    )
    yield
    poster_batch._queue.clear()
    poster_batch._seen.clear()
    poster_batch._thread = None


def test_folder_scope_and_progress(video, monkeypatch):
    calls = []
    monkeypatch.setattr(thumbnails, "set_poster", lambda p, t: calls.append((p, t)))
    result = poster_batch.enqueue(None, str(get_settings().library_dir))
    assert result["added"] == 1
    assert poster_batch.enqueue([video, video], None)["added"] == 0
    poster_batch._run()
    assert calls == [(video, 20.0)]
    assert poster_batch.status()["completed"] == 1
    assert poster_batch.status()["status"] == "idle"


def test_failed_file_continues(video, monkeypatch):
    def fail(*args):
        raise WriteError("Extraction failed", 500)

    monkeypatch.setattr(thumbnails, "set_poster", fail)
    poster_batch.enqueue([video], None)
    poster_batch._run()
    assert poster_batch.status()["failed"] == 1
    assert poster_batch.status()["error"] == "Extraction failed"


def test_scope_validation():
    for paths, folder in [(None, None), ([], str(get_settings().library_dir))]:
        with pytest.raises(WriteError):
            poster_batch.enqueue(paths, folder)
    with TestClient(create_app()) as client:
        assert (
            client.post(
                "/api/thumbnails/poster/batch", json={"paths": ["/etc/passwd"]}
            ).status_code
            == 403
        )
        assert client.post("/api/thumbnails/poster/batch", json={}).status_code == 422
        assert client.get("/api/thumbnails/poster/status").status_code == 200


def test_poster_clears_visual_hash(video, monkeypatch, tmp_path):
    monkeypatch.setattr(thumbnails, "_extract_frame", lambda *args: None)
    thumbnails.set_poster(video, 20.0)
    with reader() as conn:
        row = conn.execute(
            "SELECT thumb_hash, thumb_mtime FROM files WHERE path = ?", (video,)
        ).fetchone()
    assert row["thumb_hash"] is None
    assert row["thumb_mtime"] > 0


def test_missing_duration_preserves_poster(video, monkeypatch):
    with writer() as conn, conn:
        conn.execute("UPDATE files SET duration = NULL WHERE path = ?", (video,))
    monkeypatch.setattr(thumbnails, "_probe_duration", lambda path: None)
    poster_batch.enqueue([video], None)
    poster_batch._run()
    assert poster_batch.status()["failed"] == 1
    with reader() as conn:
        assert (
            conn.execute(
                "SELECT thumb_hash FROM files WHERE path = ?", (video,)
            ).fetchone()[0]
            == "old"
        )


def test_routes_queue_and_refuse_read_only(video, monkeypatch):
    from media_library.routers import media

    with TestClient(create_app()) as client:
        result = client.post(
            "/api/thumbnails/poster/batch", json={"paths": [video]}
        ).json()
        assert result["added"] == 1
        monkeypatch.setattr(media, "writes_available", lambda: False)
        assert (
            client.post(
                "/api/thumbnails/poster/batch", json={"paths": [video]}
            ).status_code
            == 503
        )
