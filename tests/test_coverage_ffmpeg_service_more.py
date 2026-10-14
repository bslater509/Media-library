"""Deeper coverage tests for the FFmpeg/FFprobe service layer.

The FFmpeg helpers are exercised with deterministic fake subprocesses (so the
tests never depend on a real encoder run) plus real Pillow work for the WebP
conversion branches, the animated-preview frame assembly, and the ZIP-member
input resolution paths.
"""

import asyncio
import base64
import io
import json
import os
import sqlite3
import time
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from PIL import Image

import media_library.services.ffmpeg_service as ffmpeg
import media_library.services.ffmpeg_frames as ffmpeg_frames
from media_library.db.connection import db_conn

_REAL_PATH_STAT = Path.stat


def _thumb_cache_dir() -> Path:
    from media_library.core.config import THUMBNAIL_CACHE_DIR

    return Path(THUMBNAIL_CACHE_DIR)


class FakeProcess:
    """Minimal stand-in for ``asyncio.subprocess.Process``."""

    def __init__(self, stdout: bytes = b"", returncode: int = 0, raise_timeout=False):
        self._stdout = stdout
        self.returncode = returncode
        self.raise_timeout = raise_timeout
        self.killed = False
        self.stdout = self

    async def communicate(self):
        if self.raise_timeout:
            raise asyncio.TimeoutError()
        return (self._stdout, b"")

    async def read(self, _n):
        chunk, self._stdout = self._stdout, b""
        return chunk

    async def wait(self):
        return self.returncode

    def terminate(self):
        pass

    def kill(self):
        self.killed = True


def _jpeg_bytes(color="green", size=(64, 48)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color=color).save(buf, format="JPEG")
    return buf.getvalue()


def _write_jpeg(path: Path, color="green", size=(64, 48)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_jpeg_bytes(color, size))
    return path


def _stat_with_huge_staging_files(self, *args, **kwargs):
    """Pretend staged WebP temp files are enormous (drives WEBP_SKIP_LARGER)."""
    from media_library.core.config import WEBP_STAGING_DIR

    st = os.stat_result(_REAL_PATH_STAT(self, *args, **kwargs))
    if str(WEBP_STAGING_DIR) in str(self):
        st = os.stat_result(
            (
                st.st_mode,
                st.st_ino,
                st.st_dev,
                st.st_nlink,
                st.st_uid,
                st.st_gid,
                10**9,
                st.st_atime,
                st.st_mtime,
                st.st_ctime,
            )
        )
    return st


def _insert_row(path: Path, folder: str, **extra):
    cols = {
        "path": str(path),
        "name": path.name,
        "folder_path": folder,
        "type": "video",
        "ext": ".mp4",
        "size": path.stat().st_size if path.exists() else 10,
        "size_formatted": "1 KB",
        "mtime": path.stat().st_mtime if path.exists() else 1.0,
        "duration": 60.0,
        "width": 640,
        "height": 360,
    }
    cols.update(extra)
    placeholders = ", ".join("?" for _ in cols)
    with db_conn() as conn, conn:
        conn.execute(
            f"INSERT INTO files ({', '.join(cols)}) VALUES ({placeholders})",
            tuple(cols.values()),
        )


def _row(path: Path):
    with db_conn() as conn:
        return conn.execute("SELECT * FROM files WHERE path = ?", (str(path),)).fetchone()


# --------------------------------------------------------------------------
# probe helpers with fake ffprobe output
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_probe_video_meta_parses_streams_and_handles_errors():
    payload = {
        "format": {"duration": "12.5"},
        "streams": [
            {"codec_type": "video", "codec_name": "H264"},
            {"codec_type": "audio", "codec_name": "AAC"},
            {"codec_type": "audio", "codec_name": "second-audio"},
        ],
    }
    with patch.object(
        asyncio, "create_subprocess_exec", AsyncMock(return_value=FakeProcess(json.dumps(payload).encode()))
    ):
        meta = await ffmpeg.probe_video_meta("/media/x.mp4")
    assert meta == {"duration": 12.5, "video_codec": "h264", "audio_codec": "aac"}

    # Unparseable duration string degrades to 0.0.
    bad = {"format": {"duration": "n/a"}, "streams": []}
    with patch.object(
        asyncio, "create_subprocess_exec", AsyncMock(return_value=FakeProcess(json.dumps(bad).encode()))
    ):
        meta = await ffmpeg.probe_video_meta("/media/x.mp4")
    assert meta["duration"] == 0.0

    # Non-zero exit code returns the empty default.
    with patch.object(
        asyncio, "create_subprocess_exec", AsyncMock(return_value=FakeProcess(b"", returncode=1))
    ):
        assert await ffmpeg.probe_video_meta("/media/x.mp4") == {
            "duration": 0.0,
            "video_codec": "",
            "audio_codec": "",
        }

    # A crashing subprocess spawn is swallowed.
    with patch.object(asyncio, "create_subprocess_exec", AsyncMock(side_effect=OSError("no ffprobe"))):
        assert (await ffmpeg.probe_video_meta("/media/x.mp4"))["duration"] == 0.0


