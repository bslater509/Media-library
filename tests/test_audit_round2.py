"""Regression tests for the second code-audit round.

Each test pins one behaviour the audit changed: item-level failures in batch
moves, the suffix-range guard on empty files, the per-upload staging lock, the
conditional ``has_thumb`` mark, the ZIP-member exclusion in the content-hash
sweep, transient probe failures leaving the row NULL, the oversized-image WebP
budget and the login throttle.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from media_library import (
    config as v2_config,
)
from media_library import (
    dedupe,
    downloads,
    probe,
    thumbnails,
    webp,
    writes,
)
from media_library.paths import resolve_library_path
from media_library.routers.media import _parse_range


@pytest.fixture()
def library_env(tmp_path, monkeypatch):
    """Library root + schema row structure for direct writes.* calls."""
    library = tmp_path / "library"
    (library / "A").mkdir(parents=True)
    thumbs = tmp_path / "thumbs"
    (thumbs / "candidates").mkdir(parents=True)
    monkeypatch.setenv("LIBRARY_DIR", str(library))
    monkeypatch.setenv("THUMBNAIL_DIR", str(thumbs))
    monkeypatch.setenv("WRITES", "true")
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
    return {"library": library, "folder": library / "A", "db_path": db_path}


def _insert_file(db_path: Path, path: str, size: int = 10) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO files (path, name, folder_path, type, ext, size, "
            "size_formatted, mtime, needs_transcode, has_thumb) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                path,
                os.path.basename(path),
                os.path.dirname(path),
                "image",
                os.path.splitext(path)[1],
                size,
                f"{size} B",
                1.0,
                0,
                0,
            ),
        )


# ---------------------------------------------------------------------------
# Batch moves: one rejected path must not abort the batch
# ---------------------------------------------------------------------------


def test_move_files_fails_the_item_not_the_batch_on_a_403(library_env):
    good = library_env["folder"] / "keep.jpg"
    good.write_bytes(b"x" * 4)
    target = library_env["library"] / "B"
    target.mkdir()

    result = writes.move_files(
        [str(good), "../outside.jpg"], str(target), conflict="error"
    )
    assert {failed["path"] for failed in result["failed"]} == {"../outside.jpg"}
    assert [moved["source"] for moved in result["moved"]] == [str(good)]
    # The good item was really moved; the bad one never left the library root.
    assert not good.exists() and (target / "keep.jpg").exists()


# ---------------------------------------------------------------------------
# Range parsing: a suffix range on an empty file is unsatisfiable
# ---------------------------------------------------------------------------


def test_suffix_range_on_empty_file_is_rejected():
    assert _parse_range("bytes=-100", 0) is None
    # A valid suffix range keeps working.
    assert _parse_range("bytes=-10", 100) == (90, 99)
    assert _parse_range("bytes=-1000", 100) == (0, 99)


# ---------------------------------------------------------------------------
# Uploads: concurrent chunks for one id must not interleave
# ---------------------------------------------------------------------------


def test_stage_chunk_serialises_racing_writers_for_the_same_upload(
    library_env, tmp_path
):
    upload_id = "audit-upload-0001"
    writes._upload_locks.pop(upload_id, None)

    started = threading.Barrier(2)
    results: dict[str, int] = {}

    # Two clients believed they were at offset 0: the loser must see the
    # winner's data instead of truncating it away.
    def write_payload(tag: str) -> None:
        started.wait()
        result = writes.stage_chunk(upload_id, 0, tag.encode())
        results[tag] = result["received_bytes"]

    threads = [
        threading.Thread(target=write_payload, args=(name,))
        for name in ("first", "second")
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    staged = v2_config.get_settings().upload_staging_dir / f"{upload_id}.part"
    body = staged.read_bytes()
    # One of the two payloads survives intact — never interleaved bytes.
    assert body in (b"first", b"second")
    assert set(results) == {"first", "second"}
    assert set(results.values()) <= {len("first"), len("second")}
    writes.cancel_upload(upload_id)
    writes._upload_locks.pop(upload_id, None)


# ---------------------------------------------------------------------------
# Posters: has_thumb is only marked for the mtime/size it was generated for
# ---------------------------------------------------------------------------


def test_mark_has_thumb_skips_a_row_that_changed_underneath(library_env):
    path = str(library_env["folder"] / "a.jpg")
    db_path = library_env["db_path"]
    _insert_file(db_path, path)
    thumbnails.mark_has_thumb(path, 1.0, 10, 1)
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT has_thumb FROM files").fetchone()[0] == 1

    # Re-index the file with new stat values, then a stale mark must skip.
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE files SET has_thumb = 0, mtime = 2.0, size = 20")
    assert thumbnails.mark_has_thumb(path, 1.0, 10, 1) is False
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT has_thumb FROM files").fetchone()[0] == 0


# ---------------------------------------------------------------------------
# Dedupe: ZIP members cannot starve the content-hash sweep
# ---------------------------------------------------------------------------


def test_hash_content_excludes_zip_members_and_converges(library_env):
    db_path = library_env["db_path"]
    archive = library_env["library"] / "pics.zip"
    loose_a = library_env["folder"] / "a.jpg"
    loose_b = library_env["folder"] / "b.jpg"
    for path in (loose_a, loose_b):
        path.write_bytes(os.urandom(64))
    loose_a_copy = library_env["folder"] / "a2.jpg"
    loose_a_copy.write_bytes(loose_a.read_bytes())
    for path in (loose_a, loose_b, loose_a_copy):
        _insert_file(db_path, str(path), size=64)

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO folders (path, name, parent_path, file_count, "
            "subfolder_count, last_scanned, is_zip, total_size) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (str(archive), "pics.zip", str(library_env["library"]), 0, 0, 1.0, 1, 0),
        )
    member = f"{archive}/big.png"
    _insert_file(db_path, member, size=64)

    first = dedupe.hash_content(limit=10)
    # ZIP members are never byte-hashed; the batch must not stall on them.
    assert first["hashed"] > 0
    second = dedupe.hash_content(limit=10)
    assert second["remaining"] == 0


# ---------------------------------------------------------------------------
# Probe: a transient failure must not freeze 0 x 0 into the row
# ---------------------------------------------------------------------------


def test_probe_batch_leaves_null_for_transient_failures(library_env, monkeypatch):
    db_path = library_env["db_path"]
    path = str(library_env["folder"] / "video.mp4")
    _insert_file(db_path, path)

    import subprocess

    def flaky(*args: Any, **kwargs: Any) -> Any:
        raise subprocess.TimeoutExpired("ffprobe", 25)

    monkeypatch.setattr(probe.subprocess, "run", flaky)
    result = probe.probe_batch(limit=10)
    assert result["transient"] == 1
    assert result["probed"] == 0 and result["failed"] == 0
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT width FROM files").fetchone()[0] is None

    # A definitive failure still freezes the 0 x 0 sentinel.
    monkeypatch.setattr(
        probe.subprocess,
        "run",
        lambda *a, **k: type("R", (), {"returncode": 1, "stdout": b""})(),
    )
    result = probe.probe_batch(limit=10)
    assert result["failed"] == 1
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT width FROM files").fetchone()[0] == 0


# ---------------------------------------------------------------------------
# WebP: oversized images skip conversion instead of decoding into RAM
# ---------------------------------------------------------------------------


def test_convert_one_skips_an_oversized_image_without_touching_it(
    library_env, monkeypatch
):
    monkeypatch.setattr(webp, "_MAX_CONVERT_PIXELS", 10_000)
    source = library_env["folder"] / "big.png"
    Image.new("RGB", (200, 100), "red").save(source)
    original = source.read_bytes()
    db_path = library_env["db_path"]
    _insert_file(db_path, str(source), size=source.stat().st_size)

    outcome = webp._convert_one(str(source), quality=85, skip_larger=False)
    assert outcome["status"] == "skipped"
    assert source.read_bytes() == original
    assert not source.with_suffix(".webp").exists()


def test_zip_webp_stream_returns_none_for_oversized_members(library_env, monkeypatch):
    from media_library import zip_webp

    monkeypatch.setattr(zip_webp, "_MAX_CONVERT_PIXELS", 10_000)
    archive = library_env["library"] / "pics.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        info = zipfile.ZipInfo("huge.png")
        buffer = BytesIO()
        # A real encoded member whose decoded size exceeds the patched budget.
        Image.new("RGB", (150, 150), "blue").save(buffer, format="PNG")
        bundle.writestr(info, buffer.getvalue())

    with zipfile.ZipFile(archive) as bundle, bundle.open("huge.png") as member:
        assert zip_webp._webp_bytes_stream(member, quality=85) is None


# ---------------------------------------------------------------------------
# Auth: repeated failures throttle the offending client
# ---------------------------------------------------------------------------


def test_login_backoff_grows_with_failures(monkeypatch):
    from media_library.routers import auth

    monkeypatch.setattr(auth, "_failures", {})
    assert auth._backoff("10.0.0.1") == 0.0
    auth._record_failure("10.0.0.1")
    assert auth._backoff("10.0.0.1") == 1.0
    auth._record_failure("10.0.0.1")
    assert auth._backoff("10.0.0.1") == 2.0
    auth._failures.clear()


def test_retry_destination_reports_the_409_wording(library_env):
    resolve_library_path(str(library_env["library"]))
    with pytest.raises(writes.WriteError) as exc:
        downloads._retry_destination(str(library_env["library"] / "missing"))
    assert "no longer exists" in exc.value.message


# ---------------------------------------------------------------------------
# Configuration: legacy V2_ env names still win over defaults
# ---------------------------------------------------------------------------


def test_v2_fallback_environment_names_still_apply(monkeypatch, tmp_path):
    # The legacy V2_ names apply when the current plain names are unset.
    for name in (
        "OWN_WORKERS",
        "CRAWL_INTERVAL_MINUTES",
        "UPLOAD_STAGING_DIR",
        "BACKUP_INTERVAL_HOURS",
        "DB_READ_ONLY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("V2_OWN_WORKERS", "true")
    monkeypatch.setenv("V2_CRAWL_INTERVAL_MINUTES", "7")
    monkeypatch.setenv("V2_UPLOAD_STAGING_DIR", str(tmp_path / "staged"))
    monkeypatch.setenv("V2_BACKUP_INTERVAL_HOURS", "12.5")
    v2_config.reload_settings()
    try:
        settings = v2_config.get_settings()
        assert settings.own_workers is True
        assert settings.crawl_interval_minutes == 7
        assert settings.upload_staging_dir == tmp_path / "staged"
        assert settings.backup_interval_hours == 12.5
    finally:
        v2_config.reload_settings()


def test_plain_names_win_over_the_v2_fallback(monkeypatch, tmp_path):
    monkeypatch.setenv("OWN_WORKERS", "true")
    monkeypatch.setenv("V2_OWN_WORKERS", "false")
    monkeypatch.setenv("CRAWL_INTERVAL_MINUTES", "11")
    monkeypatch.setenv("V2_CRAWL_INTERVAL_MINUTES", "7")
    monkeypatch.setenv("DB_READ_ONLY", "false")
    v2_config.reload_settings()
    try:
        settings = v2_config.get_settings()
        assert settings.own_workers is True
        assert settings.crawl_interval_minutes == 11
    finally:
        v2_config.reload_settings()
