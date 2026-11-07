"""Full line-coverage suite for ``media_library.downloads`` and ``media_library.growth``.

No network and no real subprocesses: the downloader run loop drives a stub
``asyncio.create_subprocess_exec``, and the growth worker's clock is injected
(or its ``Event.wait`` scripted) so no test sleeps.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import pathlib
import shutil
import sqlite3
import sys
import threading
import time
from contextlib import closing
from pathlib import Path

import pytest

from media_library import config as v2_config
from media_library import downloads as downloads_module
from media_library import growth
from media_library.downloads import (
    DownloadManager,
    _coerce,
    is_media_file,
    looks_unsupported,
    parse_progress_line,
    prefers_gallery_dl,
    tools_available,
)
from media_library.writes import WriteError

DAY_A = 1720000000.0  # 2024-07-03 in UTC
DAY_B = DAY_A + 86400.0  # 2024-07-04


# ---------------------------------------------------------------- fixtures


@pytest.fixture
def download_env(tmp_path, monkeypatch):
    """Library root with an Inbox, a staging dir and matching folders rows."""
    library = tmp_path / "library"
    inbox = library / "Inbox"
    inbox.mkdir(parents=True)
    staging = tmp_path / "download_staging"
    staging.mkdir()

    monkeypatch.setenv("LIBRARY_DIR", str(library))
    monkeypatch.setenv("DOWNLOAD_STAGING_DIR", str(staging))
    monkeypatch.setenv("DOWNLOAD_WORKER_CONCURRENCY", "1")
    monkeypatch.setenv("DB_READ_ONLY", "false")
    monkeypatch.setenv("WRITES", "true")
    v2_config.reload_settings()

    with closing(sqlite3.connect(os.environ["DB_PATH"])) as conn, conn:
        for folder in (library, inbox):
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
        yield {"library": library, "staging": staging, "inbox": inbox}
    finally:
        v2_config.reload_settings()


# ------------------------------------------------------------------ stubs


class _StubStream:
    """readline() replays prepared chunks; a raised instance aborts the read."""

    def __init__(self, chunks):
        self._chunks = list(chunks)

    async def readline(self):
        if not self._chunks:
            return b""
        chunk = self._chunks.pop(0)
        if isinstance(chunk, BaseException):
            raise chunk
        return chunk


class _StubProcess:
    def __init__(self, chunks, code=0):
        self.stdout = _StubStream(chunks)
        self.killed = False
        self.returncode = code

    def kill(self):
        self.killed = True

    async def wait(self):
        return self.returncode


class _KillingProcess:
    """A job's ``_process`` stand-in whose kill() may already have happened."""

    def __init__(self, raises=False):
        self.raises = raises
        self.calls = 0

    def kill(self):
        self.calls += 1
        if self.raises:
            raise ProcessLookupError("gone")


def _stub_subprocess(monkeypatch, chunk_lists):
    """Replace asyncio.create_subprocess_exec; report the commands issued."""
    processes: list[_StubProcess] = []
    commands: list[tuple] = []

    async def fake_exec(*command, **kwargs):
        index = len(processes)
        chunks = chunk_lists[index] if index < len(chunk_lists) else []
        process = _StubProcess(chunks)
        processes.append(process)
        commands.append(command)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    return commands, processes


def _db_rows(sql: str, params: tuple = ()) -> list[tuple]:
    """Run one read with a fully closed connection."""
    with closing(sqlite3.connect(os.environ["DB_PATH"])) as conn:
        return conn.execute(sql, params).fetchall()


def _seed_files(rows: list[tuple[str, str, int]]) -> None:
    """Seed ``files`` rows for the growth rollups."""
    with closing(sqlite3.connect(os.environ["DB_PATH"])) as conn, conn:
        for index, (name, kind, size) in enumerate(rows):
            conn.execute(
                "INSERT INTO files (path, name, folder_path, type, ext, size, "
                "size_formatted, mtime, has_thumb) VALUES "
                "(?, ?, ?, ?, ?, ?, 'size', 1.0, 0)",
                (f"/lib/item{index}{name}", name, "/lib", kind, name[-4:], size),
            )


def _seed_history(points: list[tuple[str, float]]) -> None:
    with closing(sqlite3.connect(os.environ["DB_PATH"])) as conn, conn:
        for day, ts in points:
            conn.execute(
                "INSERT INTO growth_history (day, files_count, video_count, "
                "image_count, bytes_total, recorded_at) VALUES (?,?,?,?,?,?)",
                (day, 10, 6, 3, 900, ts),
            )


