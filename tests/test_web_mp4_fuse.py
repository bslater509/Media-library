"""rclone's EINVAL rename flags must not block every verified replacement."""

from __future__ import annotations

import asyncio
import errno
import json
import subprocess
from unittest.mock import Mock

import pytest
from test_web_mp4 import indexed, info, staged

from media_library import web_mp4


@pytest.fixture
def rclone_rename(monkeypatch):
    class Libc:
        def renameat2(self, *args):
            web_mp4.ctypes.set_errno(errno.EINVAL)
            return -1

    monkeypatch.setattr(web_mp4.ctypes, "CDLL", lambda *a, **k: Libc())


def test_rclone_fallback_installs_verified_output(rclone_rename, tmp_path):
    source = tmp_path / "encoded"
    target = tmp_path / "final.mp4"
    source.write_bytes(b"verified output")
    web_mp4._install(str(source), str(target))
    assert target.read_bytes() == b"verified output" and not source.exists()


def test_rclone_fallback_refuses_an_existing_file(rclone_rename, tmp_path):
    source = tmp_path / "encoded"
    target = tmp_path / "final.mp4"
    source.write_bytes(b"verified output")
    target.write_bytes(b"unrelated video")
    with pytest.raises(FileExistsError):
        web_mp4._install(str(source), str(target))
    assert target.read_bytes() == b"unrelated video"
    assert source.read_bytes() == b"verified output"


def test_native_permission_errors_are_not_masked(monkeypatch, tmp_path):
    class Libc:
        def renameat2(self, *args):
            web_mp4.ctypes.set_errno(errno.EACCES)
            return -1

    monkeypatch.setattr(web_mp4.ctypes, "CDLL", lambda *a, **k: Libc())
    with pytest.raises(PermissionError):
        web_mp4._install(str(tmp_path / "source"), str(tmp_path / "target"))


def test_portable_link_failure_uses_reservation(monkeypatch, tmp_path):
    source = tmp_path / "encoded"
    target = tmp_path / "final.mp4"
    source.write_bytes(b"verified output")
    monkeypatch.setattr(web_mp4.ctypes, "CDLL", lambda *a, **k: object())

    def unsupported(*args):
        raise OSError(errno.EOPNOTSUPP, "No hard links")

    monkeypatch.setattr(web_mp4.os, "link", unsupported)
    web_mp4._install(str(source), str(target))
    assert target.read_bytes() == b"verified output"


def test_portable_link_collision_is_not_masked(monkeypatch, tmp_path):
    monkeypatch.setattr(web_mp4.ctypes, "CDLL", lambda *a, **k: object())

    def conflict(*args):
        raise FileExistsError(errno.EEXIST, "Already exists")

    monkeypatch.setattr(web_mp4.os, "link", conflict)
    with pytest.raises(FileExistsError):
        web_mp4._install(str(tmp_path / "source"), str(tmp_path / "target"))


def test_failed_install_removes_only_its_own_reservation(
    rclone_rename, monkeypatch, tmp_path
):
    source = tmp_path / "encoded"
    target = tmp_path / "final.mp4"
    source.write_bytes(b"verified output")

    def fail(*args):
        raise OSError("Mount rename failed")

    monkeypatch.setattr(web_mp4.os, "replace", fail)
    with pytest.raises(OSError, match="Mount rename failed"):
        web_mp4._install(str(source), str(target))
    assert not target.exists() and source.read_bytes() == b"verified output"
    assert not web_mp4._reservation_matches(str(target), b"token")


def test_changed_reservation_is_never_deleted(rclone_rename, monkeypatch, tmp_path):
    source = tmp_path / "encoded"
    target = tmp_path / "final.mp4"
    source.write_bytes(b"verified output")
    original = web_mp4._reservation_matches

    def changed(path, token):
        target.write_bytes(b"unrelated concurrent file")
        return original(path, token)

    monkeypatch.setattr(web_mp4, "_reservation_matches", changed)
    with pytest.raises(web_mp4.writes.WriteError, match="destination changed"):
        web_mp4._install(str(source), str(target))
    assert target.read_bytes() == b"unrelated concurrent file"
    assert source.read_bytes() == b"verified output"


@pytest.mark.parametrize("same_path", [False, True])
def test_rclone_commit_updates_index_or_restores_original(
    rclone_rename, monkeypatch, same_path
):
    source = indexed("video.mp4" if same_path else "video.mkv")
    temp = staged(source)

    def fail(*args):
        raise RuntimeError("Index failed")

    monkeypatch.setattr(web_mp4.writes, "recompute_folder", fail)
    with pytest.raises(RuntimeError, match="Index failed"):
        web_mp4.commit(
            str(source),
            str(source.with_suffix(".mp4")),
            str(temp),
            web_mp4._fingerprint(source),
            info(),
        )
    assert source.read_bytes() == b"original"
    assert not list(source.parent.glob(".mllibtmp-*.original"))


def test_delayed_video_retries_with_a_longer_probe_window(monkeypatch):
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        video = {**info()["video"], "codec_type": "video"}
        if "-analyzeduration" not in args:
            video["pix_fmt"] = None
        payload = {
            "streams": [video, {"codec_type": "audio", **info()["audio"][0]}],
            "format": {"duration": "137.47", "format_name": "mov,mp4"},
        }
        return subprocess.CompletedProcess(args, 0, json.dumps(payload).encode())

    monkeypatch.setattr(subprocess, "run", run)
    result = web_mp4.probe("delayed.mp4")
    assert result["video"]["pix_fmt"] == "yuv420p"
    assert web_mp4.compatible(result)
    assert (
        len(calls) == 2
        and calls[1][calls[1].index("-analyzeduration") + 1] == "20000000"
    )


def test_deep_probe_is_bounded_even_when_pixel_format_stays_unknown(monkeypatch):
    payload = {
        "streams": [{"codec_type": "video", **info()["video"], "pix_fmt": None}],
        "format": {"duration": "2"},
    }
    mocked = Mock(
        return_value=subprocess.CompletedProcess([], 0, json.dumps(payload).encode())
    )
    monkeypatch.setattr(subprocess, "run", mocked)
    assert web_mp4.probe("unknown.mp4")["video"]["pix_fmt"] is None
    assert mocked.call_count == 2


def test_old_unknown_pixel_format_cache_is_rechecked(monkeypatch):
    source = indexed("delayed.mp4")

    async def run():
        manager = web_mp4.WebMP4Manager()
        stale = info()
        stale["video"]["pix_fmt"] = None
        manager._cache[str(source)] = {
            "fingerprint": list(web_mp4._fingerprint(str(source))),
            "info": stale,
        }
        mocked = Mock(return_value=info())
        monkeypatch.setattr(web_mp4, "probe", mocked)
        result = await manager._check(str(source))
        assert not result["cached"] and web_mp4.compatible(result["info"])
        mocked.assert_called_once()

    asyncio.run(run())
