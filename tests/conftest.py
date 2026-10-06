"""Pytest fixtures for isolated media library tests."""

import os
import shutil
import tempfile
import pytest
from pathlib import Path

from media_library.core.constants import VIDEO_EXTENSIONS, IMAGE_EXTENSIONS
from media_library.db.connection import get_db
from media_library.db.schema import init_backup_db

@pytest.fixture
def sandbox_env(tmp_path, monkeypatch):
    """Sets up a sandboxed environment with mock directories and SQLite database."""
    base_dir = tmp_path / "sandbox"
    base_dir.mkdir()

    media_dir = base_dir / "media"
    media_dir.mkdir()

    incoming_dir = base_dir / "incoming"
    incoming_dir.mkdir()

    data_dir = base_dir / "data"
    data_dir.mkdir()

    thumb_dir = data_dir / "thumbnails"
    thumb_dir.mkdir()

    db_path = data_dir / "test_library.db"

    # Set environment variables for isolation
    monkeypatch.setenv("LIBRARY_DIR", str(media_dir))
    monkeypatch.setenv("INCOMING_DIR", str(incoming_dir))
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("THUMBNAIL_DIR", str(thumb_dir))
    monkeypatch.setenv("DB_PATH", str(db_path))

    # Initialize schema
    init_backup_db(db_path)

    return {
        "base_dir": base_dir,
        "media_dir": media_dir,
        "incoming_dir": incoming_dir,
        "data_dir": data_dir,
        "thumb_dir": thumb_dir,
        "db_path": db_path,
    }

@pytest.fixture
def sample_media(sandbox_env):
    """Creates sample categories, video files, and images in the sandboxed media dir."""
    media_dir = sandbox_env["media_dir"]

    # Category 1: action
    action_cat = media_dir / "action"
    action_cat.mkdir()
    vid1 = action_cat / "clip1.mp4"
    vid1.write_bytes(b"\x00\x00\x00 ftypmp42" + b"A" * 1024)

    vid2 = action_cat / "clip2.mkv"
    vid2.write_bytes(b"\x1a\x45\xdf\xa3" + b"B" * 2048)

    img1 = action_cat / "poster1.jpg"
    img1.write_bytes(b"\xff\xd8\xff\xe0" + b"C" * 512)

    # Category 2: comedy
    comedy_cat = media_dir / "comedy"
    comedy_cat.mkdir()
    vid3 = comedy_cat / "funny.mp4"
    vid3.write_bytes(b"\x00\x00\x00 ftypmp42" + b"D" * 4096)

    return {
        "action_cat": action_cat,
        "comedy_cat": comedy_cat,
        "vid1": vid1,
        "vid2": vid2,
        "img1": img1,
        "vid3": vid3,
    }
