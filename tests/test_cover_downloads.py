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
import subprocess
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


def test_download_retries_a_name_too_long_album_with_truncated_names(download_env):
    manager = DownloadManager()
    calls: list[bool] = []

    async def run(job, downloader, staging, name_limit=False):
        calls.append(name_limit)
        if not name_limit:
            return (
                1,
                [
                    "Katarina Meis kassandra nude public panties street casting",
                    (
                        "OSError: [Errno 36] File name too long: "
                        "'/data/staging/Katarina Meis ...'"
                    ),
                ],
            )
        _write_media(staging, "truncated-name.jpg")
        return 0, ["truncated-name.jpg"]

    manager.run = run  # type: ignore[method-assign]

    async def scenario():
        await manager.enqueue(["https://gofile.io/d/abc"], str(download_env["inbox"]))
        await manager.wait_idle()

    asyncio.run(scenario())

    assert calls == [False, True]
    job = manager.history[0]
    assert job["status"] == "completed"
    assert Path(job["files"][0]).name == "truncated-name.jpg"


def test_download_retries_the_fallback_album_with_truncated_names(download_env):
    """A bunkr-style URL: yt-dlp first, gallery-dl fallback, then the
    name-truncating retry — the retry must key on the job's downloader, not
    the URL's first choice."""
    manager = DownloadManager()
    calls: list[tuple[str, bool]] = []

    async def run(job, downloader, staging, name_limit=False):
        calls.append((downloader, name_limit))
        if downloader == "yt-dlp":
            return 1, ["ERROR: Unsupported URL: https://bunkr.ph/a/JlJMOsew"]
        if not name_limit:
            return 1, ["[download][error] File name too long: 'x'"]
        _write_media(staging, "truncated-name.mp4")
        return 0, ["truncated-name.mp4"]

    manager.run = run  # type: ignore[method-assign]

    async def scenario():
        await manager.enqueue(
            ["https://bunkr.ph/a/JlJMOsew"], str(download_env["inbox"])
        )
        await manager.wait_idle()

    asyncio.run(scenario())

    assert calls == [("yt-dlp", False), ("gallery-dl", False), ("gallery-dl", True)]
    job = manager.history[0]
    assert job["status"] == "completed"
    assert Path(job["files"][0]).name == "truncated-name.mp4"


def test_download_does_not_retry_other_gallery_dl_failures(download_env):
    manager = DownloadManager()
    calls: list[bool] = []

    async def run(job, downloader, staging, name_limit=False):
        calls.append(name_limit)
        return 1, ["[gofile][error] something else went wrong"]

    manager.run = run  # type: ignore[method-assign]

    async def scenario():
        await manager.enqueue(["https://gofile.io/d/abc"], str(download_env["inbox"]))
        await manager.wait_idle()

    asyncio.run(scenario())

    assert calls == [False]
    assert manager.history[0]["status"] == "failed"


def test_name_too_long_detection():
    assert downloads_module._name_too_long(
        ["[downloads.error] OSError: [Errno 36] File name too long: 'x'"]
    )
    assert downloads_module._name_too_long(["[ss] File name too long: 'y'"])
    assert not downloads_module._name_too_long(["[gofile][error] nope"])
    assert not downloads_module._name_too_long([])


def test_gallery_dl_command_adds_the_name_limit_only_when_asked(download_env):
    manager = DownloadManager()
    job = manager._new_job("https://gofile.io/d/abc", "unused")
    staging = download_env["staging"]

    plain = manager._gallery_dl_command(job, staging)
    assert "filename={filename[b:180]}.{extension}" not in plain

    limited = manager._gallery_dl_command(job, staging, name_limit=True)
    assert "filename={filename[b:180]}.{extension}" in limited


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
        monkeypatch, [[asyncio.TimeoutError("no output")] * 10_000]
    )
    monkeypatch.setattr(downloads_module, "_OUTPUT_TIMEOUT", 0.05)
    monkeypatch.setattr(downloads_module, "_PROGRESS_POLL", 0.001)
    manager = DownloadManager()
    job = manager._new_job("https://example.com/a", "unused")

    with pytest.raises(RuntimeError) as caught:
        asyncio.run(manager.run(job, "yt-dlp", staging))

    assert "produced no output" in str(caught.value)
    assert processes[0].killed is True
    assert commands[0][2] == "yt_dlp"
    assert "_process" not in job


def test_staged_bytes_ignores_a_file_that_vanishes(download_env, monkeypatch):
    staging = download_env["staging"]
    kept = staging / "kept.m4v"
    kept.write_bytes(b"x" * 10)
    doomed = staging / "doomed.m4v"
    doomed.write_bytes(b"y" * 5)
    real_stat = os.stat

    def flaky(path, *args, **kwargs):
        if str(path).endswith("doomed.m4v"):
            raise FileNotFoundError(path)
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(downloads_module.os, "stat", flaky)

    assert downloads_module._staged_bytes(staging) == 10
    assert downloads_module._staged_bytes(staging / "missing") == 0


def test_run_keeps_a_silent_download_that_is_still_growing(download_env, monkeypatch):
    """gallery-dl prints nothing while a file streams in pipe mode.

    A large file on a slow CDN must not be mistaken for a wedged process just
    because no line arrived: growing staging bytes are activity too.
    """
    staging = download_env["staging"]
    timeouts = [asyncio.TimeoutError("no output")] * 3
    _commands, processes = _stub_subprocess(monkeypatch, [timeouts + [b""]])
    monkeypatch.setattr(downloads_module, "_OUTPUT_TIMEOUT", 10.0)
    monkeypatch.setattr(downloads_module, "_PROGRESS_POLL", 0.001)
    seen: list[int] = []

    def growing(_staging):
        seen.append(len(seen))
        return len(seen) * 1_000

    monkeypatch.setattr(downloads_module, "_staged_bytes", growing)
    manager = DownloadManager()
    job = manager._new_job("https://example.com/a", "unused")

    code, lines = asyncio.run(manager.run(job, "gallery-dl", staging))

    assert code == 0
    assert processes[0].killed is False
    assert lines == []
    assert len(seen) >= 3


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
        job = manager._new_job("https://example.com/a", "/tmp/library/Inbox")
        yt = manager._ytdlp_command(job, staging)
        assert yt[0] == sys.executable
        assert yt[1:3] == ["-m", "yt_dlp"]
        assert yt[-3:] == ["--cookies", str(cookies), "https://example.com/a"]
        gallery = manager._gallery_dl_command(job, staging)
        assert gallery[1:3] == ["-m", "gallery_dl"]
        assert gallery[-3:] == ["--cookies", str(cookies), "https://example.com/a"]
    finally:
        v2_config.reload_settings()