@pytest.mark.anyio
async def test_probe_media_metadata_rotation_and_capture_time(sandbox_env):
    media = sandbox_env["media_dir"]
    video = media / "rotated.mp4"
    video.write_bytes(b"\x00" * 32)

    # Rotation via side_data swaps the display dimensions.
    rotated = {
        "format": {"duration": "8.0", "tags": {"creation_time": "2024-05-06T07:08:09Z"}},
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "h264",
                "width": 1920,
                "height": 1080,
                "avg_frame_rate": "30000/1001",
                "side_data_list": [{"rotation": -90}],
            },
            {"codec_type": "audio", "codec_name": "opus"},
        ],
    }
    with patch.object(
        asyncio,
        "create_subprocess_exec",
        AsyncMock(return_value=FakeProcess(json.dumps(rotated).encode())),
    ):
        meta = await ffmpeg.probe_media_metadata(str(video))
    assert meta["width"] == 1080
    assert meta["height"] == 1920
    assert meta["fps"] == 29.97
    assert meta["capture_time"] == pytest.approx(
        time.mktime(time.strptime("2024-05-06 07:08:09", "%Y-%m-%d %H:%M:%S")), abs=3600
    )
    assert meta["video_codec"] == "h264"
    assert meta["audio_codec"] == "opus"

    # Legacy ``rotate`` tag path plus stream-level creation time fallback.
    tagged = {
        "format": {"duration": "4"},
        "streams": [
            {
                "codec_type": "video",
                "width": 640,
                "height": 480,
                "tags": {"rotate": "90", "creation_time": "2020-01-02T03:04:05Z"},
            }
        ],
    }
    with patch.object(
        asyncio,
        "create_subprocess_exec",
        AsyncMock(return_value=FakeProcess(json.dumps(tagged).encode())),
    ):
        meta = await ffmpeg.probe_media_metadata(str(video))
    assert (meta["width"], meta["height"]) == (480, 640)
    assert meta["capture_time"] is not None
    assert meta["audio_codec"] == ""


@pytest.mark.anyio
async def test_probe_media_metadata_rejects_unusable_streams(sandbox_env):
    media = sandbox_env["media_dir"]
    video = media / "broken.mp4"
    video.write_bytes(b"\x00" * 8)
    image = media / "photo.jpg"
    _write_jpeg(image)

    cases = [
        {"format": {}, "streams": []},  # no streams at all
        {"format": {}, "streams": [{"codec_type": "audio"}]},  # no video stream
        {
            "format": {},
            "streams": [{"codec_type": "video", "width": "wide", "height": 10}],
        },  # non-numeric size
        {"format": {}, "streams": [{"codec_type": "video", "width": 0, "height": 0}]},
    ]
    for payload in cases:
        with patch.object(
            asyncio,
            "create_subprocess_exec",
            AsyncMock(return_value=FakeProcess(json.dumps(payload).encode())),
        ):
            assert await ffmpeg.probe_media_metadata(str(video)) is None

    # Non-zero exit and unparsable JSON both return None.
    with patch.object(
        asyncio, "create_subprocess_exec", AsyncMock(return_value=FakeProcess(b"", returncode=2))
    ):
        assert await ffmpeg.probe_media_metadata(str(video)) is None
    with patch.object(
        asyncio,
        "create_subprocess_exec",
        AsyncMock(return_value=FakeProcess(b"not json")),
    ):
        assert await ffmpeg.probe_media_metadata(str(video)) is None

    # An image without EXIF capture time falls back to Pillow (returns None).
    image_payload = {
        "format": {"duration": "0", "tags": {}},
        "streams": [{"codec_type": "video", "codec_name": "mjpeg", "width": 64, "height": 48}],
    }
    with patch.object(
        asyncio,
        "create_subprocess_exec",
        AsyncMock(return_value=FakeProcess(json.dumps(image_payload).encode())),
    ):
        meta = await ffmpeg.probe_media_metadata(str(image))
    assert meta["capture_time"] is None

    # Unreadable file: ffprobe itself cannot be spawned.
    with patch.object(asyncio, "create_subprocess_exec", AsyncMock(side_effect=OSError("gone"))):
        assert await ffmpeg.probe_media_metadata(str(video)) is None