def _history_rows() -> list[str]:
    return [
        row[0] for row in _db_rows("SELECT day FROM growth_history ORDER BY day ASC")
    ]


def _boom(exc: Exception):
    """A ``record_today`` stand-in that always raises ``exc``."""

    def raise_boom(now=None):
        raise exc

    return raise_boom


# ----------------------------------------------------------------- helpers


def _progress_line(**overrides) -> str:
    payload: dict[str, object] = {
        "title": None,
        "filename": None,
        "percent": None,
        "downloaded_bytes": None,
        "total_bytes": None,
        "total_bytes_estimate": None,
        "speed": None,
        "eta": None,
        "playlist_index": None,
        "playlist_count": None,
    }
    payload.update(overrides)
    return json.dumps(payload)


def _job_in(manager: DownloadManager, url: str, destination: str) -> dict:
    """Register a hand-built job as ``enqueue`` would."""
    job = manager._new_job(url, destination)
    manager.jobs[job["id"]] = job
    return job


def _write_media(staging: Path, name: str = "clip.mp4") -> None:
    (staging / name).write_bytes(b"x" * 120)


def _fake_run(manager: DownloadManager) -> None:
    """Pretend yt-dlp produced a merged clip."""

    async def run(job, downloader, staging):
        job["downloader"] = downloader
        _write_media(staging)
        return 0, [_progress_line(percent=80.0)]

    manager.run = run  # type: ignore[method-assign]


# ============================================================ downloads.py


def test_tools_available_reports_both_cli_tools():
    tools = tools_available()
    assert tools["ytdlp"] is True
    assert tools["gallery_dl"] is True


def test_parse_progress_line_variants():
    parsed = parse_progress_line(_progress_line(title="Clip", percent=42.0))
    assert parsed["percent"] == 42.0
    assert parse_progress_line("not json") is None
    assert parse_progress_line("{broken json") is None
    # The bare NA token is normalised to 0 so the line still parses.
    assert parse_progress_line('{"percent": "NA"}') == {"percent": 0}


def test_looks_unsupported_and_hosts():
    assert looks_unsupported(["ERROR: Unsupported URL: https://x"])
    assert looks_unsupported(["[Piracy] This website is no longer supported"])
    assert not looks_unsupported(["ERROR: network unreachable"])
    assert prefers_gallery_dl("https://gofile.io/d/abc")
    assert prefers_gallery_dl("https://sub.gofile.io/d/abc")
    assert not prefers_gallery_dl("https://youtube.com/watch?v=1")
    # A URL that cannot be parsed at all is simply not gallery-dl material.
    assert not prefers_gallery_dl("http://[::1")


def test_is_media_file_filters_the_noise():
    assert is_media_file(Path("clip.mp4"))
    assert is_media_file(Path("photo.webp"))
    assert is_media_file(Path("audio.m4a"))
    assert not is_media_file(Path("clip.mp4.part"))
    assert not is_media_file(Path("meta.json"))
    assert not is_media_file(Path(".hidden.mp4"))
    assert not is_media_file(Path("archive.xyz"))


def test_coerce_falls_back_on_garbage():
    assert _coerce(42, 0.0) == 42.0
    assert _coerce("nope", 3.5) == 3.5
    assert _coerce(None, 1.25) == 1.25
    assert _coerce(float("nan"), 2.0) == 2.0


def test_enqueue_rejects_when_no_downloader_is_installed(download_env, monkeypatch):
    monkeypatch.setattr(
        downloads_module,
        "tools_available",
        lambda: {"ytdlp": False, "gallery_dl": False},
    )
    manager = DownloadManager()

    with pytest.raises(WriteError) as caught:
        asyncio.run(
            manager.enqueue(["https://example.com/x"], str(download_env["inbox"]))
        )
    assert caught.value.status == 503


def test_enqueue_rejects_an_unknown_destination(download_env):
    manager = DownloadManager()

    with pytest.raises(WriteError) as caught:
        asyncio.run(
            manager.enqueue(
                ["https://example.com/x"], str(download_env["library"] / "nope")
            )
        )
    assert caught.value.status == 404


def test_enqueue_requires_at_least_one_valid_url(download_env):
    manager = DownloadManager()

    with pytest.raises(WriteError) as caught:
        asyncio.run(
            manager.enqueue(["   ", "ftp://example.com/x"], str(download_env["inbox"]))
        )
    assert caught.value.status == 400


