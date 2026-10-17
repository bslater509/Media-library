"""Tests for media service, transfers, and deduplication logic."""

import os
import threading
from pathlib import Path

import pytest
from fastapi import HTTPException

from media_library.core.state import state
from media_library.services.deduplication_service import (
    compute_quick_hash,
    normalize_pair,
    resolve_duplicate_group,
    score_duplicate_file,
)
from media_library.services.media_service import (
    do_transfer,
    scan_videos,
    validate_backup_path,
)


def test_validate_backup_path(sandbox_env):
    media_dir = str(sandbox_env["media_dir"])
    state["destination_dir"] = media_dir

    # Normal valid subfolder
    sub = os.path.join(media_dir, "action")
    os.makedirs(sub, exist_ok=True)
    assert validate_backup_path(sub) == sub

    # None or empty returns media_dir
    assert validate_backup_path(None) == media_dir
    assert validate_backup_path("") == media_dir

    # Path traversal outside media_dir raises HTTPException 403
    with pytest.raises(HTTPException) as exc_info:
        validate_backup_path("/etc/passwd")
    assert exc_info.value.status_code == 403


def test_scan_videos(sandbox_env, sample_media):
    action_cat = str(sample_media["action_cat"])
    videos = scan_videos(action_cat)
    # clip1.mp4 and clip2.mkv are videos, poster1.jpg is not
    filenames = [v["filename"] for v in videos]
    assert "clip1.mp4" in filenames
    assert "clip2.mkv" in filenames
    assert "poster1.jpg" not in filenames


def test_compute_quick_hash_and_normalize_pair(tmp_path):
    f1 = tmp_path / "file1.bin"
    f1.write_bytes(b"content" * 100)

    h = compute_quick_hash(str(f1))
    assert h is not None
    assert len(h) == 64

    # normalize_pair ensures consistent alphabetical ordering
    p1, p2 = normalize_pair("/z/b.mp4", "/a/a.mp4")
    assert p1 == "/a/a.mp4"
    assert p2 == "/z/b.mp4"


def test_score_duplicate_file():
    # File with copy indicators should score lower than clean title
    clean_score = score_duplicate_file(
        "Movie Title.mp4", "/media/action", 1000.0, 5000000
    )
    copy_score = score_duplicate_file(
        "Movie Title (1).mp4", "/media/action", 1000.0, 5000000
    )
    assert clean_score > copy_score


def test_do_transfer(sandbox_env):
    incoming = sandbox_env["incoming_dir"]
    media = sandbox_env["media_dir"]
    state["destination_dir"] = str(media)

    src = incoming / "source_video.mp4"
    src.write_bytes(b"DATA" * 500)
    dest = media / "sorted" / "source_video.mp4"

    abort_ev = threading.Event()
    item = {
        "id": "item1",
        "filename": "source_video.mp4",
        "source_path": str(src),
        "target_path": str(dest),
        "total_bytes": src.stat().st_size,
        "bytes_copied": 0,
        "percent": 0.0,
        "abort_event": abort_ev,
        "status": "queued",
    }

    do_transfer(item)
    assert item["status"] == "completed"
    assert dest.exists()
    assert not src.exists()


def test_resolve_duplicate_group(sandbox_env):
    media = sandbox_env["media_dir"]
    state["destination_dir"] = str(media)

    keep = media / "best.mp4"
    keep.write_bytes(b"keep this" * 100)

    dupe1 = media / "copy1.mp4"
    dupe1.write_bytes(b"delete this" * 100)

    res = resolve_duplicate_group(str(keep), [str(dupe1)])
    assert res["success"] is True
    assert keep.exists()
    assert not dupe1.exists()


def test_ffmpeg_service_helpers():
    from media_library.services.ffmpeg_service import (
        _parse_fraction,
        _parse_iso_time,
        build_live_transcode_command,
        format_eta_str,
        get_candidate_cache_path,
    )

    # format_eta_str
    assert format_eta_str(0) == "0s"
    assert format_eta_str(45) == "45s"
    assert format_eta_str(75) == "1m 15s"
    assert format_eta_str(3665) == "1h 1m"

    # _parse_fraction
    assert _parse_fraction(None) == 0.0
    assert _parse_fraction("") == 0.0
    assert _parse_fraction("30000/1001") == round(30000 / 1001, 3)
    assert _parse_fraction("25/1") == 25.0
    assert _parse_fraction("10/0") == 0.0
    assert _parse_fraction(60) == 60.0
    assert _parse_fraction("invalid") == 0.0

    # _parse_iso_time
    assert _parse_iso_time(None) is None
    assert _parse_iso_time("") is None
    assert _parse_iso_time("invalid-date") is None
    assert _parse_iso_time("2026-01-01T12:00:00Z") is not None

    # build_live_transcode_command
    cmd = build_live_transcode_command("/path/to/vid.mkv")
    assert "ffmpeg" in cmd[0]
    assert "/path/to/vid.mkv" in cmd
    assert "libx264" in cmd
    assert "-ss" not in cmd

    cmd_ss = build_live_transcode_command("/path/to/vid.mkv", ss=12.5)
    assert "-ss" in cmd_ss
    assert "12.5" in cmd_ss

    # get_candidate_cache_path
    cand_p = get_candidate_cache_path("/path/to/vid.mp4", 10.5)
    assert str(cand_p).endswith(".jpg")
    assert "cand_" in str(cand_p)

    # relay_process_stdout
    import asyncio
    from unittest.mock import AsyncMock

    from media_library.services.ffmpeg_service import (
        probe_media_metadata,
        probe_video_meta,
        relay_process_stdout,
    )

    async def _test_relay():
        mock_proc = AsyncMock()
        mock_proc.stdout.read.side_effect = [b"chunk1", b"chunk2", b""]
        mock_proc.terminate = AsyncMock()
        mock_proc.wait = AsyncMock()
        chunks = []
        async for ch in relay_process_stdout(mock_proc):
            chunks.append(ch)
        assert chunks == [b"chunk1", b"chunk2"]

        # probe functions on missing path
        m_vid = await probe_video_meta("/nonexistent/video.mp4")
        assert m_vid["duration"] == 0.0

        m_media = await probe_media_metadata("/nonexistent/video.mp4")
        assert m_media is None

    asyncio.run(_test_relay())


