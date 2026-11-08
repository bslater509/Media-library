"""Line-coverage tests for media_library.dedupe and its router.

Split from the functional suite so every defensive branch (hash guards,
cap/ignore skips, queue bookkeeping) is exercised directly, with sampled
frame work faked and no real FFmpeg encodes.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from media_library import dedupe, writes
from media_library.app import create_app


def _insert_row(
    conn,
    path,
    kind="video",
    size=None,
    quick=None,
    thumb_hash=None,
    duration=None,
    has_thumb=1,
    folder=None,
) -> str:
    """Insert one files row shaped the way the dedupe scanners read it."""
    if size is None:
        size = path.stat().st_size if path.is_file() else 0
    parent = folder or str(path.parent)
    conn.execute(
        "INSERT INTO files (path, name, folder_path, type, ext, size, "
        "size_formatted, mtime, needs_transcode, has_thumb, width, height, "
        "duration, thumb_hash, quick_hash) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            str(path),
            path.name,
            parent,
            kind,
            ".mp4" if kind == "video" else ".jpg",
            size,
            f"{size} B",
            1000.0,
            0,
            has_thumb,
            640,
            480,
            duration,
            thumb_hash,
            quick,
        ),
    )
    return str(path)


def _real_file(conn, path, **kwargs) -> str:
    """Write a tiny on-disk file and insert a matching row keyed to it."""
    path.write_bytes(b"payload")
    return _insert_row(conn, path, **kwargs)


def _checkerboard(path) -> None:
    from PIL import Image

    image = Image.new("RGB", (96, 96), (20, 20, 20))
    for x in range(0, 96, 12):
        for y in range(0, 96, 12):
            if (x // 12 + y // 12) % 2 == 0:
                for dx in range(12):
                    for dy in range(12):
                        image.putpixel((x + dx, y + dy), (230, 210, 190))
    image.save(path, "JPEG")


def _solid(path) -> None:
    from PIL import Image

    Image.new("RGB", (96, 96), (128, 128, 128)).save(path, "JPEG")


@pytest.fixture
def library_env(tmp_path, monkeypatch):
    """Library root, thumbnail cache and a fresh schema-complete index."""
    library = tmp_path / "library"
    (library / "A").mkdir(parents=True)
    thumbs = tmp_path / "thumbs"
    thumbs.mkdir()
    monkeypatch.setenv("LIBRARY_DIR", str(library))
    monkeypatch.setenv("THUMBNAIL_DIR", str(thumbs))
    monkeypatch.setenv("DB_READ_ONLY", "false")
    monkeypatch.setenv("WRITES", "true")
    from media_library import config as v2_config

    v2_config.reload_settings()

    db_path = Path(os.environ["DB_PATH"])
    with sqlite3.connect(db_path) as conn:
        for folder in (library, library / "A"):
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
    return {"library": library, "db_path": db_path, "thumbs": thumbs}


_DEFAULT_STATE = {
    "status": "idle",
    "total_groups": 0,
    "completed_groups": 0,
    "queued_groups": 0,
    "current": [],
    "stage": "",
    "active_files": 0,
    "active_index": 0,
    "active_file": "",
    "active_frame": 0,
    "active_frames": 0,
    "confirmed": 0,
    "rejected": 0,
    "unknown": 0,
    "errors": 0,
    "started_at": None,
    "finished_at": None,
}


@pytest.fixture(autouse=True)
def _calm_dedupe_queues():
    """Leave no queued groups, live threads or merge cache behind a test."""
    yield
    with dedupe._verify_lock:
        dedupe._verify_queue.clear()
        dedupe._verify_state.update(_DEFAULT_STATE, current=[])
    dedupe._verify_thread = None
    dedupe.invalidate_merged_cache()


def _wait_until(predicate, timeout: float = 5.0):
    deadline = time.time() + timeout
    while not predicate() and time.time() < deadline:
        time.sleep(0.01)
    return predicate()


def test_quick_hash_formats(library_env, tmp_path):
    library = library_env["library"]
    keeper = library / "A" / "keeper.bin"
    keeper.write_bytes(b"payload-" * 900)
    empty = library / "A" / "empty.bin"
    empty.write_bytes(b"")
    first = dedupe.quick_hash(str(keeper))
    assert first is not None
    assert len(first) == 64
    assert dedupe.quick_hash(str(keeper)) == first
    assert dedupe.quick_hash(str(keeper)) != dedupe.quick_hash(str(empty))
    assert dedupe.quick_hash(str(empty)) == "empty_file"
    assert dedupe.quick_hash(str(tmp_path / "gone.bin")) is None


def test_files_identical_streams_and_fails_safe(library_env, tmp_path):
    library = library_env["library"]
    same = b"equal-bytes-" * 100_000
    left = library / "A" / "left.bin"
    right = library / "A" / "right.bin"
    other = library / "A" / "other.bin"
    tail = library / "A" / "tail.bin"
    left.write_bytes(same)
    right.write_bytes(same)
    other.write_bytes(same[:-1])
    tail.write_bytes(same[:-1] + b"X")
    assert dedupe.files_identical(str(left), str(right)) is True
    assert dedupe.files_identical(str(left), str(other)) is False
    assert dedupe.files_identical(str(left), str(tail)) is False
    assert dedupe.files_identical(str(left), str(tmp_path / "gone.bin")) is False
    assert dedupe.files_identical(str(tmp_path / "gone.bin"), str(left)) is False


def test_thumbnail_phash_kinds(library_env):
    thumbs = library_env["thumbs"]
    rich = thumbs / "rich.jpg"
    _checkerboard(rich)
    _solid(thumbs / "blank.jpg")
    corrupt = thumbs / "corrupt.jpg"
    corrupt.write_bytes(b"not-an-image")
    value = dedupe.thumbnail_phash(str(rich))
    assert re.fullmatch(r"[0-9a-f]{16}", value)
    assert dedupe.thumbnail_phash(str(thumbs / "blank.jpg")) == "blank_thumb"
    assert dedupe.thumbnail_phash(str(corrupt)) == "no_thumb"
    assert dedupe.thumbnail_phash(str(thumbs / "missing.jpg")) == "no_thumb"


def test_parse_hashes_shapes():
    good = ["aaaaaaaaaaaaaaaa", None, "bbbbbbbbbbbbbbbb", None, "cccccccccccccccc"]
    assert dedupe._parse_hashes(json.dumps(good)) == good
    assert dedupe._parse_hashes(json.dumps([1, 2, 3, 4, 5])) == [None] * 5
    assert dedupe._parse_hashes(None) is None
    assert dedupe._parse_hashes("{not json") is None
    assert dedupe._parse_hashes(json.dumps("not-a-list")) is None
    assert dedupe._parse_hashes(json.dumps(["aaaaaaaaaaaaaaaa"] * 4)) is None


def test_frame_phash_paths(library_env, monkeypatch):
    from media_library import thumbnails

    thumbs = library_env["thumbs"]
    good = thumbs / "frame-good.jpg"
    _checkerboard(good)
    corrupt = thumbs / "frame-bad.jpg"
    corrupt.write_bytes(b"garbage")

    def boom(*_args):
        raise RuntimeError("no frame")

    monkeypatch.setattr(thumbnails, "frame", lambda path, timestamp: good)
    value = dedupe._frame_phash("ignored", 1.0)
    assert re.fullmatch(r"[0-9a-f]{16}", value)

    monkeypatch.setattr(thumbnails, "frame", lambda path, timestamp: corrupt)
    assert dedupe._frame_phash("ignored", 3.0) is None

    monkeypatch.setattr(thumbnails, "frame", boom)
    assert dedupe._frame_phash("ignored", 4.0) is None


def test_signature_for_rejects_missing_and_non_video(library_env):
    library = library_env["library"]
    image = library / "A" / "photo.jpg"
    image.write_bytes(b"image")
    with sqlite3.connect(library_env["db_path"]) as conn:
        _insert_row(conn, image, kind="image")
    assert dedupe._signature_for(str(image)) is None
    assert dedupe._signature_for(str(library / "A" / "missing.mp4")) is None


def test_signature_for_uses_the_cached_frames(library_env):
    video = library_env["library"] / "A" / "vid-cached.mp4"
    video.write_bytes(b"video")
    size = video.stat().st_size
    with sqlite3.connect(library_env["db_path"]) as conn:
        _insert_row(conn, video, duration=150.0)
        conn.execute(
            "INSERT INTO visual_frames "
            "(path, mtime, size, duration, hashes, captured_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                str(video),
                999.0,
                4242,
                150.0,
                json.dumps(["aaaaaaaaaaaaaaaa"] * 5),
                1.0,
            ),
        )
    cached = dedupe._signature_for(str(video))
    assert cached is not None
    assert cached["duration"] == 150.0
    assert cached["hashes"] == ["aaaaaaaaaaaaaaaa"] * 5
    # The cached signature wins even when the stored stats differ.
    assert cached["mtime"] == 1000.0
    assert cached["size"] == size


def test_signature_for_computes_and_persists(library_env, monkeypatch):
    video = library_env["library"] / "A" / "vid-computed.mp4"
    video.write_bytes(b"video")
    with sqlite3.connect(library_env["db_path"]) as conn:
        _insert_row(conn, video, duration=100.0)
    marks: list[tuple[int, int]] = []
    monkeypatch.setattr(
        dedupe, "_frame_phash", lambda _path, _timestamp: "1234567890abcdef"
    )
    signature = dedupe._signature_for(
        str(video), on_frame=lambda done, frames: marks.append((done, frames))
    )
    assert signature is not None
    assert signature["duration"] == 100.0
    assert signature["hashes"] == ["1234567890abcdef"] * 5
    assert marks == [(1, 5), (2, 5), (3, 5), (4, 5), (5, 5)]
    with sqlite3.connect(library_env["db_path"]) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT hashes, duration FROM visual_frames WHERE path = ?",
            (str(video),),
        ).fetchone()
    assert json.loads(row["hashes"]) == ["1234567890abcdef"] * 5
    assert row["duration"] == 100.0


def test_signature_for_duration_fallback(library_env, monkeypatch):
    from media_library import thumbnails

    video = library_env["library"] / "A" / "vid-nodur.mp4"
    video.write_bytes(b"video")
    with sqlite3.connect(library_env["db_path"]) as conn:
        _insert_row(conn, video, duration=0.0)
    monkeypatch.setattr(
        dedupe, "_frame_phash", lambda _path, _timestamp: "1234567890abcdef"
    )
    monkeypatch.setattr(
        thumbnails, "candidates", lambda path, count: {"duration": 42.0}
    )
    signature = dedupe._signature_for(str(video))
    assert signature is not None
    assert signature["duration"] == 42.0

    # A missing cache row plus a failing candidates probe lands on duration <= 0.
    with sqlite3.connect(library_env["db_path"]) as conn:
        conn.execute("DELETE FROM visual_frames WHERE path = ?", (str(video),))

    def boom(*_args, **_kwargs):
        raise RuntimeError("no probe")

    monkeypatch.setattr(thumbnails, "candidates", boom)
    assert dedupe._signature_for(str(video)) is None

    monkeypatch.setattr(thumbnails, "candidates", lambda path, count: {"duration": 0})
    assert dedupe._signature_for(str(video)) is None


def test_cached_signatures_filters(library_env):
    library = library_env["library"]
    good = library / "A" / "vid-good.mp4"
    changed_mtime = library / "A" / "vid-mtime.mp4"
    changed_size = library / "A" / "vid-size.mp4"
    bad_hashes = library / "A" / "vid-bad.mp4"
    removed = library / "A" / "vid-removed.mp4"
    signature = json.dumps(["aaaaaaaaaaaaaaaa"] * 5)
    with sqlite3.connect(library_env["db_path"]) as conn:
        for row in (good, changed_mtime, changed_size, bad_hashes):
            _real_file(conn, row, duration=100.0)
        conn.executemany(
            "INSERT INTO visual_frames "
            "(path, mtime, size, duration, hashes, captured_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [
                (str(good), 1000.0, 7, 100.0, signature, 1.0),
                (str(changed_mtime), 9999.0, 7, 100.0, signature, 1.0),
                (str(changed_size), 1000.0, 4242, 100.0, signature, 1.0),
                (str(bad_hashes), 1000.0, 7, 100.0, "[]", 1.0),
                (str(removed), 1000.0, 7, 100.0, signature, 1.0),
            ],
        )
    paths = [
        str(good),
        str(changed_mtime),
        str(changed_size),
        str(bad_hashes),
        str(removed),
    ]
    result = dedupe._cached_signatures(paths)
    assert list(result) == [str(good), str(changed_mtime), str(changed_size)]
    assert result[str(good)]["duration"] == 100.0
    assert dedupe._cached_signatures([]) == {}


def test_compare_signatures_variants():
    zeros = {"hashes": ["0000000000000000"] * 5}
    almost = {"hashes": ["0000000000000001"] * 5}
    far = {"hashes": ["ffffffffffffffff"] * 5}
    half_left = {"hashes": ["0000000000000000", None, None, None, None]}
    half_right = {"hashes": ["0000000000000003", None, None, None, None]}

    empty = dedupe._compare_signatures({"hashes": [None] * 5}, {"hashes": [None] * 5})
    assert empty == {
        "compared": 0,
        "matches": 0,
        "distances": [],
        "confirmed": None,
    }

    match = dedupe._compare_signatures(zeros, almost)
    assert match["compared"] == 5
    assert match["matches"] == 5
    assert match["confirmed"] is True

    uncertain = dedupe._compare_signatures(half_left, half_right)
    assert uncertain["compared"] == 1
    assert uncertain["confirmed"] is None

    rejected = dedupe._compare_signatures(zeros, far)
    assert rejected["compared"] == 5
    assert rejected["confirmed"] is False


def test_verification_for_variants():
    zeros = {"hashes": ["0000000000000000"] * 5}
    almost = {"hashes": ["0000000000000001"] * 5}
    far = {"hashes": ["ffffffffffffffff"] * 5}
    half_left = {"hashes": ["0000000000000000", None, None, None, None]}
    half_right = {"hashes": ["0000000000000003", None, None, None, None]}

    assert dedupe._verification_for(["only-one"], {"only-one": zeros}) is None
    assert dedupe._verification_for(["x", "y"], {}) is None

    pair = dedupe._verification_for(
        ["x", "y"], {"x": zeros, "y": almost}, include_pairs=True
    )
    assert pair is not None
    assert pair["available"] is True
    assert pair["confirmed"] is True
    assert pair["checked_pairs"] == 1
    assert pair["frames"] == 5
    assert pair["pairs"][0]["path"] == "y"
    assert pair["pairs"][0]["against"] == "x"
    assert pair["pairs"][0]["distances"] == [1] * 5

    uncertain = dedupe._verification_for(["x", "y"], {"x": half_left, "y": half_right})
    assert uncertain is not None
    assert uncertain["confirmed"] is None

    mixed = dedupe._verification_for(
        ["x", "y", "z"], {"x": zeros, "y": far, "z": half_right}
    )
    assert mixed is not None
    assert mixed["confirmed"] is False
    assert mixed["pairs"] == []


def test_verify_visual_group_needs_two_files(library_env):
    library = library_env["library"]
    solo = str(library / "A" / "solo.mp4")
    with pytest.raises(writes.WriteError) as single:
        dedupe.verify_visual_group([solo])
    assert single.value.status == 400
    with pytest.raises(writes.WriteError) as doubled:
        dedupe.verify_visual_group([solo, solo])
    assert doubled.value.status == 400


def test_verify_visual_group_without_video_rows(library_env):
    library = library_env["library"]
    one = str(library / "A" / "anon-a.jpg")
    two = str(library / "A" / "anon-b.jpg")
    result = dedupe.verify_visual_group([one, two])
    assert result == {
        "available": False,
        "confirmed": None,
        "checked_pairs": 0,
        "confirmed_pairs": 0,
        "frames": 5,
        "pairs": [],
    }


def test_verify_visual_group_samples_and_reuses(library_env, monkeypatch):
    library = library_env["library"]
    a = library / "A" / "vid-a.mp4"
    b = library / "A" / "vid-b.mp4"
    with sqlite3.connect(library_env["db_path"]) as conn:
        _real_file(conn, a, duration=100.0, has_thumb=0)
        _real_file(conn, b, duration=100.0, has_thumb=0)
    monkeypatch.setattr(
        dedupe,
        "_frame_phash",
        lambda path, _timestamp: (
            "0000000000000003" if path == str(b) else "0000000000000000"
        ),
    )

    updates: list[dict] = []
    result = dedupe.verify_visual_group([str(a), str(b)], progress=updates.append)
    assert result["available"] is True
    assert result["confirmed"] is True
    assert result["checked_pairs"] == 1
    assert result["pairs"][0]["distances"] == [2] * 5
    assert updates[-1]["stage"] == "comparing"
    assert updates[0]["active_files"] == 2

    # The stored signatures serve the second call; nothing is re-sampled.
    sampled: list[str] = []

    def counting(_path: str, _timestamp: float) -> str:
        sampled.append(_path)
        return "0000000000000000"

    monkeypatch.setattr(dedupe, "_frame_phash", counting)
    again = dedupe.verify_visual_group([str(a), str(b)])
    assert again["confirmed"] is True
    assert sampled == []


def test_visual_check_status_snapshot():
    state = dedupe.visual_check_status()
    assert state["status"] == "idle"
    assert state["running"] is False
    assert state["queued_groups"] == 0
    assert state["current"] == []


def test_ensure_verify_thread_reuses_a_live_worker(library_env, monkeypatch):
    library = library_env["library"]
    a = str(library / "A" / "live-a.mp4")
    b = str(library / "A" / "live-b.mp4")
    gate = threading.Event()

    def slow(paths, progress=None):
        gate.wait(5)
        return {
            "available": True,
            "confirmed": True,
            "checked_pairs": 1,
            "confirmed_pairs": 1,
            "frames": 5,
            "pairs": [],
        }

    monkeypatch.setattr(dedupe, "verify_visual_group", slow)
    dedupe.start_visual_checks([[a, b]])
    assert _wait_until(lambda: dedupe.visual_check_status()["running"])
    dedupe._ensure_verify_thread()
    gate.set()
    assert _wait_until(lambda: dedupe.visual_check_status()["status"] == "done")
    assert dedupe.visual_check_status()["confirmed"] == 1


def test_start_visual_checks_rejects_empty_input():
    with pytest.raises(writes.WriteError) as empty:
        dedupe.start_visual_checks([[], ["", None], ["only-one"], ["one", "one"]])
    assert empty.value.status == 400
    assert dedupe.visual_check_status()["queued_groups"] == 0


def test_start_visual_checks_dedupes_the_running_group(library_env, monkeypatch):
    library = library_env["library"]
    a = str(library / "A" / "run-a.mp4")
    b = str(library / "A" / "run-b.mp4")
    gate = threading.Event()

    def slow(paths, progress=None):
        gate.wait(5)
        return {
            "available": True,
            "confirmed": True,
            "checked_pairs": 1,
            "confirmed_pairs": 1,
            "frames": 5,
            "pairs": [],
        }

    monkeypatch.setattr(dedupe, "verify_visual_group", slow)
    dedupe.start_visual_checks([[a, b]])
    assert _wait_until(lambda: dedupe.visual_check_status()["current"])
    repeat = dedupe.start_visual_checks([[a, b], [b, a]])
    assert repeat["total_groups"] == 1
    assert repeat["queued_groups"] == 0
    gate.set()
    assert _wait_until(lambda: dedupe.visual_check_status()["status"] == "done")
    assert dedupe.visual_check_status()["completed_groups"] == 1


def test_start_visual_checks_requeues_from_done_state(library_env):
    library = library_env["library"]
    a = str(library / "A" / "requeue-a.mp4")
    b = str(library / "A" / "requeue-b.mp4")
    stop = threading.Event()
    dummy = threading.Thread(target=lambda: stop.wait(5), daemon=True)
    with dedupe._verify_lock:
        dedupe._verify_state["current"] = []
        dedupe._verify_state["status"] = "done"
    dedupe._verify_thread = dummy
    dummy.start()
    result = dedupe.start_visual_checks([[a, b]])
    assert result["status"] == "queued"
    assert result["total_groups"] == 1
    assert result["queued_groups"] == 1
    stop.set()
    dummy.join(5)
    dedupe._verify_thread = None


def test_verify_loop_counts_every_outcome(library_env, monkeypatch):
    library = library_env["library"]
    ok = str(library / "A" / "run1-copy-keep.mp4")
    bad = str(library / "A" / "run1-copy2.mp4")
    unsure = str(library / "A" / "run3-copy.mp4")
    boom = str(library / "A" / "run4-copy.mp4")

    def flaky(paths: list[str], progress=None):
        marker = Path(paths[0]).stem
        if "run4" in marker:
            raise RuntimeError("sample error")
        if progress is not None:
            progress({"stage": "sampling", "active_files": len(paths)})
        if "run1-copy" in marker:
            verdict = "-keep" in marker
            return {
                "available": True,
                "confirmed": verdict,
                "checked_pairs": 1,
                "confirmed_pairs": 1 if verdict else 0,
                "frames": 5,
                "pairs": [],
            }
        return {
            "available": True,
            "confirmed": None,
            "checked_pairs": 1,
            "confirmed_pairs": 0,
            "frames": 5,
            "pairs": [],
        }

    monkeypatch.setattr(dedupe, "verify_visual_group", flaky)
    dedupe.start_visual_checks([[ok, "x"], [bad, "x"], [unsure, "x"], [boom, "x"]])
    assert _wait_until(lambda: dedupe.visual_check_status()["completed_groups"] == 4)
    state = dedupe.visual_check_status()
    assert state["status"] == "done"
    assert state["confirmed"] == 1
    assert state["rejected"] == 1
    assert state["unknown"] == 1
    assert state["errors"] == 1


def test_reset_duplicates_clears_everything(library_env):
    library = library_env["library"]
    a = library / "A" / "reset-a.mp4"
    b = library / "A" / "reset-b.mp4"
    with sqlite3.connect(library_env["db_path"]) as conn:
        _real_file(conn, a)
        path_b = str(_real_file(conn, b, thumb_hash="aaaaaaaaaaaaaaaa"))
    dedupe.ignore_pair(str(a), path_b, "content")
    assert dedupe.reset_duplicates() == {
        "ignored_cleared": 1,
        "signatures_cleared": 0,
    }
    assert dedupe.visual_check_status()["status"] == "idle"
    assert dedupe.ignored_page()["total"] == 0


def test_ignored_pairs_survive_a_broken_connection():
    class Broken:
        @staticmethod
        def execute(*_args, **_kwargs):
            raise sqlite3.Error("no such table")

    assert dedupe._ignored_pairs(Broken()) == set()


def test_group_fully_ignored_variants():
    pair = {frozenset(("a", "b"))}
    triple = pair | {frozenset(("a", "c")), frozenset(("b", "c"))}
    assert dedupe._group_fully_ignored(["a"], set()) is True
    assert dedupe._group_fully_ignored(["a"], frozenset()) is True
    assert dedupe._group_fully_ignored(["a", "b", "c"], triple) is True
    assert dedupe._group_fully_ignored(["a", "b", "c"], pair) is False
    assert dedupe._group_fully_ignored(["a", "b"], set()) is False
    assert dedupe._group_fully_ignored(["a", "b"], pair) is True


def test_candidate_row_helpers(library_env):
    with sqlite3.connect(library_env["db_path"]) as conn:
        assert dedupe._candidate_sizes(conn) == []
        assert dedupe._candidate_rows(conn, []) == []


def test_content_groups_variants(library_env):
    library = library_env["library"]
    pair_size, split_size = 700, 800
    p1 = library / "A" / "group-p1.mp4"
    p2 = library / "A" / "group-p2.mp4"
    zipped = library / "archive.zip" / "member.mp4"
    split_a = library / "A" / "split-a.mp4"
    split_b = library / "A" / "split-b.mp4"
    p1.write_bytes(b"group-one")
    p2.write_bytes(b"group-one")
    with sqlite3.connect(library_env["db_path"]) as conn:
        quick_one = "a" * 64
        _insert_row(conn, p1, size=pair_size, quick=quick_one)
        _insert_row(conn, p2, size=pair_size, quick=quick_one)
        _insert_row(
            conn,
            zipped,
            size=pair_size,
            quick=quick_one,
            folder=str(library / "archive.zip"),
        )
        _insert_row(conn, split_a, size=split_size, quick="b" * 64)
        _insert_row(conn, split_b, size=split_size, quick="c" * 64)
        _insert_row(
            conn,
            library / "A" / "unhashed.mp4",
            size=split_size,
            quick=None,
        )
        _insert_row(
            conn,
            library / "A" / "empty-marker.mp4",
            size=pair_size,
            quick="empty_file",
        )

    result = dedupe.content_groups(limit=10)
    assert result["mode"] == "content"
    assert result["unhashed"] == 2
    group = next(g for g in result["groups"] if g["size"] == pair_size)
    paths = {f["path"] for f in group["files"]}
    assert paths == {str(p1), str(p2)}
    assert group["reclaimable"] == pair_size
    assert group["match_type"] == "content"

    dedupe.ignore_pair(str(p1), str(p2), "content")
    after = dedupe.content_groups()
    assert all(g["size"] != pair_size for g in after["groups"])


def test_visual_groups_variants(library_env):
    library = library_env["library"]
    close = ("ffff000000000000", "ffff000000000001")
    far_pair = ("0000000000000001", "00000000ffff0001")
    names = ("v-f1.jpg", "v-f2.jpg", "v-g1.jpg", "v-g2.jpg", "v-n.jpg", "v-z.jpg")
    hashes = (
        far_pair[0],
        far_pair[1],
        close[0],
        close[1],
        None,
        "zzzzzzzzzzzzzzzz",
    )
    with sqlite3.connect(library_env["db_path"]) as conn:
        for name, value in zip(names, hashes):
            _real_file(conn, library / "A" / name, kind="image", thumb_hash=value)
    result = dedupe.visual_groups(limit=10)
    assert result["mode"] == "visual"
    assert result["unhashed"] == 1
    assert result["total_files"] == 2
    group = result["groups"][0]
    assert {f["path"] for f in group["files"]} == {
        str(library / "A" / "v-g1.jpg"),
        str(library / "A" / "v-g2.jpg"),
    }
    assert group["id"].startswith("v")
    assert group["match_type"] == "visual"
    assert group["reclaimable"] == 7
    assert group["verification"] is None
    assert all(
        str(library / "A" / "v-f2.jpg") not in {f["path"] for f in g["files"]}
        for g in result["groups"]
    )


def test_image_filter_excludes_images_from_both_scanners(library_env):
    library = library_env["library"]
    with sqlite3.connect(library_env["db_path"]) as conn:
        for name, kind, quick, thumb in (
            ("img-a.jpg", "image", "d" * 64, "1111000000000000"),
            ("img-b.jpg", "image", "d" * 64, "1111000000000001"),
            ("vid-a.mp4", "video", "e" * 64, "2222000000000000"),
            ("vid-b.mp4", "video", "e" * 64, "2222000000000001"),
        ):
            _real_file(
                conn,
                library / "A" / name,
                kind=kind,
                quick=quick,
                thumb_hash=thumb,
            )

    content = dedupe.content_groups(limit=10, include_images=False)
    assert include_images_paths(content) == {
        str(library / "A" / "vid-a.mp4"),
        str(library / "A" / "vid-b.mp4"),
    }

    visual = dedupe.visual_groups(limit=10, include_images=False)
    assert include_images_paths(visual) == {
        str(library / "A" / "vid-a.mp4"),
        str(library / "A" / "vid-b.mp4"),
    }
    assert visual["unhashed"] == 0

    combined = dedupe.all_groups(limit=10, include_images=False)
    assert include_images_paths(combined) == {
        str(library / "A" / "vid-a.mp4"),
        str(library / "A" / "vid-b.mp4"),
    }

    # Flip the shared merged cache back on: images are visible again.
    with_images = dedupe.all_groups(limit=10)
    assert any(
        file["path"].endswith(".jpg")
        for group in with_images["groups"]
        for file in group["files"]
    )


def test_status_and_hashing_can_skip_images(library_env):
    library = library_env["library"]
    with sqlite3.connect(library_env["db_path"]) as conn:
        for name, kind in (
            ("skip-a.jpg", "image"),
            ("skip-b.jpg", "image"),
            ("skip-a.mp4", "video"),
            ("skip-b.mp4", "video"),
        ):
            _real_file(conn, library / "A" / name, kind=kind)

    status = dedupe.status(include_images=False)
    assert status["content"]["candidate_files"] == 2
    assert status["visual"]["unhashed"] == 2

    hashed = dedupe.hash_content(limit=10, include_images=False)
    assert hashed["hashed"] == 2
    assert hashed["remaining"] == 0
    with sqlite3.connect(library_env["db_path"]) as conn:
        image_hashes = conn.execute(
            "SELECT quick_hash FROM files WHERE type = 'image'"
        ).fetchall()
        video_hashes = conn.execute(
            "SELECT quick_hash FROM files WHERE type = 'video'"
        ).fetchall()
    assert image_hashes == [(None,), (None,)]
    assert all(row[0] for row in video_hashes)

    dedupe.hash_visual(limit=10, include_images=False)
    with sqlite3.connect(library_env["db_path"]) as conn:
        image_thumbs = conn.execute(
            "SELECT thumb_hash FROM files WHERE type = 'image'"
        ).fetchall()
        video_thumbs = conn.execute(
            "SELECT thumb_hash FROM files WHERE type = 'video'"
        ).fetchall()
    assert image_thumbs == [(None,), (None,)]
    assert all(row[0] for row in video_thumbs)


def include_images_paths(result):
    return {file["path"] for group in result["groups"] for file in group["files"]}


def test_visual_groups_respects_the_cap(library_env):
    library = library_env["library"]
    clique = [f"000000000000{1 << bit:04x}" for bit in range(13)]
    with sqlite3.connect(library_env["db_path"]) as conn:
        for index, value in enumerate(clique):
            _insert_row(
                conn,
                library / "A" / f"cap-{index:02d}.jpg",
                kind="image",
                thumb_hash=value,
            )
    result = dedupe.visual_groups(limit=50)
    sized_groups = [g for g in result["groups"] if len(g["files"]) >= 2]
    assert len(sized_groups) == 1
    assert len(sized_groups[0]["files"]) == 12
    assert all(
        str(library / "A" / "cap-12.jpg") not in {f["path"] for f in g["files"]}
        for g in result["groups"]
    )


def test_visual_groups_skips_an_ignored_pair(library_env):
    library = library_env["library"]
    first = library / "A" / "ign-1.jpg"
    second = library / "A" / "ign-2.jpg"
    with sqlite3.connect(library_env["db_path"]) as conn:
        _insert_row(conn, first, kind="image", thumb_hash="0000000000000004")
        _insert_row(conn, second, kind="image", thumb_hash="0000000000000006")
    dedupe.ignore_pair(str(first), str(second), "visual")
    result = dedupe.visual_groups()
    assert result["total_groups"] == 0
    assert result["total_files"] == 0


def test_all_groups_merges_rejects_and_caches(library_env):
    library = library_env["library"]
    # Content pair whose visual cluster is fully covered by the content group.
    v1 = library / "A" / "all-v1.mp4"
    v2 = library / "A" / "all-v2.mp4"
    # Content pair plus one genuinely different file -> merged union group.
    u1 = library / "A" / "all-u1.mp4"
    u2 = library / "A" / "all-u2.mp4"
    u3 = library / "A" / "all-u3.mp4"
    # Video pair whose sampled frames disagree -> rejected visual group.
    r1 = library / "A" / "all-r1.mp4"
    r2 = library / "A" / "all-r2.mp4"
    payload = b"shared-payload"
    with sqlite3.connect(library_env["db_path"]) as conn:
        _real_file(
            conn, v1, size=len(payload), quick="b" * 64, thumb_hash="ffff000000000000"
        )
        _real_file(
            conn, v2, size=len(payload), quick="b" * 64, thumb_hash="ffff000000000001"
        )
        _real_file(
            conn, u1, size=len(payload), quick="c" * 64, thumb_hash="0000ffff00000000"
        )
        _real_file(
            conn, u2, size=len(payload), quick="c" * 64, thumb_hash="0000ffff00000001"
        )
        _insert_row(conn, u3, quick=None, thumb_hash="0000ffff00000002")
        _real_file(conn, r1, thumb_hash="00000000ffff0000")
        _real_file(conn, r2, thumb_hash="00000000ffff0001")
        conn.execute(
            "INSERT INTO visual_frames "
            "(path, mtime, size, duration, hashes, captured_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (str(r1), 1000.0, 7, 100.0, json.dumps(["0000000000000000"] * 5), 1.0),
        )
        conn.execute(
            "INSERT INTO visual_frames "
            "(path, mtime, size, duration, hashes, captured_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (str(r2), 1000.0, 7, 100.0, json.dumps(["ffffffffffffffff"] * 5), 1.0),
        )

    hidden = dedupe.all_groups(limit=50)
    assert hidden["mode"] == "all"
    assert hidden["rejected_groups"] == 1
    assert hidden["total_groups"] == 3
    content_sizes = [
        g["size"] for g in hidden["groups"] if g["match_type"] == "content"
    ]
    assert len(content_sizes) == 2
    unions = [g for g in hidden["groups"] if g["match_type"] == "visual"]
    assert len(unions) == 1
    union_paths = {f["path"] for f in unions[0]["files"]}
    assert union_paths == {str(u1), str(u3)}
    assert str(u2) not in union_paths
    assert all(
        {str(r1), str(r2)} != {f["path"] for f in g["files"]} for g in hidden["groups"]
    )

    shown = dedupe.all_groups(limit=50, include_rejected=True)
    assert shown["rejected_groups"] == 1
    assert shown["total_groups"] == 4
    rejected = [
        g
        for g in shown["groups"]
        if {f["path"] for f in g["files"]} == {str(r1), str(r2)}
    ]
    assert len(rejected) == 1
    assert rejected[0]["verification"]["confirmed"] is False

    # The cached merged universe serves the next page turn.
    assert dedupe._cached_unhashed() == 2
    paged = dedupe.all_groups(limit=2, offset=1)
    assert len(paged["groups"]) == 2
    dedupe.invalidate_merged_cache()
    assert dedupe.all_groups(limit=50)["total_groups"] == 3


def test_merge_fingerprint_failsafe(monkeypatch):
    def boom():
        raise RuntimeError("index gone")

    monkeypatch.setattr(dedupe, "reader", boom)
    assert dedupe._merge_fingerprint() == (-1, -1)
    dedupe.invalidate_merged_cache()


def test_auto_verify_groups_variants(monkeypatch):
    video_pair = [
        {"path": "/library/A/av1.mp4", "type": "video"},
        {"path": "/library/A/av2.mp4", "type": "video"},
    ]
    checked = {
        "match_type": "visual",
        "verification": {"available": True, "confirmed": True},
        "files": list(video_pair),
    }
    content = {"match_type": "content", "verification": None, "files": list(video_pair)}
    single_video = {
        "match_type": "visual",
        "verification": None,
        "files": [video_pair[0], {"path": "/library/A/img.jpg", "type": "image"}],
    }
    assert dedupe.auto_verify_groups([checked, content, single_video]) == 0

    queued: list[list[str]] = []

    def fake_start(groups, _progress=None):
        queued.extend(groups)
        return {
            "status": "queued",
            "running": True,
            "total_groups": len(groups),
            "completed_groups": 0,
            "queued_groups": len(groups),
            "current": [],
            "confirmed": 0,
            "rejected": 0,
            "unknown": 0,
            "errors": 0,
            "started_at": 1.0,
            "finished_at": None,
        }

    monkeypatch.setattr(dedupe, "start_visual_checks", fake_start)
    candidate = {
        "match_type": "visual",
        "verification": None,
        "files": [dict(f) for f in video_pair],
    }
    assert dedupe.auto_verify_groups([candidate]) == 1
    assert queued == [["/library/A/av1.mp4", "/library/A/av2.mp4"]]

    def refusing(groups):
        raise writes.WriteError("Choose at least one visual group", 400)

    monkeypatch.setattr(dedupe, "start_visual_checks", refusing)
    assert dedupe.auto_verify_groups([candidate]) == 0


def test_status_counts_candidates_and_ignored(library_env):
    library = library_env["library"]
    same = b"candidate-payload"
    a = library / "A" / "st-a.mp4"
    b = library / "A" / "st-b.mp4"
    a.write_bytes(same)
    b.write_bytes(same)
    with sqlite3.connect(library_env["db_path"]) as conn:
        _insert_row(conn, a, has_thumb=0)
        _insert_row(conn, b, quick="a" * 64, has_thumb=0)
        _insert_row(
            conn,
            library / "A" / "st-c.jpg",
            kind="image",
            thumb_hash="aaaaaaaaaaaaaaaa",
        )
        _insert_row(conn, library / "A" / "st-d.jpg", kind="image")
        _insert_row(conn, library / "A" / "st-e.jpg", kind="image", has_thumb=0)
    dedupe.ignore_pair(str(a), str(b), "content")
    result = dedupe.status()
    assert result["content"] == {"candidate_files": 2, "unhashed": 1}
    assert result["visual"] == {"hashed": 1, "unhashed": 1}
    assert result["ignored"] == 1


def test_hash_content_variants(library_env):
    library = library_env["library"]
    f1 = library / "A" / "hash-f1.mp4"
    f2 = library / "A" / "hash-f2.mp4"
    ghost1 = library / "A" / "ghost-1.mp4"
    ghost2 = library / "A" / "ghost-2.mp4"
    zipped = library / "archive.zip" / "member.mp4"
    with sqlite3.connect(library_env["db_path"]) as conn:
        for row in (f1, f2, ghost1, ghost2):
            _real_file(conn, row, has_thumb=0)
        ghost1.unlink()
        ghost2.unlink()
        _insert_row(conn, zipped, size=7, folder=str(library / "archive.zip"))

    result = dedupe.hash_content(limit=10)
    assert result == {"hashed": 2, "failed": 2, "remaining": 1}
    with sqlite3.connect(library_env["db_path"]) as conn:
        conn.row_factory = sqlite3.Row
        quicks = {
            row["path"]: row["quick_hash"]
            for row in conn.execute(
                "SELECT path, quick_hash FROM files WHERE path LIKE "
                "'%hash-%' OR path LIKE '%ghost-%'"
            ).fetchall()
        }
    assert quicks[str(f1)] == dedupe.quick_hash(str(f1))
    assert quicks[str(ghost1)] == "unhashable"
    assert quicks[str(ghost2)] == "unhashable"

    # The ZIP member stays queued forever: it can never be byte-hashed and
    # the second sweep with only it left stores nothing at all.
    assert dedupe.hash_content() == {"hashed": 0, "failed": 0, "remaining": 1}


def test_hash_visual_variants(library_env):
    from media_library import cache

    library = library_env["library"]
    a = library / "A" / "hv-a.jpg"
    b = library / "A" / "hv-b.jpg"
    with sqlite3.connect(library_env["db_path"]) as conn:
        _real_file(conn, a, kind="image")
        _real_file(conn, b, kind="image")
    _checkerboard(cache.thumbnail_path(str(a), 1000.0, 7))

    first = dedupe.hash_visual(limit=10)
    assert first == {"hashed": 1, "remaining": 0}
    with sqlite3.connect(library_env["db_path"]) as conn:
        conn.row_factory = sqlite3.Row
        values = {
            row["path"]: row["thumb_hash"]
            for row in conn.execute(
                "SELECT path, thumb_hash FROM files WHERE path LIKE '%hv-%'"
            ).fetchall()
        }
    assert re.fullmatch(r"[0-9a-f]{16}", values[str(a)])
    assert values[str(b)] == "no_thumb"

    assert dedupe.hash_visual() == {"hashed": 0, "remaining": 0}


def test_verify_pair_matrix(library_env):
    library = library_env["library"]
    keep = library / "A" / "vp-keep.mp4"
    copy = library / "A" / "vp-copy.mp4"
    odd = library / "A" / "vp-odd.mp4"
    payload = b"verify-pair"
    keep.write_bytes(payload)
    copy.write_bytes(payload)
    odd.write_bytes(b"different")
    result = dedupe.verify_pair(str(keep), str(copy))
    assert result == {"keep": str(keep), "path": str(copy), "identical": True}
    assert dedupe.verify_pair(str(keep), str(odd))["identical"] is False
    with pytest.raises(writes.WriteError) as missing_keep:
        dedupe.verify_pair(str(library / "A" / "ghost.mp4"), str(copy))
    assert missing_keep.value.status == 404
    with pytest.raises(writes.WriteError) as missing_other:
        dedupe.verify_pair(str(keep), str(library / "A" / "ghost.mp4"))
    assert missing_other.value.status == 404


def test_resolve_matrix(library_env):
    library = library_env["library"]
    keep = library / "A" / "rs-keep.mp4"
    copy = library / "A" / "rs-copy.mp4"
    odd = library / "A" / "rs-odd.mp4"
    visual = library / "A" / "rs-visual.mp4"
    payload = b"resolve-payload"
    keep.write_bytes(payload)
    copy.write_bytes(payload)
    odd.write_bytes(b"different")
    visual.write_bytes(b"visual not identical")
    ghost = str(library / "A" / "ghost.mp4")

    result = dedupe.resolve(
        str(keep),
        [str(copy), str(odd), ghost, str(keep)],
        mode="content",
    )
    assert result["kept"] == str(keep)
    assert result["deleted"] == [str(copy)]
    assert result["mismatched"] == [str(odd), ghost]
    assert result["failed"] == []
    assert not copy.exists()
    with sqlite3.connect(library_env["db_path"]) as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM files WHERE path = ?", (str(copy),)
            ).fetchone()[0]
            == 0
        )

    with pytest.raises(writes.WriteError) as missing_keep:
        dedupe.resolve(ghost, [str(odd)], mode="content")
    assert missing_keep.value.status == 404

    with pytest.raises(writes.WriteError) as empty_deletes:
        dedupe.resolve(str(keep), [])
    assert empty_deletes.value.status == 400

    with pytest.raises(writes.WriteError) as bad_mode:
        dedupe.resolve(str(keep), [str(odd)], mode="weird")
    assert bad_mode.value.status == 400

    visual_mode = dedupe.resolve(str(keep), [str(visual)], mode="visual")
    assert visual_mode["deleted"] == [str(visual)]
    assert not visual.exists()


def test_ignore_and_unignore_matrix(library_env):
    library = library_env["library"]
    first = library / "A" / "ig-first.jpg"
    second = library / "A" / "ig-second.jpg"
    with sqlite3.connect(library_env["db_path"]) as conn:
        _real_file(conn, first, kind="image")
        _real_file(conn, second, kind="image")

    assert dedupe.ignored_page() == {"items": [], "total": 0, "limit": 50, "offset": 0}
    ignored = dedupe.ignore_pair(str(first), str(second), "visual")
    assert ignored == {
        "ignored": True,
        "path1": str(first),
        "path2": str(second),
        "match_type": "visual",
    }
    page = dedupe.ignored_page(limit=5)
    assert page["total"] == 1
    assert page["limit"] == 5
    assert page["items"][0]["path1"] == str(first)
    assert page["items"][0]["match_type"] == "visual"

    with pytest.raises(writes.WriteError) as bad_type:
        dedupe.ignore_pair(str(first), str(second), "weird")
    assert bad_type.value.status == 400
    with pytest.raises(writes.WriteError) as self_pair:
        dedupe.ignore_pair(str(first), str(first))
    assert self_pair.value.status == 400

    assert dedupe.unignore_pair(str(second), str(first)) == {
        "unignored": True,
        "path1": str(first),
        "path2": str(second),
    }
    assert dedupe.unignore_pair(str(first), str(second))["unignored"] is False
    assert dedupe.ignored_page()["total"] == 0


def test_dedupe_router_endpoints(library_env, monkeypatch):
    from media_library import cache

    library = library_env["library"]
    dup_a = library / "A" / "dup-a.mp4"
    dup_b = library / "A" / "dup-b.mp4"
    payload = b"identical-router" * 40
    dup_a.write_bytes(payload)
    dup_b.write_bytes(payload)
    with sqlite3.connect(library_env["db_path"]) as conn:
        path_a = _insert_row(conn, dup_a)
        path_b = _insert_row(conn, dup_b)
        _insert_row(
            conn,
            library / "A" / "view-x.jpg",
            kind="image",
            thumb_hash="ffff000000000000",
        )
        _insert_row(
            conn,
            library / "A" / "view-y.jpg",
            kind="image",
            thumb_hash="ffff000000000001",
        )
    poster = cache.thumbnail_path(path_a, 1000.0, len(payload))
    _checkerboard(poster)

    client = TestClient(create_app())

    status = client.get("/api/dedupe/status")
    assert status.status_code == 200
    assert status.json()["content"]["candidate_files"] == 2

    assert client.get("/api/dedupe/groups", params={"mode": "nope"}).status_code == 400
    empty = client.get("/api/dedupe/groups", params={"mode": "content"})
    assert empty.status_code == 200
    assert empty.json()["unhashed"] == 2

    filled = client.post("/api/dedupe/hash", json={"mode": "content"})
    assert filled.status_code == 200
    assert filled.json()["hashed"] == 2

    grouped = client.get("/api/dedupe/groups", params={"mode": "content"})
    assert grouped.status_code == 200
    assert grouped.json()["total_groups"] == 1

    visual = client.get(
        "/api/dedupe/groups", params={"mode": "visual", "auto_verify": "false"}
    )
    assert visual.status_code == 200
    assert visual.json()["total_groups"] == 1

    combined = client.get("/api/dedupe/groups", params={"auto_verify": "false"})
    assert combined.status_code == 200
    assert combined.json()["mode"] == "all"
    assert combined.json()["total_groups"] == 2

    video_only = client.get(
        "/api/dedupe/groups",
        params={"auto_verify": "false", "include_images": "false"},
    )
    assert video_only.status_code == 200
    assert video_only.json()["total_groups"] == 1
    assert all(
        file["type"] == "video"
        for group in video_only.json()["groups"]
        for file in group["files"]
    )

    solo = str(library / "A" / "never-indexed.mp4")
    duo = str(library / "A" / "never-indexed-too.mp4")
    assert (
        client.post(
            "/api/dedupe/verify-visual", json={"paths": [solo, solo]}
        ).status_code
        == 400
    )
    frame_check = client.post("/api/dedupe/verify-visual", json={"paths": [solo, duo]})
    assert frame_check.status_code == 200
    assert frame_check.json()["available"] is False

    assert (
        client.post(
            "/api/dedupe/verify-visual/batch", json={"groups": [["only-one"]]}
        ).status_code
        == 400
    )
    monkeypatch.setattr(
        dedupe,
        "verify_visual_group",
        lambda paths, progress=None: {
            "available": True,
            "confirmed": True,
            "checked_pairs": 1,
            "confirmed_pairs": 1,
            "frames": 5,
            "pairs": [],
        },
    )
    batch = client.post(
        "/api/dedupe/verify-visual/batch",
        json={"groups": [[str(path_a), str(path_b)]]},
    )
    assert batch.status_code == 200
    assert batch.json()["total_groups"] == 1
    assert _wait_until(
        lambda: (
            client.get("/api/dedupe/verify-visual/status").json()["completed_groups"]
            >= 1
        )
    )
    snapshot = client.get("/api/dedupe/verify-visual/status").json()
    assert snapshot["status"] == "done"

    reset = client.post("/api/dedupe/reset")
    assert reset.status_code == 200

    visual_hash = client.post("/api/dedupe/hash", json={"mode": "visual"})
    assert visual_hash.status_code == 200
    assert visual_hash.json()["hashed"] >= 1

    assert client.post("/api/dedupe/hash", json={"mode": "nope"}).status_code == 400

    verify = client.post("/api/dedupe/verify", json={"keep": path_a, "path": path_b})
    assert verify.status_code == 200
    assert verify.json()["identical"] is True
    assert (
        client.post(
            "/api/dedupe/verify", json={"keep": path_a, "path": "/etc/passwd"}
        ).status_code
        == 403
    )

    resolved = client.post(
        "/api/dedupe/resolve",
        json={"keep": path_a, "delete": [path_b], "mode": "visual"},
    )
    assert resolved.status_code == 200
    assert resolved.json()["deleted"] == [path_b]
    assert (
        client.post(
            "/api/dedupe/resolve",
            json={"keep": str(library / "A" / "ghost.mp4"), "delete": [path_a]},
        ).status_code
        == 404
    )

    ignored = client.post(
        "/api/dedupe/ignore",
        json={
            "path1": str(library / "A" / "view-x.jpg"),
            "path2": str(library / "A" / "view-y.jpg"),
            "match_type": "visual",
        },
    )
    assert ignored.status_code == 200
    assert (
        client.post(
            "/api/dedupe/ignore",
            json={"path1": path_a, "path2": path_a},
        ).status_code
        == 400
    )
    page = client.get("/api/dedupe/ignored")
    assert page.status_code == 200
    assert page.json()["total"] == 1

    unignored = client.post(
        "/api/dedupe/unignore",
        json={
            "path1": str(library / "A" / "view-x.jpg"),
            "path2": str(library / "A" / "view-y.jpg"),
        },
    )
    assert unignored.status_code == 200
    assert unignored.json()["unignored"] is True
    assert (
        client.post(
            "/api/dedupe/unignore",
            json={"path1": "/etc/passwd", "path2": path_a},
        ).status_code
        == 403
    )


def test_dedupe_router_error_translation(library_env, monkeypatch):
    client = TestClient(create_app())

    def boom(*_args, **_kwargs):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(dedupe, "reset_duplicates", boom)
    assert client.post("/api/dedupe/reset").status_code == 500

    monkeypatch.setattr(dedupe, "hash_content", boom)
    assert client.post("/api/dedupe/hash", json={"mode": "content"}).status_code == 500

    monkeypatch.setattr(dedupe, "hash_visual", boom)
    assert client.post("/api/dedupe/hash", json={"mode": "visual"}).status_code == 500
