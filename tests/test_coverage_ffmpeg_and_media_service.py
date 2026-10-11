"""Deep coverage tests for ffmpeg_service.py and media_service.py."""

import asyncio
import io
import os
import shutil
import tempfile
import threading
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from PIL import Image

from media_library.core.state import state
from media_library.db.connection import db_conn
from media_library.services.ffmpeg_service import (
    _parse_exif_capture_time,
    _parse_fraction,
    _parse_iso_time,
    build_live_transcode_command,
    convert_image_file_to_webp,
    convert_image_to_webp_bytes,
    extract_candidate_frame,
    extract_frame_poster,
    format_eta_str,
    generate_thumbnail,
    get_candidate_cache_path,
    get_thumbnail_path_fast,
    probe_media_metadata,
    relay_process_stdout,
)
from media_library.services.media_service import (
    do_transfer,
    resolve_video_path,
    scan_videos,
    search_backup_index,
    validate_backup_path,
)


def test_format_eta_str():
    assert format_eta_str(-1.0) == "0s"
    assert format_eta_str(0.0) == "0s"
    assert format_eta_str(45.2) == "45s"
    assert format_eta_str(125.0) == "2m 5s"
    assert format_eta_str(3665.0) == "1h 1m"


def test_build_live_transcode_command():
    cmd1 = build_live_transcode_command("/path/to/vid.mkv", ss=0.0)
    assert "-ss" not in cmd1
    assert "/path/to/vid.mkv" in cmd1

    cmd2 = build_live_transcode_command("/path/to/vid.mkv", ss=15.5)
    assert "-ss" in cmd2
    idx = cmd2.index("-ss")
    assert cmd2[idx + 1] == "15.5"


def test_parse_fraction_and_iso_time():
    assert round(_parse_fraction("30000/1001"), 2) == 29.97
    assert _parse_fraction("25/1") == 25.0
    assert _parse_fraction("24") == 24.0
    assert _parse_fraction(0) == 0.0
    assert _parse_fraction("invalid") == 0.0
    assert _parse_fraction(None) == 0.0
    assert _parse_fraction("10/0") == 0.0

    t = _parse_iso_time("2023-01-01T12:00:00Z")
    assert t is not None
    assert _parse_iso_time(None) is None
    assert _parse_iso_time("not-a-date") is None


def test_parse_exif_capture_time(tmp_path):
    img_path = tmp_path / "test_exif.jpg"
    img = Image.new("RGB", (20, 20), color="red")
    exif = img.getexif()
    # 0x9003 is DateTimeOriginal
    exif[0x9003] = "2023:05:10 14:30:00"
    img.save(img_path, exif=exif)

    parsed = _parse_exif_capture_time(str(img_path))
    assert parsed is not None

    # Image without exif
    no_exif = tmp_path / "no_exif.png"
    Image.new("RGB", (10, 10)).save(no_exif)
    assert _parse_exif_capture_time(str(no_exif)) is None

    # Invalid file
    assert _parse_exif_capture_time(str(tmp_path / "nonexistent.jpg")) is None


def test_probe_media_metadata(tmp_path):
    async def _test():
        # 1. Non-existent file
        assert await probe_media_metadata(str(tmp_path / "ghost.mp4")) is None

        # 2. Image metadata probe
        img_p = tmp_path / "photo.jpg"
        Image.new("RGB", (1920, 1080), color="blue").save(img_p)
        meta_img = await probe_media_metadata(str(img_p))
        assert meta_img is not None
        assert meta_img["width"] == 1920
        assert meta_img["height"] == 1080

        # 3. Video metadata probe via mocked ffprobe
        vid_p = tmp_path / "film.mp4"
        vid_p.write_bytes(b"\x00" * 1024)

        mock_ffprobe_out = {
            "format": {
                "duration": "120.5",
                "bit_rate": "5000000",
                "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
            },
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "h264",
                    "width": 3840,
                    "height": 2160,
                    "avg_frame_rate": "60/1",
                },
                {
                    "codec_type": "audio",
                    "codec_name": "aac",
                },
            ],
        }

        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate.return_value = (
            importlib_json_dumps(mock_ffprobe_out).encode(),
            b"",
        )

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            meta_vid = await probe_media_metadata(str(vid_p))
            assert meta_vid is not None
            assert meta_vid["width"] == 3840
            assert meta_vid["height"] == 2160
            assert meta_vid["duration"] == 120.5
            assert meta_vid["video_codec"] == "h264"
            assert meta_vid["audio_codec"] == "aac"
            assert meta_vid["fps"] == 60.0

    import json

    def importlib_json_dumps(d):
        return json.dumps(d)

    asyncio.run(_test())


