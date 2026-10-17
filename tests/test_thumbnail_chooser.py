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

    res = api_client.get(
        f"/api/backup/thumbnails/candidates?path={video_path}&count=12"
    )
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


def test_candidates_endpoint_default_interval_30s(sandbox_env, api_client):
    """Calling without count or interval defaults to candidate frames every 30 seconds."""
    from media_library.core.state import state

    dest = str(sandbox_env["media_dir"])
    state["destination_dir"] = dest
    video_path = os.path.join(dest, "test_30s_interval.mp4")
    _create_mock_video_file(video_path)

    from media_library.db.connection import db_conn

    with db_conn() as conn, conn:
        conn.execute(
            """INSERT INTO files (path, name, folder_path, type, ext, size, size_formatted, mtime, duration, width, height)
               VALUES (?, ?, ?, 'video', '.mp4', 2048, '2 KB', 12345.0, 120.0, 1920, 1080)""",
            (video_path, "test_30s_interval.mp4", dest),
        )

    # Call endpoint with NO count or interval parameter
    res = api_client.get(f"/api/backup/thumbnails/candidates?path={video_path}")
    assert res.status_code == 200
    data = res.json()
    assert data["path"] == video_path
    assert data["duration"] == 120.0
    assert data["interval"] == 30.0
    # For 120s: 0.0, 30.0, 60.0, 90.0, and near-end (119.5) -> 5 candidates
    timestamps = [c["timestamp"] for c in data["candidates"]]
    assert len(timestamps) == 5
    assert timestamps[0] == 0.0
    assert timestamps[1] == 30.0
    assert timestamps[2] == 60.0
    assert timestamps[3] == 90.0
    assert timestamps[4] > 115.0
    assert data["candidates"][0]["time_str"] == "00:00"
    assert data["candidates"][1]["time_str"] == "00:30"
    assert data["candidates"][2]["time_str"] == "01:00"
    assert data["candidates"][3]["time_str"] == "01:30"


def test_candidates_endpoint_explicit_interval(sandbox_env, api_client):
    """Explicit interval=15.0 produces candidates every 15 seconds."""
    from media_library.core.state import state

    dest = str(sandbox_env["media_dir"])
    state["destination_dir"] = dest
    video_path = os.path.join(dest, "test_15s.mp4")
    _create_mock_video_file(video_path)

    from media_library.db.connection import db_conn

    with db_conn() as conn, conn:
        conn.execute(
            """INSERT INTO files (path, name, folder_path, type, ext, size, size_formatted, mtime, duration)
               VALUES (?, ?, ?, 'video', '.mp4', 1024, '1 KB', 12345.0, 60.0)""",
            (video_path, "test_15s.mp4", dest),
        )

    res = api_client.get(
        f"/api/backup/thumbnails/candidates?path={video_path}&interval=15"
    )
    assert res.status_code == 200
    data = res.json()
    assert data["interval"] == 15.0
    timestamps = [c["timestamp"] for c in data["candidates"]]
    # 0, 15, 30, 45, and end frame ~59.5 -> 5 candidates
    assert len(timestamps) == 5
    assert timestamps[0] == 0.0
    assert timestamps[1] == 15.0
    assert timestamps[2] == 30.0
    assert timestamps[3] == 45.0
    assert timestamps[4] > 58.0


def test_candidates_endpoint_short_video_fallback(sandbox_env, api_client):
    """Short video (< 30s) produces a reasonable set of evenly spaced candidate frames."""
    from media_library.core.state import state

    dest = str(sandbox_env["media_dir"])
    state["destination_dir"] = dest
    video_path = os.path.join(dest, "short_clip.mp4")
    _create_mock_video_file(video_path)

    from media_library.db.connection import db_conn

    with db_conn() as conn, conn:
        conn.execute(
            """INSERT INTO files (path, name, folder_path, type, ext, size, size_formatted, mtime, duration)
               VALUES (?, ?, ?, 'video', '.mp4', 512, '512 B', 12345.0, 10.0)""",
            (video_path, "short_clip.mp4", dest),
        )

    res = api_client.get(
        f"/api/backup/thumbnails/candidates?path={video_path}&interval=30"
    )
    assert res.status_code == 200
    data = res.json()
    assert data["count"] >= 3
    assert len(data["candidates"]) == data["count"]
    # Timestamps are strictly within [0.0, 10.0]
    for c in data["candidates"]:
        assert 0.0 <= c["timestamp"] < 10.0