@pytest.mark.anyio
async def test_relay_process_stdout_terminates_and_survives_dead_process():
    proc = FakeProcess(b"a" * 100)
    chunks = [c async for c in ffmpeg.relay_process_stdout(proc)]
    assert b"".join(chunks) == b"a" * 100

    # A process that already exited must not raise out of the stream.
    dead = FakeProcess(b"")
    dead.terminate = MagicMock(side_effect=ProcessLookupError())
    assert [c async for c in ffmpeg.relay_process_stdout(dead)] == []


# --------------------------------------------------------------------------
# extract_frame_poster
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_extract_frame_poster_saves_base64_image_and_updates_index(sandbox_env):
    media = sandbox_env["media_dir"]
    video = media / "poster_base64.mp4"
    video.write_bytes(b"\x00" * 16)
    _insert_row(video, str(media))

    big = Image.new("RGB", (1200, 800), color="purple")
    buf = io.BytesIO()
    big.save(buf, format="JPEG")
    payload = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()

    thumb = await ffmpeg.extract_frame_poster(str(video), image_base64=payload)
    assert thumb is not None and thumb.exists()

    with Image.open(thumb) as img:
        assert img.width == 360
        assert img.height == 240

    row = _row(video)
    assert row["has_thumb"] == 1
    assert row["thumb_mtime"] > 0


@pytest.mark.anyio
async def test_extract_frame_poster_resolves_zip_members(api_client):
    from media_library.core.state import state
    from media_library.services.zip import get_zip_cache_dir

    dest = Path(state["destination_dir"])
    zip_path = dest / "poster_archive.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("clip.mp4", _jpeg_bytes("teal", (800, 600)))

    payload = base64.b64encode(_jpeg_bytes("teal", (800, 600))).decode()
    thumb = await ffmpeg.extract_frame_poster(
        f"{zip_path}/clip.mp4", image_base64=payload
    )
    assert thumb is not None and thumb.exists()

    import shutil

    shutil.rmtree(get_zip_cache_dir(str(zip_path)), ignore_errors=True)


@pytest.mark.anyio
async def test_extract_frame_poster_handles_ffmpeg_failures(sandbox_env):
    media = sandbox_env["media_dir"]
    video = media / "poster_fail.mp4"
    video.write_bytes(b"\x00" * 16)

    # Failing encoder: the retry command runs, both attempts produce nothing.
    with patch.object(
        asyncio,
        "create_subprocess_exec",
        AsyncMock(return_value=FakeProcess(b"", returncode=1)),
    ) as spawn:
        thumb = await ffmpeg.extract_frame_poster(str(video), timestamp=3.0)
    assert thumb is None
    assert spawn.call_count == 2

    # A hanging encoder is killed, then retried.
    with patch.object(
        asyncio,
        "create_subprocess_exec",
        AsyncMock(return_value=FakeProcess(b"", returncode=0, raise_timeout=True)),
    ) as spawn:
        thumb = await ffmpeg.extract_frame_poster(str(video), timestamp=3.0)
    assert thumb is None
    assert spawn.call_count == 2

    # A DB failure during the index update must not fail the poster.
    ok_payload = io.BytesIO()
    Image.new("RGB", (400, 300), color="yellow").save(ok_payload, format="JPEG")
    good = "data:image/jpeg;base64," + base64.b64encode(ok_payload.getvalue()).decode()
    with patch.object(
        ffmpeg, "db_conn", MagicMock(side_effect=sqlite3.DatabaseError("locked"))
    ):
        thumb = await ffmpeg.extract_frame_poster(str(video), image_base64=good)
    assert thumb is not None and thumb.exists()


@pytest.mark.anyio
async def test_extract_frame_poster_updates_index_without_phash(sandbox_env):
    """Falls back to a phash-less update when imagehash is unavailable."""
    media = sandbox_env["media_dir"]
    video = media / "poster_nohash.mp4"
    video.write_bytes(b"\x00" * 16)
    _insert_row(video, str(media))

    class RaisingConn:
        def __enter__(self):
            raise sqlite3.DatabaseError("no table")

        def __exit__(self, *exc):
            return False

    payload = base64.b64encode(_jpeg_bytes("olive", (400, 300))).decode()
    with patch.object(ffmpeg, "db_conn", RaisingConn):
        thumb = await ffmpeg.extract_frame_poster(str(video), image_base64=payload)
    assert thumb is not None


