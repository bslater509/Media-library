"""Coverage tests for the media streaming router and the library admin router.

Covers ZIP-member thumbnail/media resolution, thumbnail generation HEAD/GET
handling, the thumbnail-candidate and candidate-frame endpoints, animated
preview fallbacks, and the rename/delete/poster/animated admin endpoints'
error and cleanup branches.
"""

import io
import sqlite3
import time
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from PIL import Image

from media_library.core.state import get_thumbnail_cache_path, state
from media_library.db.connection import db_conn


def _jpeg_bytes(color="green", size=(64, 48)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color=color).save(buf, format="JPEG")
    return buf.getvalue()


def _write_jpeg(path: Path, color="green", size=(64, 48)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_jpeg_bytes(color, size))
    return path


def _insert_file_row(path: Path, folder: str, ftype="video", ext=".mp4") -> None:
    with db_conn() as conn, conn:
        conn.execute(
            """INSERT INTO files (path, name, folder_path, type, ext, size, size_formatted,
                                  mtime, duration, width, height)
               VALUES (?, ?, ?, ?, ?, ?, '1 KB', ?, 60.0, 640, 360)""",
            (
                str(path),
                path.name,
                folder,
                ftype,
                ext,
                path.stat().st_size,
                path.stat().st_mtime,
            ),
        )


def _row(path: Path):
    with db_conn() as conn:
        return conn.execute(
            "SELECT * FROM files WHERE path = ?", (str(path),)
        ).fetchone()


# --------------------------------------------------------------------------
# ZIP member media & thumbnails
# --------------------------------------------------------------------------


def _make_zip(dest: Path, name="archive.zip", member="photo.jpg") -> Path:
    zip_path = dest / name
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr(member, _jpeg_bytes("orange"))
    return zip_path


def test_thumbnail_and_media_stream_for_zip_members(api_client):
    from media_library.services.zip import get_zip_cache_dir

    dest = Path(state["destination_dir"])
    zip_path = _make_zip(dest)
    member_vpath = f"{zip_path}/photo.jpg"

    res_thumb = api_client.get("/api/backup/thumbnail", params={"path": member_vpath})
    assert res_thumb.status_code == 200
    assert "image/jpeg" in res_thumb.headers.get("content-type", "")

    res_media = api_client.get("/api/backup/media", params={"path": member_vpath})
    assert res_media.status_code == 200

    res_head = api_client.head("/api/backup/media", params={"path": member_vpath})
    assert res_head.status_code == 200
    assert int(res_head.headers["content-length"]) > 0

    import shutil

    shutil.rmtree(get_zip_cache_dir(str(zip_path)), ignore_errors=True)


def test_thumbnail_rejects_unsupported_and_missing_files(api_client):
    dest = Path(state["destination_dir"])

    txt = dest / "notes.txt"
    txt.write_text("not media")
    assert (
        api_client.get("/api/backup/thumbnail", params={"path": str(txt)}).status_code
        == 400
    )
    # Non-image media still streams raw bytes through the range handler.
    assert (
        api_client.get("/api/backup/media", params={"path": str(txt)}).status_code
        == 200
    )
    assert (
        api_client.get(
            "/api/backup/thumbnail", params={"path": str(dest / "ghost.jpg")}
        ).status_code
        == 404
    )
    assert (
        api_client.get(
            "/api/backup/media", params={"path": str(dest / "ghost.mp4")}
        ).status_code
        == 404
    )


def test_thumbnail_generated_on_demand_with_head_support(api_client):
    dest = Path(state["destination_dir"])
    img = _write_jpeg(dest / "fresh_photo.jpg")

    # No cached thumbnail yet: the router generates one and serves it.
    res = api_client.get("/api/backup/thumbnail", params={"path": str(img)})
    assert res.status_code == 200
    assert "image/jpeg" in res.headers.get("content-type", "")
    assert get_thumbnail_cache_path(str(img)).exists()

    # HEAD on the generation path (thumbnail still absent for a second file).
    img2 = _write_jpeg(dest / "fresh_photo2.jpg", color="red")
    res_head = api_client.head("/api/backup/thumbnail", params={"path": str(img2)})
    assert res_head.status_code == 200
    assert int(res_head.headers["content-length"]) > 0


