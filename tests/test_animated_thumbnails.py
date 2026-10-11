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
