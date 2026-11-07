"""Line-coverage sweep for the ZIP modules (zip_ops, zipfs, zip_webp).

Happy paths that the dedicated suites already exercise are repeated here with
the cheapest real archives possible, because this file is meant to run on its
own; failure branches are built with real truncated archives where that works
and with small monkeypatched seams (a copy that truncates, a rename that
fails) where the failure is a race or a synthetic I/O fault.
"""

from __future__ import annotations

import errno
import io
import os
import sqlite3
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException
from PIL import Image

from media_library import config as v2_config
from media_library import writes, zip_ops, zip_webp, zipfs
from media_library.db import writer


class _FixedUuid:
    """Stub uuid so a staging path can be pre-created (as a directory)."""

    hex = "00000000deadbeef00000000deadbeef"


@pytest.fixture
def cover_env(tmp_path, monkeypatch):
    """Library root, ZIP staging and ZIP cache pointed at the test tree."""
    library = tmp_path / "library"
    library.mkdir()
    staging = tmp_path / "zip_staging"
    staging.mkdir()
    cache = tmp_path / "zip_cache"
    cache.mkdir()
    monkeypatch.setenv("LIBRARY_DIR", str(library))
    monkeypatch.setenv("THUMBNAIL_DIR", str(tmp_path / "thumbs"))
    monkeypatch.setenv("ZIP_STAGING_DIR", str(staging))
    monkeypatch.setenv("ZIP_CACHE_DIR", str(cache))
    monkeypatch.setenv("DB_READ_ONLY", "false")
    monkeypatch.setenv("WRITES", "true")
    v2_config.reload_settings()
    try:
        yield {"library": library, "staging": staging, "cache": cache}
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


def _seed_album(env, *, on_disk=True, name="Album"):
    """An image-only folder: rows in the index and (optionally) on disk."""
    album = env["library"] / name
    if on_disk:
        album.mkdir(parents=True)
        (album / "one.jpg").write_bytes(b"one" * 100)
        _add_seed_file(album / "sub" / "two.png", b"two" * 50)
    with _db() as conn:
        _add_folder(conn, album)
        _add_file(conn, album / "one.jpg")
        _add_file(conn, album / "sub" / "two.png" if on_disk else album / "two.png")
    return album


def _add_seed_file(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def _png_bytes(size: int = 24) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (size, size), (5, 25, 125)).save(buffer, "PNG")
    return buffer.getvalue()


def _jpg_bytes(size: int = 24) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (size, size), (120, 180, 60)).save(buffer, "JPEG", quality=90)
    return buffer.getvalue()


def _la_bytes(size: int = 16) -> bytes:
    buffer = io.BytesIO()
    Image.new("LA", (size, size), (90, 255)).save(buffer, "PNG")
    return buffer.getvalue()


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
# zip_ops: check_folder
# --------------------------------------------------------------------------


def test_check_folder_refuses_missing_and_library_root(cover_env):
    with pytest.raises(writes.WriteError) as missing:
        zip_ops.check_folder(str(cover_env["library"] / "Missing"))
    assert missing.value.status == 404

    with pytest.raises(writes.WriteError) as root:
        zip_ops.check_folder("")
    assert root.value.status == 400
    assert "library root" in root.value.message


def test_check_folder_reports_all_rejections(cover_env):
    album = _seed_album(cover_env)
    with _db() as conn:
        _add_folder(conn, album / "inner.zip", is_zip=1)
        _add_folder(conn, album / "sub")
    checked = zip_ops.check_folder(str(album))
    assert checked["eligible"] is False
    assert "ZIP archives" in checked["reason"]

    plain = cover_env["library"] / "Plain"
    plain.mkdir()
    (plain / "readme.txt").write_text("hi")
    with _db() as conn:
        _add_folder(conn, plain)
        _add_file(conn, plain / "readme.txt", kind="other", ext=".txt", size=2)
    checked = zip_ops.check_folder(str(plain))
    assert checked["eligible"] is False
    assert "No images" in checked["reason"]

    collision = cover_env["library"] / "CollFolder"
    collision.mkdir()
    (cover_env["library"] / "CollFolder.zip").write_bytes(b"x")
    with _db() as conn:
        _add_folder(conn, collision)
        _add_file(conn, collision / "pic.jpg")
    checked = zip_ops.check_folder(str(collision))
    assert "already exists" in checked["reason"]


def test_check_folder_eligible_album(cover_env):
    album = _seed_album(cover_env, on_disk=False, name="JustIndex")
    album.mkdir()
    checked = zip_ops.check_folder(str(album))
    assert checked == {
        "path": str(album),
        "eligible": True,
        "images": 2,
        "videos": 0,
        "target": str(album) + ".zip",
        "reason": "",
    }