def test_enqueue_queues_valid_urls_and_flags_duplicates(download_env):
    manager = DownloadManager()
    _fake_run(manager)

    async def scenario():
        result = await manager.enqueue(
            [
                "https://example.com/a",
                "   ",
                "https://example.com/a",
                "https://example.com/b",
            ],
            str(download_env["inbox"]),
        )
        await manager.wait_idle()
        return result

    result = asyncio.run(scenario())
    assert {item["url"] for item in result["jobs"]} == {
        "https://example.com/a",
        "https://example.com/b",
    }
    assert {item["error"] for item in result["rejected"]} == {"Duplicate URL"}

    statuses = [job["status"] for job in manager.history]
    assert statuses == ["completed", "completed"]
    assert all(job["downloader"] == "yt-dlp" for job in manager.history)


def test_execute_job_ignores_an_unknown_job():
    manager = DownloadManager()
    assert asyncio.run(manager._execute_job("missing")) is None


def test_execute_job_finalises_a_job_cancelled_while_queued(download_env):
    manager = DownloadManager()
    job = _job_in(manager, "https://example.com/x", str(download_env["inbox"]))
    job["status"] = "cancelled"

    asyncio.run(manager._execute_job(job["id"]))

    assert job["id"] not in manager.jobs
    assert manager.history[-1] is job


def test_pipeline_completes_and_indexes_the_file(download_env):
    manager = DownloadManager()
    _fake_run(manager)

    async def scenario():
        await manager.enqueue(["https://example.com/video"], str(download_env["inbox"]))
        await manager.wait_idle()
        return await manager.wait_idle()  # a second pass with no tasks

    asyncio.run(scenario())

    job = manager.history[0]
    assert job["status"] == "completed"
    assert job["percent"] == 100.0
    assert job["downloader"] == "yt-dlp"
    target = Path(job["files"][0])
    assert target.read_bytes() == b"x" * 120
    rows = _db_rows("SELECT folder_path FROM files WHERE path = ?", (str(target),))
    assert rows == [(str(download_env["inbox"]),)]


def test_download_uses_gallery_dl_directly(download_env, monkeypatch):
    monkeypatch.setattr(
        downloads_module,
        "tools_available",
        lambda: {"ytdlp": False, "gallery_dl": True},
    )
    manager = DownloadManager()
    down: list[str] = []

    async def run(job, downloader, staging):
        down.append(downloader)
        _write_media(staging, "image.jpg")
        return 0, [_progress_line(title="Gallery", percent=10.0)]

    manager.run = run  # type: ignore[method-assign]

    async def scenario():
        await manager.enqueue(
            ["https://gofile.io/d/abc", "https://example.com/x"],
            str(download_env["inbox"]),
        )
        await manager.wait_idle()

    asyncio.run(scenario())

    assert down == ["gallery-dl", "gallery-dl"]
    assert all(job["downloader"] == "gallery-dl" for job in manager.history)
    assert all(job["status"] == "completed" for job in manager.history)


def test_download_falls_back_when_ytdlp_hits_unsupported(download_env):
    manager = DownloadManager()
    down: list[str] = []

    async def run(job, downloader, staging):
        down.append(downloader)
        if downloader == "yt-dlp":
            return 1, ["ERROR: Unsupported URL: https://example.com/x"]
        _write_media(staging, "image.jpg")
        return 0, ["image.jpg"]

    manager.run = run  # type: ignore[method-assign]

    async def scenario():
        await manager.enqueue(["https://example.com/x"], str(download_env["inbox"]))
        await manager.wait_idle()

    asyncio.run(scenario())

    assert down == ["yt-dlp", "gallery-dl"]
    job = manager.history[0]
    assert job["downloader"] == "gallery-dl"
    assert job["status"] == "completed"
    assert Path(job["files"][0]).name == "image.jpg"


def test_run_kills_a_wedged_process(download_env, monkeypatch):
    staging = download_env["staging"]
    commands, processes = _stub_subprocess(
        monkeypatch, [[asyncio.TimeoutError("no output")]]
    )
    manager = DownloadManager()
    job = manager._new_job("https://example.com/a", "unused")

    with pytest.raises(RuntimeError) as caught:
        asyncio.run(manager.run(job, "yt-dlp", staging))

    assert "produced no output" in str(caught.value)
    assert processes[0].killed is True
    assert commands[0][2] == "yt_dlp"
    assert "_process" not in job


