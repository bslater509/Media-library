import asyncio
import base64
import io
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from PIL import Image

from media_library.db.connection import db_conn
from media_library.services.ffmpeg_service import (
    convert_image_file_to_webp,
    convert_image_to_webp_bytes,
    extract_frame_poster,
)
from media_library.workers.webp_worker import WebPOptimizerWorker


def test_convert_image_to_webp_bytes_formats():
    # 1. RGB Image
    rgb_img = Image.new("RGB", (100, 100), color=(255, 0, 0))
    rgb_bytes = io.BytesIO()
    rgb_img.save(rgb_bytes, format="JPEG")
    webp_out = convert_image_to_webp_bytes(rgb_bytes.getvalue(), quality=80)
    assert len(webp_out) > 0
    with Image.open(io.BytesIO(webp_out)) as out_img:
        assert out_img.format == "WEBP"

    # 2. PNG Image (lossless)
    png_bytes = io.BytesIO()
    rgb_img.save(png_bytes, format="PNG")
    webp_png = convert_image_to_webp_bytes(png_bytes.getvalue())
    assert len(webp_png) > 0

    # 3. CMYK Image (converts to RGB)
    cmyk_img = Image.new("CMYK", (50, 50), color=(0, 255, 255, 0))
    cmyk_bytes = io.BytesIO()
    cmyk_img.save(cmyk_bytes, format="JPEG")
    webp_cmyk = convert_image_to_webp_bytes(cmyk_bytes.getvalue())
    assert len(webp_cmyk) > 0
    with Image.open(io.BytesIO(webp_cmyk)) as out_img:
        assert out_img.format == "WEBP"

    # 4. Palette image with transparency (converts to RGBA)
    pal_img = Image.new("P", (50, 50))
    pal_img.info["transparency"] = 0
    pal_bytes = io.BytesIO()
    pal_img.save(pal_bytes, format="PNG")
    webp_pal = convert_image_to_webp_bytes(pal_bytes.getvalue())
    assert len(webp_pal) > 0


def test_convert_image_file_to_webp_statuses(sandbox_env):
    media_dir = sandbox_env["media_dir"]

    # 1. Non-existent file -> error (-3)
    res_missing = convert_image_file_to_webp(str(media_dir / "ghost.jpg"))
    assert res_missing["status"] == "error"

    # 2. Already webp file -> already_webp (1)
    webp_file = media_dir / "already.webp"
    webp_file.write_bytes(b"RIFF....WEBP")
    res_already = convert_image_file_to_webp(str(webp_file))
    assert res_already["status"] == "already_webp"

    # 3. Unsupported extension -> unsupported (-2)
    unsupp = media_dir / "test.xyz"
    unsupp.write_bytes(b"XYZDATA")
    res_unsupp = convert_image_file_to_webp(str(unsupp))
    assert res_unsupp["status"] == "unsupported"

    # 4. Normal JPG converted successfully
    jpg_file = media_dir / "convertible.jpg"
    im = Image.new("RGB", (200, 200), color=(100, 150, 200))
    im.save(jpg_file, "JPEG", quality=95)

    with db_conn() as conn, conn:
        conn.execute(
            "INSERT OR REPLACE INTO files (path, name, folder_path, type, ext, size, webp_optimized) VALUES (?, 'convertible.jpg', ?, 'image', '.jpg', ?, 0)",
            (str(jpg_file), str(media_dir), jpg_file.stat().st_size),
        )

    res_conv = convert_image_file_to_webp(str(jpg_file))
    assert res_conv["status"] == "converted"
    assert (media_dir / "convertible.webp").exists()
    assert not jpg_file.exists()

    # 5. Skip larger test (mock webp size >= orig size)
    jpg_tiny = media_dir / "tiny.jpg"
    im_tiny = Image.new("RGB", (10, 10), color=(0, 0, 0))
    im_tiny.save(jpg_tiny, "JPEG")

    real_stat = Path.stat

    def mock_stat(self, *args, **kwargs):
        st = real_stat(self, *args, **kwargs)
        if "temp_" in self.name or ".tmp_" in self.name:
            mock = MagicMock(st_size=999999)
            return mock
        return st

    with (
        patch("media_library.services.ffmpeg_service.WEBP_SKIP_LARGER", True),
        patch("pathlib.Path.stat", mock_stat),
    ):
        res_skip = convert_image_file_to_webp(str(jpg_tiny))
        assert res_skip["status"] == "skipped"
        assert res_skip["reason"] == "webp_larger_or_equal"