def test_command_builders_skip_missing_cookies(download_env):
    manager = DownloadManager()
    staging = download_env["staging"]
    job = manager._new_job("https://example.com/a", "/tmp/library/Inbox")
    yt = manager._ytdlp_command(job, staging)
    assert "--cookies" not in yt
    assert yt[-1] == "https://example.com/a"
    gallery = manager._gallery_dl_command(job, staging)
    assert "--cookies" not in gallery
    assert gallery[-1] == "https://example.com/a"


def test_commands_carry_a_per_url_resume_archive(download_env):
    manager = DownloadManager()
    staging = download_env["staging"]
    job = manager._new_job("https://example.com/a", "/tmp/library/Inbox")
    other = manager._new_job("https://example.com/b", "/tmp/library/Inbox")
    elsewhere = manager._new_job("https://example.com/a", "/tmp/library/Other")

    yt = manager._ytdlp_command(job, staging)
    gallery = manager._gallery_dl_command(job, staging)
    simulate = manager._gallery_dl_command(job, staging, simulate=True)

    yt_archive = yt[yt.index("--download-archive") + 1]
    gallery_archive = gallery[gallery.index("--download-archive") + 1]
    assert yt_archive.endswith("-yt-dlp.txt")
    assert gallery_archive.endswith("-gallery-dl.db")
    assert yt_archive != gallery_archive
    # the same URL into another folder must not share a resume ledger
    assert (
        manager._ytdlp_command(elsewhere, staging)[yt.index("--download-archive") + 1]
        != yt_archive
    )
    assert (
        manager._ytdlp_command(other, staging)[yt.index("--download-archive") + 1]
        != yt_archive
    )
    # the listing never touches the archive
    assert "--download-archive" not in simulate
    assert "--simulate" in simulate
    # both runs ask for media and archives; a RAR is kept now, not discarded
    for command in (gallery, simulate):
        expression = command[command.index("--filter") + 1]
        assert "'mp4'" in expression and "'jpg'" in expression
        assert "'rar'" in expression and "'7z'" in expression


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


def test_name_limit_retry_notices_cancellation(download_env):
    manager = DownloadManager()
    calls: list[bool] = []

    async def run(job, downloader, staging, name_limit=False):
        calls.append(name_limit)
        if not name_limit:
            return 1, ["OSError: [Errno 36] File name too long: 'x'"]
        job["_cancel"] = True  # cancellation noticed after the retry ends
        return 0, []

    manager.run = run  # type: ignore[method-assign]

    async def scenario():
        await manager.enqueue(["https://gofile.io/d/abc"], str(download_env["inbox"]))
        await manager.wait_idle()

    asyncio.run(scenario())

    assert calls == [False, True]
    assert manager.history[0]["status"] == "cancelled"


def test_name_limit_retry_reports_a_terminated_downloader(download_env):
    manager = DownloadManager()
    calls: list[bool] = []

    async def run(job, downloader, staging, name_limit=False):
        calls.append(name_limit)
        if not name_limit:
            return 1, ["OSError: [Errno 36] File name too long: 'x'"]
        return -9, []

    manager.run = run  # type: ignore[method-assign]

    async def scenario():
        await manager.enqueue(["https://gofile.io/d/abc"], str(download_env["inbox"]))
        await manager.wait_idle()

    asyncio.run(scenario())

    assert calls == [False, True]
    # An interrupted downloader keeps the job queued for the next run rather
    # than calling the album failed.
    assert not manager.history
    job = next(iter(manager.jobs.values()))
    assert job["status"] == "queued"


def test_apply_line_survives_a_name_too_long_warning(download_env):
    """gallery-dl echoes its own ENAMETOOLONG as a line holding the staging
    path; parsing the name out and stat-ing it raised the same error again,
    which used to kill the whole fold (and the job) from our side."""
    manager = DownloadManager()
    job = manager._new_job("https://gofile.io/d/abc", "unused")
    job["downloader"] = "gallery-dl"
    staging = download_env["staging"]
    long_name = "Katarina " + "ÑÐ¸Ñ" * 80 + "-TReIjTE6.mp4.part"
    line = f"[download][warning] OSError: [Errno 36] File name too long: '{staging / long_name}'"

    manager._apply_line(job, line, staging)  # must not raise

    row = job["file_progress"][0]
    assert row["name"] == Path(line).name
    assert row["done"] is False


def test_run_kills_the_downloader_when_the_fold_blows_up(download_env, monkeypatch):
    staging = download_env["staging"]
    _commands, processes = _stub_subprocess(
        monkeypatch, [[b"https://gofile.io/d/abc line\n"]]
    )
    manager = DownloadManager()
    job = manager._new_job("https://gofile.io/d/abc", "unused")
    job["downloader"] = "gallery-dl"

    def boom(job, line, staging):
        raise RuntimeError("fold blew up")

    monkeypatch.setattr(manager, "_apply_line", boom)

    async def scenario():
        await manager.run(job, "gallery-dl", staging)

    with pytest.raises(RuntimeError, match="fold blew up"):
        asyncio.run(scenario())
    assert processes[0].killed is True


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

    imported, skipped = asyncio.run(
        manager.import_staged(staging, str(download_env["inbox"]))
    )

    assert len(imported) == 1
    assert Path(imported[0]).name == "clip.mp4"
    assert Path(imported[0]).exists()
    assert skipped == 0


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


# ------------------------------------------------- downloads: queue and resume


