"""Extra coverage for posters, animated previews, the sweep and streaming.

Complements ``test_v2_workers.py`` / ``test_v2_animated.py`` /
``test_v2_media_transcode.py`` with the error, fallback and edge branches of
``thumbnails``, ``animated``, ``thumbnail_worker``, ``transcode_stream`` and
``routers.media``.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import subprocess
import threading
import time
import types
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from media_library import animated, thumbnail_worker, thumbnails
from media_library import config as v2_config
from media_library.app import create_app
from media_library.cache import animated_path, cache_key
from media_library.writes import WriteError

# --- shared fixture ---------------------------------------------------------


def _make_video(target: Path) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=160x120:rate=20",
            "-t",
            "1",
            "-pix_fmt",
            "yuv420p",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-y",
            str(target),
        ],
        check=True,
    )


def _add_row(
    db_path: Path,
    path: Path,
    kind: str,
    ext: str,
    duration: float | None = None,
    has_thumb: int = 0,
    has_animated: int = 0,
    folder: Path | None = None,
) -> None:
    stat = path.stat()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO folders (path, name, parent_path, "
            "file_count, subfolder_count, last_scanned, is_zip, total_size) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (str(folder or path.parent), "lib", None, 1, 0, 1.0, 0, 0),
        )
        conn.execute(
            "INSERT INTO files (path, name, folder_path, type, ext, size, "
            "size_formatted, mtime, needs_transcode, has_thumb, has_animated, "
            "duration) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                str(path),
                path.name,
                str(folder or path.parent),
                kind,
                ext,
                stat.st_size,
                "1 KB",
                stat.st_mtime,
                0,
                has_thumb,
                has_animated,
                duration,
            ),
        )


def _drain_anim_thread(timeout: float = 10.0) -> None:
    deadline = time.time() + timeout
    while animated._thread is not None and time.time() < deadline:
        time.sleep(0.01)
    assert animated._thread is None, "animated worker never drained"


@pytest.fixture
def thumbs_env(tmp_path, monkeypatch):
    """Library + thumbs dirs, a tiny clip, an indexed drawing and rows."""
    library = tmp_path / "library"
    thumbs = tmp_path / "thumbs"
    (thumbs / "animated").mkdir(parents=True)
    library.mkdir()
    monkeypatch.setenv("LIBRARY_DIR", str(library))
    monkeypatch.setenv("THUMBNAIL_DIR", str(thumbs))
    monkeypatch.setenv("ZIP_CACHE_DIR", str(tmp_path / "zip_cache"))
    monkeypatch.setenv("DB_READ_ONLY", "false")
    monkeypatch.setenv("WRITES", "true")
    v2_config.reload_settings()
    animated.cancel()
    animated._seen.clear()
    animated._state.update(status="idle", active=None, queued=0, completed=0, failed=0)

    video = library / "clip.mp4"
    _make_video(video)
    drawing = library / "drawing.png"
    Image.new("P", (24, 18), 7).save(drawing, "PNG")

    archive = library / "clips.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.write(drawing, "pic.png")

    db_path = Path(os.environ["DB_PATH"])
    _add_row(db_path, video, "video", ".mp4", duration=1.0)
    _add_row(db_path, drawing, "image", ".png")

    try:
        yield {
            "client": TestClient(create_app()),
            "library": library,
            "video": video,
            "drawing": drawing,
            "archive": archive,
            "thumbs": thumbs,
            "db_path": db_path,
        }
    finally:
        animated.cancel()
        _drain_anim_thread()
        animated._state.update(
            status="idle", active=None, queued=0, completed=0, failed=0
        )
        animated._seen.clear()
        v2_config.reload_settings()


def _key(path: Path) -> str:
    stat = path.stat()
    return cache_key(str(path), stat.st_mtime, stat.st_size)


def _insert_file_row(db_path: Path, path: Path, kind: str) -> None:
    """A bare row (no duration) for queue and sweep tests."""
    _add_row(db_path, path, kind, path.suffix)


# --- thumbnails.mark_has_thumb / generate_thumbnail -------------------------


def test_generate_thumbnail_reuses_an_existing_poster(thumbs_env):
    env = thumbs_env
    video = env["video"]
    stat = video.stat()
    target = env["thumbs"] / f"thumb_{_key(video)}.jpg"
    target.write_bytes(b"stale-poster")
    with sqlite3.connect(env["db_path"]) as conn:
        conn.execute("UPDATE files SET has_thumb = 0 WHERE path = ?", (str(video),))

    result = thumbnails.generate_thumbnail(
        str(video), "video", stat.st_mtime, stat.st_size
    )

    assert result == target
    assert target.read_bytes() == b"stale-poster"
    with sqlite3.connect(env["db_path"]) as conn:
        value = conn.execute(
            "SELECT has_thumb FROM files WHERE path = ?", (str(video),)
        ).fetchone()[0]
    assert value == 1


def test_generate_thumbnail_zips(thumbs_env):
    env = thumbs_env
    archive = env["archive"]
    member = f"{archive}/pic.png"
    poster = thumbnails.generate_thumbnail(member, "image", 1.0, 10)
    assert poster.is_file() and poster.stat().st_size > 0

    ghost = f"{env['library'] / 'nope.zip'}/pic.png"
    with pytest.raises(WriteError) as excinfo:
        thumbnails.generate_thumbnail(ghost, "image", 1.0, 10)
    assert excinfo.value.status == 404


def test_generate_thumbnail_reports_a_blank_poster(thumbs_env, monkeypatch):
    env = thumbs_env
    monkeypatch.setattr(
        thumbnails, "_video_poster", lambda source, temp: None, raising=False
    )
    with pytest.raises(WriteError) as excinfo:
        thumbnails.generate_thumbnail(
            str(env["video"]), "video", env["video"].stat().st_mtime, 1
        )
    assert excinfo.value.status == 500


def test_image_poster_converts_exotic_colour_modes(thumbs_env, tmp_path):
    palette = thumbs_env["drawing"]  # a mode-P PNG
    temp = tmp_path / "poster.jpg"
    thumbnails._image_poster(str(palette), temp)
    assert temp.stat().st_size > 0


def test_video_poster_retry_succeeds(tmp_path, monkeypatch):
    temp = tmp_path / "poster.jpg"
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(command)
        if len(calls) == 1:
            return subprocess.CompletedProcess(command, 0)
        Path(command[-1]).write_bytes(b"\xff\xd8stub")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    thumbnails._video_poster(str(tmp_path / "in.mp4"), temp)
    assert len(calls) == 2
    assert temp.is_file()


def test_video_poster_gives_up_after_timeouts(tmp_path, monkeypatch):
    def fake_run(command, **kwargs):
        raise subprocess.TimeoutExpired(command, 20)

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(WriteError) as excinfo:
        thumbnails._video_poster(str(tmp_path / "in.mp4"), tmp_path / "x.jpg")
    assert excinfo.value.status == 500


def test_probe_duration_parses_ffprobe_or_survives_errors(monkeypatch):
    class FakeResult:
        stdout = b"4.5\n"

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeResult())
    assert thumbnails._probe_duration("/any/clip.mp4") == 4.5

    def boom(*args, **kwargs):
        raise OSError("no ffmpeg")

    monkeypatch.setattr(subprocess, "run", boom)
    assert thumbnails._probe_duration("/any/clip.mp4") is None


def test_extract_frame_success_and_failure(tmp_path, monkeypatch):
    target = tmp_path / "frames" / "frame.jpg"

    def fake_run(command, **kwargs):
        Path(command[-1]).write_bytes(b"\xff\xd8frame")
        return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    thumbnails._extract_frame("/in.mp4", 1.5, target, 480)
    assert target.is_file()

    def failing_run(command, capture_output=None, **kwargs):
        return subprocess.CompletedProcess(command, 1, stdout=b"", stderr=b"no")

    monkeypatch.setattr(subprocess, "run", failing_run)
    other = tmp_path / "frames" / "bad.jpg"
    with pytest.raises(WriteError) as excinfo:
        thumbnails._extract_frame("/in.mp4", 2.0, other, 480)
    assert excinfo.value.status == 500
    assert not other.exists()


def test_extract_frame_tolerates_an_unlink_oserror(tmp_path, monkeypatch):
    target = tmp_path / "frames" / "frame.jpg"

    def raising_unlink(self, missing_ok=False):
        raise OSError("simulated failure")

    def fake_run(command, timeout=60, capture_output=None, **kwargs):
        Path(command[-1]).write_bytes(b"\xff\xd8frame")
        return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(Path, "unlink", raising_unlink)
    thumbnails._extract_frame("/in.mp4", 1.0, target, 480)
    monkeypatch.undo()
    assert target.read_bytes() == b"\xff\xd8frame"


def test_video_source_refuses_a_missing_archive(thumbs_env):
    ghost = f"{thumbs_env['library'] / 'nope.zip'}/inside.mp4"
    with pytest.raises(WriteError) as excinfo:
        thumbnails._video_source(ghost)
    assert excinfo.value.status == 404


def test_candidates_count_clamps(probes, monkeypatch):
    env = probes
    counted = thumbnails.candidates(str(env["long_video"]), interval=30.0, count=999)
    assert len(counted["frames"]) == 60

    probed = thumbnails.candidates(str(env["blank_video"]), interval=15.0)
    assert probed["duration"] == 24.0
    assert len(probed["frames"]) >= 8

    monkeypatch.setattr(thumbnails, "_probe_duration", lambda path: 0.0)
    empty = thumbnails.candidates(str(env["blank_video"]), interval=15.0)
    assert empty == {
        "path": str(env["blank_video"]),
        "duration": 0.0,
        "interval": 15.0,
        "frames": [],
    }


@pytest.fixture
def probes(thumbs_env, monkeypatch):
    long_video = thumbs_env["library"] / "long.mp4"
    long_video.write_bytes(b"stub-video")  # only used for its index row
    blank_video = thumbs_env["library"] / "blank.mp4"
    blank_video.write_bytes(b"stub-video")
    db_path = thumbs_env["db_path"]
    with sqlite3.connect(db_path) as conn:
        stub = (
            "INSERT INTO files (path, name, folder_path, type, ext, size, "
            "size_formatted, mtime, needs_transcode, has_thumb, duration) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)"
        )
        conn.execute(
            stub,
            (
                str(long_video),
                "long.mp4",
                str(thumbs_env["library"]),
                "video",
                ".mp4",
                long_video.stat().st_size,
                "10 B",
                long_video.stat().st_mtime,
                0,
                0,
                240.0,
            ),
        )
        conn.execute(
            stub,
            (
                str(blank_video),
                "blank.mp4",
                str(thumbs_env["library"]),
                "video",
                ".mp4",
                blank_video.stat().st_size,
                "10 B",
                blank_video.stat().st_mtime,
                0,
                0,
                None,
            ),
        )
    monkeypatch.setattr(thumbnails, "_probe_duration", lambda path: 24.0)
    return {
        "long_video": long_video,
        "blank_video": blank_video,
        "db_path": db_path,
    }


def test_frame_reuses_a_cached_candidate(thumbs_env):
    env = thumbs_env
    video = env["video"]
    key = _key(video)
    frames = env["thumbs"] / "frames" / key
    frames.mkdir(parents=True)
    cached = frames / "frame_000000002000.jpg"
    cached.write_bytes(b"\xff\xd8cached")

    result = thumbnails.frame(str(video), 2.0)
    assert result == cached


def test_frame_and_set_poster_extract_when_not_cached(thumbs_env, monkeypatch):
    env = thumbs_env
    video = env["video"]

    def fake_extract(source, timestamp, target, width):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"\xff\xd8fresh" + str(width).encode())

    monkeypatch.setattr(thumbnails, "_extract_frame", fake_extract)
    frame = thumbnails.frame(str(video), 3.0)
    key = _key(video)
    assert frame == env["thumbs"] / "frames" / key / "frame_000000003000.jpg"
    assert frame.is_file()

    result = thumbnails.set_poster(str(video), 5.0)
    assert result["path"] == str(video)
    assert result["thumb_mtime"] > 0
    poster = env["thumbs"] / f"thumb_{key}.jpg"
    assert poster.is_file()
    with sqlite3.connect(env["db_path"]) as conn:
        row = conn.execute(
            "SELECT has_thumb, thumb_mtime FROM files WHERE path = ?",
            (str(video),),
        ).fetchone()
    assert row[0] == 1
    assert row[1] == pytest.approx(result["thumb_mtime"])


def test_video_row_rejects_missing_rows(thumbs_env):
    ghost = str(thumbs_env["library"] / "ghost.mp4")
    with pytest.raises(WriteError) as excinfo:
        thumbnails._video_row(ghost)
    assert excinfo.value.status == 404


def test_video_source_extracts_a_zip_member(thumbs_env):
    env = thumbs_env
    member = str(env["archive"]) + "/pic.png"
    extracted = thumbnails._video_source(member)
    assert Path(extracted).is_file()
    assert Path(extracted).read_bytes() == env["drawing"].read_bytes()


def test_mark_has_thumb_refuses_a_stale_key(thumbs_env):
    env = thumbs_env
    video = env["video"]
    stat = video.stat()
    assert thumbnails.mark_has_thumb(str(video), stat.st_mtime, stat.st_size, 1)
    wrong = thumbnails.mark_has_thumb(str(video), stat.st_mtime + 5, stat.st_size, 1)
    assert wrong is False
    assert thumbnails.mark_has_thumb(str(video), 1.0, 1, 1) is False


def test_generate_thumbnail_survives_an_unlink_oserror(thumbs_env, monkeypatch):
    env = thumbs_env
    drawing = env["drawing"]
    stat = drawing.stat()

    def raising_unlink(self, missing_ok=False):
        raise OSError("simulated failure")

    monkeypatch.setattr(Path, "unlink", raising_unlink)
    with sqlite3.connect(env["db_path"]) as conn:
        conn.execute("UPDATE files SET has_thumb = 0 WHERE path = ?", (str(drawing),))
    target = thumbnails.generate_thumbnail(
        str(drawing), "image", stat.st_mtime, stat.st_size
    )
    monkeypatch.undo()
    assert target.is_file()


def test_image_poster_reports_corrupt_files(thumbs_env, tmp_path):
    corrupt = thumbs_env["library"] / "corrupt.jpg"
    corrupt.write_bytes(b"definitely not an image")
    with pytest.raises(WriteError) as excinfo:
        thumbnails._image_poster(str(corrupt), tmp_path / "out.jpg")
    assert excinfo.value.status == 500


# --- animated --------------------------------------------------------------


def test_animated_duration_prefers_the_row_then_ffprobe(thumbs_env, monkeypatch):
    env = thumbs_env
    video = env["video"]
    assert animated._duration(str(video)) == 1.0

    ghost = env["library"] / "ghost.mp4"
    ghost.write_bytes(b"stub")

    class FakeResult:
        stdout = b"7.5\n"

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeResult())
    assert animated._duration(str(ghost)) == 7.5

    def boom(*args, **kwargs):
        raise subprocess.TimeoutExpired("ffprobe", 20)

    monkeypatch.setattr(subprocess, "run", boom)
    assert animated._duration(str(ghost)) is None


def test_generate_rejects_unknown_rows_and_non_videos(thumbs_env):
    env = thumbs_env
    with pytest.raises(WriteError) as missing:
        animated.generate(str(env["library"] / "not-indexed.mp4"))
    assert missing.value.status == 404

    image_path = str(env["drawing"])
    with pytest.raises(WriteError) as wrong:
        animated.generate(image_path)
    assert wrong.value.status == 400


def test_generate_reuses_a_cached_preview(thumbs_env):
    env = thumbs_env
    video = env["video"]
    stat = video.stat()
    target = animated_path(str(video), stat.st_mtime, stat.st_size)
    target.write_bytes(b"RIFFcachedwebp")

    result = animated.generate(str(video))
    assert result == target
    with sqlite3.connect(env["db_path"]) as conn:
        value = conn.execute(
            "SELECT has_animated FROM files WHERE path = ?", (str(video),)
        ).fetchone()[0]
    assert value == 1


def test_generate_refuses_zip_members_and_missing_files(thumbs_env, monkeypatch):
    env = thumbs_env
    zip_member = str(env["archive"]) + "/inside.mp4"
    with sqlite3.connect(env["db_path"]) as conn:
        conn.execute(
            "INSERT INTO files (path, name, folder_path, type, ext, size, "
            "size_formatted, mtime, needs_transcode, has_thumb, duration) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                zip_member,
                "inside.mp4",
                str(env["archive"]),
                "video",
                ".mp4",
                1,
                "1 B",
                1.0,
                0,
                0,
                1.0,
            ),
        )
    with pytest.raises(WriteError) as zipped:
        animated.generate(zip_member)
    assert zipped.value.status == 400

    vanished = env["library"] / "vanished.mp4"
    vanished.write_bytes(b"stub")
    _insert_file_row(env["db_path"], vanished, "video")
    vanished.unlink()
    with pytest.raises(WriteError) as missing_file:
        animated.generate(str(vanished))
    assert missing_file.value.status == 404


def test_generate_bails_on_micro_durations(thumbs_env):
    env = thumbs_env
    stub = env["library"] / "tiny.mp4"
    stub.write_bytes(b"stub")
    _add_row(env["db_path"], stub, "video", ".mp4", duration=0.01)
    with pytest.raises(WriteError) as excinfo:
        animated.generate(str(stub))
    assert excinfo.value.status == 500


def test_generate_probes_duration_then_reports_ffmpeg_errors(
    thumbs_env, monkeypatch, tmp_path
):
    env = thumbs_env
    ghost = env["library"] / "probe.mp4"
    ghost.write_bytes(b"stub")
    with sqlite3.connect(env["db_path"]) as conn:
        conn.execute(
            "INSERT INTO files (path, name, folder_path, type, ext, size, "
            "size_formatted, mtime, needs_transcode, has_thumb, duration) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                str(ghost),
                "probe.mp4",
                str(env["library"]),
                "video",
                ".mp4",
                ghost.stat().st_size,
                "4 B",
                ghost.stat().st_mtime,
                0,
                0,
                None,
            ),
        )

    class ProbeResult:
        stdout = b"8.0\n"

    def fake_run(command, timeout=None, check=None, capture_output=None, **kw):
        if command[0] == "ffprobe":
            return ProbeResult()
        return subprocess.CompletedProcess(
            command, 1, stdout=b"", stderr=b"encode exploded"
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(WriteError) as excinfo:
        animated.generate(str(ghost))
    assert excinfo.value.status == 500
    assert "encode exploded" in str(excinfo.value)


def test_generate_tolerates_an_unlink_oserror(thumbs_env, monkeypatch):
    env = thumbs_env
    video = env["video"]

    def raising_unlink(self, missing_ok=False):
        raise OSError("simulated failure")

    def fake_run(command, timeout=None, check=None, capture_output=None, **kw):
        Path(command[-1]).write_bytes(b"RIFFwebpstub")
        return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(Path, "unlink", raising_unlink)
    generated = animated.generate(str(video))
    monkeypatch.undo()
    assert generated.read_bytes() == b"RIFFwebpstub"


def test_enqueue_dedupes_and_reuses_a_live_thread(thumbs_env, monkeypatch):
    gate = threading.Event()
    monkeypatch.setattr(animated, "generate", lambda path: gate.wait(15))

    assert animated.enqueue(["a.mp4"]) == 1
    deadline = time.time() + 5
    while animated.status()["active"] != "a.mp4" and time.time() < deadline:
        time.sleep(0.01)
    assert animated.status()["active"] == "a.mp4"

    # The duplicate is skipped; the new path still queues onto the live thread.
    assert animated.enqueue(["a.mp4", "b.mp4"]) == 1
    status = animated.status()
    assert status["status"] == "working"
    assert status["queued"] == 1

    gate.set()
    _drain_anim_thread()
    final = animated.status()
    assert final["completed"] == 2
    assert final["active"] is None


def test_enqueue_batch_filters_by_folder(thumbs_env, monkeypatch):
    env = thumbs_env
    monkeypatch.setattr(animated, "generate", lambda path: Path(path))
    other = env["library"] / "other"
    other.mkdir()
    stray = other / "stray.mp4"
    stray.write_bytes(b"stub")
    with sqlite3.connect(env["db_path"]) as conn:
        stub = (
            "INSERT INTO files (path, name, folder_path, type, ext, size, "
            "size_formatted, mtime, needs_transcode, has_thumb) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)"
        )
        conn.execute(
            stub,
            (
                str(stray),
                "stray.mp4",
                str(other),
                "video",
                ".mp4",
                stray.stat().st_size,
                "4 B",
                stray.stat().st_mtime,
                0,
                0,
            ),
        )

    with pytest.raises(WriteError) as empty:
        animated.enqueue_batch(folder=None, all_videos=False)
    assert empty.value.status == 400

    assert animated.enqueue_batch(folder=str(other), limit=50) == 1
    _drain_anim_thread()
    assert animated.status()["active"] is None


def test_queue_marks_persistent_failures(thumbs_env, monkeypatch):
    env = thumbs_env
    broken = env["library"] / "broken.mp4"
    broken.write_bytes(b"stub")
    _insert_file_row(env["db_path"], broken, "video")
    monkeypatch.setattr(animated, "generate", lambda path: 1 / 0)

    assert animated.enqueue([str(broken)]) == 1
    _drain_anim_thread()
    assert animated.status()["failed"] == 1
    with sqlite3.connect(env["db_path"]) as conn:
        value = conn.execute(
            "SELECT has_animated FROM files WHERE path = ?", (str(broken),)
        ).fetchone()[0]
    assert value == -1


def test_queue_survives_a_failing_failure_marker(thumbs_env, monkeypatch):
    def broken_writer(*args, **kwargs):
        raise RuntimeError("index locked")

    monkeypatch.setattr(animated, "generate", lambda path: 1 / 0)
    monkeypatch.setattr(animated, "writer", broken_writer)
    assert animated.enqueue(["loose.mp4"]) == 1
    _drain_anim_thread()
    assert animated.status()["failed"] == 1


def test_stop_cancels_pending_work(thumbs_env, monkeypatch):
    gate = threading.Event()
    monkeypatch.setattr(animated, "generate", lambda path: gate.wait(15))
    animated.stop()  # a no-op history keeps the queue empty
    assert animated.enqueue(["a.mp4", "b.mp4"]) == 2
    deadline = time.time() + 5
    while animated.status()["active"] != "a.mp4" and time.time() < deadline:
        time.sleep(0.01)
    assert animated.status()["queued"] == 1

    animated.stop()  # delegates to cancel() and drops the queued entries
    status = animated.status()
    assert status["queued"] == 0
    assert status["active"] == "a.mp4"  # the running job is untouched
    gate.set()
    _drain_anim_thread()


# --- thumbnail_worker ------------------------------------------------------


def test_sweep_marks_zip_members_failed(thumbs_env, monkeypatch):
    env = thumbs_env
    calls: list[str] = []

    def fake_generate(path, kind, mtime, size):
        calls.append(path)  # a poster would be built here
        thumbnails.mark_has_thumb(path, mtime, size, 1)
        return env["thumbs"] / f"thumb_stub_{Path(path).name}.jpg"

    monkeypatch.setattr(thumbnail_worker, "generate_thumbnail", fake_generate)
    members = [str(env["archive"]) + f"/m{i}.png" for i in range(2)]
    with sqlite3.connect(env["db_path"]) as conn:
        stub = (
            "INSERT INTO files (path, name, folder_path, type, ext, size, "
            "size_formatted, mtime, needs_transcode, has_thumb) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)"
        )
        for member in members:
            conn.execute(
                stub,
                (
                    member,
                    "m.png",
                    str(env["archive"]),
                    "image",
                    ".png",
                    5,
                    "5 B",
                    1.0,
                    0,
                    0,
                ),
            )

    sweep = thumbnail_worker.ThumbnailSweep(batch=10)
    result = sweep.run_once()
    assert result["generated"] == 2
    assert result["failed"] == 2
    assert result["remaining"] == 0
    assert sorted(calls) == sorted([str(env["video"]), str(env["drawing"])])
    with sqlite3.connect(env["db_path"]) as conn:
        values = [
            row[0]
            for row in conn.execute(
                "SELECT has_thumb FROM files WHERE path LIKE ?",
                (str(env["archive"]) + "%",),
            ).fetchall()
        ]
    assert values == [-1, -1]


def test_sweep_failures_mark_the_row(thumbs_env, monkeypatch):
    env = thumbs_env
    monkeypatch.setattr(
        thumbnail_worker,
        "generate_thumbnail",
        lambda *a, **k: 1 / 0,
    )

    sweep = thumbnail_worker.ThumbnailSweep(batch=5)
    result = sweep.run_once()
    assert result["generated"] == 0
    assert result["failed"] == 2
    stat = env["video"].stat()
    with sqlite3.connect(env["db_path"]) as conn:
        values = {
            row[0]: row[1]
            for row in conn.execute(
                "SELECT path, has_thumb FROM files WHERE has_thumb = -1"
            ).fetchall()
        }
    assert str(env["video"]) in values
    assert str(env["drawing"]) in values
    del stat


def test_sweep_start_is_idempotent_and_status_reports_liveness(thumbs_env, monkeypatch):
    monkeypatch.setattr(
        thumbnail_worker.ThumbnailSweep,
        "run_once",
        lambda self: {"remaining": 0},
    )
    sweep = thumbnail_worker.ThumbnailSweep()
    sweep.start()
    sweep.start()  # the second call returns without a second thread
    assert sweep._thread is not None and sweep._thread.is_alive()
    assert thumbnail_worker.status(sweep)["running"] is True
    assert thumbnail_worker.status(None)["running"] is False
    sweep.stop()
    assert sweep._thread is not None and not sweep._thread.is_alive()
    assert thumbnail_worker.status(sweep)["running"] is False


def test_sweep_loop_survives_a_crash_and_stops_during_backoff(thumbs_env, monkeypatch):
    first_crash = threading.Event()
    busy_seen = threading.Event()

    class StubStop:
        """A stop event we control fast-forwarding the long idle waits."""

        def __init__(self) -> None:
            self._flag = False

        def is_set(self) -> bool:
            return self._flag

        def set(self) -> None:
            self._flag = True

        def clear(self) -> None:
            self._flag = False

        def wait(self, timeout: float) -> bool:
            if self._flag:
                return True
            if timeout > 1:
                return False  # skip the idle backoff window
            busy_seen.set()
            deadline = time.time() + timeout
            while not self._flag and time.time() < deadline:
                time.sleep(0.005)
            return self._flag

    def fake_run_once():
        if not first_crash.is_set():
            first_crash.set()
            raise RuntimeError("boom")
        return {"remaining": 1}

    sweep = thumbnail_worker.ThumbnailSweep()
    sweep._stop = StubStop()
    monkeypatch.setattr(sweep, "run_once", fake_run_once)
    sweep.start()
    assert first_crash.wait(5)
    assert busy_seen.wait(5)
    sweep._stop.set()  # while the sweep is waiting: break out promptly
    sweep._thread.join(timeout=5)
    assert not sweep._thread.is_alive()
    assert thumbnail_worker.status(sweep)["running"] is False


# --- transcode_stream ------------------------------------------------------


def test_build_command_adds_a_seek():
    from media_library.transcode_stream import build_command

    command = build_command("/tmp/in.mkv", 3.5)
    assert command[command.index("-ss") + 1] == "3.5"
    assert "pipe:1" in command
    no_seek = build_command("/tmp/in.mkv", 0.0)
    assert "-ss" not in no_seek


def test_relay_throws_away_a_vanished_process():
    from media_library.transcode_stream import relay

    class GoneProc:
        def __init__(self, reader):
            self.stdout = reader
            self.raise_on_terminate = True

        def terminate(self):
            if self.raise_on_terminate:
                raise ProcessLookupError("already gone")

        def kill(self):  # pragma: no cover
            raise ProcessLookupError("already gone")

        async def wait(self):  # pragma: no cover
            return -15

    async def scenario():
        reader = asyncio.StreamReader()
        reader.feed_data(b"tail")
        reader.feed_eof()
        proc = GoneProc(reader)
        chunks = [chunk async for chunk in relay(proc)]
        assert chunks

    asyncio.run(scenario())


def test_relay_yields_stdout_and_terminates_normally():
    import media_library.transcode_stream as stream

    class FakeProc:
        def __init__(self, reader):
            self.stdout = reader
            self.terminated = False
            self.killed = False
            self._done = asyncio.Event()

        def terminate(self):
            self.terminated = True
            self._done.set()

        def kill(self):  # pragma: no cover - not on this path
            self.killed = True
            self._done.set()

        async def wait(self):
            await self._done.wait()
            return 0 if self.terminated else -9

    async def scenario():
        reader = asyncio.StreamReader()
        reader.feed_data(b"part1")
        reader.feed_data(b"part2")
        reader.feed_eof()
        proc = FakeProc(reader)
        chunks = [chunk async for chunk in stream.relay(proc)]
        assert b"".join(chunks) == b"part1part2"
        assert proc.terminated
        assert not proc.killed

    asyncio.run(scenario())


def test_relay_kills_a_hung_process(monkeypatch):
    import media_library.transcode_stream as stream

    async def impatient_wait_for(fut, timeout):
        fut.close()  # never started; prevents a "never awaited" warning
        raise asyncio.TimeoutError("hung process")

    monkeypatch.setattr(asyncio, "wait_for", impatient_wait_for)

    class HungProc:
        def __init__(self, reader):
            self.stdout = reader
            self.killed = False
            self._dead = asyncio.Event()

        def terminate(self):
            pass  # a hung ffmpeg ignores a polite SIGTERM

        def kill(self):
            self.killed = True
            self._dead.set()

        async def wait(self):
            await self._dead.wait()
            return -9

    async def scenario():
        reader = asyncio.StreamReader()
        reader.feed_data(b"partial output")
        reader.feed_eof()
        proc = HungProc(reader)
        chunks = [chunk async for chunk in stream.relay(proc)]
        assert chunks == [b"partial output"]
        assert proc.killed

    asyncio.run(scenario())


# --- routers/media.py ------------------------------------------------------


def test_row_stat_missing_row_is_404_and_exists_answers_false(thumbs_env):
    env = thumbs_env
    client = env["client"]
    missing_in_index = env["library"] / "not-indexed.png"
    missing_in_index.write_bytes(b"x")
    assert (
        client.get(
            "/api/thumbnails", params={"path": str(missing_in_index)}
        ).status_code
        == 404
    )
    body = client.get(
        "/api/thumbnails/exists", params={"path": str(missing_in_index)}
    ).json()
    assert body == {"static": False, "animated": False}


def test_media_range_parsing_rejects_malformed_ranges(thumbs_env):
    env = thumbs_env
    client = env["client"]
    params = {"path": str(env["drawing"])}
    bad = client.get("/api/media", params=params, headers={"Range": "bytes=abc"})
    assert bad.status_code == 416
    empty = client.get("/api/media", params=params, headers={"Range": "bytes=-"})
    assert empty.status_code == 416
    zero = client.get("/api/media", params=params, headers={"Range": "bytes=-0"})
    assert zero.status_code == 416


def test_transcode_streams_a_zip_member(thumbs_env):
    env = thumbs_env
    client = env["client"]
    archive = env["library"] / "video.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.write(env["video"], "inside.mp4")
    member = f"{archive}/inside.mp4"
    response = client.get("/api/media/transcode", params={"path": member})
    assert response.status_code == 200, response.text
    assert b"ftyp" in response.content[:64]


def test_transcode_zip_member_missing_archive(thumbs_env):
    env = thumbs_env
    ghost = f"{env['library'] / 'ghost.zip'}/inside.mp4"
    response = env["client"].get(
        "/api/media/transcode", params={"path": ghost, "ss": 1.0}
    )
    assert response.status_code == 404


def test_media_supports_etag_304_and_head_ranges(thumbs_env):
    env = thumbs_env
    client = env["client"]
    params = {"path": str(env["video"])}
    first = client.get("/api/media", params=params)
    assert first.status_code == 200
    etag = first.headers["etag"]

    cached = client.get("/api/media", params=params, headers={"If-None-Match": etag})
    assert cached.status_code == 304
    assert cached.content == b""

    head = client.head("/api/media", params=params, headers={"Range": "bytes=0-15"})
    assert head.status_code == 206
    assert head.headers["content-range"].startswith("bytes 0-15/")
    assert head.content == b""


def test_stat_failure_is_a_404(thumbs_env, monkeypatch):
    from media_library.routers import media as media_module

    def raising_stat(path):
        raise OSError("simulated stat failure")

    fake_os = types.SimpleNamespace(stat=raising_stat, path=os.path)
    monkeypatch.setattr(media_module, "os", fake_os)
    response = thumbs_env["client"].get(
        "/api/media", params={"path": str(thumbs_env["video"])}
    )
    assert response.status_code == 404
    assert response.json() == {"detail": "File not found"}


def test_thumbnails_endpoint_edges(thumbs_env):
    env = thumbs_env
    client = env["client"]
    assert client.get("/api/thumbnails", params={"path": ""}).status_code == 400

    video = env["video"]
    key = _key(video)
    animated_file = env["thumbs"] / "animated" / f"anim_{key}.webp"
    animated_file.write_bytes(b"RIFFwebpstub")
    response = client.get(
        "/api/thumbnails", params={"path": str(video), "kind": "animated"}
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/webp"
    animated_file.unlink()

    still_missing = client.get(
        "/api/thumbnails", params={"path": str(video), "kind": "animated"}
    )
    assert still_missing.status_code == 404


def test_frame_errors_are_translated(thumbs_env):
    response = thumbs_env["client"].get(
        "/api/thumbnails/frame",
        params={"path": str(thumbs_env["drawing"]), "timestamp": 1.0},
    )
    assert response.status_code == 400


def test_poster_errors_are_translated(thumbs_env):
    response = thumbs_env["client"].post(
        "/api/thumbnails/poster",
        json={"path": str(thumbs_env["drawing"]), "timestamp": 1.0},
    )
    assert response.status_code == 400


def test_animated_enqueue_errors_are_translated(thumbs_env, monkeypatch):
    def refusing_enqueue(paths):
        raise WriteError("Not indexed", 404)

    monkeypatch.setattr(animated, "enqueue", refusing_enqueue)
    response = thumbs_env["client"].post(
        "/api/thumbnails/animated",
        json={"path": str(thumbs_env["library"] / "unknown.mp4")},
    )
    assert response.status_code == 404


def test_thumbnails_exists_reports_cached_artwork(thumbs_env):
    env = thumbs_env
    client = env["client"]
    assert client.get("/api/thumbnails/exists", params={"path": ""}).status_code == 400

    video = env["video"]
    key = _key(video)
    (env["thumbs"] / f"thumb_{key}.jpg").write_bytes(b"poster")
    (env["thumbs"] / "animated" / f"anim_{key}.webp").write_bytes(b"webp")
    body = client.get("/api/thumbnails/exists", params={"path": str(video)}).json()
    assert body == {"static": True, "animated": True}

    (env["thumbs"] / f"thumb_{key}.jpg").unlink()
    (env["thumbs"] / "animated" / f"anim_{key}.webp").unlink()
    body = client.get("/api/thumbnails/exists", params={"path": str(video)}).json()
    assert body == {"static": False, "animated": False}


def test_suffix_range_and_never_satisfiable_requests(thumbs_env):
    env = thumbs_env
    client = env["client"]
    params = {"path": str(env["drawing"])}
    stat = env["drawing"].stat()
    body = client.get(
        "/api/media", params=params, headers={"Range": f"bytes=-{min(5, stat.st_size)}"}
    )
    assert body.status_code == 206
    assert len(body.content) == 5
    beyond = client.get(
        "/api/media", params=params, headers={"Range": "bytes=99999999-"}
    )
    assert beyond.status_code == 416

    ranged = client.get("/api/media", params=params, headers={"Range": "bytes=0-3"})
    assert ranged.status_code == 206
    assert ranged.headers["content-range"].startswith("bytes 0-3/")

    plain_head = client.head("/api/media", params=params)
    assert plain_head.status_code == 200
    assert plain_head.content == b""


def test_media_without_a_body_or_disposition(thumbs_env):
    env = thumbs_env
    client = env["client"]
    downloaded = client.get(
        "/api/media", params={"path": str(env["drawing"]), "download": "true"}
    )
    assert downloaded.status_code == 200
    assert downloaded.headers["content-disposition"].startswith("attachment")
    assert 'filename="drawing.png"' in downloaded.headers["content-disposition"]
    assert "filename*=UTF-8''drawing.png" in downloaded.headers["content-disposition"]


def test_media_stream_through_a_zip_member(thumbs_env):
    env = thumbs_env
    client = env["client"]
    member = str(env["archive"]) + "/pic.png"
    response = client.get("/api/media", params={"path": member})
    assert response.status_code == 200
    assert response.content == env["drawing"].read_bytes()


def test_media_streams_a_ghost_member_404(thumbs_env):
    ghost_member = str(thumbs_env["library"] / "ghost.zip") + "/pic.png"
    response = thumbs_env["client"].get("/api/media", params={"path": ghost_member})
    assert response.status_code == 404


def test_transcode_of_a_loose_library_file(thumbs_env):
    response = thumbs_env["client"].get(
        "/api/media/transcode",
        params={"path": str(thumbs_env["video"]), "ss": 0.25},
    )
    assert response.status_code == 200
    assert b"ftyp" in response.content[:64]


def test_served_static_thumbnail_is_reused(thumbs_env):
    env = thumbs_env
    key = _key(env["video"])
    poster = env["thumbs"] / f"thumb_{key}.jpg"
    poster.write_bytes(b"\xff\xd8cached")
    response = env["client"].get("/api/thumbnails", params={"path": str(env["video"])})
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    poster.unlink()

    # Missing videos/images regenerate the poster on demand (real ffmpeg).
    response = env["client"].get("/api/thumbnails", params={"path": str(env["video"])})
    assert response.status_code == 200
    assert response.content[:2] == b"\xff\xd8"


def test_candidates_endpoint_lists_frames(thumbs_env):
    response = thumbs_env["client"].get(
        "/api/thumbnails/candidates",
        params={"path": str(thumbs_env["video"]), "count": 5},
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body["frames"]) == 5
    assert body["frames"][0]["timestamp"] == 0


def test_frame_endpoint_serves_a_real_jpeg(thumbs_env):
    response = thumbs_env["client"].get(
        "/api/thumbnails/frame",
        params={"path": str(thumbs_env["video"]), "timestamp": 0.5},
    )
    assert response.status_code == 200
    assert response.content[:2] == b"\xff\xd8"


def test_read_only_writes_are_refused(thumbs_env, monkeypatch):
    monkeypatch.setenv("DB_READ_ONLY", "true")
    v2_config.reload_settings()
    client = TestClient(create_app())
    json_poster = {
        "path": str(thumbs_env["video"]),
        "timestamp": 1.0,
    }
    assert client.post("/api/thumbnails/poster", json=json_poster).status_code == 503
    assert (
        client.post(
            "/api/thumbnails/animated", json={"path": json_poster["path"]}
        ).status_code
        == 503
    )
    assert (
        client.post(
            "/api/thumbnails/animated/batch", json={"all_videos": True}
        ).status_code
        == 503
    )


def test_animated_endpoints_queue_and_cancel(thumbs_env, monkeypatch):
    env = thumbs_env
    client = env["client"]
    queued: list[list[str]] = []

    def fake_enqueue(paths):
        queued.append(paths)
        return len(paths)

    monkeypatch.setattr(animated, "enqueue", fake_enqueue)
    response = client.post("/api/thumbnails/animated", json={"path": str(env["video"])})
    assert response.status_code == 200
    body = response.json()
    assert body["added"] == 1
    assert queued == [[str(env["video"])]]

    def fake_batch(folder=None, all_videos=False, limit=500):
        return 7

    monkeypatch.setattr(animated, "enqueue_batch", fake_batch)
    response = client.post(
        "/api/thumbnails/animated/batch", json={"all_videos": True, "limit": 20}
    )
    assert response.status_code == 200
    assert response.json()["added"] == 7

    response = client.get("/api/thumbnails/animated/status")
    assert response.status_code == 200
    assert "queued" in response.json()

    response = client.post("/api/thumbnails/animated/cancel")
    assert response.status_code == 200
    assert response.json()["queued"] == 0
