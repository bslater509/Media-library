"""Deep coverage tests for webp_worker, duplicate_hash_worker, and library_media router."""

import asyncio
import io
import os
import shutil
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from PIL import Image

from media_library.core.state import state
from media_library.db.connection import db_conn
from media_library.workers.duplicate_hash_worker import (
    DuplicateHashWorker,
    ThumbHashWorker,
)
from media_library.workers.webp_worker import (
    WebPOptimizerWorker,
    _available_memory_mb,
    _cgroup_headroom_mb,
    _host_available_memory_mb,
    _release_memory,
)


def test_webp_worker_memory_helpers():
    # 1. _host_available_memory_mb with OSError
    with patch("builtins.open", side_effect=OSError("No /proc/meminfo")):
        assert _host_available_memory_mb() == float("inf")

    # 2. _cgroup_headroom_mb with OSError
    with patch("builtins.open", side_effect=OSError("No /proc/self/cgroup")):
        assert _cgroup_headroom_mb() == float("inf")

    # 3. _release_memory execution
    _release_memory()


def test_webp_worker_state_and_edge_cases(sandbox_env):
    worker = WebPOptimizerWorker()
    assert worker.state["status"] == "idle"

    # Pause and resume
    worker.pause()
    assert worker.is_paused() is True
    worker.resume()
    assert worker.is_paused() is False

    # Enqueue folder and files
    media = sandbox_env["media_dir"]
    sub = media / "webp_folder"
    sub.mkdir()
    pic = sub / "pic.png"
    pic.write_bytes(b"\x00" * 10)

    with db_conn() as conn, conn:
        conn.execute(
            """INSERT INTO files (path, name, folder_path, type, ext, size, size_formatted, mtime, webp_optimized)
               VALUES (?, 'pic.png', ?, 'image', '.png', 10, '10 B', 100.0, 0)""",
            (str(pic), str(sub)),
        )

    n_enq = worker.enqueue_folder(str(sub))
    assert n_enq == 1
    assert not worker.priority_queue.empty()


def test_duplicate_hash_worker_pause_and_error(sandbox_env):
    worker = DuplicateHashWorker()
    worker.pause()
    assert worker.is_paused() is True
    worker.resume()
    assert worker.is_paused() is False

    thumb_worker = ThumbHashWorker()
    thumb_worker.pause()
    assert thumb_worker.is_paused() is True
    thumb_worker.resume()
    assert thumb_worker.is_paused() is False


def test_library_media_thumbnail_and_candidate_routes(api_client, sandbox_env, tmp_path):
    dest = Path(state["destination_dir"])

    # 1. /api/backup/thumbnail with invalid extension -> 400
    doc_file = dest / "document.txt"
    doc_file.write_text("not media")
    res_bad_ext = api_client.get("/api/backup/thumbnail", params={"path": str(doc_file)})
    assert res_bad_ext.status_code == 400

    # 2. /api/backup/thumbnail with non-existent file -> 404
    res_404 = api_client.get("/api/backup/thumbnail", params={"path": str(dest / "ghost.jpg")})
    assert res_404.status_code == 404

    # 3. Image thumbnail generation failure serves raw image fallback
    img_fallback = dest / "raw_fallback.jpg"
    Image.new("RGB", (20, 20), color="blue").save(img_fallback)

    with patch(
        "media_library.routers.library_media.generate_thumbnail",
        new_callable=AsyncMock,
        return_value=None,
    ), patch(
        "media_library.routers.library_media.get_thumbnail_path_fast",
        return_value=Path("/fake/no_thumb.jpg"),
    ):
        res_raw = api_client.get("/api/backup/thumbnail", params={"path": str(img_fallback)})
        assert res_raw.status_code == 200
        # HEAD request on raw image
        res_raw_head = api_client.head("/api/backup/thumbnail", params={"path": str(img_fallback)})
        assert res_raw_head.status_code == 200

    # 4. Video thumbnail generation failure -> 500
    vid_p = dest / "fail_thumb.mp4"
    vid_p.write_bytes(b"\x00" * 100)
    with patch(
        "media_library.routers.library_media.generate_thumbnail",
        new_callable=AsyncMock,
        return_value=None,
    ), patch(
        "media_library.routers.library_media.get_thumbnail_path_fast",
        return_value=Path("/fake/no_thumb.jpg"),
    ):
        res_fail_vid = api_client.get("/api/backup/thumbnail", params={"path": str(vid_p)})
        assert res_fail_vid.status_code == 500

    # 5. /api/backup/thumbnails/candidates on non-video -> 400
    res_cand_bad = api_client.get("/api/backup/thumbnails/candidates", params={"path": str(img_fallback)})
    assert res_cand_bad.status_code == 400

    # 6. /api/backup/thumbnails/candidates on missing video -> 404
    res_cand_404 = api_client.get("/api/backup/thumbnails/candidates", params={"path": str(dest / "ghost.mp4")})
    assert res_cand_404.status_code == 404

    # 7. /api/backup/thumbnails/candidates valid video with 0 duration fallback
    vid_zero = dest / "zero_dur.mp4"
    vid_zero.write_bytes(b"\x00" * 100)
    with patch(
        "media_library.routers.library_media.probe_video_meta",
        new_callable=AsyncMock,
        return_value={"duration": 0.0},
    ):
        res_cand_ok = api_client.get(
            "/api/backup/thumbnails/candidates",
            params={"path": str(vid_zero), "count": 6},
        )
        assert res_cand_ok.status_code == 200
        data = res_cand_ok.json()
        assert data["count"] == 6
        assert data["duration"] == 1.0

    # 8. /api/backup/thumbnails/frame on non-video -> 400
    res_frame_bad = api_client.get("/api/backup/thumbnails/frame", params={"path": str(img_fallback)})
    assert res_frame_bad.status_code == 400

    # 9. /api/backup/thumbnails/frame frame extraction failure -> 500
    with patch(
        "media_library.routers.library_media.extract_candidate_frame",
        new_callable=AsyncMock,
        return_value=None,
    ), patch(
        "media_library.routers.library_media.get_candidate_cache_path",
        return_value=Path("/fake/no_cand.jpg"),
    ):
        res_frame_fail = api_client.get(
            "/api/backup/thumbnails/frame",
            params={"path": str(vid_zero), "timestamp": 1.0},
        )
        assert res_frame_fail.status_code == 500


def test_zip_core_and_extract_edge_cases(tmp_path):
    from media_library.services.zip.core import (
        is_zip_file,
        manage_lru_cache,
        split_zip_path,
    )

    # 1. split_zip_path
    zp, inp = split_zip_path("/path/to/archive.zip/inside/photo.jpg")
    assert zp == "/path/to/archive.zip"
    assert inp == "inside/photo.jpg"

    zp2, inp2 = split_zip_path("/path/to/regular_folder/photo.jpg")
    assert zp2 is None
    assert inp2 == ""

    # 2. is_zip_file
    fake_zip = tmp_path / "test.zip"
    fake_zip.write_bytes(b"PK\x03\x04" + b"\x00" * 20)
    assert is_zip_file(str(fake_zip)) is True

    fake_txt = tmp_path / "test.txt"
    fake_txt.write_text("hello")
    assert is_zip_file(str(fake_txt)) is False

    # 3. manage_lru_cache
    manage_lru_cache()
