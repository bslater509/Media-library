"""Comprehensive integration and unit tests for HTTP byte-range streaming and library media endpoints."""

import asyncio
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from starlette.requests import Request

from media_library.core.http_stream import stream_file_with_range
from media_library.core.state import state
from media_library.db.connection import db_conn


def test_http_stream_byte_ranges(tmp_path):
    test_file = tmp_path / "stream_data.bin"
    content = b"0123456789" * 10  # 100 bytes
    test_file.write_bytes(content)

    async def _run():
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [(b"range", b"bytes=0-9")],
        }
        req = Request(scope)
        res = await stream_file_with_range(
            str(test_file), req, mime_type="application/octet-stream"
        )
        assert res.status_code == 206
        assert res.headers["content-length"] == "10"
        assert res.headers["content-range"] == "bytes 0-9/100"

        # Suffix range bytes=-15
        scope_suffix = {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [(b"range", b"bytes=-15")],
        }
        req_suffix = Request(scope_suffix)
        res_suffix = await stream_file_with_range(str(test_file), req_suffix)
        assert res_suffix.status_code == 206
        assert res_suffix.headers["content-length"] == "15"
        assert res_suffix.headers["content-range"] == "bytes 85-99/100"

        # Start-only range bytes=80-
        scope_start = {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [(b"range", b"bytes=80-")],
        }
        req_start = Request(scope_start)
        res_start = await stream_file_with_range(str(test_file), req_start)
        assert res_start.status_code == 206
        assert res_start.headers["content-length"] == "20"
        assert res_start.headers["content-range"] == "bytes 80-99/100"

        # Invalid range order (start > end) -> 416
        scope_inv = {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [(b"range", b"bytes=50-20")],
        }
        req_inv = Request(scope_inv)
        res_inv = await stream_file_with_range(str(test_file), req_inv)
        assert res_inv.status_code == 416

        # Range outside file size -> 416
        scope_out = {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [(b"range", b"bytes=200-300")],
        }
        req_out = Request(scope_out)
        res_out = await stream_file_with_range(str(test_file), req_out)
        assert res_out.status_code == 416

        # Full file (no range header)
        scope_full = {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [],
        }
        req_full = Request(scope_full)
        res_full = await stream_file_with_range(str(test_file), req_full)
        assert res_full.status_code == 200
        assert res_full.headers["content-length"] == "100"

        # HEAD request
        scope_head = {
            "type": "http",
            "method": "HEAD",
            "path": "/",
            "headers": [(b"range", b"bytes=0-9")],
        }
        req_head = Request(scope_head)
        res_head = await stream_file_with_range(str(test_file), req_head)
        assert res_head.status_code == 200
        assert res_head.headers["content-length"] == "100"

    asyncio.run(_run())


def test_library_media_thumbnails_and_streaming(api_client, tmp_path):
    dest = Path(state["destination_dir"])

    # 1. Image file
    img_file = dest / "test_photo.jpg"
    img = Image.new("RGB", (100, 100), color="blue")
    img.save(img_file, format="JPEG")

    # GET /api/backup/media for image
    res_img_media = api_client.get("/api/backup/media", params={"path": str(img_file)})
    assert res_img_media.status_code == 200
    assert "image/jpeg" in res_img_media.headers.get("content-type", "")

    # HEAD /api/backup/media for image
    res_img_head = api_client.head("/api/backup/media", params={"path": str(img_file)})
    assert res_img_head.status_code == 200
    assert res_img_head.headers.get("content-type") == "image/jpeg"

    # Missing file 404
    res_missing_media = api_client.get(
        "/api/backup/media", params={"path": str(dest / "ghost.jpg")}
    )
    assert res_missing_media.status_code == 404

    # 2. Thumbnail endpoint
    # Unsupported media extension -> 400
    txt_file = dest / "note.txt"
    txt_file.write_text("hello")
    res_txt_thumb = api_client.get(
        "/api/backup/thumbnail", params={"path": str(txt_file)}
    )
    assert res_txt_thumb.status_code == 400

    # Missing file -> 404
    res_miss_thumb = api_client.get(
        "/api/backup/thumbnail", params={"path": str(dest / "ghost.jpg")}
    )
    assert res_miss_thumb.status_code == 404

    # Image thumbnail generation (first call generates it)
    res_thumb_img = api_client.get(
        "/api/backup/thumbnail", params={"path": str(img_file)}
    )
    assert res_thumb_img.status_code in (200, 304)

    # Second call hits the cached thumbnail fast path and provides an ETag
    res_thumb_cached = api_client.get(
        "/api/backup/thumbnail", params={"path": str(img_file)}
    )
    assert res_thumb_cached.status_code == 200
    etag = res_thumb_cached.headers.get("etag")
    assert etag is not None

    # Third call with If-None-Match yields 304
    res_304 = api_client.get(
        "/api/backup/thumbnail",
        params={"path": str(img_file)},
        headers={"if-none-match": etag},
    )
    assert res_304.status_code == 304

    res_thumb_head = api_client.head(
        "/api/backup/thumbnail", params={"path": str(img_file)}
    )
    assert res_thumb_head.status_code == 200


