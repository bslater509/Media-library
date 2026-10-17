"""Comprehensive tests for MediaService functions, LibraryOpsWorker, and Zip sync-back."""

import os
import threading
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from media_library.core.state import state
from media_library.db.connection import db_conn
from media_library.services.media_service import (
    do_transfer,
    get_lan_ip,
    resolve_video_path,
    search_backup_index,
    validate_backup_path,
)
from media_library.services.zip.compress import (
    _execute_sync_back,
    trigger_debounced_sync_back,
)
from media_library.workers.library_ops_worker import LibraryOpsWorker
from media_library.workers.metadata_worker import MediaMetadataWorker, _read_image_size


def test_get_lan_ip_fallback():
    with patch("socket.socket", side_effect=Exception("Network error")):
        ip = get_lan_ip()
        assert ip == "127.0.0.1"


def test_validate_backup_path_edge_cases(sandbox_env):
    media = str(sandbox_env["media_dir"])
    state["destination_dir"] = media

    # Root / parent path clamps to destination_dir
    assert validate_backup_path("/") == media
    assert validate_backup_path(os.path.dirname(media)) == media
    assert validate_backup_path(media) == media

    # Subpath
    sub = os.path.join(media, "subfolder")
    assert validate_backup_path(sub) == sub

    # Traversal raises 403
    with pytest.raises(HTTPException) as exc:
        validate_backup_path("/tmp/outside")
    assert exc.value.status_code == 403


def test_resolve_video_path_all_scenarios(sandbox_env):
    incoming = sandbox_env["incoming_dir"]
    state["source_dir"] = str(incoming)

    # 1. None when source_dir invalid or missing
    state["source_dir"] = "/nonexistent/dir"
    assert resolve_video_path("video.mp4") is None

    state["source_dir"] = str(incoming)

    # 2. Direct match
    direct = incoming / "direct.mp4"
    direct.write_bytes(b"\x00" * 10)
    assert resolve_video_path("direct.mp4") == str(direct)

    # 3. Transcode items map lookup
    transcode_map = {
        "t1": {
            "filename": "source.mkv",
            "target_path": str(direct),
        }
    }
    assert resolve_video_path("source.mkv", transcode_map) == str(direct)

    # 4. Converted mp4
    orig_name = "clip.avi"
    conv_mp4 = incoming / "clip.mp4"
    conv_mp4.write_bytes(b"\x00" * 10)
    assert resolve_video_path(orig_name) == str(conv_mp4)

    # 5. Counter converted mp4
    counter_name = "movie.wmv"
    counter_mp4 = incoming / "movie_converted_1.mp4"
    counter_mp4.write_bytes(b"\x00" * 10)
    assert resolve_video_path(counter_name) == str(counter_mp4)


def test_search_backup_index_sorting_and_filtering(sandbox_env):
    media = sandbox_env["media_dir"]
    state["destination_dir"] = str(media)

    # Empty query returns empty list
    assert search_backup_index("   ") == []

    f1 = media / "apple.mp4"
    f2 = media / "banana.jpg"
    f3 = media / "cherry.mp4"
    f1.write_bytes(b"\x00" * 100)
    f2.write_bytes(b"\x00" * 200)
    f3.write_bytes(b"\x00" * 300)

    with db_conn() as conn, conn:
        conn.execute(
            """INSERT INTO files (path, name, folder_path, type, ext, size, size_formatted, mtime)
               VALUES (?, ?, ?, 'video', '.mp4', 100, '100 B', 10.0)""",
            (str(f1), "apple.mp4", str(media)),
        )
        conn.execute(
            """INSERT INTO files (path, name, folder_path, type, ext, size, size_formatted, mtime)
               VALUES (?, ?, ?, 'image', '.jpg', 200, '200 B', 20.0)""",
            (str(f2), "banana.jpg", str(media)),
        )
        conn.execute(
            """INSERT INTO files (path, name, folder_path, type, ext, size, size_formatted, mtime)
               VALUES (?, ?, ?, 'video', '.mp4', 300, '300 B', 30.0)""",
            (str(f3), "cherry.mp4", str(media)),
        )

    # Search with type filter 'video'
    res_vids = search_backup_index("a", type_filter="video")
    names = [r["name"] for r in res_vids]
    assert "apple.mp4" in names
    assert "banana.jpg" not in names

    # Search with sort_by: name_desc, date_asc, size_desc
    res_desc = search_backup_index("e", sort_by="name_desc")
    assert [r["name"] for r in res_desc] == sorted(
        [r["name"] for r in res_desc], reverse=True
    )

    res_date_asc = search_backup_index("e", sort_by="date_asc")
    mtimes = [r["mtime"] for r in res_date_asc]
    assert mtimes == sorted(mtimes)

    res_size_desc = search_backup_index("e", sort_by="size_desc")
    sizes = [r["size"] for r in res_size_desc]
    assert sizes == sorted(sizes, reverse=True)