def test_extract_frame_poster_image_base64_and_zip(sandbox_env):
    media_dir = sandbox_env["media_dir"]
    target_vid = media_dir / "poster_test.mp4"
    target_vid.write_bytes(b"fake_video_bytes")

    with db_conn() as conn, conn:
        conn.execute(
            "INSERT OR REPLACE INTO files (path, name, folder_path, type, has_thumb) VALUES (?, 'poster_test.mp4', ?, 'video', 0)",
            (str(target_vid), str(media_dir)),
        )

    # Create dummy 400x200 image and encode to base64 data URI
    pil_im = Image.new("RGB", (400, 200), color=(50, 100, 150))
    buf = io.BytesIO()
    pil_im.save(buf, format="JPEG")
    b64_str = (
        f"data:image/jpeg;base64,{base64.b64encode(buf.getvalue()).decode('ascii')}"
    )

    async def _test():
        thumb_path = await extract_frame_poster(str(target_vid), image_base64=b64_str)
        assert thumb_path is not None
        assert thumb_path.exists()
        with Image.open(thumb_path) as out:
            # Should have resized width <= 360
            assert out.size[0] <= 360

    asyncio.run(_test())

    with db_conn() as conn:
        row = conn.execute(
            "SELECT has_thumb, thumb_hash FROM files WHERE path = ?", (str(target_vid),)
        ).fetchone()
        assert row["has_thumb"] == 1
        assert row["thumb_hash"] is not None


def test_webp_worker_run_loop(sandbox_env):
    worker = WebPOptimizerWorker()
    media_dir = sandbox_env["media_dir"]

    # 1. Create a loose image
    loose_img = media_dir / "loose.jpg"
    Image.new("RGB", (100, 100)).save(loose_img, "JPEG")

    # 2. Create a zip file containing an image
    zip_img = media_dir / "album.zip"
    with zipfile.ZipFile(zip_img, "w") as zf:
        zf.writestr("in_zip.png", b"PNGfake")

    with db_conn() as conn, conn:
        conn.execute(
            "INSERT OR REPLACE INTO files (path, name, folder_path, type, ext, size, webp_optimized) VALUES (?, 'loose.jpg', ?, 'image', '.jpg', 500, 0)",
            (str(loose_img), str(media_dir)),
        )
        conn.execute(
            "INSERT OR REPLACE INTO files (path, name, folder_path, type, ext, size, webp_optimized) VALUES (?, 'in_zip.png', ?, 'image', '.png', 300, 0)",
            (f"{zip_img}/in_zip.png", str(zip_img)),
        )

    # Queue zip item in priority queue
    worker.priority_queue.put_nowait(
        {
            "path": f"{zip_img}/in_zip.png",
            "name": "in_zip.png",
            "folder_path": str(zip_img),
            "is_zip": 1,
        }
    )

    real_sleep = asyncio.sleep
    iteration = 0

    async def _test():
        nonlocal iteration

        async def fast_sleep(sec):
            nonlocal iteration
            iteration += 1
            if iteration >= 4:
                raise asyncio.CancelledError()
            await real_sleep(0)

        with (
            patch("asyncio.sleep", side_effect=fast_sleep),
            patch(
                "media_library.workers.webp_worker.convert_image_file_to_webp",
                return_value={"status": "converted", "converted_count": 1},
            ),
            patch(
                "media_library.workers.webp_worker.optimize_zip_images_to_webp",
                return_value={"status": "converted", "converted_count": 1},
            ),
        ):
            try:
                await worker._run_loop()
            except asyncio.CancelledError:
                pass

    asyncio.run(_test())
    assert worker.state["status"] in ("converting", "idle")