def test_thumbnail_etag_and_immutable_cache_headers(api_client):
    dest = Path(state["destination_dir"])
    img = _write_jpeg(dest / "cached_photo.jpg")

    # First request generates the poster, the second one hits the cache and
    # carries the router's own ETag.
    api_client.get("/api/backup/thumbnail", params={"path": str(img)})
    cached_hit = api_client.get("/api/backup/thumbnail", params={"path": str(img)})
    assert cached_hit.status_code == 200
    etag = cached_hit.headers["etag"]
    assert etag.startswith('"thumb_')

    cached = api_client.get(
        "/api/backup/thumbnail",
        params={"path": str(img)},
        headers={"If-None-Match": etag},
    )
    assert cached.status_code == 304

    # mtime+size addressed thumbnails are served as immutable.
    stat = img.stat()
    immutable = api_client.get(
        "/api/backup/thumbnail",
        params={
            "path": str(img),
            "mtime": stat.st_mtime,
            "size": stat.st_size,
        },
    )
    assert immutable.status_code == 200
    assert "immutable" in immutable.headers["cache-control"]


# --------------------------------------------------------------------------
# Thumbnail candidates & candidate frames
# --------------------------------------------------------------------------


def test_candidates_endpoint_count_interval_and_fallbacks(api_client):
    dest = Path(state["destination_dir"])
    video = dest / "long_clip.mp4"
    video.write_bytes(b"\x00" * 64)
    with db_conn() as conn, conn:
        conn.execute(
            """INSERT INTO files (path, name, folder_path, type, ext, size, size_formatted,
                                  mtime, duration, width, height)
               VALUES (?, ?, ?, 'video', '.mp4', ?, '1 KB', ?, 7500.0, 1920, 1080)""",
            (
                str(video),
                video.name,
                str(dest),
                video.stat().st_size,
                video.stat().st_mtime,
            ),
        )

    # Interval mode (default 30s) with an hour-plus duration formats as HH:MM:SS.
    data = api_client.get(
        "/api/backup/thumbnails/candidates", params={"path": str(video)}
    ).json()
    assert data["duration"] == 7500.0
    assert data["duration_formatted"].startswith("02:05")
    assert data["width"] == 1920
    assert len(data["candidates"]) > 100
    assert data["interval"] == 30.0

    # Count mode divides the duration evenly.
    counted = api_client.get(
        "/api/backup/thumbnails/candidates",
        params={"path": str(video), "count": 5},
    ).json()
    assert counted["count"] == 5
    assert counted["interval"] is None
    stamps = [c["timestamp"] for c in counted["candidates"]]
    assert stamps == sorted(stamps)

    # Explicit interval mode.
    spaced = api_client.get(
        "/api/backup/thumbnails/candidates",
        params={"path": str(video), "interval": 1800.0},
    ).json()
    assert spaced["interval"] == 1800.0
    assert spaced["count"] == len(spaced["candidates"])

    # Validation branches.
    assert (
        api_client.get(
            "/api/backup/thumbnails/candidates", params={"path": str(dest / "a.jpg")}
        ).status_code
        == 400
    )
    assert (
        api_client.get(
            "/api/backup/thumbnails/candidates",
            params={"path": str(dest / "ghost.mp4")},
        ).status_code
        == 404
    )


def test_candidates_endpoint_probes_when_index_has_no_duration(api_client):
    dest = Path(state["destination_dir"])
    video = dest / "unindexed_clip.mp4"
    video.write_bytes(b"\x00" * 32)

    with patch(
        "media_library.routers.library_media.db_conn",
        side_effect=sqlite3.DatabaseError("x"),
    ):
        data = api_client.get(
            "/api/backup/thumbnails/candidates", params={"path": str(video)}
        ).json()

    # ffprobe cannot read the stub file, so the duration degrades to 1s.
    assert data["duration"] == 1.0
    assert data["count"] >= 2