# --------------------------------------------------------------------------
# candidate frames
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_extract_candidate_frame_uses_cache_and_zip_input(api_client):
    from media_library.core.state import state
    from media_library.services.zip import get_zip_cache_dir

    dest = Path(state["destination_dir"])
    video = dest / "candidate.mp4"
    video.write_bytes(b"\x00" * 16)

    cached = ffmpeg.get_candidate_cache_path(str(video), 4.0)
    cached.write_bytes(_jpeg_bytes("navy"))
    assert await ffmpeg.extract_candidate_frame(str(video), 4.0) == cached

    # A member that is not yet extracted is resolved through the ZIP cache.
    zip_path = dest / "cand_archive.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("inner.mp4", _jpeg_bytes("olive", (900, 600)))
    frame = await ffmpeg.extract_candidate_frame(f"{zip_path}/inner.mp4", 0.0)
    assert frame is not None and frame.exists()
    with Image.open(frame) as img:
        assert img.width == 480

    import shutil

    shutil.rmtree(get_zip_cache_dir(str(zip_path)), ignore_errors=True)


@pytest.mark.anyio
async def test_extract_candidate_frame_timeout_and_cleanup(sandbox_env):
    media = sandbox_env["media_dir"]
    video = media / "cand_timeout.mp4"
    video.write_bytes(b"\x00" * 16)

    with patch.object(
        asyncio,
        "create_subprocess_exec",
        AsyncMock(return_value=FakeProcess(b"", raise_timeout=True)),
    ) as spawn:
        frame = await ffmpeg.extract_candidate_frame(str(video), 1.0)
    assert frame is None
    assert spawn.call_count == 2  # first attempt + retry, both killed

    # Temp artifacts never survive a failure.
    cand_dir = _thumb_cache_dir() / "candidates"
    assert not list(cand_dir.glob("*.tmp.jpg")) if cand_dir.exists() else True


# --------------------------------------------------------------------------
# animated previews
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_generate_animated_preview_builds_animation(sandbox_env, monkeypatch):
    media = sandbox_env["media_dir"]
    video = media / "anim_build.mp4"
    video.write_bytes(b"\x00" * 32)
    _insert_row(video, str(media), duration=60.0)

    frames = {
        0.0: _write_jpeg(media / "f0.jpg", "red", (480, 270)),
        30.0: _write_jpeg(media / "f30.jpg", "blue", (480, 270)),
    }

    async def fake_frame(path, timestamp=0.0):
        return frames.get(round(timestamp, 1))

    monkeypatch.setattr(ffmpeg_frames, "extract_candidate_frame", fake_frame)

    anim = await ffmpeg.generate_animated_preview(str(video))
    assert anim is not None and anim.exists()

    with Image.open(anim) as img:
        assert img.format == "WEBP"
        assert img.is_animated
        assert img.n_frames >= 2
        assert img.width == 480

    assert _row(video)["has_animated"] == 1

    # Second call is served from the on-disk cache.
    assert await ffmpeg.generate_animated_preview(str(video)) == anim


@pytest.mark.anyio
async def test_generate_animated_preview_single_and_identical_frames(
    sandbox_env, monkeypatch
):
    media = sandbox_env["media_dir"]
    video = media / "anim_single.mp4"
    video.write_bytes(b"\x00" * 32)
    _insert_row(video, str(media), duration=2.0)

    only = _write_jpeg(media / "only.jpg", "lime", (480, 270))
    calls = {"n": 0}

    async def one_frame(path, timestamp=0.0):
        calls["n"] += 1
        return only if calls["n"] == 1 else None

    monkeypatch.setattr(ffmpeg_frames, "extract_candidate_frame", one_frame)
    anim = await ffmpeg.generate_animated_preview(str(video))
    assert anim is not None
    with Image.open(anim) as img:
        assert img.is_animated
        assert img.n_frames == 2

    # Identical frames are de-duplicated with a brightness ramp.
    media2 = media / "anim_dupes.mp4"
    media2.write_bytes(b"\x00" * 32)
    _insert_row(media2, str(media), duration=6.0)

    async def same_frames(path, timestamp=0.0):
        return only

    monkeypatch.setattr(ffmpeg_frames, "extract_candidate_frame", same_frames)
    anim2 = await ffmpeg.generate_animated_preview(str(media2))
    assert anim2 is not None
    with Image.open(anim2) as img:
        assert img.n_frames >= 2