def test_relay_process_stdout():
    async def _test():
        proc = AsyncMock()
        chunks = [b"chunk1", b"chunk2", b""]

        async def fake_read(size):
            return chunks.pop(0)

        proc.stdout.read = fake_read
        proc.terminate = MagicMock()
        proc.wait = AsyncMock()

        received = []
        async for c in relay_process_stdout(proc):
            received.append(c)

        assert received == [b"chunk1", b"chunk2"]
        proc.terminate.assert_called()

        # Test process already terminated (ProcessLookupError)
        proc2 = AsyncMock()
        proc2.stdout.read = AsyncMock(return_value=b"")
        proc2.terminate = MagicMock(side_effect=ProcessLookupError())
        async for _ in relay_process_stdout(proc2):
            pass

    asyncio.run(_test())


def test_generate_thumbnail_and_poster(tmp_path, sandbox_env):
    async def _test():
        media = sandbox_env["media_dir"]
        state["destination_dir"] = str(media)

        # 1. Image thumbnail generation
        img_p = media / "pic.png"
        Image.new("RGBA", (800, 600), color=(100, 200, 50, 255)).save(img_p)

        thumb = await generate_thumbnail(str(img_p), is_video=False, update_db=True)
        assert thumb is not None
        assert thumb.exists()

        # Fast path check
        fast = get_thumbnail_path_fast(str(img_p), mtime=img_p.stat().st_mtime, size=img_p.stat().st_size)
        assert fast.exists()

        # 2. Extract candidate frame
        vid_p = media / "clip.mp4"
        vid_p.write_bytes(b"\x00" * 1024)

        cand_cache = get_candidate_cache_path(str(vid_p), 5.0)
        assert cand_cache is not None

        # Mock ffmpeg thumbnail extraction
        async def fake_ffmpeg(*args, **kwargs):
            mock_proc = AsyncMock()
            mock_proc.returncode = 0
            mock_proc.wait = AsyncMock(return_value=0)
            # Find the output path in args
            out_path = args[-1]
            Path(out_path).write_bytes(b"\xff\xd8\xff\xe0" + b"THUMB" * 50)
            return mock_proc

        with patch("asyncio.create_subprocess_exec", side_effect=fake_ffmpeg):
            cf = await extract_candidate_frame(str(vid_p), 5.0)
            assert cf is not None
            assert cf.exists()

            fp = await extract_frame_poster(str(vid_p), 10.0)
            assert fp is not None
            assert fp.exists()

    asyncio.run(_test())