def test_frame_endpoint_serves_cached_candidate_frames(api_client):
    from media_library.services.ffmpeg_service import get_candidate_cache_path

    dest = Path(state["destination_dir"])
    video = dest / "frame_clip.mp4"
    video.write_bytes(b"\x00" * 32)

    # Pre-seed the candidate cache so no FFmpeg run is needed.
    cached = get_candidate_cache_path(str(video), 12.0)
    cached.write_bytes(_jpeg_bytes("cyan"))

    res = api_client.get(
        "/api/backup/thumbnails/frame",
        params={"path": str(video), "timestamp": 12.0},
    )
    assert res.status_code == 200
    assert "image/jpeg" in res.headers.get("content-type", "")

    head = api_client.head(
        "/api/backup/thumbnails/frame",
        params={"path": str(video), "timestamp": 12.0},
    )
    assert head.status_code == 200
    assert int(head.headers["content-length"]) > 0

    assert (
        api_client.get(
            "/api/backup/thumbnails/frame", params={"path": str(dest / "x.jpg")}
        ).status_code
        == 400
    )
    assert (
        api_client.get(
            "/api/backup/thumbnails/frame", params={"path": str(dest / "ghost.mp4")}
        ).status_code
        == 404
    )


def test_frame_endpoint_fails_when_extraction_is_impossible(api_client):
    dest = Path(state["destination_dir"])
    video = dest / "bad_frame.mp4"
    video.write_bytes(b"\x00" * 16)

    with patch(
        "media_library.routers.library_media.extract_candidate_frame",
        new=AsyncMock(return_value=None),
    ):
        res = api_client.get(
            "/api/backup/thumbnails/frame",
            params={"path": str(video), "timestamp": 1.0},
        )
    assert res.status_code == 500


# --------------------------------------------------------------------------
# Animated preview fallbacks
# --------------------------------------------------------------------------


def test_animated_preview_falls_back_to_static_thumbnail(api_client):
    dest = Path(state["destination_dir"])
    video = dest / "anim_fallback.mp4"
    video.write_bytes(b"\x00" * 32)
    static_thumb = _write_jpeg(dest / "anim_fallback.jpg")

    with (
        patch(
            "media_library.routers.library_media.generate_animated_preview",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "media_library.routers.library_media.generate_thumbnail",
            new=AsyncMock(return_value=static_thumb),
        ),
    ):
        res = api_client.get(
            "/api/backup/thumbnails/animated", params={"path": str(video)}
        )
    assert res.status_code == 200
    assert "image/jpeg" in res.headers.get("content-type", "")

    # HEAD of the fallback thumbnail.
    with (
        patch(
            "media_library.routers.library_media.generate_animated_preview",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "media_library.routers.library_media.generate_thumbnail",
            new=AsyncMock(return_value=static_thumb),
        ),
    ):
        head = api_client.head(
            "/api/backup/thumbnails/animated", params={"path": str(video)}
        )
    assert head.status_code == 200
    assert int(head.headers["content-length"]) > 0

    # Without any usable thumbnail the request is a hard failure.
    with (
        patch(
            "media_library.routers.library_media.generate_animated_preview",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "media_library.routers.library_media.generate_thumbnail",
            new=AsyncMock(return_value=None),
        ),
    ):
        res = api_client.get(
            "/api/backup/thumbnails/animated", params={"path": str(video)}
        )
    assert res.status_code == 500


def test_animated_preview_wait_false_enqueue_branches(api_client):
    import importlib

    library_media = importlib.import_module("media_library.routers.library_media")

    dest = Path(state["destination_dir"])
    video = dest / "anim_queued.mp4"
    video.write_bytes(b"\x00" * 32)
    static_thumb = _write_jpeg(dest / "anim_queued.jpg")

    enqueued = []
    worker = MagicMock()
    worker.enqueue.side_effect = lambda p, priority=False: enqueued.append(
        (p, priority)
    )

    with (
        patch.object(library_media, "animated_thumb_worker_instance", worker),
        patch.object(
            library_media,
            "generate_thumbnail",
            new=AsyncMock(return_value=static_thumb),
        ),
    ):
        res = api_client.get(
            "/api/backup/thumbnails/animated",
            params={"path": str(video), "wait": "false"},
        )
    assert res.status_code == 200
    assert res.headers["x-thumbnail-status"] == "generating"
    assert enqueued == [(str(video), True)]

    # HEAD on the same path reports the pending generation.
    with (
        patch.object(library_media, "animated_thumb_worker_instance", worker),
        patch.object(
            library_media,
            "generate_thumbnail",
            new=AsyncMock(return_value=static_thumb),
        ),
    ):
        head = api_client.head(
            "/api/backup/thumbnails/animated",
            params={"path": str(video), "wait": "false"},
        )
    assert head.status_code == 200
    assert head.headers["x-thumbnail-status"] == "generating"

    # No thumbnail available yet: the client is told to retry later.
    with (
        patch.object(library_media, "animated_thumb_worker_instance", worker),
        patch.object(
            library_media, "generate_thumbnail", new=AsyncMock(return_value=None)
        ),
    ):
        res = api_client.get(
            "/api/backup/thumbnails/animated",
            params={"path": str(video), "wait": "false"},
        )
    assert res.status_code == 202


