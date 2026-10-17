"""Extra coverage for ZIP compress/optimize service branches.

Targets the branches the existing ZIP tests do not reach: compressibility
edge cases, archive integrity failures, staging cleanup on transfer errors,
sync-back dispatch and cleanup, and the WebP optimizer's skip/error/failure
paths.
"""

import os
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

from media_library.services.zip import compress as zc
from media_library.services.zip import optimize as zo
from media_library.services.zip.core import get_zip_cache_dir, zip_tasks_state


def _make_zip(path: Path, members: dict[str, bytes], with_dir: bool = False):
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        if with_dir:
            zf.writestr("sub/", "")
        for name, data in members.items():
            zf.writestr(name, data)
    return path


# ==============================================================================
# check_folder_compressibility
# ==============================================================================


def test_compressibility_not_a_directory(sandbox_env):
    media = sandbox_env["media_dir"]
    lone = media / "file.jpg"
    lone.write_bytes(b"J" * 10)
    assert zc.check_folder_compressibility(str(lone))["eligible"] is False
    assert zc.check_folder_compressibility(str(media / "ghost"))["eligible"] is False


def test_compressibility_ignores_hidden_and_unreadable(sandbox_env):
    media = sandbox_env["media_dir"]
    folder = media / "mixed"
    folder.mkdir()
    (folder / ".hidden.jpg").write_bytes(b"J" * 10)
    (folder / "visible.jpg").write_bytes(b"J" * 20)
    (folder / "unstable.jpg").write_bytes(b"J" * 30)

    real_stat = os.stat

    def flaky_stat(path, *args, **kwargs):
        if str(path).endswith("unstable.jpg"):
            raise OSError("FUSE stat timeout")
        return real_stat(path, *args, **kwargs)

    with patch("os.stat", flaky_stat):
        res = zc.check_folder_compressibility(str(folder))

    assert res["eligible"] is True
    assert res["image_count"] == 1
    assert res["total_size"] == 20


def test_compressibility_no_images(sandbox_env):
    media = sandbox_env["media_dir"]
    folder = media / "textonly"
    folder.mkdir()
    (folder / "readme.txt").write_bytes(b"hello")
    res = zc.check_folder_compressibility(str(folder))
    assert res["eligible"] is False
    assert "no image files" in res["reason"]


# ==============================================================================
# compress_folder_to_zip
# ==============================================================================


def test_compress_ineligible_folder_raises(sandbox_env):
    media = sandbox_env["media_dir"]
    folder = media / "emptyish"
    folder.mkdir()
    with pytest.raises(ValueError):
        zc.compress_folder_to_zip(str(folder))


def test_compress_success_converts_and_cleans_cache(sandbox_env):
    from PIL import Image

    media = sandbox_env["media_dir"]
    folder = media / "album"
    folder.mkdir()
    Image.new("RGB", (16, 16), color="red").save(folder / "pic.jpg", "JPEG")
    (folder / "notes.txt").write_text("plain text")
    (folder / ".DS_Store").write_bytes(b"junk")

    # Pre-create an extraction cache for the future archive: it must be purged.
    cache_dir = get_zip_cache_dir(str(media / "album.zip"))
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / ".extracted_done").write_text("done")

    # Simulate a read-only staging dir so the temp zip cannot be removed; the
    # success path must swallow that OSError and still report success.
    real_remove = os.remove

    def flaky_remove(path, *args, **kwargs):
        if ".tmp_album_" in str(path):
            raise OSError("staging is read-only")
        return real_remove(path, *args, **kwargs)

    steps = []
    with patch("os.remove", flaky_remove):
        res = zc.compress_folder_to_zip(
            str(folder), progress_cb=lambda cur, tot, name: steps.append(name)
        )

    assert res["success"] is True
    assert Path(res["zip_path"]).name == "album.zip"
    assert not folder.exists()
    assert not cache_dir.exists()

    with zipfile.ZipFile(res["zip_path"]) as zf:
        names = zf.namelist()
        assert "pic.webp" in names
        assert "notes.txt" in names
        assert ".DS_Store" not in names

    assert "pic.jpg" in steps
    task = zip_tasks_state["active_tasks"].get(str(folder))
    assert task["status"] == "completed"


def test_compress_integrity_failure_marks_task_failed(sandbox_env):
    from PIL import Image

    media = sandbox_env["media_dir"]
    folder = media / "corruptme"
    folder.mkdir()
    Image.new("RGB", (16, 16), color="blue").save(folder / "pic.jpg", "JPEG")

    def corrupted_testzip(self):
        return "pic.webp"

    with (
        patch.object(zipfile.ZipFile, "testzip", corrupted_testzip),
        pytest.raises(RuntimeError, match="integrity check failed"),
    ):
        zc.compress_folder_to_zip(str(folder))

    assert folder.exists()
    task = zip_tasks_state["active_tasks"].get(str(folder))
    assert task["status"] == "failed"