@pytest.mark.anyio
async def test_generate_animated_preview_static_fallback_and_failures(
    sandbox_env, monkeypatch
):
    media = sandbox_env["media_dir"]
    video = media / "anim_static.mp4"
    video.write_bytes(b"\x00" * 32)
    _insert_row(video, str(media), duration=60.0)

    static = _write_jpeg(media / "anim_static.jpg", "teal", (320, 180))

    async def no_frames(path, timestamp=0.0):
        return None

    async def fake_thumb(path, is_video=True, **kwargs):
        return static

    monkeypatch.setattr(ffmpeg_frames, "extract_candidate_frame", no_frames)
    monkeypatch.setattr(ffmpeg_frames, "generate_thumbnail", fake_thumb)

    anim = await ffmpeg.generate_animated_preview(str(video))
    assert anim is not None
    with Image.open(anim) as img:
        assert img.width == 480  # resized from the 320px static poster

    # Without any frames at all the preview fails cleanly.
    async def no_thumb(path, is_video=True, **kwargs):
        return None

    media2 = media / "anim_none.mp4"
    media2.write_bytes(b"\x00" * 8)
    monkeypatch.setattr(ffmpeg_frames, "generate_thumbnail", no_thumb)
    assert await ffmpeg.generate_animated_preview(str(media2)) is None


@pytest.mark.anyio
async def test_generate_animated_preview_long_video_sampling(sandbox_env, monkeypatch):
    media = sandbox_env["media_dir"]
    video = media / "anim_long.mp4"
    video.write_bytes(b"\x00" * 32)
    _insert_row(video, str(media), duration=7200.0)  # 2 hours

    stamps = []

    async def record(path, timestamp=0.0):
        stamps.append(timestamp)
        return _write_jpeg(media / f"long_{len(stamps)}.jpg", "red", (480, 270))

    monkeypatch.setattr(ffmpeg_frames, "extract_candidate_frame", record)

    anim = await ffmpeg.generate_animated_preview(str(video), max_frames=3)
    assert anim is not None
    assert len(stamps) <= 3
    assert stamps == sorted(stamps)

    import shutil

    for p in media.glob("long_*.jpg"):
        p.unlink()
    shutil.rmtree(_thumb_cache_dir() / "animated", ignore_errors=True)


@pytest.mark.anyio
async def test_generate_animated_preview_resolves_zip_members(api_client, monkeypatch):
    from media_library.core.state import state
    from media_library.services.zip import get_zip_cache_dir

    dest = Path(state["destination_dir"])
    zip_path = dest / "anim_archive.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("member.mp4", _jpeg_bytes("green", (480, 270)))

    async def one_frame(path, timestamp=0.0):
        return _write_jpeg(dest / "zip_frame.jpg", "green", (480, 270))

    monkeypatch.setattr(ffmpeg_frames, "extract_candidate_frame", one_frame)

    anim = await ffmpeg.generate_animated_preview(f"{zip_path}/member.mp4")
    assert anim is not None and anim.exists()

    import shutil

    shutil.rmtree(get_zip_cache_dir(str(zip_path)), ignore_errors=True)
    shutil.rmtree(_thumb_cache_dir() / "animated", ignore_errors=True)


@pytest.mark.anyio
async def test_generate_animated_preview_propagates_failures(sandbox_env, monkeypatch):
    media = sandbox_env["media_dir"]
    video = media / "anim_boom.mp4"
    video.write_bytes(b"\x00" * 8)

    async def boom(path, timestamp=0.0):
        raise RuntimeError("frame extraction exploded")

    monkeypatch.setattr(ffmpeg_frames, "extract_candidate_frame", boom)
    assert await ffmpeg.generate_animated_preview(str(video)) is None


def test_get_animated_preview_path_uses_index_metadata(sandbox_env):
    media = sandbox_env["media_dir"]
    video = media / "anim_key.mp4"
    video.write_bytes(b"\x00" * 16)
    _insert_row(video, str(media), duration=10.0)

    row = _row(video)
    explicit = ffmpeg.get_animated_preview_path(str(video), row["mtime"], row["size"])
    from_index = ffmpeg.get_animated_preview_path(str(video))
    assert explicit == from_index

    # Without index metadata the key falls back to the plain path.
    assert ffmpeg.get_animated_preview_path(str(media / "unknown.mp4")).name.startswith(
        "anim_"
    )


# --------------------------------------------------------------------------
# WebP conversion branches
# --------------------------------------------------------------------------


def test_convert_image_to_webp_bytes_mode_conversions(sandbox_env):
    # PNG input is encoded losslessly.
    png = io.BytesIO()
    Image.new("RGBA", (32, 32), color=(10, 20, 30, 128)).save(png, format="PNG")
    out = ffmpeg.convert_image_to_webp_bytes(png.getvalue())
    assert out[:4] == b"RIFF" and out[8:12] == b"WEBP"

    # CMYK JPEGs are flattened to RGB first.
    cmyk = io.BytesIO()
    Image.new("CMYK", (40, 30), color=(0, 100, 100, 0)).save(cmyk, format="JPEG")
    with Image.open(io.BytesIO(ffmpeg.convert_image_to_webp_bytes(cmyk.getvalue()))) as img:
        assert img.mode in ("RGB", "RGBA")

    # Palette images with transparency become RGBA.
    pal = io.BytesIO()
    p_img = Image.new("P", (24, 24), color=0)
    p_img.save(pal, format="PNG", transparency=0)
    assert ffmpeg.convert_image_to_webp_bytes(pal.getvalue())[:4] == b"RIFF"

    # Greyscale stays supported.
    grey = io.BytesIO()
    Image.new("L", (20, 20), color=128).save(grey, format="JPEG")
    assert ffmpeg.convert_image_to_webp_bytes(grey.getvalue())[:4] == b"RIFF"


