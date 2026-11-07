"""WebP optimisation tests for v2."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest
from PIL import Image

from media_library import cache, webp
from media_library import config as v2_config


@pytest.fixture
def webp_env(tmp_path, monkeypatch):
    library = tmp_path / "library"
    (library / "Pics").mkdir(parents=True)
    thumbs = tmp_path / "thumbs"
    (thumbs / "animated").mkdir(parents=True)
    monkeypatch.setenv("LIBRARY_DIR", str(library))
    monkeypatch.setenv("LIBRARY_DIR", str(library))
    monkeypatch.setenv("THUMBNAIL_DIR", str(thumbs))
    monkeypatch.setenv("DB_READ_ONLY", "false")
    monkeypatch.setenv("WRITES", "true")
    monkeypatch.setenv("WEBP_QUALITY", "80")
    monkeypatch.setenv("WEBP_SKIP_LARGER", "false")
    v2_config.reload_settings()

    db_path = Path(os.environ["DB_PATH"])
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO folders (path, name, parent_path, file_count, "
            "subfolder_count, last_scanned, is_zip, total_size) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (str(library / "Pics"), "Pics", str(library), 1, 0, 1.0, 0, 0),
        )
        conn.execute(
            "UPDATE webp_stats SET saved_bytes = 0, converted_count = 0, "
            "skipped_count = 0 WHERE id = 1"
        )

    import random

    photo = library / "Pics" / "holiday.png"
    image = Image.new("RGB", (320, 240))
    rng = random.Random(7)
    image.putdata(
        [
            (rng.randrange(256), rng.randrange(256), rng.randrange(256))
            for _ in range(320 * 240)
        ]
    )
    image.save(photo, "PNG")

    stat = photo.stat()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO files (path, name, folder_path, type, ext, size, "
            "size_formatted, mtime, needs_transcode, has_thumb, width, height, "
            "webp_optimized) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                str(photo),
                photo.name,
                str(photo.parent),
                "image",
                ".png",
                stat.st_size,
                f"{stat.st_size} B",
                stat.st_mtime,
                0,
                1,
                240,
                180,
                0,
            ),
        )
        conn.execute(
            "INSERT INTO favorites (path, created_at) VALUES (?, ?)",
            (str(photo), 1.0),
        )
    thumb = cache.thumbnail_path(str(photo), stat.st_mtime, stat.st_size)
    Image.new("RGB", (32, 32), (120, 90, 200)).save(thumb, "JPEG")

    try:
        yield {"library": library, "photo": photo, "thumb": thumb, "db_path": db_path}
    finally:
        v2_config.reload_settings()


def _db(env):
    return sqlite3.connect(env["db_path"])


def test_optimize_converts_updates_index_and_moves_the_poster(webp_env):
    before = webp.status()
    assert before["pending"] == 1
    original_size = webp_env["photo"].stat().st_size

    result = webp.optimize_batch(limit=5)
    assert result["converted"] == 1
    assert result["remaining"] == 0
    assert result["saved_bytes"] > 0

    photo = webp_env["photo"]
    target = photo.with_suffix(".webp")
    assert target.is_file()
    assert not photo.exists()
    assert target.stat().st_size > 0
    assert result["saved_bytes"] == original_size - target.stat().st_size

    with _db(webp_env) as conn:
        row = conn.execute(
            "SELECT path, ext, size, webp_optimized, quick_hash FROM files "
            "WHERE path = ?",
            (str(target),),
        ).fetchone()
        favorite = conn.execute(
            "SELECT path FROM favorites WHERE path = ?", (str(target),)
        ).fetchone()
        stats = conn.execute(
            "SELECT saved_bytes, converted_count FROM webp_stats WHERE id = 1"
        ).fetchone()
    assert row[0] == str(target)
    assert row[1] == ".webp"
    assert row[2] == target.stat().st_size
    assert row[3] == 1
    assert row[4] is None
    assert favorite == (str(target),)
    assert stats[0] == result["saved_bytes"]
    assert stats[1] == 1

    new_thumb = cache.thumbnail_path(
        str(target), target.stat().st_mtime, target.stat().st_size
    )
    assert new_thumb.is_file()
    assert not webp_env["thumb"].exists()


def test_already_webp_rows_are_marked_without_conversion(webp_env):
    with _db(webp_env) as conn:
        conn.execute(
            "UPDATE files SET ext = '.webp', name = 'holiday.webp', "
            "webp_optimized = 0 WHERE path = ?",
            (str(webp_env["photo"]),),
        )
    result = webp.optimize_batch(limit=5)
    assert result["converted"] == 0
    with _db(webp_env) as conn:
        value = conn.execute(
            "SELECT webp_optimized FROM files WHERE path = ?",
            (str(webp_env["photo"]),),
        ).fetchone()[0]
    assert value == 1


def test_optimize_reports_a_missing_file_without_crashing(webp_env):
    webp_env["photo"].unlink()
    result = webp.optimize_batch(limit=5)
    assert result["failed"] == 1
    assert result["converted"] == 0
    assert result["errors"]