def test_convert_image_file_to_webp_edge_cases(tmp_path, sandbox_env):
    media = sandbox_env["media_dir"]

    # 1. Target collision handling (_opt1.webp, _opt2.webp)
    img_p = media / "coll.jpg"
    Image.new("RGB", (50, 50), color="green").save(img_p)
    opt1 = media / "coll.webp"
    opt1.write_bytes(b"existing")

    res = convert_image_file_to_webp(str(img_p))
    assert res["status"] == "converted"
    assert "_opt1.webp" in res["new_path"]
    assert Path(res["new_path"]).exists()

    # 2. Image mode 'P' (palette with transparency)
    p_img_path = media / "palette.png"
    im_p = Image.new("P", (40, 40))
    im_p.info["transparency"] = 0
    im_p.save(p_img_path)

    res_p = convert_image_file_to_webp(str(p_img_path))
    assert res_p["status"] == "converted"

    # 3. Target copy verification failure
    img_fail = media / "fail_verify.jpg"
    Image.new("RGB", (30, 30), color="yellow").save(img_fail)

    with patch("shutil.copy2", side_effect=OSError("Disk full")):
        with pytest.raises(OSError):
            convert_image_file_to_webp(str(img_fail))

    # 4. Error unlinking original rolls back WebP
    img_unlink_fail = media / "unlink_fail.jpg"
    Image.new("RGB", (30, 30), color="purple").save(img_unlink_fail)

    orig_unlink = Path.unlink

    def fake_unlink(p, *args, **kwargs):
        if str(p) == str(img_unlink_fail):
            raise PermissionError("Cannot unlink original")
        return orig_unlink(p, *args, **kwargs)

    with patch.object(Path, "unlink", fake_unlink):
        res_unl = convert_image_file_to_webp(str(img_unlink_fail))
        assert res_unl["status"] == "error"
        assert "Could not remove original" in res_unl["error"]


def test_media_service_scan_videos_and_path_resolution(tmp_path, sandbox_env):
    # 1. scan_videos with invalid/unreadable directory
    assert scan_videos("/nonexistent/directory") == []

    # 2. scan_videos handles unreadable entry gracefully
    vdir = tmp_path / "vids"
    vdir.mkdir()
    (vdir / "good.mp4").write_bytes(b"\x00" * 100)
    (vdir / "bad.mp4").write_bytes(b"\x00" * 100)

    orig_stat = os.DirEntry.stat

    def flaky_stat(entry, *args, **kwargs):
        if entry.name == "bad.mp4":
            raise OSError("I/O error")
        return orig_stat(entry, *args, **kwargs)

    with patch.object(os.DirEntry, "stat", flaky_stat):
        found = scan_videos(str(vdir))
        assert len(found) == 1
        assert found[0]["filename"] == "good.mp4"

    # 3. resolve_video_path
    state["source_dir"] = str(vdir)
    assert resolve_video_path("good.mp4") == str(vdir / "good.mp4")
    assert resolve_video_path("nonexistent.mp4") is None

    # Empty source dir
    state["source_dir"] = ""
    assert resolve_video_path("good.mp4") is None


def test_media_service_validate_backup_path_edge_cases(sandbox_env):
    media = str(sandbox_env["media_dir"])
    state["destination_dir"] = media

    # Root, parent, or empty clamps safely to media dir
    assert validate_backup_path("/") == media
    assert validate_backup_path(os.path.dirname(media)) == media
    assert validate_backup_path(None) == media

    # ValueError in commonpath
    with patch("os.path.commonpath", side_effect=ValueError("Mixed paths")):
        with pytest.raises(HTTPException) as exc:
            validate_backup_path("/some/invalid/path")
        assert exc.value.status_code == 403


def test_media_service_search_backup_index(sandbox_env):
    media = str(sandbox_env["media_dir"])
    state["destination_dir"] = media

    with db_conn() as conn, conn:
        conn.execute(
            """INSERT INTO files (path, name, folder_path, type, ext, size, size_formatted, mtime)
               VALUES (?, ?, ?, 'video', '.mp4', 100, '100 B', 100.0)""",
            (os.path.join(media, "action_movie.mp4"), "action_movie.mp4", media),
        )

    res = search_backup_index("action")
    assert len(res) == 1
    assert res[0]["name"] == "action_movie.mp4"

    # DB query exception returns []
    class ErrorConn:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def execute(self, *args, **kwargs):
            raise RuntimeError("DB dead")

    with patch(
        "media_library.services.media_service.db_conn", return_value=ErrorConn()
    ):
        assert search_backup_index("action") == []