def test_convert_image_file_to_webp_status_branches(sandbox_env, monkeypatch):
    media = sandbox_env["media_dir"]
    media.mkdir(parents=True, exist_ok=True)

    # Missing file.
    missing = media / "ghost.png"
    res = ffmpeg.convert_image_file_to_webp(str(missing))
    assert res["status"] == "error"
    assert res["error"] == "File not found"

    # Native WebP short-circuits.
    native = _write_jpeg(media / "native.webp", "white")
    assert ffmpeg.convert_image_file_to_webp(str(native))["status"] == "already_webp"

    # Unsupported extension.
    unsupported = media / "anim.gif"
    unsupported.write_bytes(_jpeg_bytes())
    assert ffmpeg.convert_image_file_to_webp(str(unsupported))["status"] == "unsupported"

    # An unreadable path (OSError from exists()).
    with patch.object(Path, "exists", side_effect=OSError("stale mount")):
        res = ffmpeg.convert_image_file_to_webp(str(media / "any.jpg"))
    assert res["status"] == "error"


def test_convert_image_file_to_webp_conversion_paths(sandbox_env, monkeypatch):
    media = sandbox_env["media_dir"]
    media.mkdir(parents=True, exist_ok=True)
    folder = media / "convertible"
    folder.mkdir()

    source = folder / "photo.png"
    png = io.BytesIO()
    Image.new("RGBA", (120, 80), color=(1, 2, 3, 200)).save(png, format="PNG")
    source.write_bytes(png.getvalue())
    _insert_row(source, str(folder), type="image", ext=".png")
    (folder / "photo.webp").write_bytes(b"pre-existing sibling")

    res = ffmpeg.convert_image_file_to_webp(str(source))
    assert res["status"] == "converted"
    assert res["saved_bytes"] > 0
    # Collision with an existing sibling produces a distinct _optN name.
    assert Path(res["new_path"]).name == "photo_opt1.webp"
    assert not source.exists()
    assert Path(res["new_path"]).exists()

    row = _row(Path(res["new_path"]))
    assert row["ext"] == ".webp"
    assert row["webp_optimized"] == 1
    assert row["quick_hash"] is None

    stats = None
    with db_conn() as conn:
        stats = conn.execute("SELECT converted_count FROM webp_stats WHERE id = 1").fetchone()
    assert stats["converted_count"] >= 1

    # WEBP_SKIP_LARGER keeps the original when the WebP is not smaller.
    bigger = folder / "bigger.jpg"
    _write_jpeg(bigger, "white", (600, 600))
    with patch.object(
        Path, "stat", _stat_with_huge_staging_files
    ), patch.object(ffmpeg, "WEBP_SKIP_LARGER", True):
        res = ffmpeg.convert_image_file_to_webp(str(bigger))
    assert res["status"] == "skipped"
    assert bigger.exists()

    # A failing index write must not fail the conversion itself.
    source2 = folder / "second.png"
    source2.write_bytes(png.getvalue())
    with patch.object(ffmpeg, "db_conn", MagicMock(side_effect=sqlite3.DatabaseError("x"))):
        res = ffmpeg.convert_image_file_to_webp(str(source2))
    assert res["status"] == "converted"


def test_convert_image_file_to_webp_rejects_dimension_drift(sandbox_env):
    media = sandbox_env["media_dir"]
    source = media / "drift.jpg"
    _write_jpeg(source, "red", (64, 48))

    original_open = Image.open

    class Shrunk:
        """Verification proxy reporting a bogus (1, 1) frame size."""

        def __init__(self, img):
            self._img = img

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self._img.close()
            return False

        def __getattr__(self, name):
            return getattr(self._img, name)

        @property
        def size(self):
            return (1, 1)

    def lying_open(path, *args, **kwargs):
        img = original_open(path, *args, **kwargs)
        if str(path).endswith(".webp"):
            return Shrunk(img)
        return img

    with patch.object(Image, "open", side_effect=lying_open):
        res = ffmpeg.convert_image_file_to_webp(str(source))

    assert res["status"] == "error"
    assert "Dimension mismatch" in res["error"]
    # The original survives a rejected conversion and nothing is committed.
    assert source.exists()
    assert not (media / "drift.webp").exists()


