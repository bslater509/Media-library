"""Tests for media dimension probing and natural-ratio Library tiles."""

import asyncio
import shutil
from pathlib import Path

import pytest
from PIL import Image

from media_library.core.state import state
from media_library.db.connection import get_db
from media_library.db.indexer import (
    add_file_to_index,
    index_folder,
    update_file_in_index,
)


def _columns(conn, table):
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def test_schema_has_dimension_columns(sandbox_env):
    conn = get_db(sandbox_env["db_path"])
    try:
        cols = _columns(conn, "files")
    finally:
        conn.close()
    assert "width" in cols
    assert "height" in cols


def test_index_folder_preserves_dimensions_for_unchanged_file(sandbox_env):
    media_dir = sandbox_env["media_dir"]
    cat = media_dir / "dimcat"
    cat.mkdir()
    img = cat / "photo.jpg"
    img.write_bytes(b"\xff\xd8\xff\xe0" + b"A" * 32)

    index_folder(str(cat), base_dir=str(media_dir))

    conn = get_db(sandbox_env["db_path"])
    try:
        with conn:
            conn.execute(
                "UPDATE files SET width = 800, height = 600 WHERE path = ?", (str(img),)
            )

        # Re-indexing an unchanged file must carry the dimensions over.
        index_folder(str(cat), base_dir=str(media_dir))
        row = conn.execute(
            "SELECT width, height FROM files WHERE path = ?", (str(img),)
        ).fetchone()
        assert (row["width"], row["height"]) == (800, 600)

        # A modified file (different size) must drop its stale dimensions so the
        # metadata worker re-probes it.
        img.write_bytes(b"\xff\xd8\xff\xe0" + b"B" * 96)
        index_folder(str(cat), base_dir=str(media_dir))
        row = conn.execute(
            "SELECT width, height FROM files WHERE path = ?", (str(img),)
        ).fetchone()
        assert row["width"] is None
        assert row["height"] is None
    finally:
        conn.close()


def test_rename_preserves_dimensions(sandbox_env):
    media_dir = sandbox_env["media_dir"]
    src = media_dir / "old.jpg"
    src.write_bytes(b"\xff\xd8\xff\xe0" + b"C" * 64)
    add_file_to_index(str(src))

    conn = get_db(sandbox_env["db_path"])
    try:
        with conn:
            conn.execute(
                "UPDATE files SET width = 300, height = 400 WHERE path = ?", (str(src),)
            )
    finally:
        conn.close()

    dst = media_dir / "new.jpg"
    src.rename(dst)
    update_file_in_index(str(src), str(dst), dst.name)

    conn = get_db(sandbox_env["db_path"])
    try:
        row = conn.execute(
            "SELECT width, height FROM files WHERE path = ?", (str(dst),)
        ).fetchone()
        assert row is not None
        assert (row["width"], row["height"]) == (300, 400)
    finally:
        conn.close()


@pytest.mark.skipif(shutil.which("ffprobe") is None, reason="ffprobe not installed")
def test_probe_media_metadata_reads_source_size(sandbox_env):
    from media_library.services.ffmpeg_service import probe_media_metadata

    media_dir = sandbox_env["media_dir"]
    img = media_dir / "probe.png"
    Image.new("RGB", (320, 240), color="red").save(img)

    meta = asyncio.run(probe_media_metadata(str(img)))
    assert meta["width"] == 320
    assert meta["height"] == 240


def test_browse_exposes_file_dimensions(api_client):
    dest = Path(state["destination_dir"])
    cat = dest / "dimcat"
    cat.mkdir()
    img = cat / "photo.jpg"
    img.write_bytes(b"\xff\xd8\xff\xe0" + b"A" * 64)

    index_folder(str(dest), str(dest))
    index_folder(str(cat), str(dest))

    conn = get_db()
    try:
        with conn:
            conn.execute(
                "UPDATE files SET width = 1280, height = 720 WHERE path = ?",
                (str(img),),
            )
    finally:
        conn.close()

    res = api_client.get("/api/backup/browse", params={"path": str(cat)})
    assert res.status_code == 200
    files = {f["name"]: f for f in res.json()["files"]}
    assert files["photo.jpg"]["width"] == 1280
    assert files["photo.jpg"]["height"] == 720


def test_browse_exposes_folder_cover_dimensions(api_client):
    dest = Path(state["destination_dir"])
    cat = dest / "coverdim"
    cat.mkdir()
    cover = cat / "a.jpg"
    cover.write_bytes(b"\xff\xd8\xff\xe0" + b"A" * 64)

    index_folder(str(dest), str(dest))
    index_folder(str(cat), str(dest))

    conn = get_db()
    try:
        with conn:
            conn.execute(
                "UPDATE files SET width = 400, height = 900 WHERE path = ?",
                (str(cover),),
            )
    finally:
        conn.close()

    res = api_client.get("/api/backup/browse", params={"path": str(dest)})
    assert res.status_code == 200
    subs = {s["name"]: s for s in res.json()["subfolders"]}
    assert subs["coverdim"]["width"] == 400
    assert subs["coverdim"]["height"] == 900
    assert subs["coverdim"]["total_size"] == 68