def test_run_exits_when_the_job_is_cancelled(download_env, monkeypatch):
    staging = download_env["staging"]
    commands, processes = _stub_subprocess(monkeypatch, [[b"first line\n"]])
    manager = DownloadManager()
    job = manager._new_job("https://example.com/a", "unused")
    job["_cancel"] = True

    code, lines = asyncio.run(manager.run(job, "yt-dlp", staging))

    assert code == 0
    assert lines == ["first line"]
    assert processes[0].killed is True
    assert commands[0][2] == "yt_dlp"


def test_run_streams_real_subprocess_output(download_env, monkeypatch):
    staging = download_env["staging"]
    filler = [b"noise\n"] * 401  # longer than the rolling output buffer
    commands, processes = _stub_subprocess(
        monkeypatch,
        [
            [
                b"   \n",
                _progress_line(title="Clip", percent=42.5, total_bytes=100).encode(),
                b"plain notice\n",
            ],
            [b"quieter\n"],
            filler,
        ],
    )
    manager = DownloadManager()

    first = manager._new_job("https://example.com/a", "unused")
    code, lines = asyncio.run(manager.run(first, "yt-dlp", staging))
    assert code == 0
    assert first["title"] == "Clip"
    assert first["percent"] == 42.5
    assert "plain notice" in lines
    assert commands[0][2] == "yt_dlp"
    assert processes[0].killed is False

    second = manager._new_job("https://gofile.io/b", "unused")
    second["downloader"] = "gallery-dl"
    code, lines = asyncio.run(manager.run(second, "gallery-dl", staging))
    assert code == 0
    assert lines == ["quieter"]
    assert commands[1][2] == "gallery_dl"

    long_job = manager._new_job("https://example.com/c", "unused")
    code, lines = asyncio.run(manager.run(long_job, "yt-dlp", staging))
    assert code == 0
    assert len(lines) == 201  # trimmed from 401


def test_run_builds_cookie_commands(download_env, tmp_path, monkeypatch):
    cookies = tmp_path / "cookies.txt"
    cookies.write_text("# Netscape HTTP Cookie File\nfetch\n", encoding="utf-8")
    monkeypatch.setenv("DOWNLOAD_COOKIES_FILE", str(cookies))
    v2_config.reload_settings()
    try:
        manager = DownloadManager()
        staging = download_env["staging"]
        yt = manager._ytdlp_command("https://example.com/a", staging)
        assert yt[0] == sys.executable
        assert yt[1:3] == ["-m", "yt_dlp"]
        assert yt[-3:] == ["--cookies", str(cookies), "https://example.com/a"]
        gallery = manager._gallery_dl_command("https://example.com/a", staging)
        assert gallery[1:3] == ["-m", "gallery_dl"]
        assert gallery[-3:] == ["--cookies", str(cookies), "https://example.com/a"]
    finally:
        v2_config.reload_settings()


def test_command_builders_skip_missing_cookies(download_env):
    manager = DownloadManager()
    staging = download_env["staging"]
    yt = manager._ytdlp_command("https://example.com/a", staging)
    assert "--cookies" not in yt
    assert yt[-1] == "https://example.com/a"
    gallery = manager._gallery_dl_command("https://example.com/a", staging)
    assert "--cookies" not in gallery
    assert gallery[-1] == "https://example.com/a"


def test_cancel_flag_after_run_marks_the_job_cancelled(download_env):
    manager = DownloadManager()
    down: list[str] = []

    async def run(job, downloader, staging):
        down.append(downloader)
        job["_cancel"] = True  # cancellation noticed after the run ends
        return 0, [""]

    manager.run = run  # type: ignore[method-assign]

    async def scenario():
        await manager.enqueue(["https://example.com/x"], str(download_env["inbox"]))
        await manager.wait_idle()

    asyncio.run(scenario())

    job = manager.history[0]
    assert down == ["yt-dlp"]
    assert job["status"] == "cancelled"


def test_cancel_flag_after_gallery_fallback_marks_the_job_cancelled(download_env):
    manager = DownloadManager()
    down: list[str] = []

    async def run(job, downloader, staging):
        down.append(downloader)
        if downloader == "yt-dlp":
            return 1, ["ERROR: Unsupported URL: https://example.com/x"]
        job["_cancel"] = True
        return 0, []

    manager.run = run  # type: ignore[method-assign]

    async def scenario():
        await manager.enqueue(["https://example.com/x"], str(download_env["inbox"]))
        await manager.wait_idle()

    asyncio.run(scenario())

    assert down == ["yt-dlp", "gallery-dl"]
    assert manager.history[0]["status"] == "cancelled"


