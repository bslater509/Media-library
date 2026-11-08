"""Batch ZIP runner tests (unpack only, no real archives)."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from media_library import writes, zip_batch, zip_ops
from media_library.app import create_app
from media_library.zip_batch import ZipBatchRunner


def _wait(predicate, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def runner():
    return ZipBatchRunner()


def test_batch_unpacks_every_path(monkeypatch, runner):
    seen: list[str] = []

    def fake(path):
        seen.append(path)
        return {"folder": path[:-4]}

    monkeypatch.setattr(zip_ops, "unpack_archive", fake)
    status = runner.start("unpack", ["/a.zip", "/b.zip", "/c.zip"])
    assert status["status"] == "running"
    assert status["mode"] == "unpack"
    assert status["total"] == 3

    assert _wait(lambda: runner.status()["status"] == "completed")
    final = runner.status()
    assert final["succeeded"] == 3
    assert final["processed"] == 3
    assert final["percent"] == 100.0
    assert seen == ["/a.zip", "/b.zip", "/c.zip"]


def test_failures_are_reported_and_the_batch_continues(monkeypatch, runner):
    def fake(path):
        if path == "/boom.zip":
            raise RuntimeError("cannot unpack")
        return {"folder": path[:-4]}

    monkeypatch.setattr(zip_ops, "unpack_archive", fake)
    runner.start("unpack", ["/ok1.zip", "/boom.zip", "/ok2.zip"])
    assert _wait(lambda: runner.status()["status"] == "completed")
    final = runner.status()
    assert final["succeeded"] == 2
    assert final["failed"] == 1
    assert final["errors"][0]["path"] == "/boom.zip"
    assert "cannot unpack" in final["errors"][0]["error"]


def test_pause_and_resume(monkeypatch, runner):
    def slow(path):
        time.sleep(0.12)
        return {"folder": path}

    monkeypatch.setattr(zip_ops, "unpack_archive", slow)
    runner.start("unpack", ["/1.zip", "/2.zip", "/3.zip"])
    assert _wait(lambda: runner.status()["processed"] >= 1)
    paused = runner.pause()
    assert paused["status"] == "paused"
    processed_at_pause = runner.status()["processed"]
    time.sleep(0.25)
    # At most the item already in flight can finish while paused.
    assert runner.status()["processed"] <= processed_at_pause + 1
    resumed = runner.resume()
    assert resumed["status"] == "running"
    assert _wait(lambda: runner.status()["status"] == "completed")
    assert runner.status()["succeeded"] == 3


def test_cancel_stops_the_queue(monkeypatch, runner):
    def slow(path):
        time.sleep(0.15)
        return {"folder": path}

    monkeypatch.setattr(zip_ops, "unpack_archive", slow)
    runner.start("unpack", ["/1.zip", "/2.zip", "/3.zip", "/4.zip"])
    assert _wait(lambda: runner.status()["status"] == "running")
    cancelled = runner.cancel()
    assert cancelled["status"] == "cancelled"
    assert _wait(lambda: runner.status()["status"] == "cancelled")
    time.sleep(0.2)
    assert runner.status()["processed"] < 4


def test_only_one_batch_at_a_time(monkeypatch, runner):
    blocker = __import__("threading").Event()

    def blocking(path):
        blocker.wait(3)
        return {"folder": path}

    monkeypatch.setattr(zip_ops, "unpack_archive", blocking)
    runner.start("unpack", ["/one.zip"])
    assert _wait(lambda: runner.status()["status"] == "running")
    with pytest.raises(writes.WriteError) as caught:
        runner.start("unpack", ["/two.zip"])
    assert caught.value.status == 409
    runner.cancel()
    blocker.set()


def test_invalid_mode_and_empty_queue(monkeypatch, runner):
    with pytest.raises(writes.WriteError) as caught:
        runner.start("zap", ["/a.zip"])
    assert caught.value.status == 400

    with pytest.raises(writes.WriteError) as compress_mode:
        runner.start("compress", ["/a.zip"])
    assert compress_mode.value.status == 400

    monkeypatch.setattr(zip_ops, "archives", list)
    with pytest.raises(writes.WriteError) as empty:
        runner.start("unpack")
    assert empty.value.status == 400


def test_batch_defaults_to_the_indexed_archives(monkeypatch, runner):
    monkeypatch.setattr(
        zip_ops,
        "archives",
        lambda: [
            {
                "path": "/lib/one.zip",
                "name": "one.zip",
                "file_count": 1,
                "size_formatted": "1 B",
            }
        ],
    )

    def fake(path):
        return {"folder": path[:-4]}

    monkeypatch.setattr(zip_ops, "unpack_archive", fake)
    runner.start("unpack")
    assert _wait(lambda: runner.status()["status"] == "completed")
    assert runner.status()["processed"] == 1


def test_router_wires_batch_control(monkeypatch, runner):
    monkeypatch.setattr(zip_batch, "runner", runner)

    def fake(path):
        return {"folder": path}

    monkeypatch.setattr(zip_ops, "unpack_archive", fake)
    client = TestClient(create_app())

    assert client.get("/api/zip/batch/status").json()["status"] == "idle"

    started = client.post(
        "/api/zip/batch/start", json={"mode": "unpack", "paths": ["/a.zip"]}
    )
    assert started.status_code == 200, started.text
    assert _wait(lambda: runner.status()["status"] == "completed")
    final = client.get("/api/zip/batch/status").json()
    assert final["succeeded"] == 1

    assert (
        client.post(
            "/api/zip/batch/start", json={"mode": "nope", "paths": ["/a.zip"]}
        ).status_code
        == 400
    )
    assert client.post("/api/zip/batch/pause").status_code == 409