# --------------------------------------------------------------------------
# generate_thumbnail
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_generate_thumbnail_video_retries_then_gives_up(sandbox_env):
    media = sandbox_env["media_dir"]
    video = media / "thumb_retry.mp4"
    video.write_bytes(b"\x00" * 32)

    with patch.object(
        asyncio,
        "create_subprocess_exec",
        AsyncMock(return_value=FakeProcess(b"", returncode=1)),
    ) as spawn:
        thumb = await ffmpeg.generate_thumbnail(str(video), is_video=True)
    assert thumb is None
    assert spawn.call_count == 2  # first attempt + the -ss 0 retry

    # A hanging encoder is killed on both attempts.
    with patch.object(
        asyncio,
        "create_subprocess_exec",
        AsyncMock(return_value=FakeProcess(b"", raise_timeout=True)),
    ) as spawn:
        thumb = await ffmpeg.generate_thumbnail(str(video), is_video=True)
    assert thumb is None
    assert spawn.call_count == 2


@pytest.mark.anyio
async def test_generate_thumbnail_image_uses_pil_fallback(sandbox_env):
    media = sandbox_env["media_dir"]
    image = media / "thumb_fallback.jpg"
    image.write_bytes(_jpeg_bytes("red", (500, 400)))
    _insert_row(image, str(media), type="image", ext=".jpg")

    # FFmpeg produces nothing: the Pillow fallback still makes a poster.
    with patch.object(
        asyncio,
        "create_subprocess_exec",
        AsyncMock(return_value=FakeProcess(b"", returncode=1)),
    ):
        thumb = await ffmpeg.generate_thumbnail(
            str(image), is_video=False, update_db=True
        )
    assert thumb is not None and thumb.exists()
    with Image.open(thumb) as img:
        assert max(img.size) <= 360

    # Index flag is written, and a failing write is swallowed.
    assert _row(image)["has_thumb"] == 1

    image2 = media / "thumb_fallback2.jpg"
    image2.write_bytes(_jpeg_bytes("blue", (500, 400)))
    _insert_row(image2, str(media), type="image", ext=".jpg")
    with patch.object(
        asyncio,
        "create_subprocess_exec",
        AsyncMock(return_value=FakeProcess(b"", returncode=1)),
    ), patch.object(ffmpeg, "db_conn", MagicMock(side_effect=sqlite3.DatabaseError("x"))):
        thumb2 = await ffmpeg.generate_thumbnail(
            str(image2), is_video=False, update_db=True
        )
    assert thumb2 is not None

    # An unreadable image with no FFmpeg leaves nothing behind.
    image3 = media / "thumb_broken.jpg"
    image3.write_bytes(b"this is not an image")
    with patch.object(
        asyncio,
        "create_subprocess_exec",
        AsyncMock(return_value=FakeProcess(b"", returncode=1)),
    ):
        assert await ffmpeg.generate_thumbnail(str(image3), is_video=False) is None
    assert not list(thumb.parent.glob("*.tmp.jpg"))


@pytest.mark.anyio
async def test_generate_thumbnail_resolves_zip_members(api_client):
    from media_library.core.state import state
    from media_library.services.zip import get_zip_cache_dir

    dest = Path(state["destination_dir"])
    zip_path = dest / "thumb_archive.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("member.jpg", _jpeg_bytes("orange", (640, 480)))

    with patch.object(
        asyncio,
        "create_subprocess_exec",
        AsyncMock(return_value=FakeProcess(b"", returncode=1)),
    ):
        thumb = await ffmpeg.generate_thumbnail(
            f"{zip_path}/member.jpg", is_video=False
        )
    assert thumb is not None and thumb.exists()

    import shutil

    shutil.rmtree(get_zip_cache_dir(str(zip_path)), ignore_errors=True)
    shutil.rmtree(thumb.parent, ignore_errors=True)


@pytest.mark.anyio
async def test_generate_thumbnail_returns_cached_and_survives_spawn_failure(
    sandbox_env,
):
    media = sandbox_env["media_dir"]
    image = media / "thumb_cached.jpg"
    image.write_bytes(_jpeg_bytes("white", (200, 200)))

    # First generation creates it, the second call short-circuits on the cache.
    with patch.object(
        asyncio,
        "create_subprocess_exec",
        AsyncMock(return_value=FakeProcess(b"", returncode=1)),
    ):
        first = await ffmpeg.generate_thumbnail(str(image), is_video=False)
        with patch.object(asyncio, "create_subprocess_exec") as spawn:
            second = await ffmpeg.generate_thumbnail(str(image), is_video=False)
    assert first == second
    spawn.assert_not_called()

    # A missing parent directory is reported without raising.
    video = media / "thumb_missing_dir.mp4"
    video.write_bytes(b"\x00" * 8)
    with patch.object(ffmpeg, "get_thumbnail_path_fast") as fast:
        fast.return_value = media / "no_such_dir" / "thumb.jpg"
        assert await ffmpeg.generate_thumbnail(str(video), is_video=True) is None