def test_media_service_do_transfer_waiting_on_transcode(tmp_path, sandbox_env):
    media = sandbox_env["media_dir"]
    state["destination_dir"] = str(media)

    src = tmp_path / "orig.mkv"
    src.write_bytes(b"\x00" * 200)
    dest = media / "sorted" / "orig.mkv"

    converted_target = tmp_path / "orig_converted.mp4"
    converted_target.write_bytes(b"CONVERTED" * 100)

    # 1. Waiting on transcode completes
    t_task = {
        "id": "tc1",
        "status": "completed",
        "target_path": str(converted_target),
    }
    tc_map = {"tc1": t_task}

    item = {
        "id": "item1",
        "filename": "orig.mkv",
        "source_path": str(src),
        "target_path": str(dest),
        "total_bytes": 200,
        "bytes_copied": 0,
        "percent": 0.0,
        "abort_event": threading.Event(),
        "status": "queued",
        "waiting_on_transcode": "tc1",
    }

    do_transfer(item, transcode_items_map=tc_map)
    assert item["status"] == "completed"
    assert item["filename"] == "orig_converted.mp4"
    assert os.path.exists(item["target_path"])

    # 2. Waiting on transcode cancelled via abort_event
    abort_ev = threading.Event()
    abort_ev.set()
    t_task_running = {
        "id": "tc2",
        "status": "transcoding",
    }
    tc_map2 = {"tc2": t_task_running}
    item2 = {
        "id": "item2",
        "filename": "orig.mkv",
        "source_path": str(src),
        "target_path": str(dest),
        "total_bytes": 200,
        "bytes_copied": 0,
        "percent": 0.0,
        "abort_event": abort_ev,
        "status": "queued",
        "waiting_on_transcode": "tc2",
    }
    do_transfer(item2, transcode_items_map=tc_map2)
    assert item2["status"] == "cancelled"


def test_media_service_do_transfer_cross_device_and_errors(tmp_path, sandbox_env):
    media = sandbox_env["media_dir"]
    state["destination_dir"] = str(media)

    src = tmp_path / "large_vid.mp4"
    src.write_bytes(b"DATAPAYLOAD" * 1000)
    dest = media / "target" / "large_vid.mp4"

    # Simulate cross-device move by having os.rename fail with EXDEV
    real_rename = os.rename

    def fake_rename(s, d):
        raise OSError(18, "Invalid cross-device link")

    abort_ev = threading.Event()
    item = {
        "id": "xdev1",
        "filename": "large_vid.mp4",
        "source_path": str(src),
        "target_path": str(dest),
        "total_bytes": src.stat().st_size,
        "bytes_copied": 0,
        "percent": 0.0,
        "abort_event": abort_ev,
        "status": "queued",
    }

    with patch("os.rename", side_effect=fake_rename):
        do_transfer(item)

    assert item["status"] == "completed"
    assert item["percent"] == 100.0
    assert os.path.exists(dest)
    assert not os.path.exists(src)

    # Test abort mid-transfer
    src2 = tmp_path / "large_vid2.mp4"
    src2.write_bytes(b"DATAPAYLOAD" * 1000)
    dest2 = media / "target" / "large_vid2.mp4"

    abort_ev2 = threading.Event()
    abort_ev2.set()

    item2 = {
        "id": "xdev2",
        "filename": "large_vid2.mp4",
        "source_path": str(src2),
        "target_path": str(dest2),
        "total_bytes": src2.stat().st_size,
        "bytes_copied": 0,
        "percent": 0.0,
        "abort_event": abort_ev2,
        "status": "queued",
    }

    with patch("os.rename", side_effect=fake_rename):
        do_transfer(item2)

    assert item2["status"] == "cancelled"

    # Test write error during chunked copy
    src3 = tmp_path / "large_vid3.mp4"
    src3.write_bytes(b"DATAPAYLOAD" * 100)
    dest3 = media / "target" / "large_vid3.mp4"

    item3 = {
        "id": "xdev3",
        "filename": "large_vid3.mp4",
        "source_path": str(src3),
        "target_path": str(dest3),
        "total_bytes": src3.stat().st_size,
        "bytes_copied": 0,
        "percent": 0.0,
        "abort_event": threading.Event(),
        "status": "queued",
    }

    with patch("os.rename", side_effect=fake_rename), patch(
        "builtins.open", side_effect=IOError("Read-only filesystem")
    ):
        do_transfer(item3)

    assert item3["status"] == "failed"


