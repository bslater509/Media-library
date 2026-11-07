"""Batch ZIP runner tests for v2 (no real archives)."""

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


def test_batch_compresses_every_path(monkeypatch, runner):
    seen: list[str] = []

    def fake(path):
        seen.append(path)
        return {"archive": path + ".zip"}

    monkeypatch.setattr(zip_ops, "compress_folder", fake)
    status = runner.start("compress", ["/a", "/b", "/c"])
    assert status["status"] == "running"
    assert status["total"] == 3

    assert _wait(lambda: runner.status()["status"] == "completed")
    final = runner.status()
    assert final["succeeded"] == 3
    assert final["processed"] == 3
    assert final["percent"] == 100.0
    assert seen == ["/a", "/b", "/c"]


def test_failures_are_reported_and_the_batch_continues(monkeypatch, runner):
    def fake(path):
        if path == "/boom":
            raise RuntimeError("cannot archive")
        return {"archive": path + ".zip"}

    monkeypatch.setattr(zip_ops, "compress_folder", fake)
    runner.start("compress", ["/ok1", "/boom", "/ok2"])
    assert _wait(lambda: runner.status()["status"] == "completed")
    final = runner.status()
    assert final["succeeded"] == 2
    assert final["failed"] == 1
    assert final["errors"][0]["path"] == "/boom"
    assert "cannot archive" in final["errors"][0]["error"]


def test_pause_and_resume(monkeypatch, runner):
    def slow(path):
        time.sleep(0.12)
        return {"archive": path}

    monkeypatch.setattr(zip_ops, "compress_folder", slow)
    runner.start("compress", ["/1", "/2", "/3"])
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
        return {"archive": path}

    monkeypatch.setattr(zip_ops, "compress_folder", slow)
    runner.start("compress", ["/1", "/2", "/3", "/4"])
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
        return {"archive": path}

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
        runner.start("zap", ["/a"])
    assert caught.value.status == 400

    monkeypatch.setattr(zip_ops, "archive_candidates", lambda folder: [])
    with pytest.raises(writes.WriteError) as empty:
        runner.start("compress")
    assert empty.value.status == 400


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