def test_gallery_dl_required_but_missing_fails(download_env, monkeypatch):
    monkeypatch.setattr(
        downloads_module,
        "tools_available",
        lambda: {"ytdlp": True, "gallery_dl": False},
    )
    manager = DownloadManager()

    async def scenario():
        await manager.enqueue(["https://gofile.io/d/abc"], str(download_env["inbox"]))
        await manager.wait_idle()

    asyncio.run(scenario())

    job = manager.history[0]
    assert job["status"] == "failed"
    assert "gallery-dl" in job["error"]


def test_pipeline_fails_with_the_output_tail(download_env):
    manager = DownloadManager()

    async def run(job, downloader, staging):
        return 0, ["", "error: nope", "bexter"]

    manager.run = run  # type: ignore[method-assign]

    async def scenario():
        await manager.enqueue(["https://example.com/x"], str(download_env["inbox"]))
        await manager.wait_idle()

    asyncio.run(scenario())

    job = manager.history[0]
    assert job["status"] == "failed"
    assert "error: nope" in job["error"]


def test_download_cancelled_mid_run(download_env):
    manager = DownloadManager()

    async def run(job, downloader, staging):
        raise asyncio.CancelledError

    manager.run = run  # type: ignore[method-assign]

    async def scenario():
        await manager.enqueue(["https://example.com/x"], str(download_env["inbox"]))
        await manager.wait_idle()

    asyncio.run(scenario())

    job = manager.history[0]
    assert job["status"] == "cancelled"
    assert job["error"] == "Cancelled"


def test_import_staged_moves_only_media(download_env):
    manager = DownloadManager()
    staging = download_env["staging"] / "job1"
    staging.mkdir()
    _write_media(staging)
    (staging / "meta.json").write_text("{}", encoding="utf-8")
    (staging / "notes.txt").write_text("x", encoding="utf-8")
    (staging / ".secret.mp4").write_bytes(b"v")
    (staging / "nested").mkdir()

    imported = asyncio.run(manager.import_staged(staging, str(download_env["inbox"])))

    assert len(imported) == 1
    assert Path(imported[0]).name == "clip.mp4"
    assert Path(imported[0]).exists()


def test_apply_line_tracks_every_progress_field():
    manager = DownloadManager()
    anchor = pathlib.PurePath("unused")
    job = manager._new_job("https://example.com/a", "somewhere")
    manager._apply_line(
        job,
        _progress_line(
            title="Anthem",
            percent="NA",
            downloaded_bytes=25,
            total_bytes_estimate=50,
            speed="1.5M",
            eta=12,
            playlist_index=2,
            playlist_count=5,
        ),
        anchor,
    )
    assert job["title"] == "Anthem"
    assert job["percent"] == 0.0  # "NA" leaves the percent alone
    assert job["downloaded_bytes"] == 25
    assert job["total_bytes"] == 50  # the estimate filled the gap
    assert job["speed"] == "1.5M"
    assert job["eta"] == "12"
    assert job["playlist_index"] == 2
    assert job["playlist_count"] == 5

    manager._apply_line(job, _progress_line(percent=42.0, total_bytes=100), anchor)
    assert job["percent"] == 42.0
    assert job["total_bytes"] == 100

    # A later estimate never overwrites a real total.
    manager._apply_line(
        job,
        _progress_line(percent=None, total_bytes_estimate=90),
        anchor,
    )
    assert job["total_bytes"] == 100


def test_apply_line_titles_gallery_dl_output(download_env):
    manager = DownloadManager()
    staging = download_env["staging"]

    yt_job = manager._new_job("https://example.com/a", "somewhere")
    yt_job["downloader"] = "yt-dlp"
    manager._apply_line(yt_job, f"{staging}/x.mp4", staging)
    assert yt_job["title"] == ""
    assert yt_job["percent"] == 0.0

    gallery_job = manager._new_job("https://example.com/a", "somewhere")
    gallery_job["downloader"] = "gallery-dl"
    manager._apply_line(gallery_job, f"{staging}/DSC_0001.jpg", staging)
    assert gallery_job["title"] == "DSC_0001.jpg"
    assert gallery_job["percent"] == 5.0

    manager._apply_line(gallery_job, "somewhere else entirely", staging)
    assert gallery_job["title"] == "DSC_0001.jpg"
    assert gallery_job["percent"] == 5.0