def test_do_transfer_chunked_and_abort(tmp_path):
    src = tmp_path / "src_file.bin"
    dest = tmp_path / "sub" / "dest_file.bin"
    src.write_bytes(b"DATA" * 500)

    # 1. Missing source file sets status to failed
    item_missing = {
        "id": "t_miss",
        "filename": "missing.bin",
        "source_path": str(tmp_path / "ghost.bin"),
        "target_path": str(dest),
        "total_bytes": 100,
        "bytes_copied": 0,
        "percent": 0.0,
        "abort_event": threading.Event(),
        "status": "queued",
    }
    do_transfer(item_missing)
    assert item_missing["status"] == "failed"

    # 2. Cancelled transfer via abort_event
    abort_ev = threading.Event()
    abort_ev.set()  # Already cancelled

    item_abort = {
        "id": "t_abort",
        "filename": "src_file.bin",
        "source_path": str(src),
        "target_path": str(dest),
        "total_bytes": src.stat().st_size,
        "bytes_copied": 0,
        "percent": 0.0,
        "abort_event": abort_ev,
        "status": "queued",
    }
    # Force cross-dev branch by patching os.stat
    with patch("os.rename", side_effect=OSError("Cross-device link")):
        do_transfer(item_abort)
    assert item_abort["status"] == "cancelled"


def test_library_ops_worker_queue_and_undo():
    worker = LibraryOpsWorker()

    # 1. Pruning items_map > 1000
    for i in range(1050):
        worker.items_map[f"op_{i}"] = {
            "id": f"op_{i}",
            "op": "move",
            "status": "completed",
        }
    new_item = {"id": "new_op", "op": "delete"}
    worker.enqueue(new_item)
    assert len(worker.items_map) <= 1001

    # 2. Cancel
    assert worker.cancel("new_op") is True
    assert worker.cancel("nonexistent") is False

    # 3. Status
    st = worker.get_status()
    assert "queue_length" in st
    assert "undo_count" in st

    # 4. Undo stack push and pop
    worker.push_undo({"source_path": "/a", "target_path": "/b", "kind": "file"})
    assert worker.get_status()["undo_count"] == 1
    popped = worker.pop_undo()
    assert popped["source_path"] == "/a"
    assert worker.pop_undo() is None


def test_metadata_worker_helpers_and_backfill(sandbox_env):
    from PIL import Image

    media = sandbox_env["media_dir"]
    img_p = media / "dim_test.jpg"
    Image.new("RGB", (120, 80), color="yellow").save(img_p, format="JPEG")

    # _read_image_size
    w, h = _read_image_size(str(img_p))
    assert w == 120
    assert h == 80
    assert _read_image_size(str(media / "ghost.jpg")) is None

    worker = MediaMetadataWorker()
    worker.pause()
    assert worker.is_paused()
    assert worker.get_status()["status"] == "paused"
    worker.resume()
    assert not worker.is_paused()
    assert worker.get_status()["status"] == "probing"

    worker._refresh_stats()
    st = worker.get_status()
    assert "total_files" in st
    assert "pending_count" in st


def test_zip_debounced_sync_back(sandbox_env):
    import zipfile

    media = sandbox_env["media_dir"]
    zip_path = media / "sync_test.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("test.txt", "hello")

    # Execute sync back with missing cache (should safely early return)
    _execute_sync_back(str(zip_path))

    # Trigger debounced sync back
    trigger_debounced_sync_back(str(zip_path), delay_seconds=0.01)
