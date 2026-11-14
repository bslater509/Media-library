"""Diagnostic history outlives bounded GUI errors and queue resets."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from test_web_mp4 import indexed

from media_library import web_mp4, web_mp4_log
from media_library.app import create_app


def rows():
    return [
        json.loads(line) for line in web_mp4_log.log_path().read_text().splitlines()
    ]


def test_private_log_retains_backfilled_and_live_errors():
    web_mp4_log.seed_recent(
        [{"path": "old.mp4", "error": "Old diagnostic unavailable"}]
    )
    web_mp4_log.seed_recent([{"path": "should-not-import-twice", "error": "ignored"}])
    record = web_mp4_log.event(
        "video.mp4",
        "checking",
        "Unreadable MP4",
        diagnostic="moov atom not found",
        fingerprint=[123, 456],
    )
    web_mp4_log.append(record)
    saved = rows()
    assert len(saved) == 2
    assert saved[0]["origin"] == "restored_recent" and saved[0]["occurred_at"] is None
    assert saved[1]["diagnostic"] == "moov atom not found"
    assert saved[1]["fingerprint"] == [123, 456]
    assert saved[1]["occurred_at"] == saved[1]["recorded_at"]
    assert os.stat(web_mp4_log.log_path()).st_mode & 0o777 == 0o600
    assert not list(web_mp4_log.log_path().parent.glob("web_mp4_failures.*.tmp"))


def test_concurrent_appends_never_interleave_records():
    def append(number):
        web_mp4_log.append(web_mp4_log.event(str(number), "checking", "error"))

    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(append, range(80)))
    assert len(rows()) == 80 and {row["path"] for row in rows()} == {
        str(n) for n in range(80)
    }


def test_seed_failure_preserves_existing_history_and_cleans_temp(monkeypatch):
    def fail(*args):
        raise OSError("disk full")

    monkeypatch.setattr(web_mp4_log.os, "replace", fail)
    with pytest.raises(OSError):
        web_mp4_log.seed_recent([{"path": "old.mp4", "error": "failed"}])
    assert not web_mp4_log.log_path().exists()
    assert not list(web_mp4_log.log_path().parent.glob("web_mp4_failures.*.tmp"))


@pytest.mark.parametrize("kind", ["moov", "generic", "empty", "timeout", "os"])
def test_probe_preserves_real_diagnostics(kind, monkeypatch):
    if kind == "timeout":
        failure = subprocess.TimeoutExpired(
            "ffprobe", 60, stderr=b"remote read stalled"
        )
    elif kind == "os":
        failure = OSError("ffprobe unavailable")
    else:
        stderr = {
            "moov": b"moov atom not found",
            "generic": b"Invalid data found",
            "empty": b"",
        }[kind]
        failure = subprocess.CalledProcessError(1, ["ffprobe"], stderr=stderr)

    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr(subprocess, "run", fail)
    with pytest.raises(web_mp4.ProbeFailure) as captured:
        web_mp4.probe("original.mp4")
    message = str(captured.value)
    assert "Command '['" not in message
    if kind == "moov":
        assert "moov atom not found" in message
    if kind == "empty":
        assert "no diagnostic" in message
    if kind == "generic":
        assert "Invalid data found" in captured.value.diagnostic
    if kind == "timeout":
        assert "remote read stalled" in captured.value.diagnostic
    if kind == "os":
        assert "ffprobe unavailable" in captured.value.diagnostic


def test_failed_check_is_logged_with_source_fingerprint(monkeypatch):
    source = indexed("bad.mp4")

    def fail(path):
        raise web_mp4.ProbeFailure("Missing MP4 metadata", "moov atom not found")

    monkeypatch.setattr(web_mp4, "probe", fail)

    async def run():
        manager = web_mp4.WebMP4Manager()
        manager.state["total"] = 1
        await manager._batch([str(source)])
        assert manager.state["failed"] == 1 and source.read_bytes() == b"original"

    asyncio.run(run())
    saved = rows()[0]
    assert saved["path"] == str(source) and saved["stage"] == "checking"
    assert saved["diagnostic"] == "moov atom not found"
    assert saved["fingerprint"] == list(web_mp4._fingerprint(str(source)))


def test_logging_failure_is_visible_without_losing_queue_errors(monkeypatch):
    def fail(*args):
        raise OSError("disk full")

    async def run():
        manager = web_mp4.WebMP4Manager()
        monkeypatch.setattr(web_mp4_log, "seed_recent", fail)
        await manager._seed_failures([])
        assert "initialize" in manager.state["failure_log_error"]
        monkeypatch.setattr(web_mp4_log, "append", fail)
        manager.state["total"] = 1
        manager._check = AsyncMock(side_effect=OSError("bad video"))
        await manager._batch(["bad.mp4"])
        assert manager.state["failed"] == 1
        assert manager.state["errors"][0]["error"] == "bad video"
        assert "append" in manager.state["failure_log_error"]

    asyncio.run(run())


def test_log_download_is_private_ndjson_and_does_not_clear_history():
    client = TestClient(create_app())
    assert client.get("/api/transcode/web-mp4/failures/log").status_code == 404
    web_mp4_log.append(web_mp4_log.event("video.mp4", "verifying", "decode error"))
    response = client.get("/api/transcode/web-mp4/failures/log")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/x-ndjson"
    assert response.headers["cache-control"] == "no-store"
    assert "attachment" in response.headers["content-disposition"]
    assert json.loads(response.text)["stage"] == "verifying"
    assert len(rows()) == 1


def test_restore_imports_recent_errors_once_without_resetting_progress():
    async def run():
        original = web_mp4.WebMP4Manager()
        original.state.update(
            total=1,
            processed=1,
            failed=1,
            status="completed",
            errors=[{"path": "failed.mp4", "error": "previous error"}],
        )
        await original._persist(force=True)
        restored = web_mp4.WebMP4Manager()
        state = await restored.restore()
        assert state["processed"] == state["failed"] == 1
        assert len(rows()) == 1
        await restored.restore()
        assert len(rows()) == 1

    asyncio.run(run())
