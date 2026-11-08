"""Line-coverage sweep for the ZIP modules (zip_ops, zipfs).

Happy paths that the dedicated suites already exercise are repeated here with
the cheapest real archives possible, because this file is meant to run on its
own; failure branches are built with real truncated archives where that works
and with small monkeypatched seams (a rename that fails) where the failure is a
race or a synthetic I/O fault.
"""

from __future__ import annotations

import errno
import os
import sqlite3
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from media_library import config as v2_config
from media_library import writes, zip_ops, zipfs
from media_library.db import writer


@pytest.fixture
def cover_env(tmp_path, monkeypatch):
    """Library root and ZIP cache pointed at the test tree."""
    library = tmp_path / "library"
    library.mkdir()
    cache = tmp_path / "zip_cache"
    cache.mkdir()
    monkeypatch.setenv("LIBRARY_DIR", str(library))
    monkeypatch.setenv("THUMBNAIL_DIR", str(tmp_path / "thumbs"))
    monkeypatch.setenv("ZIP_CACHE_DIR", str(cache))
    monkeypatch.setenv("DB_READ_ONLY", "false")
    monkeypatch.setenv("WRITES", "true")
    v2_config.reload_settings()
    try:
        yield {"library": library, "cache": cache}
    finally:
        v2_config.reload_settings()


def _db():
    return sqlite3.connect(os.environ["DB_PATH"])


def _add_folder(conn, folder, *, is_zip=0):
    folder = Path(folder)
    conn.execute(
        "INSERT INTO folders (path, name, parent_path, file_count, "
        "subfolder_count, last_scanned, is_zip, total_size) VALUES (?,?,?,?,?,?,?,?)",
        (str(folder), folder.name, str(folder.parent), 0, 0, 1.0, is_zip, 0),
    )


def _add_file(conn, path, kind="image", ext=".jpg", size=300):
    path = Path(path)
    conn.execute(
        "INSERT INTO files (path, name, folder_path, type, ext, size, "
        "size_formatted, mtime, needs_transcode, has_thumb) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            str(path),
            path.name,
            str(path.parent),
            kind,
            ext,
            size,
            f"{size} B",
            1.0,
            0,
            0,
        ),
    )


