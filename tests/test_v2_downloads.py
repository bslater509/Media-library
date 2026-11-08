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


def _clip_progress(name: str, percent: float, done_bytes: int, total: int) -> str:
    return json.dumps(
        {
            "title": "Album",
            "filename": f"/tmp/staging/{name}",
            "percent": percent,
            "downloaded_bytes": done_bytes,
            "total_bytes": total,
            "speed": None,
            "eta": None,
            "playlist_index": None,
            "playlist_count": None,
        }
    )


def test_multi_file_job_tracks_per_file_progress():
    """Each yt-dlp filename becomes a row; the job percent reflects all rows."""
    manager = DownloadManager()
    job = manager._new_job("https://example.com/album", "/tmp/library/Inbox")
    job["downloader"] = "yt-dlp"
    staging = Path("/tmp/staging")

    manager._apply_line(job, _clip_progress("a.mp4", 100.0, 100, 100), staging)
    manager._apply_line(job, _clip_progress("b.mp4", 25.0, 50, 200), staging)
    manager._apply_line(job, _clip_progress("c.mp4", 0.0, 4, 400), staging)

    rows = [(row["name"], row["done"], row["percent"]) for row in job["file_progress"]]
    assert rows == [
        ("a.mp4", True, 100.0),
        ("b.mp4", True, 25.0),
        ("c.mp4", False, 0.0),
    ]
    # a's 100, b concluded at its full 200 when c started, c's first 4
    assert job["percent"] == 43.4
    assert job["downloaded_bytes"] == 304
    assert job["total_bytes"] == 700
    # a new name implies the previous file finished even without an exact 100%
    assert next(row["name"] for row in job["file_progress"] if row["done"]) == "a.mp4"

    manager._apply_line(job, _clip_progress("c.mp4", 100.0, 400, 400), staging)
    assert job["percent"] == 100.0
    assert all(row["done"] for row in job["file_progress"])


def test_single_file_job_keeps_the_line_totals_in_its_row():
    """One filename means one row; the job numbers equal the line values."""
    manager = DownloadManager()
    job = manager._new_job("https://example.com/x", "/tmp/library/Inbox")
    job["downloader"] = "yt-dlp"

    manager._apply_line(job, _progress(percent=42.0), Path("/tmp/staging"))

    assert job["percent"] == 42.0
    assert job["downloaded_bytes"] == 42
    assert job["total_bytes"] == 100
    assert job["file_progress"] == [
        {
            "name": "clip.mp4",
            "done": False,
            "percent": 42.0,
            "downloaded_bytes": 42,
            "total_bytes": 100,
        }
    ]


def test_gallery_dl_lines_track_started_files():
    """One staging-path line per album member, newest starts unfinished."""
    manager = DownloadManager()
    job = manager._new_job("https://example.com/album", "/tmp/library/Inbox")
    job["downloader"] = "gallery-dl"
    staging = Path("/tmp/staging")

    manager._apply_line(job, f"{staging}/one.mp4", staging)
    manager._apply_line(job, f"{staging}/two.jpg", staging)
    manager._apply_line(job, f"{staging}/three.png", staging)

    rows = [(row["name"], row["done"]) for row in job["file_progress"]]
    assert rows == [("one.mp4", True), ("two.jpg", True), ("three.png", False)]
    assert job["title"] == "three.png"
    assert job["percent"] == 15.0
    assert not any(row["total_bytes"] for row in job["file_progress"])

    # a repeated line for the same file closes it instead of adding a row
    manager._apply_line(job, f"{staging}/three.png", staging)
    assert [row["name"] for row in job["file_progress"]] == [
        "one.mp4",
        "two.jpg",
        "three.png",
    ]
    assert all(row["done"] for row in job["file_progress"])


def test_row_total_falls_back_to_the_estimate():
    """When only an estimate is available, rows size themselves from it."""
    manager = DownloadManager()
    job = manager._new_job("https://example.com/x", "/tmp/library/Inbox")
    job["downloader"] = "yt-dlp"

    line = json.dumps(
        {
            "filename": "/tmp/staging/a.mp4",
            "percent": 50.0,
            "downloaded_bytes": 300,
            "total_bytes_estimate": 600,
        }
    )
    manager._apply_line(job, line, Path("/tmp/staging"))

    row = job["file_progress"][0]
    assert row["total_bytes"] == 600
    assert row["done"] is False
    assert job["total_bytes"] == 600
    assert job["downloaded_bytes"] == 300
    assert job["percent"] == 50.0