def test_thumbnail_candidates_and_frame_endpoints(api_client, tmp_path):
    dest = Path(state["destination_dir"])
    vid_file = dest / "sample_video.mp4"
    vid_file.write_bytes(b"\x00\x00\x00 ftypmp42" + b"X" * 1024)

    with db_conn() as conn, conn:
        conn.execute(
            """INSERT INTO files (path, name, folder_path, type, ext, size, size_formatted, mtime, duration, width, height)
               VALUES (?, ?, ?, 'video', '.mp4', 1024, '1 KB', 100.0, 60.0, 1920, 1080)""",
            (str(vid_file), "sample_video.mp4", str(dest)),
        )

    # 1. Thumbnail Candidates
    # Unsupported file type -> 400
    txt_file = dest / "note.txt"
    txt_file.write_text("hello")
    res_txt_cand = api_client.get(
        "/api/backup/thumbnails/candidates", params={"path": str(txt_file)}
    )
    assert res_txt_cand.status_code == 400

    # Missing video -> 404
    res_miss_cand = api_client.get(
        "/api/backup/thumbnails/candidates", params={"path": str(dest / "ghost.mp4")}
    )
    assert res_miss_cand.status_code == 404

    # Valid video
    res_cand = api_client.get(
        "/api/backup/thumbnails/candidates", params={"path": str(vid_file), "count": 6}
    )
    assert res_cand.status_code == 200
    c_data = res_cand.json()
    assert c_data["duration"] == 60.0
    assert c_data["count"] == 6
    assert len(c_data["candidates"]) == 6
    assert c_data["width"] == 1920
    assert c_data["height"] == 1080

    # 2. Thumbnail Frame extraction
    # Unsupported file type -> 400
    res_txt_frame = api_client.get(
        "/api/backup/thumbnails/frame", params={"path": str(txt_file), "timestamp": 1.0}
    )
    assert res_txt_frame.status_code == 400

    # Missing video -> 404
    res_miss_frame = api_client.get(
        "/api/backup/thumbnails/frame",
        params={"path": str(dest / "ghost.mp4"), "timestamp": 1.0},
    )
    assert res_miss_frame.status_code == 404

    # Mock candidate frame extraction
    fake_frame = tmp_path / "cand_frame.jpg"
    Image.new("RGB", (64, 64), color="red").save(fake_frame, format="JPEG")

    with patch(
        "media_library.routers.library_media.extract_candidate_frame",
        return_value=fake_frame,
    ):
        res_frame = api_client.get(
            "/api/backup/thumbnails/frame",
            params={"path": str(vid_file), "timestamp": 12.5},
        )
        assert res_frame.status_code == 200
        assert "image/jpeg" in res_frame.headers.get("content-type", "")

        # HEAD request
        res_frame_head = api_client.head(
            "/api/backup/thumbnails/frame",
            params={"path": str(vid_file), "timestamp": 12.5},
        )
        assert res_frame_head.status_code == 200
        assert res_frame_head.headers.get("content-type") == "image/jpeg"