# --------------------------------------------------------------------------
# zip_ops: compress
# --------------------------------------------------------------------------


def test_compress_full_run_and_index_rewrite(cover_env):
    album = _seed_album(cover_env)
    (album / ".DS_Store").write_bytes(b"junk")
    with _db() as conn:
        _add_folder(conn, cover_env["library"])
        _add_file(conn, album / "ignored.jpg")  # stale index row, no on-disk file
        conn.execute(
            "INSERT INTO favorites (path, created_at) VALUES (?, ?)",
            (str(album / "one.jpg"), 1.0),
        )

    result = zip_ops.compress_folder(str(album))
    archive = cover_env["library"] / "Album.zip"
    assert result["archive"] == str(archive)
    assert result["members"] == 2
    with zipfile.ZipFile(archive) as bundle:
        assert sorted(bundle.namelist()) == ["one.jpg", "sub/two.png"]
        assert bundle.testzip() is None
    assert not album.exists()
    with _db() as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM favorites WHERE path LIKE ?",
                (str(album) + "%",),
            ).fetchone()[0]
            == 0
        )
        parent = conn.execute(
            "SELECT subfolder_count FROM folders WHERE path = ?",
            (str(cover_env["library"]),),
        ).fetchone()
    # The new Album.zip archive folder is the library root's only subfolder.
    assert parent[0] == 1


def test_compress_refuses_when_the_disk_has_no_files(cover_env):
    album = _seed_album(cover_env, on_disk=False)
    album.mkdir()
    with pytest.raises(writes.WriteError) as caught:
        zip_ops.compress_folder(str(album))
    assert caught.value.status == 400
    assert "no files to archive" in caught.value.message
    assert album.is_dir()


def test_compress_rejects_a_failing_verification(cover_env, monkeypatch):
    album = _seed_album(cover_env)
    monkeypatch.setattr(zipfile.ZipFile, "testzip", lambda self, *a, **k: "one.jpg")
    with pytest.raises(writes.WriteError) as caught:
        zip_ops.compress_folder(str(album))
    assert caught.value.status == 500
    assert "verification failed at one.jpg" in caught.value.message
    assert album.is_dir()
    assert not (cover_env["library"] / "Album.zip").exists()


def test_compress_detects_a_truncated_copy(cover_env, monkeypatch):
    album = _seed_album(cover_env)

    def half_copy(src, dst):
        Path(dst).write_bytes(Path(src).read_bytes()[:4])

    monkeypatch.setattr(zip_ops.shutil, "copyfile", half_copy)
    with pytest.raises(writes.WriteError) as caught:
        zip_ops.compress_folder(str(album))
    assert caught.value.status == 500
    assert "truncated" in caught.value.message
    assert not (cover_env["library"] / "Album.zip").exists()


def test_compress_survives_a_failed_staging_write(cover_env, monkeypatch):
    album = _seed_album(cover_env)
    monkeypatch.setattr(zip_ops.uuid, "uuid4", lambda: _FixedUuid())
    (cover_env["staging"] / f"Album-{_FixedUuid.hex}.zip").mkdir()
    with pytest.raises(writes.WriteError) as caught:
        zip_ops.compress_folder(str(album))
    assert "Could not create the archive" in caught.value.message
    # The staging-directory cleanup swallowed its failure too.
    assert (cover_env["staging"] / f"Album-{_FixedUuid.hex}.zip").is_dir()


def test_compress_refuses_video_folders(cover_env):
    library = cover_env["library"]
    folder = library / "VideoFolder"
    folder.mkdir()
    (folder / "clip.mkv").write_bytes(b"clip")
    with _db() as conn:
        _add_folder(conn, folder)
        _add_file(conn, folder / "clip.mkv", kind="video", ext=".mkv", size=50)
        _add_file(conn, folder / "pic.jpg", size=300)

    checked = zip_ops.check_folder(str(folder))
    assert checked["eligible"] is False
    assert "Contains videos" in checked["reason"]

    with pytest.raises(writes.WriteError) as caught:
        zip_ops.compress_folder(str(folder))
    assert caught.value.status == 409
    assert folder.is_dir()


def test_compress_rolls_back_when_the_source_cannot_go(cover_env, monkeypatch):
    album = _seed_album(cover_env)
    target = cover_env["library"] / "Album.zip"

    def broken_rmtree(*args, **kwargs):
        raise OSError(errno.EIO, "nope")

    real_unlink = Path.unlink

    def stuck_unlink(self, missing_ok=False):
        if self == target:
            raise OSError(errno.EIO, "held")
        return real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(zip_ops.shutil, "rmtree", broken_rmtree)
    monkeypatch.setattr(Path, "unlink", stuck_unlink)
    with pytest.raises(writes.WriteError) as caught:
        zip_ops.compress_folder(str(album))
    assert caught.value.status == 500
    assert "Could not remove the source folder" in caught.value.message
    assert album.is_dir()
    # The rollback itself failed too, so the archive survives.
    assert target.is_file()


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


