"""Download manager tests for v2 (no network, no real subprocesses)."""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from media_library import config as v2_config
from media_library import downloads as downloads_module
from media_library.app import create_app
from media_library.downloads import (
    DownloadManager,
    is_media_file,
    looks_unsupported,
    parse_progress_line,
    prefers_gallery_dl,
)
from media_library.routers import downloads as downloads_router


@pytest.fixture
def download_env(tmp_path, monkeypatch):
    library = tmp_path / "library"
    (library / "Inbox").mkdir(parents=True)
    staging = tmp_path / "download_staging"
    staging.mkdir()

    monkeypatch.setenv("LIBRARY_DIR", str(library))
    monkeypatch.setenv("LIBRARY_DIR", str(library))
    monkeypatch.setenv("THUMBNAIL_DIR", str(tmp_path / "thumbs"))
    monkeypatch.setenv("DOWNLOAD_STAGING_DIR", str(staging))
    monkeypatch.setenv("DOWNLOAD_WORKER_CONCURRENCY", "1")
    monkeypatch.setenv("DB_READ_ONLY", "false")
    monkeypatch.setenv("WRITES", "true")
    v2_config.reload_settings()

    db_path = Path(os.environ["DB_PATH"])
    with sqlite3.connect(db_path) as conn:
        for folder in (library, library / "Inbox"):
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
    try:
        yield {
            "library": library,
            "staging": staging,
            "db_path": db_path,
            "inbox": library / "Inbox",
        }
    finally:
        v2_config.reload_settings()


def _db(env):
    return sqlite3.connect(env["db_path"])


def _progress(title: str = "Clip", percent: float = 42.0) -> str:
    return json.dumps(
        {
            "title": title,
            "filename": "clip.mp4",
            "percent": percent,
            "downloaded_bytes": 42,
            "total_bytes": 100,
            "speed": 1_200_000,
            "eta": 3,
            "playlist_index": None,
            "playlist_count": None,
        }
    )


def _fake_run(manager: DownloadManager, write_file: bool = True):
    async def run(job, downloader, staging):
        job["downloader"] = downloader
        line = _progress()
        manager._apply_line(job, line, staging)
        if write_file:
            (staging / "clip.mp4").write_bytes(b"x" * 200)
        return 0, [line]

    return run


def test_parse_progress_line_handles_na_and_garbage():
    parsed = parse_progress_line(_progress())
    assert parsed["title"] == "Clip"
    assert parse_progress_line("not json") is None
    # yt-dlp writes the unquoted token NA for missing values; it is normalised
    # to 0 so the line still parses as JSON.
    assert parse_progress_line('{"percent": "NA"}') == {"percent": 0}
    assert parse_progress_line("[download] 12%") is None


def test_unsupported_and_host_detection():
    assert looks_unsupported(["ERROR: Unsupported URL: https://x"])
    assert looks_unsupported(["[Piracy] This website is no longer supported"])
    assert not looks_unsupported(["ERROR: network unreachable"])
    assert prefers_gallery_dl("https://gofile.io/d/abc")
    assert prefers_gallery_dl("https://sub.gofile.io/d/abc")
    assert not prefers_gallery_dl("https://youtube.com/watch?v=1")


def test_media_file_filter(download_env):
    assert is_media_file(Path("clip.mp4"))
    assert is_media_file(Path("photo.webp"))
    assert is_media_file(Path("audio.m4a"))
    assert not is_media_file(Path("clip.mp4.part"))
    assert not is_media_file(Path("meta.json"))
    assert not is_media_file(Path(".hidden.mp4"))


def test_download_pipeline_imports_and_indexes(download_env):
    manager = DownloadManager()
    manager.run = _fake_run(manager)  # type: ignore[method-assign]

    async def scenario():
        await manager.enqueue(["https://example.com/video"], str(download_env["inbox"]))
        await manager.wait_idle()

    asyncio.run(scenario())

    assert len(manager.history) == 1
    job = manager.history[0]
    assert job["status"] == "completed"
    assert job["percent"] == 100.0
    assert job["downloader"] == "yt-dlp"
    target = Path(job["files"][0])
    assert target.name == "clip.mp4"
    assert target.read_bytes() == b"x" * 200

    with _db(download_env) as conn:
        row = conn.execute(
            "SELECT folder_path, size FROM files WHERE path = ?", (str(target),)
        ).fetchone()
    assert row == (str(download_env["inbox"]), 200)


def test_download_falls_back_to_gallery_dl(download_env):
    manager = DownloadManager()
    calls: list[str] = []

    async def run(job, downloader, staging):
        calls.append(downloader)
        if downloader == "yt-dlp":
            return 1, ["ERROR: Unsupported URL: https://example.com/x"]
        (staging / "image.jpg").write_bytes(b"j" * 50)
        return 0, ["image.jpg"]

    manager.run = run  # type: ignore[method-assign]

    async def scenario():
        await manager.enqueue(["https://example.com/x"], str(download_env["inbox"]))
        await manager.wait_idle()

    asyncio.run(scenario())

    job = manager.history[0]
    assert calls == ["yt-dlp", "gallery-dl"]
    assert job["downloader"] == "gallery-dl"
    assert job["status"] == "completed"
    assert Path(job["files"][0]).name == "image.jpg"


def test_download_without_output_fails_with_the_tail(download_env):
    manager = DownloadManager()

    async def run(job, downloader, staging):
        return 0, ["ERROR: something went wrong", "nothing downloaded"]

    manager.run = run  # type: ignore[method-assign]

    async def scenario():
        await manager.enqueue(["https://example.com/x"], str(download_env["inbox"]))
        await manager.wait_idle()

    asyncio.run(scenario())

    job = manager.history[0]
    assert job["status"] == "failed"
    assert "something went wrong" in job["error"]