def test_finish_flags_a_running_job_failed_exactly_once(download_env):
    manager = DownloadManager()
    job = _job_in(manager, "https://example.com/a", str(download_env["inbox"]))
    job["status"] = "downloading"

    manager._finish(job)
    assert job["status"] == "failed"
    assert job["error"] == "Download ended unexpectedly"
    assert job["finished_at"] is not None
    assert len(manager.history) == 1

    manager._finish(job)  # the second call returns straight away
    assert len(manager.history) == 1


def test_finish_keeps_a_completed_job_as_completed(download_env):
    manager = DownloadManager()
    job = _job_in(manager, "https://example.com/a", str(download_env["inbox"]))
    job["status"] = "completed"
    job["finished_at"] = 5.0
    job["_cancel"] = True
    job["_process"] = _KillingProcess()

    manager._finish(job)
    assert job["finished_at"] == 5.0
    assert "_cancel" not in job
    assert "_process" not in job
    assert manager.history[-1] is job


def test_cancel_control_flow(download_env):
    manager = DownloadManager()
    with pytest.raises(WriteError) as caught:
        manager.cancel("ghost")
    assert caught.value.status == 404

    # A running job: its process is told to die, the loop does the rest.
    live = _job_in(manager, "https://example.com/a", str(download_env["inbox"]))
    live["status"] = "downloading"
    live["_process"] = _KillingProcess()
    assert manager.cancel(live["id"]) == {"cancelled": live["id"]}
    assert live["_process"].calls == 1
    assert manager.jobs.get(live["id"]) is live
    assert live["_cancel"] is True

    # A dying process raises while being killed; the error is swallowed.
    dying = _job_in(manager, "https://example.com/b", str(download_env["inbox"]))
    dying["status"] = "downloading"
    dying["_process"] = _KillingProcess(raises=True)
    assert manager.cancel(dying["id"]) == {"cancelled": dying["id"]}
    assert dying["_process"].calls == 1

    # A still-queued job is retired right away into the history.
    queued = _job_in(manager, "https://example.com/c", str(download_env["inbox"]))
    assert manager.cancel(queued["id"]) == {"cancelled": queued["id"]}
    assert queued["status"] == "cancelled"
    assert queued["error"] == "Cancelled"
    assert queued["id"] not in manager.jobs
    assert queued in manager.history


def test_retry_refuses_unknown_and_unretryable_jobs(download_env):
    manager = DownloadManager()

    with pytest.raises(WriteError) as missing:
        asyncio.run(manager.retry("nope"))
    assert missing.value.status == 404

    done = manager._new_job("https://example.com/a", str(download_env["inbox"]))
    done["status"] = "completed"
    manager.history.append(done)
    with pytest.raises(WriteError) as conflict:
        asyncio.run(manager.retry(done["id"]))
    assert conflict.value.status == 409


def test_retry_needs_a_surviving_destination(download_env):
    manager = DownloadManager()
    failed = manager._new_job(
        "https://example.com/a", str(download_env["library"] / "gone")
    )
    failed["status"] = "failed"
    manager.history.append(failed)

    with pytest.raises(WriteError) as caught:
        asyncio.run(manager.retry(failed["id"]))
    assert caught.value.status == 409
    assert "no longer exists" in caught.value.message


def test_retry_clones_a_failed_job(download_env):
    manager = DownloadManager()
    failed = manager._new_job("https://example.com/a", str(download_env["inbox"]))
    failed["status"] = "failed"
    failed["error"] = "boom"
    manager.history.append(failed)
    _fake_run(manager)

    async def scenario():
        result = await manager.retry(failed["id"])
        await manager.wait_idle()
        return result

    result = asyncio.run(scenario())
    fresh = result["job"]
    assert fresh["id"] != failed["id"]
    assert fresh["url"] == failed["url"]
    assert manager.history[-1]["status"] == "completed"


def test_history_pagination_and_clear():
    manager = DownloadManager()
    for index in range(3):
        job = manager._new_job(f"https://example.com/{index}", "unused")
        job["status"] = "completed"
        job["title"] = f"T{index}"
        manager.history.append(job)

    first = manager.history_page(limit=2, offset=0)
    assert first["total"] == 3
    assert [item["title"] for item in first["items"]] == ["T2", "T1"]
    assert first["limit"] == 2
    assert first["offset"] == 0
    second = manager.history_page(limit=2, offset=2)
    assert [item["title"] for item in second["items"]] == ["T0"]

    assert manager.clear_history() == {"cleared": 3}
    assert manager.history_page()["items"] == []