def test_archive_candidates_and_listings(cover_env):
    library = cover_env["library"]
    _seed_album(cover_env, on_disk=False)
    rich = library / "Rich"
    rich.mkdir()
    (rich / "a.jpg").write_bytes(b"a" * 50)
    (rich / "b.jpg").write_bytes(b"b" * 250)
    dotted = library / "Dotted"
    dotted.mkdir()
    _add_seed_file(dotted / ".hidden.jpg", b"h" * 9)
    with _db() as conn:
        _add_folder(conn, library)
        _add_folder(conn, rich)
        _add_file(conn, rich / "a.jpg", size=50)
        _add_file(conn, rich / "b.jpg", size=250)
        _add_folder(conn, dotted)
        _add_file(conn, dotted / ".hidden.jpg", size=9)
        conn.execute(
            "UPDATE folders SET total_size = 300, file_count = 2 WHERE path = ?",
            (str(rich),),
        )
        _add_folder(conn, library / "Album.zip", is_zip=1)

    candidates = zip_ops.archive_candidates(str(library))
    assert [item["name"] for item in candidates] == ["Rich", "Album"]
    assert candidates[0]["size_formatted"].endswith("B")
    assert candidates[0]["file_count"] == 2

    listed = zip_ops.archives()
    assert listed and listed[0]["name"] == "Album.zip"

    usage = zip_ops.cache_info()
    assert usage["files"] == 0
    assert usage["bytes"] == 0
    assert usage["size_formatted"] == "0 B"


def test_listing_helpers_with_a_used_member_cache(cover_env):
    album = _seed_album(cover_env)
    result = zip_ops.compress_folder(str(album))
    archive = result["archive"]
    payload = zipfs.extract_member(archive, "one.jpg")
    usage = zip_ops.cache_info()
    assert usage["files"] == 1
    assert usage["bytes"] == Path(payload).stat().st_size
    zipfs.purge_cache(archive)
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


# --------------------------------------------------------------------------
# zip_webp
# --------------------------------------------------------------------------


def test_webp_bytes_stream_size_guard(cover_env, monkeypatch):
    monkeypatch.setattr(zip_webp, "_MAX_AXIS", 10)
    assert zip_webp._webp_bytes_stream(io.BytesIO(_png_bytes()), 85) is None
    monkeypatch.setattr(zip_webp, "_MAX_CONVERT_PIXELS", 1)
    assert zip_webp._webp_bytes_stream(io.BytesIO(_jpg_bytes()), 85) is None


def test_webp_bytes_stream_converts_rgb_png_and_jpeg():
    webp = zip_webp._webp_bytes_stream(io.BytesIO(_png_bytes()), 85)
    with Image.open(io.BytesIO(webp)) as image:
        assert image.format == "WEBP"
        assert image.mode == "RGB"
    webp = zip_webp._webp_bytes_stream(io.BytesIO(_jpg_bytes()), 85)
    with Image.open(io.BytesIO(webp)) as image:
        assert image.format == "WEBP"


def test_unique_member_counter_gives_up_after_collisions():
    assert zip_webp._unique_member("a.webp", set()) == "a.webp"
    assert zip_webp._unique_member("a.webp", {"a.webp"}) == "a (1).webp"
    assert zip_webp._unique_member("a.webp", {"a.webp", "a (1).webp"}) == "a (2).webp"


def test_optimize_rewrites_the_archive_and_the_index(cover_env):
    library = cover_env["library"]
    archive = library / "album.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr(zipfile.ZipInfo("sub/"), b"")
        bundle.writestr("Thumbs.db", b"junk")
        bundle.writestr("coll.webp", b"not a webp, just a name collision")
        bundle.writestr("coll.jpg", _jpg_bytes())
        bundle.writestr("la.png", _la_bytes())
        bundle.writestr("rgb.png", _png_bytes())
        bundle.writestr("notes.txt", "keep")
    cache_dir = zipfs.cache_dir_for(str(archive))
    cache_dir.mkdir(parents=True)
    (cache_dir / "member.jpg").write_bytes(b"stale")

    result = zip_webp.optimize_archive(str(archive))
    assert result["changed"] is True
    assert result["members"] == 7
    with zipfile.ZipFile(archive) as bundle:
        names = set(bundle.namelist())
    assert names == {
        "sub/",
        "Thumbs.db",
        "coll.webp",
        "coll (1).webp",
        "la.webp",
        "rgb.webp",
        "notes.txt",
    }
    with _db() as conn:
        folder = conn.execute(
            "SELECT is_zip, file_count, name FROM folders WHERE path = ?",
            (str(archive),),
        ).fetchone()
        stats = conn.execute(
            "SELECT converted_count, skipped_count, saved_bytes FROM webp_stats "
            "WHERE id = 1"
        ).fetchone()
    # The dir entry and the Thumbs.db member are not indexed.
    assert folder == (1, 5, "album.zip")
    assert stats[0] == 3
    assert stats[1] == 0
    assert stats[2] == result["saved_bytes"]
    assert not cache_dir.exists()