def test_prelist_fills_the_queue_and_marks_existing_members(download_env, monkeypatch):
    """gallery-dl --simulate lists the whole album without downloading it."""
    staging = download_env["staging"] / "album"
    staging.mkdir()
    inbox = download_env["inbox"]
    (inbox / "b.m4v").write_bytes(b"old")
    lines = [
        b"# a.m4v\n",
        f"# {staging / 'b.m4v'}\n".encode(),
        b"# notes.json\n",
        b"# c.m4v\n",
    ]
    _stub_subprocess(monkeypatch, [lines])
    manager = DownloadManager()
    job = manager._new_job("https://example.com/a", str(inbox))
    job["downloader"] = "gallery-dl"

    asyncio.run(manager._prelist(job, staging))

    assert [(row["name"], row["done"]) for row in job["file_progress"]] == [
        ("a.m4v", False),
        ("b.m4v", True),
        ("c.m4v", False),
    ]
    assert job["_queue_complete"] is True
    assert job["percent"] == 33.3


def test_prelist_survives_a_missing_downloader(download_env, monkeypatch):
    staging = download_env["staging"] / "album"
    staging.mkdir()

    async def broken(*command, **kwargs):
        raise OSError("gallery-dl went away")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", broken)
    manager = DownloadManager()
    job = manager._new_job("https://example.com/a", str(download_env["inbox"]))
    job["downloader"] = "gallery-dl"

    asyncio.run(manager._prelist(job, staging))

    assert job["file_progress"] == []
    assert "_queue_complete" not in job


def test_prelist_stops_at_its_deadline(download_env, monkeypatch):
    staging = download_env["staging"] / "album"
    staging.mkdir()
    monkeypatch.setattr(downloads_module, "_LIST_TIMEOUT", 0.001)

    class _SlowStream:
        async def readline(self):
            await asyncio.sleep(1)
            return b""

    class _SlowProcess:
        stdout = _SlowStream()
        returncode = None

        def kill(self):
            self.returncode = -9

        async def wait(self):
            return self.returncode

    async def fake_exec(*command, **kwargs):
        return _SlowProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    manager = DownloadManager()
    job = manager._new_job("https://example.com/a", str(download_env["inbox"]))
    job["downloader"] = "gallery-dl"

    asyncio.run(manager._prelist(job, staging))

    assert job["file_progress"] == []
    assert "_queue_complete" not in job


def test_fully_archived_album_completes_with_no_new_files(download_env, monkeypatch):
    manager = DownloadManager()

    async def run(job, downloader, staging):
        # every member is skipped by the returned archive
        manager._apply_line(job, f"# {staging}/a.m4v", staging)
        manager._apply_line(job, f"# {staging}/b.m4v", staging)
        return 0, []

    async def no_listing(job, staging):
        return None

    manager.run = run  # type: ignore[method-assign]
    monkeypatch.setattr(manager, "_prelist", no_listing)

    async def scenario():
        await manager.enqueue(["https://example.com/a"], str(download_env["inbox"]))
        await manager.wait_idle()

    asyncio.run(scenario())

    job = manager.history[0]
    assert job["status"] == "completed"
    assert job["files"] == []
    assert all(row["done"] for row in job["file_progress"])


def test_a_failed_job_salvages_its_finished_members(download_env):
    manager = DownloadManager()

    async def run(job, downloader, staging):
        (staging / "done.m4v").write_bytes(b"x" * 10)
        (staging / "half.m4v.part").write_bytes(b"y" * 4)
        raise RuntimeError("cdn stalled")

    manager.run = run  # type: ignore[method-assign]

    async def scenario():
        await manager.enqueue(["https://example.com/x"], str(download_env["inbox"]))
        await manager.wait_idle()

    asyncio.run(scenario())

    job = manager.history[0]
    assert job["status"] == "failed"
    assert "cdn stalled" in job["error"]
    assert job["files"] == [str(download_env["inbox"] / "done.m4v")]
    assert (download_env["inbox"] / "done.m4v").read_bytes() == b"x" * 10
    assert not (download_env["inbox"] / "half.m4v.part").exists()


def test_queue_is_persisted_and_restored_after_a_restart(download_env):
    manager = DownloadManager()
    job = manager._new_job("https://example.com/x", str(download_env["inbox"]))
    job["status"] = "downloading"
    job["file_progress"] = [
        {
            "name": "clip.mp4",
            "done": False,
            "percent": 12.0,
            "downloaded_bytes": 12,
            "total_bytes": 100,
        }
    ]
    job["_imported"] = {"known.mp4"}
    manager.jobs[job["id"]] = job

    asyncio.run(manager.persist())

    state = json.loads(
        downloads_module.get_settings().download_state_path.read_text(encoding="utf-8")
    )
    saved = state["jobs"][0]
    assert saved["url"] == job["url"]
    assert saved["status"] == "downloading"
    assert saved["imported"] == ["known.mp4"]
    assert saved["file_progress"][0]["name"] == "clip.mp4"

    restored = DownloadManager()
    _fake_run(restored)

    async def restart():
        count = await restored.restore()
        await restored.wait_idle()
        return count

    assert asyncio.run(restart()) == 1
    assert [job_entry["id"] for job_entry in restored.history] == [job["id"]]
    assert restored.history[0]["status"] == "completed", restored.history[0]["error"]


def test_restore_without_state_is_a_noop(download_env):
    manager = DownloadManager()
    assert asyncio.run(manager.restore()) == 0
    assert manager.jobs == {}
    assert asyncio.run(manager.restore()) == 0  # a second pass does nothing


def test_restore_ignores_a_corrupt_state_file(download_env):
    path = downloads_module.get_settings().download_state_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    manager = DownloadManager()
    assert asyncio.run(manager.restore()) == 0


def test_restore_drops_rows_without_a_url_or_destination(download_env):
    path = downloads_module.get_settings().download_state_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "jobs": [
                    "not a job",
                    {"url": "https://example.com/a"},
                    {"destination": "/tmp/x"},
                ]
            }
        ),
        encoding="utf-8",
    )
    manager = DownloadManager()
    assert asyncio.run(manager.restore()) == 0


def test_shutdown_keeps_the_queue_and_the_staging_of_a_running_job(download_env):
    manager = DownloadManager()
    job = manager._new_job("https://example.com/x", str(download_env["inbox"]))
    job["status"] = "downloading"
    manager.jobs[job["id"]] = job
    staging = download_env["staging"] / job["id"]
    staging.mkdir()
    (staging / "done.m4v").write_bytes(b"x")

    asyncio.run(manager.shutdown())

    assert manager._closing is True
    state = json.loads(
        downloads_module.get_settings().download_state_path.read_text(encoding="utf-8")
    )
    assert [entry["url"] for entry in state["jobs"]] == ["https://example.com/x"]
    assert (staging / "done.m4v").exists()


