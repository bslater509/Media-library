"""Tests for core configuration, constants, and formatting."""

import pytest
from pathlib import Path
from media_library.core.constants import VIDEO_EXTENSIONS, IMAGE_EXTENSIONS, NATIVE_EXTENSIONS
from media_library.core.state import format_size, make_video_meta, get_thumbnail_cache_path

def test_extensions():
    assert ".mp4" in VIDEO_EXTENSIONS
    assert ".mkv" in VIDEO_EXTENSIONS
    assert ".jpg" in IMAGE_EXTENSIONS
    assert ".webp" in IMAGE_EXTENSIONS
    assert ".mp4" in NATIVE_EXTENSIONS
    assert ".mkv" not in NATIVE_EXTENSIONS

def test_format_size():
    assert format_size(0) == "0.0 B"
    assert format_size(1024) == "1.0 KB"
    assert format_size(1048576) == "1.0 MB"
    assert format_size(1073741824) == "1.0 GB"
    assert format_size("invalid") == "0 B"

def test_make_video_meta(tmp_path):
    sample = tmp_path / "test_clip.mkv"
    sample.write_bytes(b"0" * 2048)

    meta = make_video_meta(str(sample))
    assert meta is not None
    assert meta["filename"] == "test_clip.mkv"
    assert meta["ext"] == ".mkv"
    assert meta["size"] == 2048
    assert meta["needs_transcode"] is True

def test_thumbnail_cache_path():
    path_str = "/some/path/movie.mp4"
    thumb_path = get_thumbnail_cache_path(path_str, mtime=12345.0, size=5000)
    assert thumb_path.name.startswith("thumb_")
    assert thumb_path.name.endswith(".jpg")