def test_candidates_endpoint_invalid_file_type(sandbox_env, api_client):
    from media_library.core.state import state

    dest = str(sandbox_env["media_dir"])
    state["destination_dir"] = dest
    photo_path = os.path.join(dest, "sample.jpg")
    with open(photo_path, "wb") as f:
        f.write(b"\x00" * 100)

    res = api_client.get(
        f"/api/backup/thumbnails/candidates?path={photo_path}&count=12"
    )
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
    res_bad = api_client.get(
        f"/api/backup/thumbnails/frame?path={img_path}&timestamp=2.5"
    )
    assert res_bad.status_code == 400

    # 2. Reject 404
    res_404 = api_client.get(
        f"/api/backup/thumbnails/frame?path={os.path.join(dest, 'ghost.mp4')}&timestamp=2.5"
    )
    assert res_404.status_code == 404

    # 3. Successful extraction (using mock image fallback)
    res_ok = api_client.get(
        f"/api/backup/thumbnails/frame?path={video_path}&timestamp=5.0"
    )
    assert res_ok.status_code == 200
    assert res_ok.headers["content-type"] == "image/jpeg"
    assert len(res_ok.content) > 0

    # 4. HEAD request works
    res_head = api_client.head(
        f"/api/backup/thumbnails/frame?path={video_path}&timestamp=5.0"
    )
    assert res_head.status_code == 200
    assert res_head.headers["content-type"] == "image/jpeg"
    assert "content-length" in res_head.headers

    # 5. Subsequent request hits cache
    res_cached = api_client.get(
        f"/api/backup/thumbnails/frame?path={video_path}&timestamp=5.0"
    )
    assert res_cached.status_code == 200
    assert res_cached.content == res_ok.content


def test_set_poster_frame_cache_busting_and_persistence(sandbox_env, api_client):
    from media_library.core.state import state
    from media_library.db.connection import db_conn

    dest = str(sandbox_env["media_dir"])
    state["destination_dir"] = dest
    video_path = os.path.join(dest, "poster_test.mp4")
    _create_mock_video_file(video_path)

    # Insert file record without custom thumb_mtime
    with db_conn() as conn, conn:
        conn.execute(
            """INSERT INTO files (path, name, folder_path, type, ext, size, size_formatted, mtime, duration)
               VALUES (?, ?, ?, 'video', '.mp4', 2048, '2 KB', 50000.0, 10.0)""",
            (video_path, "poster_test.mp4", dest),
        )

    # Browse before setting poster
    res_browse_before = api_client.get(f"/api/backup/browse?folder={dest}")
    assert res_browse_before.status_code == 200
    files_before = res_browse_before.json()["files"]
    target_before = next(f for f in files_before if f["path"] == video_path)
    assert "&v=" not in target_before["thumb_url"]

    # Set custom poster
    import base64

    with open(video_path, "rb") as fh:
        img_b64 = "data:image/jpeg;base64," + base64.b64encode(fh.read()).decode()
    res_set = api_client.post(
        "/api/backup/thumbnails/set-poster",
        json={"path": video_path, "timestamp": 3.0, "image_base64": img_b64},
    )
    assert res_set.status_code == 200
    set_data = res_set.json()
    assert set_data["success"] is True
    assert "&v=" in set_data["thumb_url"]

    # Verify DB has thumb_mtime set
    with db_conn() as conn:
        row = conn.execute(
            "SELECT thumb_mtime, has_thumb FROM files WHERE path = ?", (video_path,)
        ).fetchone()
        assert row["has_thumb"] == 1
        assert row["thumb_mtime"] is not None

    # Browse after setting poster: must include &v= matching the saved version
    res_browse_after = api_client.get(f"/api/backup/browse?folder={dest}")
    assert res_browse_after.status_code == 200
    files_after = res_browse_after.json()["files"]
    target_after = next(f for f in files_after if f["path"] == video_path)
    assert "&v=" in target_after["thumb_url"]
    assert f"&v={int(row['thumb_mtime'])}" in target_after["thumb_url"]
    assert target_after["thumb_url"] == set_data["thumb_url"]