def test_a_failed_queue_write_does_not_escape(download_env, monkeypatch):
    manager = DownloadManager()
    job = manager._new_job("https://example.com/x", str(download_env["inbox"]))
    manager.jobs[job["id"]] = job

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(downloads_module, "_write_json", boom)

    async def scenario():
        manager._save_soon()
        assert manager._save_task is not None
        await manager._save_task

    asyncio.run(scenario())
    assert manager._closing is False


def test_directory_names_tolerates_a_missing_folder(tmp_path):
    assert downloads_module._directory_names(str(tmp_path / "nope")) == set()
    folder = tmp_path / "dest"
    folder.mkdir()
    (folder / "kept.m4v").write_bytes(b"x")
    assert downloads_module._directory_names(str(folder)) == {"kept.m4v"}


def test_refresh_percent_ignores_an_incomplete_or_empty_queue():
    manager = DownloadManager()
    job = manager._new_job("https://example.com/a", "/tmp/x")
    job["percent"] = 7.0
    manager._refresh_percent(job)
    assert job["percent"] == 7.0
    job["_queue_complete"] = True
    manager._refresh_percent(job)
    assert job["percent"] == 7.0


def test_save_soon_without_a_running_loop_only_marks_it_dirty(download_env):
    manager = DownloadManager()
    manager._save_soon()
    assert manager._save_task is None
    assert manager._dirty is True
    manager._closing = True
    manager._dirty = False
    manager._save_soon()
    assert manager._dirty is False


def test_persist_cancels_a_pending_save_and_reports_write_errors(
    download_env, monkeypatch
):
    manager = DownloadManager()
    job = manager._new_job("https://example.com/x", str(download_env["inbox"]))
    manager.jobs[job["id"]] = job

    def boom(*args, **kwargs):
        raise OSError("disk full")

    async def scenario():
        manager._save_soon()
        assert manager._save_task is not None
        monkeypatch.setattr(downloads_module, "_write_json", boom)
        await manager.persist()

    asyncio.run(scenario())


def test_prelist_exits_at_a_zero_deadline(download_env, monkeypatch):
    staging = download_env["staging"] / "album"
    staging.mkdir()
    _stub_subprocess(monkeypatch, [[f"# {staging / 'a.m4v'}\n".encode()]])
    monkeypatch.setattr(downloads_module, "_LIST_TIMEOUT", 0.0)
    manager = DownloadManager()
    job = manager._new_job("https://example.com/a", str(download_env["inbox"]))
    job["downloader"] = "gallery-dl"

    asyncio.run(manager._prelist(job, staging))

    assert job["file_progress"] == []


def test_prelist_ignores_noise_duplicates_and_caps_the_queue(download_env, monkeypatch):
    staging = download_env["staging"] / "album"
    staging.mkdir()
    lines = [
        b"an unrelated log line\n",
        f"# {staging / 'a.m4v'}\n".encode(),
        f"# {staging / 'a.m4v'}\n".encode(),
        f"# {staging / 'b.m4v'}\n".encode(),
    ]
    _stub_subprocess(monkeypatch, [lines])
    monkeypatch.setattr(downloads_module, "_MAX_QUEUE_ROWS", 1)
    manager = DownloadManager()
    job = manager._new_job("https://example.com/a", str(download_env["inbox"]))
    job["downloader"] = "gallery-dl"

    asyncio.run(manager._prelist(job, staging))

    assert [row["name"] for row in job["file_progress"]] == ["a.m4v"]


def test_prelist_tolerates_a_process_that_cannot_be_reaped(download_env, monkeypatch):
    staging = download_env["staging"] / "album"
    staging.mkdir()

    class _Unreapable:
        returncode = 0

        class _Stdout:
            async def readline(self):
                return b""

        stdout = _Stdout()

        async def wait(self):
            raise OSError("gone")

    async def fake_exec(*command, **kwargs):
        return _Unreapable()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    manager = DownloadManager()
    job = manager._new_job("https://example.com/a", str(download_env["inbox"]))
    job["downloader"] = "gallery-dl"

    asyncio.run(manager._prelist(job, staging))

    assert job["file_progress"] == []


def test_a_salvage_failure_is_swallowed(download_env, monkeypatch):
    manager = DownloadManager()

    async def run(job, downloader, staging):
        (staging / "done.m4v").write_bytes(b"x")
        raise RuntimeError("boom")

    async def broken_import(*args, **kwargs):
        raise OSError("mount went away")

    manager.run = run  # type: ignore[method-assign]
    monkeypatch.setattr(manager, "import_staged", broken_import)

    async def scenario():
        await manager.enqueue(["https://example.com/x"], str(download_env["inbox"]))
        await manager.wait_idle()

    asyncio.run(scenario())

    job = manager.history[0]
    assert job["status"] == "failed"
    assert job["files"] == []


def test_gallery_dl_lines_use_the_listed_queue_for_percent(download_env):
    manager = DownloadManager()
    job = manager._new_job("https://example.com/a", "/tmp/library/Inbox")
    job["downloader"] = "gallery-dl"
    job["_queue_complete"] = True
    for name in ("a.m4v", "b.m4v", "c.m4v", "d.m4v"):
        manager._register_row(job, name, None, None, None, conclude_others=False)
    staging = download_env["staging"]
    (staging / "a.m4v").write_bytes(b"x")

    manager._apply_line(job, f"{staging}/a.m4v", staging)

    assert job["percent"] == 25.0


def test_shutdown_kills_processes_and_joins_running_tasks(download_env):
    manager = DownloadManager()
    job = manager._new_job("https://example.com/x", str(download_env["inbox"]))
    manager.jobs[job["id"]] = job

    class _Process:
        killed = False

        def kill(self):
            self.killed = True

    class _DeadProcess:
        def kill(self):
            raise ProcessLookupError("already gone")

    process = _Process()
    job["_process"] = process
    job["_list_process"] = _DeadProcess()

    async def scenario():
        task = asyncio.create_task(asyncio.sleep(60))
        manager._tasks.add(task)
        await manager.shutdown()
        assert task.cancelled()

    asyncio.run(scenario())

    assert process.killed is True