def test_search_exposes_dimensions(api_client):
    dest = Path(state["destination_dir"])
    cat = dest / "searchdim"
    cat.mkdir()
    img = cat / "uniquename.jpg"
    img.write_bytes(b"\xff\xd8\xff\xe0" + b"A" * 64)

    index_folder(str(dest), str(dest))
    index_folder(str(cat), str(dest))

    conn = get_db()
    try:
        with conn:
            conn.execute(
                "UPDATE files SET width = 1920, height = 1080 WHERE path = ?",
                (str(img),),
            )
    finally:
        conn.close()

    res = api_client.get("/api/backup/search", params={"q": "uniquename"})
    assert res.status_code == 200
    results = res.json()["results"]
    match = next(r for r in results if r["name"] == "uniquename.jpg")
    assert match["width"] == 1920
    assert match["height"] == 1080


def test_metadata_worker_probes_loose_files_and_skips_zip(sandbox_env, monkeypatch):
    """The worker probes loose files once and never extracts ZIP archives to probe."""
    import io
    import zipfile

    from media_library.services.zip import index_zip_archive
    from media_library.workers.metadata_worker import MediaMetadataWorker

    media_dir = sandbox_env["media_dir"]

    loose = media_dir / "loose"
    loose.mkdir()
    loose_img = loose / "photo.jpg"
    loose_img.write_bytes(b"\xff\xd8\xff\xe0" + b"A" * 64)
    loose_vid = loose / "clip.mp4"
    loose_vid.write_bytes(b"\x00\x00\x00 ftypmp42" + b"B" * 128)
    index_folder(str(loose), base_dir=str(media_dir))

    zip_path = media_dir / "album.zip"
    img_bytes = io.BytesIO()
    Image.new("RGB", (16, 16), color="blue").save(img_bytes, format="JPEG")
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("inside.jpg", img_bytes.getvalue())
    index_zip_archive(str(zip_path), parent_path=str(media_dir))

    probed_paths = []

    async def fake_probe(path):
        probed_paths.append(path)
        return {
            "width": 640,
            "height": 480,
            "duration": 12.5,
            "video_codec": "h264",
            "audio_codec": "aac",
            "fps": 29.97,
            "capture_time": None,
        }

    monkeypatch.setattr(
        "media_library.workers.metadata_worker.probe_media_metadata", fake_probe
    )

    worker = MediaMetadataWorker()

    async def run():
        task = asyncio.create_task(worker._run_loop())
        try:
            for _ in range(100):
                await asyncio.sleep(0.05)
                conn = get_db()
                try:
                    done = conn.execute(
                        "SELECT COUNT(*) FROM files WHERE width IS NOT NULL AND path IN (?, ?)",
                        (str(loose_img), str(loose_vid)),
                    ).fetchone()[0]
                finally:
                    conn.close()
                if done == 2:
                    break
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    asyncio.run(run())

    assert str(loose_img) in probed_paths
    assert str(loose_vid) in probed_paths
    assert not any("album.zip" in p for p in probed_paths), (
        "ZIP members must not be probed"
    )

    conn = get_db(sandbox_env["db_path"])
    try:
        vid_row = conn.execute(
            "SELECT width, height, duration, video_codec, audio_codec, fps FROM files WHERE path = ?",
            (str(loose_vid),),
        ).fetchone()
        assert (vid_row["width"], vid_row["height"]) == (640, 480)
        assert vid_row["duration"] == 12.5
        assert vid_row["video_codec"] == "h264"
        assert vid_row["audio_codec"] == "aac"
        assert vid_row["fps"] == 29.97

        # Images keep dimensions/capture time but must not carry video-only fields.
        img_row = conn.execute(
            "SELECT width, height, duration, video_codec, audio_codec, fps FROM files WHERE path = ?",
            (str(loose_img),),
        ).fetchone()
        assert (img_row["width"], img_row["height"]) == (640, 480)
        assert img_row["duration"] is None
        assert img_row["video_codec"] is None
        assert img_row["audio_codec"] is None
        assert img_row["fps"] is None

        zip_row = conn.execute(
            "SELECT width FROM files WHERE path LIKE ?", ("%album.zip%inside.jpg",)
        ).fetchone()
        assert zip_row is not None
        assert zip_row["width"] is None
    finally:
        conn.close()