def test_status_idle_active_and_queued():
    idle = DownloadManager().status()
    assert idle["status"] == "idle"
    assert idle["active"] == []
    assert idle["active_count"] == 0
    assert idle["queued_count"] == 0
    assert idle["history"] == []
    assert idle["concurrency"] >= 1

    manager = DownloadManager()
    active = manager._new_job("https://example.com/a", "unused")
    active["status"] = "downloading"
    manager.jobs[active["id"]] = active
    queued = manager._new_job("https://example.com/b", "unused")
    manager.jobs[queued["id"]] = queued

    payload = manager.status()
    assert payload["status"] == "active"
    assert payload["active_count"] == 1
    assert payload["queued_count"] == 1
    assert payload["tools"]["ytdlp"] is True


def test_public_strips_private_keys():
    job = _job_in(DownloadManager(), "https://example.com/a", "unused")
    job["_cancel"] = True
    job["_process"] = _KillingProcess()
    public = DownloadManager._public(job)  # type: ignore[arg-type]
    assert "_cancel" not in public
    assert "_process" not in public
    assert public["url"] == job["url"]


def test_gc_staging_ignores_a_missing_root(download_env, monkeypatch):
    monkeypatch.setenv(
        "DOWNLOAD_STAGING_DIR", str(download_env["staging"].parent / "absent")
    )
    v2_config.reload_settings()
    try:
        DownloadManager()._gc_staging()  # nothing to sweep, returns quietly
    finally:
        v2_config.reload_settings()


def test_gc_staging_sweeps_old_entries_and_survives_errors(download_env, monkeypatch):
    staging = download_env["staging"]

    def build(name):
        entry = staging / name
        entry.mkdir()
        (entry / "part.bin").write_bytes(b"partial")
        return entry

    old = build("ab12")
    fresh_dir = build("cd34")
    cursed = build("cursed")
    loose_file = staging / "ef56"  # a file, not a directory: never swept
    loose_file.write_bytes(b"loose")

    aged = time.time() - 3 * 86400.0  # three days old
    os.utime(old, (aged, aged))

    real_stat = pathlib.Path.stat

    def cursed_stat(self, *args, **kwargs):
        if self.name == "cursed":
            raise OSError(13, "stat denied")
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "stat", cursed_stat)
    DownloadManager()._gc_staging()

    assert not old.exists()  # past the cutoff: gone
    assert fresh_dir.exists()  # recent: kept
    assert loose_file.exists()
    assert os.path.exists(cursed)  # never stat-able: kept untouched
    shutil.rmtree(cursed, ignore_errors=True)


# ============================================================= growth.py


def test_current_day_uses_utc_and_accepts_now():
    assert growth.current_day(DAY_A) == "2024-07-03"
    assert growth.current_day(DAY_B) == "2024-07-04"
    assert growth.current_day() == growth.current_day(time.time())


def test_live_counts_sample_the_index():
    _seed_files(
        [
            ("a.mp4", "video", 1000),
            ("b.jpg", "image", 500),
            ("c.xyz", "other", 125),
        ]
    )
    counts = growth.live_counts()
    assert counts == {
        "files_count": 3,
        "video_count": 1,
        "image_count": 1,
        "bytes_total": 1625,
    }


def test_record_today_frames_the_day_and_upserts():
    _seed_files([("a.mp4", "video", 100), ("b.jpg", "image", 300)])

    point = growth.record_today(DAY_A)
    assert point["day"] == "2024-07-03"
    assert point["files_count"] == 2
    assert point["bytes_total"] == 400
    assert point["live"] is False
    assert _history_rows() == ["2024-07-03"]

    # A same-day recompute upserts over the row.
    with closing(sqlite3.connect(os.environ["DB_PATH"])) as conn, conn:
        conn.execute("DELETE FROM files WHERE ext = '.jpg'")
    later = growth.record_today(DAY_A + 3600.0)
    assert later["files_count"] == 1
    assert _history_rows() == ["2024-07-03"]

    # Without an explicit now, the real clock frames the row.
    now = growth.record_today()
    assert now["day"] == growth.current_day()