def test_gc_removes_stale_download_archives(download_env):
    archive_dir = downloads_module.get_settings().download_archive_dir
    archive_dir.mkdir(parents=True, exist_ok=True)
    stale = archive_dir / "stale.txt"
    stale.write_text("x", encoding="utf-8")
    os.utime(stale, (0, 0))
    fresh = archive_dir / "fresh.txt"
    fresh.write_text("x", encoding="utf-8")

    DownloadManager()._gc_staging()

    assert not stale.exists()
    assert fresh.exists()


def test_lifespan_restores_downloads_and_shuts_them_down(download_env, monkeypatch):
    from fastapi.testclient import TestClient

    from media_library.app import create_app
    from media_library.routers import downloads as downloads_router

    manager = downloads_router.manager
    calls: list[str] = []

    async def fake_restore():
        calls.append("restore")
        return 1

    async def fake_shutdown():
        calls.append("shutdown")
        raise RuntimeError("boom")

    monkeypatch.setattr(manager, "restore", fake_restore)
    monkeypatch.setattr(manager, "shutdown", fake_shutdown)

    with TestClient(create_app()) as client:
        assert client.get("/api/downloads/history").status_code == 200

    assert calls == ["restore", "shutdown"]


def test_lifespan_tolerates_a_restore_failure(download_env, monkeypatch):
    from fastapi.testclient import TestClient

    from media_library.app import create_app
    from media_library.routers import downloads as downloads_router

    manager = downloads_router.manager

    async def broken_restore():
        raise RuntimeError("state unreadable")

    async def broken_shutdown():
        raise RuntimeError("boom")

    monkeypatch.setattr(manager, "restore", broken_restore)
    monkeypatch.setattr(manager, "shutdown", broken_shutdown)

    with TestClient(create_app()) as client:
        assert client.get("/api/downloads/history").status_code == 200


def test_a_download_renames_a_listed_row_when_the_extension_changes(download_env):
    """gallery-dl fixes the extension from the response; the row follows."""
    staging = download_env["staging"]
    manager = DownloadManager()
    job = manager._new_job("https://example.com/a", "/tmp/library/Inbox")
    job["downloader"] = "gallery-dl"
    manager._register_row(job, "clip.mp4", None, None, None, conclude_others=False)

    (staging / "clip.m4v").write_bytes(b"x")
    manager._apply_line(job, f"{staging}/clip.m4v", staging)

    assert [(row["name"], row["done"]) for row in job["file_progress"]] == [
        ("clip.m4v", True)
    ]


def test_prelist_keeps_the_name_the_download_already_reported(
    download_env, monkeypatch
):
    staging = download_env["staging"] / "album"
    staging.mkdir()
    _stub_subprocess(monkeypatch, [[f"# {staging / 'clip.mp4'}\n".encode()]])
    manager = DownloadManager()
    job = manager._new_job("https://example.com/a", str(download_env["inbox"]))
    job["downloader"] = "gallery-dl"
    manager._register_row(job, "clip.m4v", None, None, None)

    asyncio.run(manager._prelist(job, staging))

    assert [row["name"] for row in job["file_progress"]] == ["clip.m4v"]


def test_prelist_marks_a_member_done_when_only_the_extension_differs(
    download_env, monkeypatch
):
    staging = download_env["staging"] / "album"
    staging.mkdir()
    inbox = download_env["inbox"]
    (inbox / "clip.m4v").write_bytes(b"old")
    _stub_subprocess(monkeypatch, [[f"# {staging / 'clip.mp4'}\n".encode()]])
    manager = DownloadManager()
    job = manager._new_job("https://example.com/a", str(inbox))
    job["downloader"] = "gallery-dl"

    asyncio.run(manager._prelist(job, staging))

    assert [(row["name"], row["done"]) for row in job["file_progress"]] == [
        ("clip.mp4", True)
    ]


def test_a_run_that_stops_early_fails_but_keeps_its_files(download_env):
    """A non-zero exit with some files landed is a failure, not a success.

    gallery-dl returns non-zero when a member or the connection fails. The
    finished members are salvaged and the job stays retryable, so the archive
    resumes instead of the client showing a half album as completed.
    """
    manager = DownloadManager()

    async def run(job, downloader, staging):
        (staging / "landed.mp4").write_bytes(b"x" * 4)
        return 4, ["[bunkr][error] Request failed"]

    manager.run = run  # type: ignore[method-assign]

    async def scenario():
        await manager.enqueue(["https://example.com/a"], str(download_env["inbox"]))
        await manager.wait_idle()

    asyncio.run(scenario())

    job = manager.history[0]
    assert job["status"] == "failed"
    assert "exit 4" in job["error"]
    assert "Request failed" in job["error"]
    assert job["files"] == [str(download_env["inbox"] / "landed.mp4")]
    assert (download_env["inbox"] / "landed.mp4").exists()


def test_a_signal_killed_downloader_keeps_the_job_queued(download_env):
    """A stop must not turn into a completed/failed job that leaves the queue."""
    manager = DownloadManager()

    async def run(job, downloader, staging):
        (staging / "half.mp4.part").write_bytes(b"x")
        return -15, []

    manager.run = run  # type: ignore[method-assign]

    async def scenario():
        await manager.enqueue(["https://example.com/a"], str(download_env["inbox"]))
        await manager.wait_idle()

    asyncio.run(scenario())

    job = next(iter(manager.jobs.values()))
    assert job["status"] == "queued"
    assert list(manager.history) == []
    staging = downloads_module.get_settings().download_staging_dir / job["id"]
    assert (staging / "half.mp4.part").exists()
    state = json.loads(
        downloads_module.get_settings().download_state_path.read_text(encoding="utf-8")
    )
    assert [entry["id"] for entry in state["jobs"]] == [job["id"]]

    # a queued job that is still around can be cancelled as usual
    manager.cancel(job["id"])
    assert manager.history[-1]["status"] == "cancelled"


