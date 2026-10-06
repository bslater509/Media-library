"""Tests for media service, transfers, and deduplication logic."""

import os
import threading
import pytest
from fastapi import HTTPException

from media_library.core.state import state
from media_library.services.media_service import (
    validate_backup_path,
    scan_videos,
    do_transfer,
)
from media_library.services.deduplication_service import (
    compute_quick_hash,
    normalize_pair,
    score_duplicate_file,
    resolve_duplicate_group,
)
from media_library.db.connection import get_db

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
    clean_score = score_duplicate_file("Movie Title.mp4", "/media/action", 1000.0, 5000000)
    copy_score = score_duplicate_file("Movie Title (1).mp4", "/media/action", 1000.0, 5000000)
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