def test_metadata_worker_backfills_zip_from_thumbnail(sandbox_env, monkeypatch):
    """ZIP members get dimensions from their cached local thumbnail (no extraction)."""
    import io
    import zipfile

    from media_library.core.state import get_thumbnail_cache_path
    from media_library.services.zip import index_zip_archive
    from media_library.workers.metadata_worker import MediaMetadataWorker

    media_dir = sandbox_env["media_dir"]
    zip_path = media_dir / "thumbs.zip"

    vid = io.BytesIO()
    Image.new("RGB", (16, 16), color="red").save(vid, format="JPEG")
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("clip.mp4", vid.getvalue())
    index_zip_archive(str(zip_path), parent_path=str(media_dir))

    member_path = str(zip_path / "clip.mp4")
    conn = get_db(sandbox_env["db_path"])
    try:
        row = conn.execute(
            "SELECT mtime, size FROM files WHERE path = ?", (member_path,)
        ).fetchone()
    finally:
        conn.close()

    # Write a thumbnail with a non-square aspect ratio at the exact cache path.
    thumb = get_thumbnail_cache_path(member_path, row["mtime"], row["size"])
    thumb.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (90, 60), color="green").save(thumb, format="JPEG")

    # No ffprobe call should happen for the ZIP member.
    async def fail_probe(path):
        raise AssertionError("source probing must not run for ZIP members")

    monkeypatch.setattr(
        "media_library.workers.metadata_worker.probe_media_metadata", fail_probe
    )

    worker = MediaMetadataWorker()

    async def run():
        task = asyncio.create_task(worker._run_loop())
        try:
            for _ in range(100):
                await asyncio.sleep(0.05)
                conn = get_db()
                try:
                    done = conn.execute(
                        "SELECT width FROM files WHERE path = ?", (member_path,)
                    ).fetchone()
                finally:
                    conn.close()
                if done and done["width"] is not None:
                    break
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    asyncio.run(run())

    conn = get_db(sandbox_env["db_path"])
    try:
        row = conn.execute(
            "SELECT width, height FROM files WHERE path = ?", (member_path,)
        ).fetchone()
        assert (row["width"], row["height"]) == (90, 60)
    finally:
        conn.close()


def test_folder_total_size_recorded(sandbox_env, sample_media):
    action_cat = sample_media["action_cat"]
    media_dir = sandbox_env["media_dir"]

    index_folder(str(action_cat), base_dir=str(media_dir))

    expected = sum(p.stat().st_size for p in action_cat.iterdir() if p.is_file())

    conn = get_db(sandbox_env["db_path"])
    try:
        row = conn.execute(
            "SELECT total_size FROM folders WHERE path = ?", (str(action_cat),)
        ).fetchone()
        assert row is not None
        assert row["total_size"] == expected
    finally:
        conn.close()


def test_system_status_includes_metadata(api_client):
    res = api_client.get("/api/system/status")
    assert res.status_code == 200
    data = res.json()
    assert "metadata" in data
    assert "status" in data["metadata"]
    assert "pending_count" in data["metadata"]


def test_system_status_metadata_reports_zip_member_counts(sandbox_env):
    """The metadata payload exposes ZIP totals/pending so the UI can show the
    ZIP-member sweep separately from the loose-file probe."""
    import io
    import zipfile

    from media_library.services.zip import index_zip_archive
    from media_library.workers.metadata_worker import MediaMetadataWorker

    media_dir = sandbox_env["media_dir"]
    zip_path = media_dir / "counts.zip"
    img = io.BytesIO()
    Image.new("RGB", (16, 16), color="blue").save(img, format="JPEG")
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("one.jpg", img.getvalue())
        zf.writestr("two.jpg", img.getvalue())
    index_zip_archive(str(zip_path), parent_path=str(media_dir))

    worker = MediaMetadataWorker()
    worker._refresh_stats()

    assert worker.state["total_files"] == 0
    assert worker.state["zip_total"] == 2
    assert worker.state["zip_pending_count"] == 2


def test_metadata_worker_stays_probing_during_zip_sweep(sandbox_env, monkeypatch):
    """While the ZIP-member sweep still has batches, the worker must report
    ``probing`` instead of flipping to ``idle`` — the old code cleared the
    status every loop iteration when no loose files were pending, which hid the
    sweep from /api/system/status and the Library's worker panel."""
    from media_library.workers.metadata_worker import MediaMetadataWorker

    worker = MediaMetadataWorker()
    calls = {"n": 0}

    async def always_active():
        calls["n"] += 1
        worker.state["current_file"] = "member.jpg"
        return True

    monkeypatch.setattr(worker, "_backfill_zip_batch", always_active)

    async def run():
        task = asyncio.create_task(worker._run_loop())
        try:
            for _ in range(200):
                await asyncio.sleep(0.02)
                if calls["n"] > 0 and worker.state["status"] == "probing":
                    break
            assert calls["n"] > 0, "ZIP sweep should have been invoked"
            assert worker.state["status"] == "probing"
            assert worker.state["current_file"] == "member.jpg"
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    asyncio.run(run())