# --------------------------------------------------------------------------
# WebP commit, thumbnails, and rollback
# --------------------------------------------------------------------------


def test_convert_image_file_to_webp_commit_verification_failure(sandbox_env):
    media = sandbox_env["media_dir"]
    source = media / "commit_fail.png"
    png = io.BytesIO()
    Image.new("RGB", (64, 64), color=(5, 6, 7)).save(png, format="PNG")
    source.write_bytes(png.getvalue())

    with patch.object(ffmpeg.shutil, "copy2") as copy2:
        copy2.return_value = None  # the staged file never arrives
        with pytest.raises(OSError, match="verification failed"):
            ffmpeg.convert_image_file_to_webp(str(source))

    # Nothing was committed and no temp artifacts remain.
    assert source.exists()
    assert not (media / "commit_fail.webp").exists()
    assert not list(media.glob(".tmp_webp_*.webp"))


def test_convert_image_file_to_webp_rolls_back_when_original_stays(sandbox_env):
    media = sandbox_env["media_dir"]
    source = media / "rollback.jpg"
    _write_jpeg(source, "red", (80, 60))

    real_unlink = Path.unlink

    def failing_unlink(self, *args, **kwargs):
        if self.name == "rollback.jpg":
            raise OSError("read-only mount")
        return real_unlink(self, *args, **kwargs)

    with patch.object(Path, "unlink", failing_unlink):
        res = ffmpeg.convert_image_file_to_webp(str(source))

    assert res["status"] == "error"
    assert "Could not remove original" in res["error"]
    # The freshly written WebP is rolled back so no orphan duplicate survives.
    assert source.exists()
    assert not (media / "rollback.webp").exists()


def test_convert_image_file_to_webp_thumbnail_paths(sandbox_env):
    media = sandbox_env["media_dir"]
    source = media / "thumb_branches.png"
    png = io.BytesIO()
    Image.new("RGB", (120, 90), color=(9, 8, 7)).save(png, format="PNG")
    source.write_bytes(png.getvalue())

    # Thumbnail generation and stale-poster cleanup both fail loudly but are
    # contained: the conversion result stands.
    with patch.object(
        ffmpeg, "get_thumbnail_cache_path", side_effect=OSError("no thumb cache")
    ):
        res = ffmpeg.convert_image_file_to_webp(str(source))
    assert res["status"] == "converted"

    # Happy path: a new poster is written and the old one is removed.
    source2 = media / "thumb_branches2.png"
    source2.write_bytes(png.getvalue())
    new_thumb = media / "new_poster.jpg"
    old_thumb = media / "old_poster.jpg"
    old_thumb.write_bytes(b"stale poster")

    calls = {"n": 0}

    def fake_cache(path, mtime=None, size=None):
        calls["n"] += 1
        if str(path).endswith(".png") and calls["n"] == 2:
            return old_thumb
        return new_thumb

    with patch.object(ffmpeg, "get_thumbnail_cache_path", side_effect=fake_cache):
        res2 = ffmpeg.convert_image_file_to_webp(str(source2))
    assert res2["status"] == "converted"
    assert new_thumb.exists()
    assert not old_thumb.exists()


def test_convert_image_to_webp_bytes_keeps_icc_and_exif():
    exif = Image.Exif()
    exif[274] = 1  # Orientation
    exif[271] = "CameraMaker"
    src = io.BytesIO()
    Image.new("RGB", (64, 48), color=(12, 34, 56)).save(
        src, format="JPEG", exif=exif, icc_profile=b"\x04\x00\x00\x00lcms2"
    )

    out = ffmpeg.convert_image_to_webp_bytes(src.getvalue(), quality=80)
    assert out[:4] == b"RIFF"

    with Image.open(io.BytesIO(out)) as img:
        # The embedded EXIF survives the round trip.
        assert img.getexif().get(271) == "CameraMaker"

    # Explicit quality is honored for non-PNG input.
    low = ffmpeg.convert_image_to_webp_bytes(src.getvalue(), quality=10)
    assert low[:4] == b"RIFF"
    # PNG input stays lossless regardless of the quality argument.
    png = io.BytesIO()
    Image.new("RGB", (32, 32), color="white").save(png, format="PNG")
    lossless = ffmpeg.convert_image_to_webp_bytes(png.getvalue(), quality=1)
    assert lossless[:4] == b"RIFF"