def test_compress_transfer_failure_cleans_staging(sandbox_env):
    from PIL import Image

    media = sandbox_env["media_dir"]
    folder = media / "xferfail"
    folder.mkdir()
    Image.new("RGB", (16, 16), color="green").save(folder / "pic.jpg", "JPEG")

    def broken_copyfile(src, dst, *args, **kwargs):
        raise OSError("remote mount went away")

    with (
        patch("media_library.services.zip.compress.shutil.copyfile", broken_copyfile),
        pytest.raises(OSError),
    ):
        zc.compress_folder_to_zip(str(folder))

    staging = Path(os.environ["ZIP_STAGING_DIR"])
    leftovers = [p.name for p in staging.iterdir() if "xferfail" in p.name]
    assert leftovers == []
    assert folder.exists()

    task = zip_tasks_state["active_tasks"].get(str(folder))
    assert task["status"] == "failed"
    assert "remote mount went away" in task["error"]


# ==============================================================================
# _execute_sync_back / _execute_sync_back_impl
# ==============================================================================


def _seed_cache(zip_path: Path) -> Path:
    zip_path.write_bytes(b"PK\x05\x06" + b"\x00" * 18)
    cache_dir = get_zip_cache_dir(str(zip_path))
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / ".extracted_done").write_text("done")
    (cache_dir / "inner.txt").write_bytes(b"synced content")
    return cache_dir


def test_execute_sync_back_dispatches_to_impl(sandbox_env):
    media = sandbox_env["media_dir"]
    zip_path = media / "dispatch.zip"
    _seed_cache(zip_path)

    zc._execute_sync_back(str(zip_path))

    with zipfile.ZipFile(zip_path) as zf:
        assert zf.namelist() == ["inner.txt"]
    task = zip_tasks_state["active_tasks"].get(str(zip_path))
    assert task["status"] == "completed"


def test_execute_sync_back_skips_when_cache_or_zip_missing(sandbox_env):
    media = sandbox_env["media_dir"]
    zip_path = media / "vanished.zip"
    zip_path.write_bytes(b"PK\x05\x06" + b"\x00" * 18)
    # No cache dir: the sync must be a no-op.
    zc._execute_sync_back(str(zip_path))
    assert str(zip_path) not in zip_tasks_state["active_tasks"]

    # Cache exists but the archive is gone: still a no-op.
    _seed_cache(zip_path)
    zip_path.unlink()
    zc._execute_sync_back(str(zip_path))
    assert str(zip_path) not in zip_tasks_state["active_tasks"]


def test_sync_back_transfer_failure_cleans_temp(sandbox_env):
    media = sandbox_env["media_dir"]
    zip_path = media / "syncfail.zip"
    _seed_cache(zip_path)

    def broken_copyfile(src, dst, *args, **kwargs):
        raise OSError("sync copy failed")

    with patch("media_library.services.zip.compress.shutil.copyfile", broken_copyfile):
        # The impl swallows the failure after marking the task failed.
        zc._execute_sync_back_impl(str(zip_path))

    staging = Path(os.environ["ZIP_STAGING_DIR"])
    assert [p.name for p in staging.iterdir() if "syncfail" in p.name] == []
    task = zip_tasks_state["active_tasks"].get(str(zip_path))
    assert task["status"] == "failed"
    assert "sync copy failed" in task["error"]


def test_sync_back_integrity_failure_marks_task_failed(sandbox_env):
    media = sandbox_env["media_dir"]
    zip_path = media / "synccorrupt.zip"
    _seed_cache(zip_path)

    def corrupted_testzip(self):
        return "inner.txt"

    with patch.object(zipfile.ZipFile, "testzip", corrupted_testzip):
        # The integrity failure is swallowed after the task is marked failed.
        zc._execute_sync_back_impl(str(zip_path))

    task = zip_tasks_state["active_tasks"].get(str(zip_path))
    assert task["status"] == "failed"


# ==============================================================================
# optimize_zip_images_to_webp
# ==============================================================================


def _jpg_bytes(size=(12, 12), color="red") -> bytes:
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", size, color=color).save(buf, "JPEG")
    return buf.getvalue()


def test_optimize_invalid_path(sandbox_env):
    media = sandbox_env["media_dir"]
    res = zo.optimize_zip_images_to_webp(str(media / "ghost.zip"))
    assert res["status"] == "error"
    assert "Invalid zip path" in res["error"]

    not_a_zip = media / "album.rar"
    not_a_zip.write_bytes(b"not a zip")
    assert zo.optimize_zip_images_to_webp(str(not_a_zip))["status"] == "error"