def test_an_interrupted_job_is_respawned_when_the_server_stays_up(download_env):
    manager = DownloadManager()
    job = manager._new_job("https://example.com/a", str(download_env["inbox"]))
    manager.jobs[job["id"]] = job
    spawned: list[str] = []
    manager._spawn = lambda job_id: spawned.append(job_id)  # type: ignore[method-assign]

    manager._respawn(job["id"])
    assert spawned == [job["id"]]

    job["status"] = "downloading"
    manager._respawn(job["id"])
    manager._closing = True
    job["status"] = "queued"
    manager._respawn(job["id"])
    manager._respawn("missing")
    assert spawned == [job["id"]]


def test_an_interrupted_gallery_dl_fallback_keeps_the_job(download_env):
    manager = DownloadManager()
    calls: list[str] = []

    async def run(job, downloader, staging):
        calls.append(downloader)
        if downloader == "yt-dlp":
            return 1, ["ERROR: Unsupported URL: https://example.com/x"]
        return -9, []

    manager.run = run  # type: ignore[method-assign]

    async def scenario():
        await manager.enqueue(["https://example.com/a"], str(download_env["inbox"]))
        await manager.wait_idle()

    asyncio.run(scenario())

    assert calls == ["yt-dlp", "gallery-dl"]
    job = next(iter(manager.jobs.values()))
    assert job["status"] == "queued"
    manager.cancel(job["id"])


def test_resume_later_without_a_running_loop_is_a_noop(download_env):
    manager = DownloadManager()
    manager._resume_later("missing")  # outside asyncio.run: no loop to schedule on


# ------------------------------------------------- downloads: archive volumes


class _FakeSevenZip:
    """Stand-in for 7z: writes one media file into the -o directory."""

    def __init__(self, code: int = 0):
        self.code = code
        self.calls: list[list[str]] = []

    def __call__(self, command, **kwargs):
        self.calls.append(list(command))
        out = next(arg[2:] for arg in command if arg.startswith("-o"))
        pathlib.Path(out).joinpath("released.mp4").write_bytes(b"v")
        return subprocess.CompletedProcess(command, self.code, "", "")


def test_archives_are_downloadable_but_not_media():
    assert downloads_module.is_downloadable_file(Path("clip.mp4"))
    assert downloads_module.is_downloadable_file(Path("VIDS.part01.rar"))
    assert downloads_module.is_downloadable_file(Path("set.7z"))
    assert downloads_module.is_downloadable_file(Path("set.zip"))
    assert not downloads_module.is_downloadable_file(Path("notes.json"))
    assert not downloads_module.is_media_file(Path("VIDS.part01.rar"))


def test_store_archive_moves_a_volume_into_the_archives_folder(download_env):
    inbox = download_env["inbox"]
    staging = download_env["staging"] / "job"
    staging.mkdir()
    volume = staging / "VIDS.part02.rar"
    volume.write_bytes(b"r" * 6)

    target = downloads_module._store_archive(str(volume), str(inbox))

    assert target == str(inbox / "archives" / "VIDS.part02.rar")
    assert pathlib.Path(target).read_bytes() == b"r" * 6
    assert not volume.exists()

    # a second copy of a name already kept is dropped, not overwritten
    again = staging / "VIDS.part02.rar"
    again.write_bytes(b"x")
    assert downloads_module._store_archive(str(again), str(inbox)) is None
    assert not again.exists()
    assert pathlib.Path(target).read_bytes() == b"r" * 6


def test_a_failed_archive_copy_leaves_no_temp(download_env, monkeypatch):
    inbox = download_env["inbox"]
    staging = download_env["staging"] / "job2"
    staging.mkdir()
    volume = staging / "set.rar"
    volume.write_bytes(b"r")

    def broken(*args, **kwargs):
        raise OSError("mount dropped")

    monkeypatch.setattr(downloads_module.shutil, "move", broken)
    with pytest.raises(OSError):
        downloads_module._store_archive(str(volume), str(inbox))
    assert list((inbox / "archives").glob(".mllibtmp-*")) == []


def test_the_first_volume_is_found_whatever_the_numbering(tmp_path):
    archives = tmp_path / "archives"
    archives.mkdir()
    (archives / "VIDS.part01.rar").write_bytes(b"r")
    (archives / "OLD.part1.rar").write_bytes(b"r")
    (archives / "SET.7z").write_bytes(b"7")

    assert downloads_module._set_first_part(archives, "VIDS.part21.rar") == (
        archives / "VIDS.part01.rar"
    )
    assert downloads_module._set_first_part(archives, "OLD.part07.rar") == (
        archives / "OLD.part1.rar"
    )
    assert downloads_module._set_first_part(archives, "SET.7z") == archives / "SET.7z"
    assert downloads_module._set_first_part(archives, "notes.txt") is None
    assert not (archives / "LATE.part01.rar").exists()
    assert downloads_module._set_first_part(archives, "LATE.part02.rar") is None


def test_first_volume_detection():
    assert downloads_module._is_first_volume("VIDS.part01.rar")
    assert downloads_module._is_first_volume("VIDS.part1.rar")
    assert downloads_module._is_first_volume("SET.7z")
    assert not downloads_module._is_first_volume("VIDS.part21.rar")
    assert not downloads_module._is_first_volume("notes.txt")
    assert downloads_module._set_key("VIDS.part01.rar") == "VIDS"
    assert downloads_module._set_key("VIDS.part1.rar") == "VIDS"
    assert downloads_module._set_key("SET.7z") == "SET.7z"


def test_extract_volume_moves_results_out_and_marks_the_set(download_env, monkeypatch):
    inbox = download_env["inbox"]
    archives = inbox / "archives"
    archives.mkdir()
    first = archives / "VIDS.part01.rar"
    first.write_bytes(b"r")
    fake = _FakeSevenZip()
    monkeypatch.setattr(downloads_module.shutil, "which", lambda name: "/usr/bin/7z")
    monkeypatch.setattr(downloads_module.subprocess, "run", fake)

    assert downloads_module._extract_volume(first, str(inbox)) is True
    assert (archives / "released.mp4").read_bytes() == b"v"
    assert not (archives / ".media-library-extracting").exists()
    assert fake.calls[0][1:4] == ["x", "-y", "-aoa"]

    # the marker keeps a re-run from unpacking the same set again
    assert downloads_module._extract_volume(first, str(inbox)) is False
    assert len(fake.calls) == 1