def test_router_job_payload_carries_file_progress(download_env):
    """The API response model exposes the per-file rows."""
    manager = DownloadManager()
    manager.run = _fake_run(manager)  # type: ignore[method-assign]

    async def scenario():
        await manager.enqueue(["https://example.com/video"], str(download_env["inbox"]))
        await manager.wait_idle()

    asyncio.run(scenario())

    rows = [
        entry
        for entry in manager.history[0]["file_progress"]
        if entry["name"] == "clip.mp4"
    ]
    assert rows == [
        {
            "name": "clip.mp4",
            "done": True,
            "percent": 42.0,
            "downloaded_bytes": 42,
            "total_bytes": 100,
        }
    ]


class _StubProcess:
    """Minimal stand-in for an asyncio subprocess: stdout only, exit 0."""

    killed = False

    class _Stdout:
        def __init__(self, chunks: list[bytes]) -> None:
            self.chunks = list(chunks)

        async def readline(self) -> bytes:
            if not self.chunks:
                return b""
            await asyncio.sleep(0)
            return self.chunks.pop(0)

    def __init__(self, chunks: list[bytes]) -> None:
        self.stdout = self._Stdout(chunks)

    def kill(self) -> None:
        self.killed = True

    async def wait(self) -> int:
        return 0


def test_gallery_dl_imports_each_album_file_as_it_completes(download_env, monkeypatch):
    """A staged file joins the library as soon as the next member starts."""
    inbox = download_env["inbox"]
    staging = download_env["staging"] / "album"
    staging.mkdir(parents=True)
    (staging / "one.jpg").write_bytes(b"1")
    (staging / "two.jpg").write_bytes(b"22")
    manager = DownloadManager()
    job = manager._new_job("https://example.com/a", str(inbox))
    job["downloader"] = "gallery-dl"

    async def fake_exec(*command, **kwargs):
        return _StubProcess(
            [f"{staging / 'one.jpg'}\n".encode(), f"{staging / 'two.jpg'}\n".encode()]
        )

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    code, _lines = asyncio.run(manager.run(job, "gallery-dl", staging))

    assert code == 0
    assert [(row["name"], row["done"]) for row in job["file_progress"]] == [
        ("one.jpg", True),
        ("two.jpg", False),
    ]
    # one.jpg was handed to the library mid-download, already indexed
    assert Path(job["files"][0]) == inbox / "one.jpg"
    assert not (staging / "one.jpg").exists()
    with _db(download_env) as conn:
        rows = conn.execute(
            "SELECT path FROM files WHERE folder_path = ?", (str(inbox),)
        ).fetchall()
    assert len(rows) == 1 and Path(rows[0][0]).name == "one.jpg"

    # the end-of-job handoff picks up the rest without importing one.jpg twice
    imported, skipped = asyncio.run(
        manager.import_staged(staging, str(inbox), skip=job["_imported"])
    )
    assert imported == [str(inbox / "two.jpg")]
    assert skipped == 0
    job["files"] = list(dict.fromkeys(job["files"] + imported))
    assert sorted(Path(path).name for path in job["files"]) == ["one.jpg", "two.jpg"]
    assert (inbox / "one.jpg").exists()
    assert (inbox / "two.jpg").exists()


def test_evacuation_marks_a_missing_done_row_and_skips_the_current_file(
    download_env,
):
    """Never-written names do not block evacuation of later members."""
    staging = download_env["staging"] / "album3"
    staging.mkdir(parents=True)
    (staging / "current.jpg").write_bytes(b"c")
    manager = DownloadManager()
    job = manager._new_job("https://example.com/a", str(download_env["inbox"]))
    job["downloader"] = "gallery-dl"

    async def scenario():
        manager._apply_line(job, f"{staging / 'gone.jpg'}\n".strip(), staging)
        manager._apply_line(job, f"{staging / 'current.jpg'}\n".strip(), staging)
        # the same path logged twice concludes the file while it is still
        # the title: evacuation must not move an in-flight file
        manager._apply_line(job, f"{staging / 'current.jpg'}\n".strip(), staging)

    asyncio.run(scenario())
    asyncio.run(manager._evacuate_done_files(job, staging))

    assert job["_imported"] == {"gone.jpg"}
    assert job["files"] == []
    assert (staging / "current.jpg").exists()
    imported, skipped = asyncio.run(
        manager.import_staged(
            staging, str(download_env["inbox"]), skip=job["_imported"]
        )
    )
    # the final handoff picks up where evacuation left off
    assert len(imported) == 1 and Path(imported[0]).name == "current.jpg"
    assert (download_env["inbox"] / "current.jpg").exists()
    assert skipped == 0