def test_optimize_copies_undecodable_members_through(cover_env):
    library = cover_env["library"]
    archive = library / "broken.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("broken.jpg", b"definitely not an image")
        bundle.writestr("notes.txt", "keep")

    result = zip_webp.optimize_archive(str(archive))
    assert result["changed"] is False
    assert result["converted"] == 0
    assert result["skipped"] == 0
    with zipfile.ZipFile(archive) as bundle:
        assert bundle.read("broken.jpg") == b"definitely not an image"
        assert bundle.read("notes.txt") == b"keep"


def test_optimize_copies_through_undecodable_sizes(cover_env, monkeypatch):
    library = cover_env["library"]
    archive = library / "album.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("rgb.png", _png_bytes())
        bundle.writestr("notes.jpg", _jpg_bytes())
    before = archive.read_bytes()
    monkeypatch.setattr(zip_webp, "_webp_bytes_stream", lambda member, quality: None)

    result = zip_webp.optimize_archive(str(archive))
    assert result["changed"] is False
    assert result["skipped"] == 2
    assert archive.read_bytes() == before


def test_optimize_skips_members_that_would_grow(cover_env, monkeypatch):
    library = cover_env["library"]
    archive = library / "album.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("rgb.png", _png_bytes())
        bundle.writestr("notes.jpg", _jpg_bytes())
    before = archive.read_bytes()
    monkeypatch.setattr(
        zip_webp, "_webp_bytes_stream", lambda member, quality: b"w" * 4096
    )

    result = zip_webp.optimize_archive(str(archive), skip_larger=True)
    assert result["changed"] is False
    assert result["skipped"] == 2
    assert archive.read_bytes() == before


def test_optimize_fails_the_verification_check(cover_env, monkeypatch):
    library = cover_env["library"]
    archive = library / "album.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("rgb.png", _png_bytes())
    monkeypatch.setattr(zipfile.ZipFile, "testzip", lambda self, *a, **k: "rgb.png")

    with pytest.raises(writes.WriteError) as caught:
        zip_webp.optimize_archive(str(archive))
    assert caught.value.status == 500
    assert "failed verification at rgb.png" in caught.value.message
    assert list(cover_env["staging"].glob("webp-*")) == []


def test_optimize_detects_a_truncated_copy(cover_env, monkeypatch):
    library = cover_env["library"]
    archive = library / "album.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("rgb.png", _png_bytes())

    def half_copy(src, dst):
        Path(dst).write_bytes(Path(src).read_bytes()[:4])

    monkeypatch.setattr(zip_webp.shutil, "copyfile", half_copy)
    with pytest.raises(writes.WriteError) as caught:
        zip_webp.optimize_archive(str(archive))
    assert caught.value.status == 500
    assert "truncated" in caught.value.message
    with zipfile.ZipFile(archive) as bundle:
        assert bundle.read("rgb.png") == _png_bytes()


def test_optimize_survives_a_failed_staging_write(cover_env, monkeypatch):
    library = cover_env["library"]
    archive = library / "album.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("notes.txt", "plain text")
    monkeypatch.setattr(zip_webp.uuid, "uuid4", lambda: _FixedUuid())
    (cover_env["staging"] / f"webp-{_FixedUuid.hex}.zip").mkdir()

    with pytest.raises(OSError):
        zip_webp.optimize_archive(str(archive))
    # The finally-cleanup failure was swallowed.
    assert (cover_env["staging"] / f"webp-{_FixedUuid.hex}.zip").is_dir()


def test_optimize_rejects_missing_and_outside_archives(cover_env):
    with pytest.raises(writes.WriteError) as missing:
        zip_webp.optimize_archive(str(cover_env["library"] / "nope.zip"))
    assert missing.value.status == 404

    with pytest.raises(HTTPException) as outside:
        zip_webp.optimize_archive("/etc/foo.zip")
    assert outside.value.status_code == 403