def test_optimize_already_webp_archive(sandbox_env):
    media = sandbox_env["media_dir"]
    zip_path = media / "native.zip"
    _make_zip(zip_path, {"a.webp": b"webp", "b.webp": b"webp"})

    res = zo.optimize_zip_images_to_webp(str(zip_path))
    assert res["status"] == "already_webp"
    assert res["converted"] == 0
    task = zip_tasks_state["active_tasks"].get(str(zip_path))
    assert task["status"] == "completed"


def test_optimize_converts_images_and_keeps_structure(sandbox_env):
    media = sandbox_env["media_dir"]
    zip_path = media / "mixed.zip"
    _make_zip(zip_path, {"pic.jpg": _jpg_bytes(), "clip.txt": b"b" * 64}, with_dir=True)

    res = zo.optimize_zip_images_to_webp(str(zip_path))

    assert res["status"] == "converted"
    assert res["converted_count"] == 1
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        assert "pic.webp" in names
        assert "clip.txt" in names
        assert "sub/" in names
        assert zf.read("clip.txt") == b"b" * 64


def test_optimize_skips_larger_when_configured(sandbox_env):
    media = sandbox_env["media_dir"]
    zip_path = media / "bigger.zip"
    _make_zip(zip_path, {"pic.jpg": _jpg_bytes()})

    # Pretend the WebP encoding came out larger than the original member.
    def oversized_webp(data, quality=None):
        return b"w" * (len(data) + 1024)

    with (
        patch.object(zo, "WEBP_SKIP_LARGER", True),
        patch(
            "media_library.services.ffmpeg_service.convert_image_to_webp_bytes",
            oversized_webp,
        ),
    ):
        res = zo.optimize_zip_images_to_webp(str(zip_path))

    assert res["status"] == "converted"
    assert res["skipped_count"] == 1
    assert res["converted_count"] == 0
    with zipfile.ZipFile(zip_path) as zf:
        assert "pic.jpg" in zf.namelist()


def test_optimize_keeps_member_when_conversion_fails(sandbox_env):
    media = sandbox_env["media_dir"]
    zip_path = media / "badconv.zip"
    raw = _jpg_bytes()
    _make_zip(zip_path, {"bad.jpg": raw})

    def broken_convert(data, quality=None):
        raise ValueError("cannot decode")

    with patch(
        "media_library.services.ffmpeg_service.convert_image_to_webp_bytes",
        broken_convert,
    ):
        res = zo.optimize_zip_images_to_webp(str(zip_path))

    assert res["status"] == "converted"
    assert res["converted_count"] == 0
    with zipfile.ZipFile(zip_path) as zf:
        assert zf.read("bad.jpg") == raw


def test_optimize_verification_failure_marks_task_failed(sandbox_env):
    media = sandbox_env["media_dir"]
    zip_path = media / "verify.zip"
    _make_zip(zip_path, {"pic.jpg": _jpg_bytes()})

    def corrupted_testzip(self):
        return "pic.webp"

    with (
        patch.object(zipfile.ZipFile, "testzip", corrupted_testzip),
        pytest.raises(RuntimeError, match="verification failed"),
    ):
        zo.optimize_zip_images_to_webp(str(zip_path))

    task = zip_tasks_state["active_tasks"].get(str(zip_path))
    assert task["status"] == "failed"


def test_optimize_stats_failure_is_swallowed(sandbox_env):
    media = sandbox_env["media_dir"]
    zip_path = media / "stats.zip"
    _make_zip(zip_path, {"pic.jpg": _jpg_bytes()})

    with patch(
        "media_library.services.zip.optimize.db_conn",
        side_effect=RuntimeError("db down"),
    ):
        res = zo.optimize_zip_images_to_webp(str(zip_path))

    assert res["status"] == "converted"
    task = zip_tasks_state["active_tasks"].get(str(zip_path))
    assert task["status"] == "completed"


def test_optimize_transfer_failure_cleans_staging(sandbox_env):
    media = sandbox_env["media_dir"]
    zip_path = media / "optxfer.zip"
    _make_zip(zip_path, {"pic.jpg": _jpg_bytes()})

    def broken_copyfile(src, dst, *args, **kwargs):
        raise OSError("remote mount went away")

    with (
        patch("media_library.services.zip.optimize.shutil.copyfile", broken_copyfile),
        pytest.raises(OSError),
    ):
        zo.optimize_zip_images_to_webp(str(zip_path))

    staging = Path(os.environ["ZIP_STAGING_DIR"])
    assert [
        p.name for p in staging.iterdir() if "webp_opt" in p.name or "webp_dl" in p.name
    ] == []
    task = zip_tasks_state["active_tasks"].get(str(zip_path))
    assert task["status"] == "failed"
    assert "remote mount went away" in task["error"]