# --------------------------------------------------------------------------
# Admin: rename / delete
# --------------------------------------------------------------------------


def test_backup_rename_validation_and_noop(api_client):
    dest = Path(state["destination_dir"])
    video = dest / "rename_me.mp4"
    video.write_bytes(b"\x00" * 32)
    _insert_file_row(video, str(dest))

    for bad_name in ("", "  ", "sub/name.mp4", "sub\\name.mp4", ".hidden.mp4"):
        res = api_client.post(
            "/api/backup/rename", json={"path": str(video), "new_name": bad_name}
        )
        assert res.status_code == 400, bad_name

    # Renaming to the same name is a no-op success.
    same = api_client.post(
        "/api/backup/rename", json={"path": str(video), "new_name": "rename_me.mp4"}
    ).json()
    assert same["success"] is True
    assert same["path"] == str(video)

    # Missing file.
    assert (
        api_client.post(
            "/api/backup/rename",
            json={"path": str(dest / "ghost.mp4"), "new_name": "x.mp4"},
        ).status_code
        == 404
    )

    # Target already exists.
    other = dest / "taken.mp4"
    other.write_bytes(b"\x00" * 16)
    conflict = api_client.post(
        "/api/backup/rename", json={"path": str(video), "new_name": "taken.mp4"}
    )
    assert conflict.status_code == 400

    # Successful rename removes the stale cached poster.
    stale_thumb = dest / "stale_poster.jpg"
    stale_thumb.write_bytes(b"stale")
    with patch(
        "media_library.routers.library_admin.get_thumbnail_cache_path",
        return_value=stale_thumb,
    ):
        renamed = api_client.post(
            "/api/backup/rename", json={"path": str(video), "new_name": "renamed.mp4"}
        ).json()
    assert renamed["new_path"] == str(dest / "renamed.mp4")
    assert renamed["thumb_url"].startswith("/api/backup/thumbnail?path=")
    assert not stale_thumb.exists()
    assert _row(dest / "renamed.mp4")["name"] == "renamed.mp4"


def test_backup_rename_inside_archive(api_client):
    from media_library.services.zip import get_zip_cache_dir

    dest = Path(state["destination_dir"])
    zip_path = _make_zip(dest, "rename_archive.zip", "inner.jpg")
    member = f"{zip_path}/inner.jpg"

    # Not extracted yet: the member cannot be found in the archive.
    assert (
        api_client.post(
            "/api/backup/rename", json={"path": member, "new_name": "other.jpg"}
        ).status_code
        == 404
    )

    api_client.get("/api/backup/browse", params={"path": str(zip_path)})
    deadline = time.time() + 15
    cache_dir = get_zip_cache_dir(str(zip_path))
    while time.time() < deadline and not (cache_dir / "inner.jpg").exists():
        time.sleep(0.1)
    assert (cache_dir / "inner.jpg").exists()

    # Conflicting name inside the archive.
    (cache_dir / "conflict.jpg").write_bytes(_jpeg_bytes("black"))
    conflict = api_client.post(
        "/api/backup/rename", json={"path": member, "new_name": "conflict.jpg"}
    )
    assert conflict.status_code == 400

    with patch(
        "media_library.routers.library_admin.trigger_debounced_sync_back"
    ) as mock_sync:
        renamed = api_client.post(
            "/api/backup/rename", json={"path": member, "new_name": "renamed.jpg"}
        ).json()
    assert renamed["success"] is True
    assert renamed["new_path"].endswith("renamed.jpg")
    assert (cache_dir / "renamed.jpg").exists()
    mock_sync.assert_called_once_with(str(zip_path))

    import shutil

    shutil.rmtree(cache_dir, ignore_errors=True)


