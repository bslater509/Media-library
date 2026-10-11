"""Unit and integration tests for video thumbnail chooser endpoints."""

import io
import os
from PIL import Image


def _create_mock_video_file(path: str):
    """Creates a small mock video file that PIL can decode as a fallback."""
    buf = io.BytesIO()
    img = Image.new("RGB", (320, 240), color="blue")
    img.save(buf, format="JPEG")
    with open(path, "wb") as f:
        f.write(buf.getvalue())


def test_candidates_endpoint_valid_video(sandbox_env, api_client):
    from media_library.core.state import state
    dest = str(sandbox_env["media_dir"])
    state["destination_dir"] = dest
    video_path = os.path.join(dest, "test_clip.mp4")
    _create_mock_video_file(video_path)

    # Populate in files table with duration
    from media_library.db.connection import db_conn
    with db_conn() as conn, conn:
        conn.execute(
            """INSERT INTO files (path, name, folder_path, type, ext, size, size_formatted, mtime, duration, width, height)
               VALUES (?, ?, ?, 'video', '.mp4', 1024, '1 KB', 12345.0, 60.0, 1920, 1080)""",
            (video_path, "test_clip.mp4", dest),
        )

    res = api_client.get(f"/api/backup/thumbnails/candidates?path={video_path}&count=12")
    assert res.status_code == 200
    data = res.json()
    assert data["path"] == video_path
    assert data["duration"] == 60.0
    assert data["duration_formatted"] == "01:00"
    assert data["count"] == 12
    assert len(data["candidates"]) == 12
    assert data["width"] == 1920
    assert data["height"] == 1080

    for item in data["candidates"]:
        assert "timestamp" in item
        assert "time_str" in item
        assert "url" in item
        assert item["url"].startswith("/api/backup/thumbnails/frame?")


def test_candidates_endpoint_invalid_file_type(sandbox_env, api_client):
    from media_library.core.state import state
    dest = str(sandbox_env["media_dir"])
    state["destination_dir"] = dest
    photo_path = os.path.join(dest, "sample.jpg")
    with open(photo_path, "wb") as f:
        f.write(b"\x00" * 100)

    res = api_client.get(f"/api/backup/thumbnails/candidates?path={photo_path}&count=12")
    assert res.status_code == 400
    assert "Only video files" in res.json()["detail"]


def test_candidates_endpoint_not_found(sandbox_env, api_client):
    from media_library.core.state import state
    dest = str(sandbox_env["media_dir"])
    state["destination_dir"] = dest
    missing = os.path.join(dest, "nonexistent.mp4")
    res = api_client.get(f"/api/backup/thumbnails/candidates?path={missing}&count=12")
    assert res.status_code == 404


def test_candidate_frame_extraction_and_caching(sandbox_env, api_client):
    from media_library.core.state import state
    dest = str(sandbox_env["media_dir"])
    state["destination_dir"] = dest
    video_path = os.path.join(dest, "test_frame.mp4")
    _create_mock_video_file(video_path)

    # 1. Reject non-video
    img_path = os.path.join(dest, "photo.png")
    with open(img_path, "wb") as f:
        f.write(b"\x00" * 100)
    res_bad = api_client.get(f"/api/backup/thumbnails/frame?path={img_path}&timestamp=2.5")
    assert res_bad.status_code == 400

    # 2. Reject 404
    res_404 = api_client.get(f"/api/backup/thumbnails/frame?path={os.path.join(dest, 'ghost.mp4')}&timestamp=2.5")
    assert res_404.status_code == 404

    # 3. Successful extraction (using mock image fallback)
    res_ok = api_client.get(f"/api/backup/thumbnails/frame?path={video_path}&timestamp=5.0")
    assert res_ok.status_code == 200
    assert res_ok.headers["content-type"] == "image/jpeg"
    assert len(res_ok.content) > 0

    # 4. HEAD request works
    res_head = api_client.head(f"/api/backup/thumbnails/frame?path={video_path}&timestamp=5.0")
    assert res_head.status_code == 200
    assert res_head.headers["content-type"] == "image/jpeg"
    assert "content-length" in res_head.headers

    # 5. Subsequent request hits cache
    res_cached = api_client.get(f"/api/backup/thumbnails/frame?path={video_path}&timestamp=5.0")
    assert res_cached.status_code == 200
    assert res_cached.content == res_ok.content