def test_invalid_urls_are_rejected_and_partial_batches_queue(download_env):
    manager = DownloadManager()
    manager.run = _fake_run(manager)  # type: ignore[method-assign]

    async def scenario():
        result = await manager.enqueue(
            [
                "https://example.com/ok",
                "ftp://example.com/nope",
                "https://example.com/ok",
                "   ",
            ],
            str(download_env["inbox"]),
        )
        await manager.wait_idle()
        return result

    result = asyncio.run(scenario())
    assert len(result["jobs"]) == 1
    assert {item["url"] for item in result["rejected"]} == {
        "ftp://example.com/nope",
        "https://example.com/ok",
    }


def test_enqueue_rejects_an_unknown_destination(download_env):
    manager = DownloadManager()

    async def scenario():
        with pytest.raises(downloads_module.writes.WriteError) as caught:
            await manager.enqueue(
                ["https://example.com/x"], str(download_env["library"] / "nope")
            )
        return caught.value

    error = asyncio.run(scenario())
    assert error.status == 404


def test_enqueue_fails_when_no_downloader_is_installed(download_env, monkeypatch):
    monkeypatch.setattr(
        downloads_module,
        "tools_available",
        lambda: {"ytdlp": False, "gallery_dl": False},
    )
    manager = DownloadManager()

    async def scenario():
        with pytest.raises(downloads_module.writes.WriteError) as caught:
            await manager.enqueue(["https://example.com/x"], str(download_env["inbox"]))
        return caught.value

    error = asyncio.run(scenario())
    assert error.status == 503


def test_cancel_marks_a_queued_job_and_moves_it_to_history(download_env):
    manager = DownloadManager()
    job = manager._new_job("https://example.com/x", str(download_env["inbox"]))
    manager.jobs[job["id"]] = job

    manager.cancel(job["id"])

    assert job["id"] not in manager.jobs
    assert manager.history[-1]["status"] == "cancelled"


def test_cancel_unknown_job_is_404(download_env):
    manager = DownloadManager()
    with pytest.raises(downloads_module.writes.WriteError) as caught:
        manager.cancel("missing")
    assert caught.value.status == 404


def test_retry_clones_a_failed_job(download_env):
    manager = DownloadManager()
    failed = manager._new_job("https://example.com/x", str(download_env["inbox"]))
    failed["status"] = "failed"
    failed["error"] = "boom"
    manager.history.append(failed)
    manager.run = _fake_run(manager)  # type: ignore[method-assign]

    async def scenario():
        result = await manager.retry(failed["id"])
        await manager.wait_idle()
        return result

    result = asyncio.run(scenario())
    assert result["job"]["id"] != failed["id"]
    assert manager.history[-1]["status"] == "completed"
    assert manager.history[-1]["url"] == failed["url"]


def test_retry_refuses_a_completed_job(download_env):
    manager = DownloadManager()
    done = manager._new_job("https://example.com/x", str(download_env["inbox"]))
    done["status"] = "completed"
    manager.history.append(done)

    async def scenario():
        with pytest.raises(downloads_module.writes.WriteError) as caught:
            await manager.retry(done["id"])
        return caught.value

    assert asyncio.run(scenario()).status == 409


def test_history_pagination_and_clear(download_env):
    manager = DownloadManager()
    for index in range(3):
        job = manager._new_job(
            f"https://example.com/{index}", str(download_env["inbox"])
        )
        job["status"] = "completed"
        manager.history.append(job)

    first = manager.history_page(limit=2, offset=0)
    assert first["total"] == 3
    assert [item["url"] for item in first["items"]] == [
        "https://example.com/2",
        "https://example.com/1",
    ]
    second = manager.history_page(limit=2, offset=2)
    assert [item["url"] for item in second["items"]] == ["https://example.com/0"]

    assert manager.clear_history() == {"cleared": 3}
    assert manager.history_page()["total"] == 0


def test_status_separates_active_and_queued(download_env):
    manager = DownloadManager()
    active = manager._new_job("https://example.com/a", str(download_env["inbox"]))
    active["status"] = "downloading"
    queued = manager._new_job("https://example.com/b", str(download_env["inbox"]))
    manager.jobs[active["id"]] = active
    manager.jobs[queued["id"]] = queued

    payload = manager.status()
    assert payload["status"] == "active"
    assert payload["active_count"] == 1
    assert payload["queued_count"] == 1
    assert payload["tools"]["ytdlp"] is True


def test_router_reports_status_history_and_clear(download_env, monkeypatch):
    manager = DownloadManager()
    monkeypatch.setattr(downloads_router, "manager", manager)
    client = TestClient(create_app())

    status = client.get("/api/downloads/status").json()
    assert status["active"] == []
    assert status["tools"]["gallery_dl"] in (True, False)

    client.post("/api/downloads/clear-history")
    history = client.get("/api/downloads/history").json()
    assert history == {"items": [], "total": 0, "limit": 50, "offset": 0}


def test_finish_is_idempotent(download_env):
    """A cancelled queued job must land in history exactly once."""
    manager = DownloadManager()
    job = manager._new_job("https://example.com/x", str(download_env["inbox"]))
    manager.jobs[job["id"]] = job
    job["status"] = "cancelled"

    manager._finish(job)
    manager._finish(job)

    assert len(manager.history) == 1