def test_extract_volume_leaves_nothing_when_the_set_is_incomplete(
    download_env, monkeypatch
):
    inbox = download_env["inbox"]
    archives = inbox / "archives"
    archives.mkdir()
    first = archives / "VIDS.part01.rar"
    first.write_bytes(b"r")
    fake = _FakeSevenZip(code=2)
    monkeypatch.setattr(downloads_module.shutil, "which", lambda name: "/usr/bin/7z")
    monkeypatch.setattr(downloads_module.subprocess, "run", fake)

    assert downloads_module._extract_volume(first, str(inbox)) is False
    assert list(archives.iterdir()) == [first]
    assert not (archives / ".media-library-extracting").exists()


def test_extract_volume_without_seven_zip_keeps_the_archive(download_env, monkeypatch):
    inbox = download_env["inbox"]
    archives = inbox / "archives"
    archives.mkdir()
    first = archives / "SET.7z"
    first.write_bytes(b"r")
    monkeypatch.setattr(downloads_module.shutil, "which", lambda name: None)

    assert downloads_module._extract_volume(first, str(inbox)) is False
    assert first.exists()


def test_extract_volume_tolerates_a_broken_tool(download_env, monkeypatch):
    inbox = download_env["inbox"]
    archives = inbox / "archives"
    archives.mkdir()
    first = archives / "SET.7z"
    first.write_bytes(b"r")

    def broken(*args, **kwargs):
        raise OSError("spawn failed")

    monkeypatch.setattr(downloads_module.shutil, "which", lambda name: "/usr/bin/7z")
    monkeypatch.setattr(downloads_module.subprocess, "run", broken)
    assert downloads_module._extract_volume(first, str(inbox)) is False


def test_store_and_extract_on_import(download_env, monkeypatch):
    """A staged RAR lands in archives/ and its complete set is unpacked."""
    inbox = download_env["inbox"]
    staging = download_env["staging"] / "archivejob"
    staging.mkdir()
    (staging / "VIDS.part01.rar").write_bytes(b"r")
    (staging / "clip.mp4").write_bytes(b"m")
    fake = _FakeSevenZip()
    monkeypatch.setattr(downloads_module.shutil, "which", lambda name: "/usr/bin/7z")
    monkeypatch.setattr(downloads_module.subprocess, "run", fake)
    manager = DownloadManager()
    job = manager._new_job("https://example.com/a", str(inbox))
    job["_queue_complete"] = True
    manager._register_row(job, "VIDS.part01.rar", None, None, None)

    async def scenario():
        imported, skipped = await manager.import_staged(staging, str(inbox))
        extracted = await manager._extract_ready(job)
        return imported, skipped, extracted

    imported, skipped, extracted = asyncio.run(scenario())

    assert sorted(Path(path).name for path in imported) == [
        "VIDS.part01.rar",
        "clip.mp4",
    ]
    assert skipped == 0
    assert extracted == 1
    assert (inbox / "archives" / "VIDS.part01.rar").exists()
    assert (inbox / "archives" / "released.mp4").exists()
    assert (inbox / "clip.mp4").exists()


def test_extract_ready_waits_for_the_last_volume(download_env, monkeypatch):
    inbox = download_env["inbox"]
    archives = inbox / "archives"
    archives.mkdir()
    (archives / "VIDS.part01.rar").write_bytes(b"r")
    fake = _FakeSevenZip()
    monkeypatch.setattr(downloads_module.shutil, "which", lambda name: "/usr/bin/7z")
    monkeypatch.setattr(downloads_module.subprocess, "run", fake)
    manager = DownloadManager()
    job = manager._new_job("https://example.com/a", str(inbox))
    job["_queue_complete"] = True
    manager._register_row(job, "VIDS.part01.rar", None, None, None)
    manager._register_row(job, "VIDS.part02.rar", None, None, None)

    # part02 is still downloading: nothing is handed to 7z yet
    assert asyncio.run(manager._extract_ready(job)) == 0
    assert fake.calls == []

    (archives / "VIDS.part02.rar").write_bytes(b"r")
    assert asyncio.run(manager._extract_ready(job)) == 1
    assert len(fake.calls) == 1
    # the marker keeps the next pass quiet
    assert asyncio.run(manager._extract_ready(job)) == 0
    assert len(fake.calls) == 1


def test_extract_ready_needs_a_complete_listing(download_env):
    manager = DownloadManager()
    job = manager._new_job("https://example.com/a", str(download_env["inbox"]))
    assert asyncio.run(manager._extract_ready(job)) == 0


def test_evacuation_archives_a_finished_volume_mid_download(download_env, monkeypatch):
    inbox = download_env["inbox"]
    staging = download_env["staging"] / "archivemid"
    staging.mkdir()
    volume = staging / "VIDS.part01.rar"
    volume.write_bytes(b"r")
    fake = _FakeSevenZip()
    monkeypatch.setattr(downloads_module.shutil, "which", lambda name: "/usr/bin/7z")
    monkeypatch.setattr(downloads_module.subprocess, "run", fake)
    manager = DownloadManager()
    job = manager._new_job("https://example.com/a", str(inbox))
    job["downloader"] = "gallery-dl"

    async def scenario():
        manager._apply_line(job, f"{staging / 'VIDS.part01.rar'}", staging)

    asyncio.run(scenario())
    asyncio.run(manager._evacuate_done_files(job, staging))

    assert job["_imported"] == {"VIDS.part01.rar"}
    assert job["files"] == [str(inbox / "archives" / "VIDS.part01.rar")]
    assert not volume.exists()


def test_extract_pending_covers_a_resumed_job(download_env, monkeypatch):
    inbox = download_env["inbox"]
    archives = inbox / "archives"
    archives.mkdir()
    (archives / "VIDS.part01.rar").write_bytes(b"r")
    (archives / "VIDS.part02.rar").write_bytes(b"r")
    fake = _FakeSevenZip()
    monkeypatch.setattr(downloads_module.shutil, "which", lambda name: "/usr/bin/7z")
    monkeypatch.setattr(downloads_module.subprocess, "run", fake)

    assert downloads_module._extract_pending(str(inbox)) == 1
    assert len(fake.calls) == 1
    assert (archives / "released.mp4").exists()
    # nothing left to do on a second pass
    assert downloads_module._extract_pending(str(inbox)) == 0