def _add_seed_file(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


# --------------------------------------------------------------------------
# zip_ops: small helpers
# --------------------------------------------------------------------------


def test_archive_lock_is_per_absolute_path(cover_env):
    target = cover_env["library"] / "a.zip"
    lock = zip_ops.archive_lock(str(target))
    assert zip_ops.archive_lock(f"{target}/") is lock
    assert zip_ops.archive_lock(str(cover_env["library"] / "b.zip")) is not lock


def test_file_type_and_transcode_flags():
    assert zip_ops._file_type(".mp4") == "video"
    assert zip_ops._file_type(".jpg") == "image"
    assert zip_ops._file_type(".txt") == "other"
    assert zip_ops._needs_transcode(".mkv", "video") == 1
    assert zip_ops._needs_transcode(".mp4", "video") == 0
    assert zip_ops._needs_transcode(".jpg", "image") == 0


def test_zip_mtime_falls_back_for_a_bad_member_stamp():
    info = zipfile.ZipInfo("x.jpg", date_time=(2026, 13, 40, 25, 61, 61))
    stamp = zip_ops._zip_mtime(info)
    assert abs(stamp - time.time()) < 60
    good = zipfile.ZipInfo("x.jpg", date_time=(2020, 5, 1, 0, 0, 0))
    assert (
        zip_ops._zip_mtime(good)
        == datetime(2020, 5, 1, tzinfo=timezone.utc).timestamp()
    )


def test_update_parent_ignores_an_empty_parent():
    zip_ops._update_parent(None, "")


def test_index_tree_applies_a_real_tree_without_a_plan(cover_env):
    folder = cover_env["library"] / "Tree"
    folder.mkdir()
    (folder / "good.txt").write_bytes(b"hello")
    with writer() as conn, conn:
        count = zip_ops._index_tree(conn, str(folder))
        zip_ops._update_parent(conn, str(folder.parent))
    assert count == 1
    with _db() as conn:
        row = conn.execute(
            "SELECT name, file_count FROM folders WHERE path = ?", (str(folder),)
        ).fetchone()
        file_row = conn.execute(
            "SELECT type, ext FROM files WHERE path = ?", (str(folder / "good.txt"),)
        ).fetchone()
    assert row == ("Tree", 1)
    assert file_row == ("other", ".txt")


def test_gather_tree_survives_stat_failures(cover_env, monkeypatch):
    folder = cover_env["library"] / "Tree"
    (folder / "extra").mkdir(parents=True)
    (folder / "good.txt").write_bytes(b"keep")
    (folder / "bad.jpg").write_bytes(b"listed but its stat will fail")
    extra = folder / "extra"
    real_stat = os.stat

    def flaky(path, *args, **kwargs):
        if os.path.abspath(str(path)) == os.path.abspath(str(extra)):
            raise OSError(errno.EIO, "flaky mount")
        if os.path.basename(str(path)) == "bad.jpg":
            raise OSError(errno.EIO, "flaky member")
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(zip_ops.os, "stat", flaky)
    plan = zip_ops._gather_tree(str(folder))
    assert plan[str(folder)]["names"] == ["good.txt"]
    # The dir stat failed, so the plan keeps a fallback mtime.
    assert isinstance(plan[str(folder)]["mtime"], float)
    assert plan[str(extra)]["rows"] == []


# --------------------------------------------------------------------------
# zip_ops: unpack
# --------------------------------------------------------------------------


def _zip_with_image(path, *, content=b"img" * 20):
    with zipfile.ZipFile(path, "w") as bundle:
        bundle.writestr("one.jpg", content)
        bundle.writestr(zipfile.ZipInfo("sub/"), b"")


def test_unpack_full_run_and_index_rewrite(cover_env):
    library = cover_env["library"]
    album = library / "Album"
    archive = library / "Album.zip"
    _zip_with_image(archive)
    with _db() as conn:
        _add_folder(conn, archive, is_zip=1)
        _add_file(conn, archive / "one.jpg", size=60)
        conn.execute(
            "INSERT INTO favorites (path, created_at) VALUES (?, ?)",
            (f"{archive}/one.jpg", 1.0),
        )
        conn.execute(
            "INSERT INTO visual_frames (path, mtime, size, duration, hashes, "
            "captured_at) VALUES (?,?,?,?,?,?)",
            (f"{archive}/one.jpg", 1.0, 60, 1.0, "[]", 1.0),
        )
        conn.execute(
            "INSERT INTO ignored_duplicates (path1, path2, match_type, created_at) "
            "VALUES (?,?,?,?)",
            (f"{archive}/one.jpg", str(album / "other.jpg"), "content", 1.0),
        )

    result = zip_ops.unpack_archive(str(archive))
    assert result["folder"] == str(album)
    assert result["files"] == 1
    assert (album / "one.jpg").is_file()
    assert (album / "sub").is_dir()
    assert not archive.exists()
    with _db() as conn:
        leftovers = conn.execute(
            "SELECT COUNT(*) FROM favorites WHERE path LIKE ?", (f"{archive}%",)
        ).fetchone()[0]
        frames = conn.execute(
            "SELECT COUNT(*) FROM visual_frames WHERE path LIKE ?", (f"{archive}%",)
        ).fetchone()[0]
        dupes = conn.execute(
            "SELECT COUNT(*) FROM ignored_duplicates WHERE path1 LIKE ?",
            (f"{archive}%",),
        ).fetchone()[0]
    assert leftovers == 0
    assert frames == 0
    assert dupes == 0
    assert not zipfs.cache_dir_for(str(archive)).exists()


def test_unpack_picks_the_next_free_numbered_destination(cover_env):
    library = cover_env["library"]
    archive = library / "Album.zip"
    _zip_with_image(archive)
    (library / "Album").mkdir()
    (library / "Album (1)").mkdir()

    result = zip_ops.unpack_archive(str(archive))
    assert Path(result["folder"]) == library / "Album (2)"
    assert (library / "Album (2)" / "one.jpg").is_file()


def test_unpack_rejects_missing_and_non_zip_targets(cover_env):
    with pytest.raises(writes.WriteError) as caught:
        zip_ops.unpack_archive(str(cover_env["library"] / "nope.zip"))
    assert caught.value.status == 404

    txt = cover_env["library"] / "notes.txt"
    txt.write_text("hi")
    with pytest.raises(writes.WriteError) as caught:
        zip_ops.unpack_archive(str(txt))
    assert caught.value.status == 404


def test_unpack_reports_a_corrupt_archive(cover_env):
    broken = cover_env["library"] / "Broken.zip"
    broken.write_bytes(b"PK\x03\x04not really a zip")
    with pytest.raises(writes.WriteError) as caught:
        zip_ops.unpack_archive(str(broken))
    assert caught.value.status == 500
    assert "Could not unpack" in caught.value.message
    assert list(cover_env["library"].glob(".tmp-*")) == []


def test_unpack_rolls_back_when_the_rename_fails(cover_env, monkeypatch):
    library = cover_env["library"]
    archive = library / "Album.zip"
    _zip_with_image(archive)

    def failed_rename(*args, **kwargs):
        raise OSError(errno.EIO, "nope")

    monkeypatch.setattr(zip_ops.os, "rename", failed_rename)
    with pytest.raises(OSError):
        zip_ops.unpack_archive(str(archive))
    assert archive.is_file()
    assert not (library / "Album").exists()
    assert list(library.glob(".tmp-*")) == []


def test_unpack_tolerates_a_failed_source_unlink(cover_env, monkeypatch):
    library = cover_env["library"]
    archive = library / "Album.zip"
    _zip_with_image(archive)
    real_unlink = Path.unlink

    def stuck_unlink(self, missing_ok=False):
        if self == archive:
            raise OSError(errno.EIO, "held by a process")
        return real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", stuck_unlink)
    zip_ops.unpack_archive(str(archive))
    assert (library / "Album" / "one.jpg").is_file()
    assert archive.is_file()  # The deletion failed, so the archive survives.


# --------------------------------------------------------------------------
# zip_ops: listing helpers
# --------------------------------------------------------------------------


def test_archive_listings(cover_env):
    library = cover_env["library"]
    with _db() as conn:
        _add_folder(conn, library)
        _add_folder(conn, library / "Album.zip", is_zip=1)

    listed = zip_ops.archives()
    assert listed and listed[0]["name"] == "Album.zip"

    usage = zip_ops.cache_info()
    assert usage["files"] == 0
    assert usage["bytes"] == 0
    assert usage["size_formatted"] == "0 B"


def test_listing_helpers_with_a_used_member_cache(cover_env):
    library = cover_env["library"]
    archive = library / "Album.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("one.jpg", b"one" * 100)
    payload = zipfs.extract_member(str(archive), "one.jpg")
    usage = zip_ops.cache_info()
    assert usage["files"] == 1
    assert usage["bytes"] == Path(payload).stat().st_size
    zipfs.purge_cache(str(archive))
    assert zip_ops.cache_info()["files"] == 0


# --------------------------------------------------------------------------
# zipfs
# --------------------------------------------------------------------------


def test_split_and_invalid_member_paths():
    assert zipfs.split_zip_path("/lib/a.zip/sub/x.jpg") == ("/lib/a.zip", "sub/x.jpg")
    assert zipfs.split_zip_path("/lib/a.jpg") is None
    assert zipfs.is_zip_member("/lib/a.zip/x") is True
    assert zipfs.is_zip_member("/lib/a.zip") is False

    for bad in ("", "a/../../b"):
        with pytest.raises(writes.WriteError) as caught:
            zipfs._safe_inner(bad)
        assert caught.value.status == 400
    assert zipfs._safe_inner("\\a\\b\\") == "a/b"


def test_cache_path_helpers(cover_env):
    archive = "/lib/a.zip"
    assert zipfs.cache_dir_for(archive) == zipfs.cache_dir_for(archive)
    assert zipfs.cache_dir_for(archive).parent == cover_env["cache"]
    target = zipfs._cache_target(archive, "photos/img1.jpg")
    assert target.suffix == ".jpg"


def test_extract_member_caches_and_reuses(cover_env):
    library = cover_env["library"]
    archive = library / "album.zip"
    payload = b"photo" * 40
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("sub/photo.jpg", payload)

    first = zipfs.extract_member(str(archive), "sub\\photo.jpg")
    assert Path(first).read_bytes() == payload
    assert zipfs.extract_member(str(archive), "sub/photo.jpg") == first
    assert zipfs.cache_usage() == {"files": 1, "bytes": len(payload)}


def test_extract_member_reports_bad_targets(cover_env):
    library = cover_env["library"]
    archive = library / "album.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("sub/photo.jpg", b"photo")

    with pytest.raises(writes.WriteError) as missing:
        zipfs.extract_member(str(library / "nope.zip"), "sub/photo.jpg")
    assert missing.value.status == 404

    with pytest.raises(writes.WriteError) as no_member:
        zipfs.extract_member(str(archive), "sub/other.jpg")
    assert no_member.value.status == 404

    broken = library / "broken.zip"
    broken.write_bytes(b"PK\x03\x04truncated")
    with pytest.raises(writes.WriteError) as bad:
        zipfs.extract_member(str(broken), "photo.jpg")
    assert bad.value.status == 400
    assert "Broken ZIP" in bad.value.message


def test_extract_member_keeps_a_stale_cache_entry_when_touching_fails(
    cover_env, monkeypatch
):
    library = cover_env["library"]
    archive = library / "album.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("photo.jpg", b"photo")

    first = zipfs.extract_member(str(archive), "photo.jpg")

    def broken_utime(*args, **kwargs):
        raise OSError(errno.EIO, "read-only")

    monkeypatch.setattr(zipfs.os, "utime", broken_utime)
    assert zipfs.extract_member(str(archive), "photo.jpg") == first


def test_extract_member_rejects_an_undersized_extraction(cover_env, monkeypatch):
    library = cover_env["library"]
    archive = library / "album.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("photo.jpg", b"photo" * 40)

    def stunted(src, dst, length=None, **kwargs):
        dst.write(b"x")

    monkeypatch.setattr(zipfs.shutil, "copyfileobj", stunted)
    with pytest.raises(writes.WriteError) as caught:
        zipfs.extract_member(str(archive), "photo.jpg")
    assert caught.value.status == 500
    assert "could not be extracted" in caught.value.message
    assert not any(zipfs.cache_dir_for(str(archive)).iterdir())
    assert list(zipfs.cache_dir_for(str(archive)).parent.glob(".tmp-*")) == []


def test_extract_member_tolerates_a_failing_temp_cleanup(cover_env, monkeypatch):
    library = cover_env["library"]
    archive = library / "album.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("photo.jpg", b"photo")
    real_unlink = Path.unlink

    def stuck_unlink(self, missing_ok=False):
        if self.name.startswith(".tmp-"):
            raise OSError(errno.EIO, "busy")
        return real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", stuck_unlink)
    cached = zipfs.extract_member(str(archive), "photo.jpg")
    assert Path(cached).read_bytes() == b"photo"


def test_trim_cache_skips_an_empty_ceiling(cover_env, monkeypatch):
    group = cover_env["cache"] / "g"
    group.mkdir()
    (group / "data.bin").write_bytes(b"x" * 10)
    monkeypatch.setenv("ZIP_CACHE_MAX_SIZE_GB", "0")
    v2_config.reload_settings()
    zipfs._trim_cache()
    assert (group / "data.bin").is_file()

    # A missing cache root also takes the early return.
    monkeypatch.delenv("ZIP_CACHE_DIR")
    v2_config.reload_settings()
    zipfs._trim_cache()


def test_trim_cache_prunes_the_oldest_groups_first(cover_env, monkeypatch):
    cache = cover_env["cache"]
    for name, stamp in (("g1", 1000), ("g2", 1010), ("g3", 1020)):
        group = cache / name
        group.mkdir()
        (group / "data.bin").write_bytes(b"x" * 600)
        os.utime(group, (stamp, stamp))
    (cache / "stray.txt").write_bytes(b"not a directory")
    (cache / "boom").mkdir()
    (cache / "boom" / "boom.jpg").write_bytes(b"x" * 600)
    (cache / "ghost").mkdir()

    real_stat = Path.stat
    calls: dict[str, int] = {}

    def fake_stat(self, *args, **kwargs):
        if self.name in ("boom.jpg", "ghost"):
            key = str(self)
            calls[key] = calls.get(key, 0) + 1
            if calls[key] > 1:
                raise OSError(errno.EIO, "peekaboo")
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", fake_stat)
    monkeypatch.setenv("ZIP_CACHE_MAX_SIZE_GB", "0.000001")  # ~1073 bytes
    v2_config.reload_settings()
    zipfs._trim_cache()

    assert not (cache / "g1").exists()
    assert not (cache / "g2").exists()
    assert (cache / "g3").is_dir()
    assert (cache / "boom").is_dir()
    assert (cache / "stray.txt").is_file()


def test_extract_archive_skips_dirs_and_symlink_escapes(cover_env, tmp_path):
    library = cover_env["library"]
    archive = library / "album.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr(zipfile.ZipInfo("sub/"), b"")
        bundle.writestr("sub/ok.txt", "keep")
        bundle.writestr("link/esc.txt", "escape")
        bundle.writestr("../escape.txt", "never written")

    destination = tmp_path / "out"
    destination.mkdir()
    (destination / "link").symlink_to(tmp_path)

    count = zipfs.extract_archive(str(archive), str(destination))
    assert count == 1
    assert (destination / "sub" / "ok.txt").is_file()
    assert not (tmp_path / "esc.txt").exists()
    assert not (tmp_path / "escape.txt").exists()


def test_cache_usage_walks_the_cache_safely(cover_env, monkeypatch):
    cache = cover_env["cache"]
    (cache / "stray.txt").write_bytes(b"ab")
    group = cache / "g"
    group.mkdir()
    (group / "a.txt").write_bytes(b"abc")

    real_stat = Path.stat
    calls: dict[str, int] = {}

    def fake_stat(self, *args, **kwargs):
        if self.name == "boom.jpg":
            key = str(self)
            calls[key] = calls.get(key, 0) + 1
            if calls[key] > 1:
                raise OSError(errno.EIO, "peekaboo")
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", fake_stat)
    (group / "boom.jpg").write_bytes(b"zzz")
    assert zipfs.cache_usage() == {"files": 1, "bytes": 3}