def test_read_history_items_and_modifiers():
    _seed_history([("2024-07-03", DAY_A), ("2024-07-04", DAY_B)])

    payload = growth.read_history(days=1)
    assert payload["days"] == 1
    assert [point["day"] for point in payload["history"]] == ["2024-07-04"]
    assert payload["history"][0]["live"] is False
    assert payload["history"][0]["bytes_total"] == 900

    # ``days`` below one is clamped, but still never shows today.
    clamped = growth.read_history(days=0)
    assert [point["day"] for point in clamped["history"]] == ["2024-07-04"]

    # Today's live counts come back alongside the recorded days.
    today = payload["today"]
    assert today["day"] == growth.current_day()
    assert today["live"] is True
    assert today["files_count"] == 0
    assert today["recorded_at"] is None

    # Every recorded day returns when the window is wide enough.
    full = growth.read_history(days=5)
    assert [point["day"] for point in full["history"]] == [
        "2024-07-03",
        "2024-07-04",
    ]


def test_should_record_answers_the_rollover_question():
    assert growth.should_record(None, None, DAY_A) is True
    assert growth.should_record("2024-07-03", None, DAY_A) is True
    assert (
        growth.should_record("2024-07-03", DAY_A - 2000.0, DAY_A) is True
    )  # cadence elapsed
    assert growth.should_record("2024-07-03", DAY_A, DAY_A + 60.0) is False


def test_worker_record_and_tick():
    worker = growth.GrowthWorker(wake_seconds=growth.WAKE_SECONDS)
    result = worker.record(DAY_A)
    assert worker.recorded_day == "2024-07-03"
    assert worker.last_result == result
    assert worker.last_success_at == pytest.approx(DAY_A)
    assert worker.last_error is None

    # Same day, cadence not elapsed: nothing recorded.
    assert worker.tick(DAY_A + 60.0) is False

    # A rollover is recorded on the next wake.
    assert worker.tick(DAY_B) is True
    assert worker.recorded_day == "2024-07-04"
    assert _history_rows() == ["2024-07-03", "2024-07-04"]


def test_safe_tick_survives_a_read_only_index(monkeypatch, caplog):
    worker = growth.GrowthWorker(wake_seconds=growth.WAKE_SECONDS)
    monkeypatch.setattr(
        growth, "record_today", _boom(growth.DatabaseReadOnlyError("locked"))
    )
    with caplog.at_level(logging.INFO):
        assert worker.safe_tick(DAY_A) is False
    assert worker.last_error == "locked"
    assert worker.recorded_day is None
    assert "growth rollup skipped" in caplog.text


def test_safe_tick_survives_any_failure(monkeypatch, caplog):
    worker = growth.GrowthWorker(wake_seconds=growth.WAKE_SECONDS)
    monkeypatch.setattr(growth, "record_today", _boom(RuntimeError("boom")))
    with caplog.at_level(logging.WARNING):
        assert worker.safe_tick(DAY_A) is False
    assert worker.last_error == "boom"
    assert "growth rollup failed" in caplog.text


def test_worker_start_stop_and_double_start():
    worker = growth.GrowthWorker(wake_seconds=3600.0)
    worker.start()
    thread = worker._thread
    assert thread is not None
    worker.start()  # already alive: the second call returns at once
    assert worker._thread is thread

    worker.stop()
    assert not thread.is_alive()
    assert worker.status()["started"] is False
    assert worker.status()["recorded_day"] == growth.current_day()
    assert worker.status()["last_error"] is None


def test_worker_stop_without_a_thread():
    worker = growth.GrowthWorker(wake_seconds=3600.0)
    worker.stop()  # never started: just clears the flag
    assert worker.status()["started"] is False
    assert worker.recorded_day is None


def test_loop_records_today_and_keeps_waking(monkeypatch, caplog):
    """The loop ticks immediately and again after each wake, without sleep."""
    worker = growth.GrowthWorker(wake_seconds=growth.WAKE_SECONDS)
    stop_event = worker._stop
    real_wait = threading.Event.wait
    scripted = {"count": 0}

    def scripted_wait(self, timeout=None):
        if self is stop_event:
            scripted["count"] += 1
            return scripted["count"] >= 2  # first wake: continue, then stop
        return real_wait(self, timeout)

    monkeypatch.setattr(threading.Event, "wait", scripted_wait)
    worker.start()
    worker.stop()

    assert worker.recorded_day == growth.current_day()
    assert worker.status()["last_error"] is None


def test_worker_registration_snapshot():
    worker = growth.GrowthWorker(wake_seconds=3600.0)
    try:
        growth.register_worker(worker)
        snapshot = growth.worker_status()
        assert snapshot == worker.status()
        assert snapshot["started"] is False
        assert snapshot["last_error"] is None
    finally:
        growth.register_worker(None)
    assert growth.worker_status() is None