def test_prelist_lists_archives_and_marks_stored_volumes(download_env, monkeypatch):
    staging = download_env["staging"] / "album"
    staging.mkdir()
    inbox = download_env["inbox"]
    (inbox / "archives").mkdir()
    (inbox / "archives" / "VIDS.part01.rar").write_bytes(b"r")
    lines = [
        b"# VIDS.part01.rar\n",
        b"# VIDS.part02.rar\n",
        b"# clip.mp4\n",
    ]
    _stub_subprocess(monkeypatch, [lines])
    manager = DownloadManager()
    job = manager._new_job("https://example.com/a", str(inbox))
    job["downloader"] = "gallery-dl"

    asyncio.run(manager._prelist(job, staging))

    assert [(row["name"], row["done"]) for row in job["file_progress"]] == [
        ("VIDS.part01.rar", True),
        ("VIDS.part02.rar", False),
        ("clip.mp4", False),
    ]


class _TreeSevenZip:
    """7z stand-in that lays out a directory, a clashing file and a stale file."""

    def __call__(self, command, **kwargs):
        out = pathlib.Path(next(arg[2:] for arg in command if arg.startswith("-o")))
        (out / "pack").mkdir()
        (out / "pack" / "inside.mp4").write_bytes(b"n")
        (out / "clash.mp4").write_bytes(b"f")
        (out / "over.mp4").write_bytes(b"new")
        return subprocess.CompletedProcess(command, 0, "", "")


def test_extract_volume_merges_and_replaces_what_is_already_there(
    download_env, monkeypatch
):
    inbox = download_env["inbox"]
    archives = inbox / "archives"
    (archives / "pack").mkdir(parents=True)
    (archives / "pack" / "old.mp4").write_bytes(b"o")
    (archives / "clash.mp4").mkdir()
    (archives / "over.mp4").write_bytes(b"old")
    first = archives / "SET.7z"
    first.write_bytes(b"r")
    monkeypatch.setattr(downloads_module.shutil, "which", lambda name: "/usr/bin/7z")
    monkeypatch.setattr(downloads_module.subprocess, "run", _TreeSevenZip())

    assert downloads_module._extract_volume(first, str(inbox)) is True
    assert (archives / "pack" / "old.mp4").exists()
    assert (archives / "pack" / "inside.mp4").exists()
    assert archives / "clash.mp4" / "inside.mp4"
    assert (archives / "over.mp4").read_bytes() == b"new"


def test_evacuation_keeps_an_archive_when_storing_it_fails(download_env, monkeypatch):
    staging = download_env["staging"] / "archivestorefail"
    staging.mkdir()
    volume = staging / "VIDS.part01.rar"
    volume.write_bytes(b"r")
    manager = DownloadManager()
    job = manager._new_job("https://example.com/a", str(download_env["inbox"]))
    job["downloader"] = "gallery-dl"
    manager._apply_line(job, f"{staging / 'VIDS.part01.rar'}", staging)

    def boom(*args, **kwargs):
        raise OSError("mount dropped")

    monkeypatch.setattr(downloads_module, "_store_archive", boom)
    asyncio.run(manager._evacuate_done_files(job, staging))

    assert job["_imported"] == set()
    assert volume.exists()


def test_evacuation_ignores_sidecars(download_env):
    staging = download_env["staging"] / "sidecar"
    staging.mkdir()
    (staging / "notes.txt").write_bytes(b"t")
    manager = DownloadManager()
    job = manager._new_job("https://example.com/a", str(download_env["inbox"]))
    job["downloader"] = "gallery-dl"
    manager._apply_line(job, f"{staging / 'notes.txt'}", staging)

    asyncio.run(manager._evacuate_done_files(job, staging))

    assert job["_imported"] == set()
    assert (staging / "notes.txt").exists()


def test_extract_ready_without_an_archives_folder(download_env):
    manager = DownloadManager()
    job = manager._new_job("https://example.com/a", str(download_env["inbox"]))
    job["_queue_complete"] = True
    manager._register_row(job, "VIDS.part01.rar", None, None, None)
    assert asyncio.run(manager._extract_ready(job)) == 0


def test_extract_ready_skips_a_set_with_no_first_volume(download_env):
    inbox = download_env["inbox"]
    archives = inbox / "archives"
    archives.mkdir()
    (archives / "VIDS.part02.rar").write_bytes(b"r")
    manager = DownloadManager()
    job = manager._new_job("https://example.com/a", str(inbox))
    job["_queue_complete"] = True
    manager._register_row(job, "VIDS.part02.rar", None, None, None)
    assert asyncio.run(manager._extract_ready(job)) == 0


def test_extract_ready_survives_a_missing_first_volume(download_env, monkeypatch):
    inbox = download_env["inbox"]
    archives = inbox / "archives"
    archives.mkdir()
    (archives / "VIDS.part01.rar").write_bytes(b"r")
    manager = DownloadManager()
    job = manager._new_job("https://example.com/a", str(inbox))
    job["_queue_complete"] = True
    manager._register_row(job, "VIDS.part01.rar", None, None, None)
    monkeypatch.setattr(downloads_module, "_set_first_part", lambda *args: None)
    assert asyncio.run(manager._extract_ready(job)) == 0


def test_a_completed_job_extracts_its_ready_sets(download_env, monkeypatch):
    inbox = download_env["inbox"]
    fake = _FakeSevenZip()
    monkeypatch.setattr(downloads_module.shutil, "which", lambda name: "/usr/bin/7z")
    monkeypatch.setattr(downloads_module.subprocess, "run", fake)
    manager = DownloadManager()

    async def run(job, downloader, staging):
        job["_queue_complete"] = True
        job["downloader"] = downloader
        (staging / "VIDS.part01.rar").write_bytes(b"r")
        (staging / "clip.mp4").write_bytes(b"m")
        manager._register_row(job, "VIDS.part01.rar", None, None, None)
        return 0, []

    manager.run = run  # type: ignore[method-assign]

    async def scenario():
        await manager.enqueue(["https://example.com/a"], str(inbox))
        await manager.wait_idle()

    asyncio.run(scenario())

    job = manager.history[0]
    assert job["status"] == "completed"
    assert (inbox / "archives" / "VIDS.part01.rar").exists()
    assert (inbox / "archives" / "released.mp4").exists()
