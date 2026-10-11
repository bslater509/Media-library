"""Unit and integration tests for animated WebP video previews."""

import io
import os
from pathlib import Path
from PIL import Image
import pytest

from media_library.services.ffmpeg_service import (
    get_animated_preview_path,
    generate_animated_preview,
)


def _create_mock_video_image(path: str, color="red"):
    """Creates a mock video file that PIL can open as an image fallback."""
    buf = io.BytesIO()
    img = Image.new("RGB", (640, 360), color=color)
    img.save(buf, format="JPEG")
    with open(path, "wb") as f:
        f.write(buf.getvalue())


def test_get_animated_preview_path(sandbox_env):
    dest = str(sandbox_env["media_dir"])
    vid = Path(dest) / "sample_video.mp4"
    _create_mock_video_image(str(vid))

    stat = vid.stat()
    anim_path = get_animated_preview_path(vid, mtime=stat.st_mtime, size=stat.st_size)
    assert anim_path.suffix == ".webp"
    assert "anim_" in anim_path.name
    assert anim_path.parent.name == "animated"


@pytest.mark.anyio
async def test_generate_animated_preview_and_caching(sandbox_env):
    dest = str(sandbox_env["media_dir"])
    vid = Path(dest) / "sample_video.mp4"
    _create_mock_video_image(str(vid))

    # Populate in files table with duration
    from media_library.db.connection import db_conn
    with db_conn() as conn, conn:
        conn.execute(
            """INSERT INTO files (path, name, folder_path, type, ext, size, size_formatted, mtime, duration, width, height)
               VALUES (?, ?, ?, 'video', '.mp4', ?, '1 KB', ?, 120.0, 640, 360)""",
            (str(vid), "sample_video.mp4", dest, vid.stat().st_size, vid.stat().st_mtime),
        )

    # Generate preview
    anim_file = await generate_animated_preview(vid)
    assert anim_file is not None
    assert anim_file.exists()
    assert anim_file.stat().st_size > 0

    # Verify with PIL that it is an animated WebP with 480px width
    with Image.open(anim_file) as img:
        assert img.format == "WEBP"
        assert getattr(img, "is_animated", False) is True
        assert getattr(img, "n_frames", 1) >= 2
        assert img.width == 480

    # Second call should return cached file immediately
    cached = await generate_animated_preview(vid)
    assert cached == anim_file


def test_api_animated_preview_endpoint(sandbox_env, api_client):
    from media_library.core.state import state
    dest = str(sandbox_env["media_dir"])
    state["destination_dir"] = dest
    vid_path = os.path.join(dest, "api_video.mp4")
    _create_mock_video_image(vid_path)

    from media_library.db.connection import db_conn
    with db_conn() as conn, conn:
        conn.execute(
            """INSERT INTO files (path, name, folder_path, type, ext, size, size_formatted, mtime, duration, width, height)
               VALUES (?, ?, ?, 'video', '.mp4', 1024, '1 KB', 12345.0, 45.0, 640, 360)""",
            (vid_path, "api_video.mp4", dest),
        )

    # GET /api/backup/thumbnails/animated
    res = api_client.get(f"/api/backup/thumbnails/animated?path={vid_path}")
    assert res.status_code == 200
    assert "image/webp" in res.headers.get("content-type", "")

    # Verify bytes are animated WebP
    buf = io.BytesIO(res.content)
    with Image.open(buf) as img:
        assert img.format == "WEBP"
        assert getattr(img, "is_animated", False) is True

    # HEAD request support
    res_head = api_client.head(f"/api/backup/thumbnails/animated?path={vid_path}")
    assert res_head.status_code == 200
    assert "image/webp" in res_head.headers.get("content-type", "")


def test_api_animated_preview_errors(sandbox_env, api_client):
    from media_library.core.state import state
    dest = str(sandbox_env["media_dir"])
    state["destination_dir"] = dest

    # Non-video file returns 400
    txt_path = os.path.join(dest, "notes.txt")
    with open(txt_path, "w") as f:
        f.write("hello")
    res = api_client.get(f"/api/backup/thumbnails/animated?path={txt_path}")
    assert res.status_code == 400

    # Non-existent file returns 404
    non_path = os.path.join(dest, "missing.mp4")
    res = api_client.get(f"/api/backup/thumbnails/animated?path={non_path}")
    assert res.status_code == 404

    # Traversal/outside path returns 403
    res = api_client.get("/api/backup/thumbnails/animated?path=/etc/shadow")
    assert res.status_code == 403


def test_api_generate_and_batch_animated_endpoints(sandbox_env, api_client):
    from media_library.core.state import state
    dest = str(sandbox_env["media_dir"])
    state["destination_dir"] = dest
    vid1 = os.path.join(dest, "batch_vid1.mp4")
    vid2 = os.path.join(dest, "batch_vid2.mp4")
    _create_mock_video_image(vid1)
    _create_mock_video_image(vid2)

    from media_library.db.connection import db_conn
    with db_conn() as conn, conn:
        conn.execute(
            """INSERT INTO files (path, name, folder_path, type, ext, size, size_formatted, mtime, duration)
               VALUES (?, ?, ?, 'video', '.mp4', 1024, '1 KB', 12345.0, 30.0)""",
            (vid1, "batch_vid1.mp4", dest),
        )
        conn.execute(
            """INSERT INTO files (path, name, folder_path, type, ext, size, size_formatted, mtime, duration)
               VALUES (?, ?, ?, 'video', '.mp4', 1024, '1 KB', 12345.0, 30.0)""",
            (vid2, "batch_vid2.mp4", dest),
        )

    # Single generate endpoint
    res_single = api_client.post(
        "/api/backup/thumbnails/generate-animated",
        json={"path": vid1},
    )
    assert res_single.status_code == 200
    data = res_single.json()
    assert data["success"] is True
    assert "animated_url" in data
    assert data["path"] == vid1

    # Batch generate endpoint for folder
    res_batch = api_client.post(
        "/api/backup/thumbnails/batch-animated",
        json={"folder": "", "recursive": False},
    )
    assert res_batch.status_code == 200
    bdata = res_batch.json()
    assert "queued" in bdata
    assert bdata["queued"] >= 1