def test_backup_delete_branches(api_client):
    from media_library.services.zip import get_zip_cache_dir

    dest = Path(state["destination_dir"])

    # Missing file.
    assert (
        api_client.post(
            "/api/backup/delete", json={"path": str(dest / "ghost.mp4")}
        ).status_code
        == 404
    )

    # Directories cannot be deleted here.
    folder = dest / "a_folder"
    folder.mkdir()
    assert (
        api_client.post("/api/backup/delete", json={"path": str(folder)}).status_code
        == 400
    )

    # File delete removes the cached poster and the index row.
    video = dest / "doomed.mp4"
    video.write_bytes(b"\x00" * 32)
    _insert_file_row(video, str(dest))
    thumb = get_thumbnail_cache_path(str(video))
    thumb.parent.mkdir(parents=True, exist_ok=True)
    thumb.write_bytes(b"poster")
    res = api_client.post("/api/backup/delete", json={"path": str(video)}).json()
    assert res["success"] is True
    assert not video.exists()
    assert not thumb.exists()
    assert _row(video) is None

    # A whole archive drops its extraction cache and index rows.
    zip_path = _make_zip(dest, "delete_archive.zip")
    cache_dir = get_zip_cache_dir(str(zip_path))
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "photo.jpg").write_bytes(_jpeg_bytes())
    with db_conn() as conn, conn:
        conn.execute(
            "INSERT INTO folders (path, name, parent_path, is_zip) VALUES (?, ?, ?, 1)",
            (str(zip_path), "delete_archive.zip", str(dest)),
        )
    res = api_client.post("/api/backup/delete", json={"path": str(zip_path)}).json()
    assert res["success"] is True
    assert not zip_path.exists()
    assert not cache_dir.exists()


def test_backup_delete_inside_archive(api_client):
    from media_library.services.zip import get_zip_cache_dir

    dest = Path(state["destination_dir"])
    zip_path = _make_zip(dest, "member_delete.zip", "gone.jpg")
    api_client.get("/api/backup/browse", params={"path": str(zip_path)})

    cache_dir = get_zip_cache_dir(str(zip_path))
    deadline = time.time() + 15
    while time.time() < deadline and not (cache_dir / "gone.jpg").exists():
        time.sleep(0.1)

    member = f"{zip_path}/gone.jpg"
    # The browse above already indexed the archive members (background thread).
    deadline = time.time() + 15
    while time.time() < deadline and _row(Path(member)) is None:
        time.sleep(0.1)
    assert _row(Path(member)) is not None

    with patch(
        "media_library.routers.library_admin.trigger_debounced_sync_back"
    ) as mock_sync:
        res = api_client.post("/api/backup/delete", json={"path": member}).json()
    assert res["success"] is True
    assert not (cache_dir / "gone.jpg").exists()
    assert _row(Path(member)) is None
    mock_sync.assert_called_once_with(str(zip_path))

    import shutil

    shutil.rmtree(cache_dir, ignore_errors=True)


# --------------------------------------------------------------------------
# Admin: thumbnails / posters / animated previews
# --------------------------------------------------------------------------


def test_thumbnail_generate_resets_stale_markers(api_client):
    dest = Path(state["destination_dir"])
    video = dest / "gen_clip.mp4"
    video.write_bytes(b"\x00" * 16)
    _insert_file_row(video, str(dest))
    with db_conn() as conn, conn:
        conn.execute("UPDATE files SET has_thumb = 2 WHERE path = ?", (str(video),))

    res = api_client.post("/api/backup/thumbnails/generate", json={})
    assert res.status_code == 200
    assert res.json()["success"] is True
    assert _row(video)["has_thumb"] == 0

    folder_msg = api_client.post(
        "/api/backup/thumbnails/generate", json={"folder_path": str(dest)}
    ).json()
    assert folder_msg["success"] is True