def test_import_staged_skips_names_already_handed_to_the_library(download_env):
    staging = download_env["staging"] / "album4"
    staging.mkdir(parents=True)
    (staging / "part.jpg").write_bytes(b"p")
    (staging / "kept.jpg").write_bytes(b"k")
    manager = DownloadManager()
    imported, skipped = asyncio.run(
        manager.import_staged(staging, str(download_env["inbox"]), skip={"part.jpg"})
    )
    assert len(imported) == 1 and Path(imported[0]).name == "kept.jpg"
    assert (download_env["inbox"] / "kept.jpg").exists()
    assert (staging / "part.jpg").exists()
    assert skipped == 0


def test_import_staged_counts_files_the_destination_already_had(download_env):
    staging = download_env["staging"] / "album5"
    staging.mkdir(parents=True)
    (staging / "known.mp4").write_bytes(b"k")
    (download_env["inbox"] / "known.mp4").write_bytes(b"old")
    manager = DownloadManager()
    imported, skipped = asyncio.run(
        manager.import_staged(staging, str(download_env["inbox"]))
    )
    assert imported == [] and skipped == 1
    # the staged copy stays behind so the partial job still completes
    assert (staging / "known.mp4").exists()


def test_rerun_of_a_fully_downloaded_album_succeeds_with_no_new_files(download_env):
    """Retrying a finished album counts as done: everything was a skip."""
    manager = DownloadManager()

    async def run(job, downloader, staging):
        job["downloader"] = downloader
        line = f"{staging / 'clip.mp4'}".strip()
        (staging / "clip.mp4").write_bytes(b"x")
        manager._apply_line(job, line, staging)
        return 0, [line]

    manager.run = run  # type: ignore[method-assign]
    inbox = download_env["inbox"]
    (inbox / "clip.mp4").write_bytes(b"x")

    async def scenario():
        await manager.enqueue(["https://example.com/a"], str(inbox))
        await manager.wait_idle()

    asyncio.run(scenario())

    job = manager.history[0]
    assert job["status"] == "completed"
    assert job["percent"] == 100.0
    assert job["files"] == []
    assert (inbox / "clip.mp4").exists() and (inbox / "clip.mp4").read_bytes() == b"x"


def test_evacuation_failure_keeps_the_file_for_the_final_import(
    download_env, monkeypatch
):
    """An import error mid-album never loses the staged file."""
    import media_library.downloads as downloads_module
    from media_library import writes

    staging = download_env["staging"] / "album2"
    staging.mkdir(parents=True)
    (staging / "a.jpg").write_bytes(b"x")
    manager = DownloadManager()
    job = manager._new_job("https://example.com/a", str(download_env["inbox"]))
    job["downloader"] = "gallery-dl"
    original_import = writes.import_file

    async def scenario():
        # two lines: the first file concludes, the second is in flight
        manager._apply_line(job, f"{staging / 'a.jpg'}\n".strip(), staging)
        manager._apply_line(job, f"{staging / 'b.jpg'}\n", staging)

    def boom(source, destination, conflict):
        raise writes.WriteError("Temporarily unavailable", 503)

    monkeypatch.setattr(downloads_module.writes, "import_file", boom)
    asyncio.run(scenario())
    assert job["file_progress"][0]["done"] is True

    asyncio.run(manager._evacuate_done_files(job, staging))
    assert (staging / "a.jpg").exists()
    assert job["files"] == []

    # the failure clears: the next round moves it into the library
    monkeypatch.setattr(downloads_module.writes, "import_file", original_import)
    asyncio.run(manager._evacuate_done_files(job, staging))
    assert job["files"] == [str(download_env["inbox"] / "a.jpg")]
    assert not (staging / "a.jpg").exists()