@pytest.mark.anyio
async def test_animated_preview_dimension_normalization_and_custom_poster(sandbox_env, api_client):
    from media_library.core.state import state
    from media_library.services.ffmpeg_service import get_thumbnail_path_fast
    dest = str(sandbox_env["media_dir"])
    state["destination_dir"] = dest
    vid_path = os.path.join(dest, "custom_poster_clip.mp4")
    _create_mock_video_image(vid_path)

    from media_library.db.connection import db_conn
    with db_conn() as conn, conn:
        conn.execute(
            """INSERT INTO files (path, name, folder_path, type, ext, size, size_formatted, mtime, duration, width, height)
               VALUES (?, ?, ?, 'video', '.mp4', 1024, '1 KB', 12345.0, 1800.0, 1280, 720)""",
            (vid_path, "custom_poster_clip.mp4", dest),
        )

    # Create a custom poster frame of size 360x202 (different aspect/rounding than 480x270)
    custom_thumb = get_thumbnail_path_fast(vid_path)
    custom_thumb.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (360, 202), color="purple")
    img.save(custom_thumb, format="JPEG")

    # Generate animated preview: must succeed without "Invalid frame dimensions" error
    anim = await generate_animated_preview(vid_path)
    assert anim is not None
    assert anim.exists()

    with Image.open(anim) as out_img:
        assert out_img.format == "WEBP"
        assert getattr(out_img, "is_animated", False) is True
        assert out_img.size == (480, 270)

    # Calling set-poster should return animated_url and invalidate old anim file
    import base64
    img_b64 = "data:image/jpeg;base64," + base64.b64encode(open(vid_path, "rb").read()).decode()
    res_poster = api_client.post(
        "/api/backup/thumbnails/set-poster",
        json={"path": vid_path, "timestamp": 12.5, "image_base64": img_b64},
    )
    assert res_poster.status_code == 200
    pdata = res_poster.json()
    assert "animated_url" in pdata
    assert "/api/backup/thumbnails/animated?path=" in pdata["animated_url"]


@pytest.mark.anyio
async def test_animated_thumb_worker_and_system_status(sandbox_env, api_client):
    from media_library.core.state import state
    from media_library.workers.animated_thumb_worker import AnimatedThumbWorker
    dest = str(sandbox_env["media_dir"])
    state["destination_dir"] = dest
    vid_path = os.path.join(dest, "worker_test_clip.mp4")
    _create_mock_video_image(vid_path)

    worker = AnimatedThumbWorker()
    assert worker.get_status()["status"] == "idle"

    # Enqueue standard
    enqueued = worker.enqueue(vid_path, priority=False)
    assert enqueued is True
    st = worker.get_status()
    assert st["queue_length"] == 1
    assert len(st["queued"]) == 1
    assert st["queued"][0]["name"] == "worker_test_clip.mp4"

    # Enqueue priority promotion
    worker.enqueue(vid_path, priority=True)
    assert not worker.priority_queue.empty()

    # Pause and resume
    worker.pause()
    assert worker.is_paused() is True
    assert worker.get_status()["status"] == "paused"
    worker.resume()
    assert worker.is_paused() is False

    # Cancel all
    cancelled = worker.cancel_all()
    assert cancelled >= 1
    assert worker.get_status()["queue_length"] == 0

    # API endpoints for pause, resume, cancel
    res_pause = api_client.post("/api/backup/thumbnails/animated-pause")
    assert res_pause.status_code == 200
    assert "is_paused" in res_pause.json()

    # Toggle again to unpause
    res_resume = api_client.post("/api/backup/thumbnails/animated-pause")
    assert res_resume.status_code == 200

    res_cancel = api_client.post("/api/backup/thumbnails/animated-cancel")
    assert res_cancel.status_code == 200
    assert res_cancel.json()["success"] is True

    # Check /api/system/status returns animated_thumbnails payload
    res_sys = api_client.get("/api/system/status")
    assert res_sys.status_code == 200
    sdata = res_sys.json()
    assert "animated_thumbnails" in sdata
    assert "status" in sdata["animated_thumbnails"]
    assert "queue_length" in sdata["animated_thumbnails"]

    # Immediate fallback when wait=0
    vid_uncompiled = os.path.join(dest, "uncompiled_clip.mp4")
    _create_mock_video_image(vid_uncompiled)
    res_fast = api_client.get(f"/api/backup/thumbnails/animated?path={vid_uncompiled}&wait=0")
    assert res_fast.status_code in (200, 202)
    assert res_fast.headers.get("x-thumbnail-status") == "generating"