def test_set_poster_frame_error_branches(api_client):
    dest = Path(state["destination_dir"])
    text = dest / "notes.txt"
    text.write_text("nope")

    assert (
        api_client.post(
            "/api/backup/thumbnails/set-poster", json={"path": str(text)}
        ).status_code
        == 400
    )
    assert (
        api_client.post(
            "/api/backup/thumbnails/set-poster",
            json={"path": str(dest / "ghost.mp4")},
        ).status_code
        == 404
    )


def test_set_poster_frame_invalidation_and_success(api_client):
    import importlib

    library_admin = importlib.import_module("media_library.routers.library_admin")
    from media_library.services.ffmpeg_service import get_animated_preview_path

    dest = Path(state["destination_dir"])
    video = dest / "poster_clip.mp4"
    video.write_bytes(b"\x00" * 32)
    _insert_file_row(video, str(dest))

    poster = _write_jpeg(dest / "poster_frame.jpg")
    anim_path = get_animated_preview_path(str(video))
    anim_path.parent.mkdir(parents=True, exist_ok=True)
    anim_path.write_bytes(b"old animated preview")
    assert _row(video)["has_animated"] == 0

    with (
        patch.object(
            library_admin, "extract_frame_poster", new=AsyncMock(return_value=poster)
        ),
        patch.object(
            library_admin.animated_thumb_worker_instance, "enqueue"
        ) as mock_enqueue,
    ):
        res = api_client.post(
            "/api/backup/thumbnails/set-poster",
            json={"path": str(video), "timestamp": 5.0},
        )

    assert res.status_code == 200
    body = res.json()
    assert body["success"] is True
    assert "v=" in body["thumb_url"]
    assert body["animated_url"].startswith("/api/backup/thumbnails/animated?path=")
    # Stale animated preview is dropped and the worker re-queues it.
    assert not anim_path.exists()
    assert _row(video)["has_animated"] == 0
    mock_enqueue.assert_called_once()
    assert mock_enqueue.call_args[0][0] == str(video)

    # Extraction failure is a 500 for the client.
    with patch.object(
        library_admin, "extract_frame_poster", new=AsyncMock(return_value=None)
    ):
        res = api_client.post(
            "/api/backup/thumbnails/set-poster", json={"path": str(video)}
        )
    assert res.status_code == 500


def test_generate_animated_endpoint_error_branches(api_client):
    dest = Path(state["destination_dir"])
    text = dest / "notes2.txt"
    text.write_text("nope")

    assert (
        api_client.post(
            "/api/backup/thumbnails/generate-animated", json={"path": str(text)}
        ).status_code
        == 400
    )
    assert (
        api_client.post(
            "/api/backup/thumbnails/generate-animated",
            json={"path": str(dest / "ghost.mp4")},
        ).status_code
        == 404
    )


def test_batch_animated_endpoint_paths_and_priority(api_client):
    import importlib

    library_admin = importlib.import_module("media_library.routers.library_admin")

    dest = Path(state["destination_dir"])
    video = dest / "batch_anim.mp4"
    video.write_bytes(b"\x00" * 16)

    res = api_client.post(
        "/api/backup/thumbnails/batch-animated",
        json={"paths": [str(video), "/etc/passwd"]},
    ).json()
    assert res["success"] is True
    # Paths outside the library are dropped before queueing.
    assert res["count"] == 1
    assert res["queued"] == 1

    library_admin.animated_thumb_worker_instance.cancel_all()

    enqueued = []
    with patch.object(
        library_admin.animated_thumb_worker_instance,
        "enqueue",
        side_effect=lambda p, priority=False: enqueued.append((p, priority)) or True,
    ):
        res = api_client.post(
            "/api/backup/thumbnails/batch-animated",
            json={"paths": [str(video)], "priority": True},
        ).json()
    assert res["queued"] == 1
    assert enqueued == [(str(video), True)]

    # Folder scoping picks up indexed videos only.
    _insert_file_row(video, str(dest))
    res = api_client.post(
        "/api/backup/thumbnails/batch-animated", json={"folder_path": str(dest)}
    ).json()
    assert res["count"] == 1
    library_admin.animated_thumb_worker_instance.cancel_all()