def test_ffmpeg_video_thumbnail_and_poster_more(sandbox_env, tmp_path):
    async def _test():
        media = sandbox_env["media_dir"]
        vid_p = media / "movie_extra.mp4"
        vid_p.write_bytes(b"\x00" * 2048)

        # 1. extract_frame_poster with image_base64
        bio = io.BytesIO()
        Image.new("RGB", (400, 300), color="cyan").save(bio, "JPEG")
        import base64

        b64_str = "data:image/jpeg;base64," + base64.b64encode(bio.getvalue()).decode()
        poster_p = await extract_frame_poster(str(vid_p), image_base64=b64_str)
        assert poster_p is not None
        assert poster_p.exists()

        # 2. extract_candidate_frame cache hit
        cand_cache = get_candidate_cache_path(str(vid_p), 1.0)
        cand_cache.parent.mkdir(parents=True, exist_ok=True)
        cand_cache.write_bytes(b"\xff\xd8\xff\xe0" + b"CAND" * 50)

        cand1 = await extract_candidate_frame(str(vid_p), 1.0)
        assert cand1 is not None
        assert cand1 == cand_cache
        # Calling again triggers cache hit
        cand2 = await extract_candidate_frame(str(vid_p), 1.0)
        assert cand2 == cand1

        # 3. generate_thumbnail video retry with 00:00:00
        vid_retry = media / "retry_vid.mp4"
        vid_retry.write_bytes(b"\x00" * 2048)

        calls = 0

        async def fake_retry_ffmpeg(*args, **kwargs):
            nonlocal calls
            calls += 1
            mock_proc = AsyncMock()
            if calls == 1:
                # First attempt fails
                mock_proc.returncode = 1
                mock_proc.communicate.return_value = (b"", b"")
                return mock_proc
            else:
                # Retry attempt succeeds
                mock_proc.returncode = 0
                mock_proc.communicate.return_value = (b"", b"")
                out_path = args[-1]
                Path(out_path).write_bytes(b"\xff\xd8\xff\xe0" + b"RETRY" * 50)
                return mock_proc

        with patch("asyncio.create_subprocess_exec", side_effect=fake_retry_ffmpeg):
            th_retry = await generate_thumbnail(str(vid_retry), is_video=True)
            assert th_retry is not None
            assert th_retry.exists()

        # 4. generate_thumbnail video ffmpeg timeout killing proc
        vid_timeout = media / "timeout_vid.mp4"
        vid_timeout.write_bytes(b"\x00" * 2048)

        mock_to_proc = AsyncMock()
        mock_to_proc.returncode = 1
        mock_to_proc.kill = MagicMock()
        mock_to_proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError())

        with patch("asyncio.create_subprocess_exec", return_value=mock_to_proc):
            # When ffmpeg fails on a video, returns None
            th_vid_fail = await generate_thumbnail(str(vid_timeout), is_video=True)
            assert th_vid_fail is None

        # 5. generate_thumbnail image ffmpeg failure triggers PIL fallback
        img_p = media / "pil_fallback.png"
        Image.new("RGB", (100, 100), color="orange").save(img_p)

        mock_fail_proc = AsyncMock()
        mock_fail_proc.returncode = 1
        mock_fail_proc.communicate.return_value = (b"", b"")

        with patch("asyncio.create_subprocess_exec", return_value=mock_fail_proc):
            th_pil = await generate_thumbnail(str(img_p), is_video=False)
            assert th_pil is not None
            assert th_pil.exists()

    asyncio.run(_test())