def test_extract_frame_poster_base64(sandbox_env):
    import asyncio
    import base64
    import io

    from PIL import Image

    from media_library.services.ffmpeg_service import extract_frame_poster

    media = sandbox_env["media_dir"]
    vid = media / "test_poster_vid.mp4"
    vid.write_bytes(b"\x00" * 100)

    buf = io.BytesIO()
    Image.new("RGB", (400, 300), color="blue").save(buf, format="JPEG")
    b64_str = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()

    async def _test():
        res = await extract_frame_poster(str(vid), image_base64=b64_str)
        assert res is not None
        assert res.exists()

    asyncio.run(_test())


def test_ffmpeg_image_dimensions_and_webp(sandbox_env):
    from PIL import Image

    from media_library.services.ffmpeg_service import (
        _parse_exif_capture_time,
        convert_image_file_to_webp,
        convert_image_to_webp_bytes,
    )

    media = sandbox_env["media_dir"]

    # 1. Valid RGB JPEG
    jpg_path = media / "test_sample.jpg"
    img = Image.new("RGB", (200, 150), color=(100, 150, 200))
    img.save(jpg_path, format="JPEG")

    # _parse_exif_capture_time on non-exif image
    assert _parse_exif_capture_time(str(jpg_path)) is None

    # convert_image_to_webp_bytes
    raw_jpg = jpg_path.read_bytes()
    webp_bytes = convert_image_to_webp_bytes(raw_jpg)
    assert webp_bytes is not None
    assert len(webp_bytes) > 0

    # Convert JPG to WebP
    res_webp = convert_image_file_to_webp(str(jpg_path))
    assert res_webp["status"] in ("converted", "skipped_larger")
    if res_webp["status"] == "converted":
        assert Path(res_webp["new_path"]).exists()
        assert Path(res_webp["new_path"]).suffix.lower() == ".webp"
        assert not jpg_path.exists()

        # Converting an already-webp image
        res_already = convert_image_file_to_webp(res_webp["new_path"])
        assert res_already["status"] == "already_webp"

    # 2. Unsupported file format
    txt_path = media / "sample.txt"
    txt_path.write_text("not an image")
    res_unsupported = convert_image_file_to_webp(str(txt_path))
    assert res_unsupported["status"] == "unsupported"

    # 3. Missing file
    res_missing = convert_image_file_to_webp(str(media / "nonexistent.jpg"))
    assert res_missing["status"] == "error"


def test_deduplication_service_byte_and_hash_checks(sandbox_env):
    from PIL import Image

    from media_library.services.deduplication_service import (
        _files_have_identical_content,
        compute_quick_hash,
        compute_thumbnail_phash,
        get_ignored_pairs_set,
        invalidate_visual_cache,
        visual_duplicates_cache,
    )

    media = sandbox_env["media_dir"]

    # _files_have_identical_content
    f1 = media / "file1.bin"
    f2 = media / "file2.bin"
    f3 = media / "file3.bin"

    f1.write_bytes(b"HELLO WORLD 12345")
    f2.write_bytes(b"HELLO WORLD 12345")
    f3.write_bytes(b"DIFFERENT 1234567")

    assert _files_have_identical_content(str(f1), str(f2)) is True
    assert _files_have_identical_content(str(f1), str(f3)) is False
    assert _files_have_identical_content(str(f1), str(media / "none.bin")) is False

    # compute_quick_hash empty file
    empty_f = media / "empty.bin"
    empty_f.write_bytes(b"")
    assert compute_quick_hash(str(empty_f)) == "empty_file"
    assert compute_quick_hash(str(media / "none.bin")) is None

    # compute_thumbnail_phash
    # 1. Blank thumbnail returns 'blank_thumb'
    blank_p = media / "blank.jpg"
    blank_img = Image.new("RGB", (100, 100), color=(0, 0, 0))
    blank_img.save(blank_p, format="JPEG")
    assert compute_thumbnail_phash(blank_p) == "blank_thumb"

    # 2. Patterned image returns valid perceptual hash
    pattern_p = media / "pattern.jpg"
    pattern_img = Image.new("RGB", (100, 100))
    pixels = pattern_img.load()
    for x in range(100):
        for y in range(100):
            pixels[x, y] = ((x * 5) % 256, (y * 5) % 256, (x * y) % 256)
    pattern_img.save(pattern_p, format="JPEG")
    phash = compute_thumbnail_phash(pattern_p)
    assert phash is not None
    assert phash != "blank_thumb"

    # 3. Missing file returns None
    assert compute_thumbnail_phash(media / "none.jpg") is None

    # invalidate_visual_cache
    visual_duplicates_cache["timestamp"] = 999999
    invalidate_visual_cache()
    assert visual_duplicates_cache["timestamp"] == 0

    # get_ignored_pairs_set
    ignored = get_ignored_pairs_set()
    assert isinstance(ignored, set)
