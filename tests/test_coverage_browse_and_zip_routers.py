"""Coverage tests for the library browsing router and the ZIP operation router.

Covers the branches that only trigger on unusual library states: background
index scheduling and its debounce, cached-view fallbacks when SQLite fails,
path clamping when ``commonpath`` misbehaves, ZIP archive extraction/indexing
while browsing, and the ZIP router's validation/409 guards.
"""

import io
import sqlite3
import time
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from media_library.core.state import state
from media_library.routers.library_browse import (
    _browse_index_inflight,
    _browse_index_lock,
)


@pytest.fixture(autouse=True)
def _clear_index_debounce():
    with _browse_index_lock:
        _browse_index_inflight.clear()
    yield
    with _browse_index_lock:
        _browse_index_inflight.clear()


def _jpeg_bytes(color="green", size=(40, 30)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color=color).save(buf, format="JPEG")
    return buf.getvalue()


def _wait_until(predicate, timeout=15.0, interval=0.1) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


# --------------------------------------------------------------------------
# /api/backup/browse
# --------------------------------------------------------------------------


def test_browse_unindexed_folder_schedules_background_index(api_client):
    dest = Path(state["destination_dir"])
    sub = dest / "index_me_later"
    sub.mkdir()
    (sub / "clip.mp4").write_bytes(b"\x00" * 32)

    res = api_client.get("/api/backup/browse", params={"path": str(sub)})
    assert res.status_code == 200
    data = res.json()
    assert data["is_root"] is False
    assert data["indexing"] is True
    assert data["total_files"] == 0
    assert data["breadcrumbs"][-1]["name"] == "index_me_later"

    # Client re-poll inside the debounce window must not spawn a second job.
    assert api_client.get("/api/backup/browse", params={"path": str(sub)}).json()[
        "indexing"
    ] is False

    assert _wait_until(
        lambda: api_client.get(
            "/api/backup/browse", params={"path": str(sub)}
        ).json()["total_files"]
        == 1
    )
    final = api_client.get("/api/backup/browse", params={"path": str(sub)}).json()
    assert final["files"][0]["name"] == "clip.mp4"
    assert final["indexing"] is False


def test_browse_root_scandir_fallback_lists_folders_and_zip_files(api_client):
    dest = Path(state["destination_dir"])
    (dest / "plain_folder").mkdir()
    (dest / "loose_archive.zip").write_bytes(b"PK\x05\x06" + b"\x00" * 18)
    (dest / "notes.txt").write_text("ignore me")

    mock_conn = MagicMock()
    mock_conn.execute.return_value.fetchall.return_value = []
    mock_conn.execute.return_value.fetchone.return_value = None
    with patch("media_library.routers.library_browse.db_conn") as mock_db:
        mock_db.return_value.__enter__.return_value = mock_conn
        res = api_client.get("/api/backup/browse", params={"refresh": "true"})

    assert res.status_code == 200
    data = res.json()
    assert data["is_root"] is True
    names = [rf["name"] for rf in data["root_folders"]]
    assert names == sorted(names, key=str.lower)
    assert "loose_archive" in names
    assert "plain_folder" in names
    assert "notes.txt" not in names
    zip_entry = next(rf for rf in data["root_folders"] if rf["name"] == "loose_archive")
    assert zip_entry["is_zip"] is True
    assert data["indexing"] is True  # refresh on root re-crawls in the background
    assert data["subfolders"]  # root folders are surfaced as subfolders too


def test_browse_clamps_paths_on_commonpath_and_isdir_errors(api_client):
    dest = Path(state["destination_dir"])
    sub = dest / "clamp_target"
    sub.mkdir()

    with patch(
        "media_library.routers.library_browse.os.path.commonpath",
        side_effect=ValueError("mixed drives"),
    ):
        res = api_client.get("/api/backup/browse", params={"path": str(sub)})
        assert res.status_code == 200
        assert res.json()["is_root"] is True

        res_folders = api_client.get("/api/backup/folders", params={"path": str(sub)})
        assert res_folders.status_code == 200
        assert res_folders.json()["is_root"] is True

    # An unreadable directory entry is clamped to its parent (or the root).
    with patch("os.path.isdir", side_effect=OSError("stale NFS handle")):
        res = api_client.get("/api/backup/browse", params={"path": str(sub)})
    assert res.status_code == 200
    assert res.json()["current_path"] in (str(dest), str(sub))


def test_browse_returns_cached_view_when_db_read_fails(api_client):
    dest = Path(state["destination_dir"])
    sub = dest / "broken_db"
    sub.mkdir()
    (sub / "clip.mp4").write_bytes(b"\x00" * 16)

    mock_conn = MagicMock()
    mock_conn.execute.side_effect = sqlite3.DatabaseError("database is locked")
    with patch("media_library.routers.library_browse.db_conn") as mock_db:
        mock_db.return_value.__enter__.return_value = mock_conn
        res = api_client.get("/api/backup/browse", params={"path": str(sub)})
        res_root = api_client.get("/api/backup/browse")
        res_folders = api_client.get("/api/backup/folders", params={"path": str(sub)})

    assert res.status_code == 200
    assert res.json()["files"] == []
    assert res_root.status_code == 200
    assert res_folders.status_code == 200
    assert res_folders.json()["subfolders"] == []


def test_browse_zip_archive_extracts_then_indexes(api_client):
    from media_library.services.zip import (
        get_zip_cache_dir,
        is_zip_extracted,
        zip_lock,
        zip_tasks_state,
    )

    dest = Path(state["destination_dir"])
    zip_path = dest / "album.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("photo.jpg", _jpeg_bytes())

    # Freshly browsed, not yet extracted: the request returns immediately and
    # extraction continues on a background thread.
    first = api_client.get("/api/backup/browse", params={"path": str(zip_path)}).json()
    assert first["is_zip"] is True
    assert first["zip_extracting"] is True
    assert first["breadcrumbs"][-1]["name"] == "album"  # ZIP stem in breadcrumb

    # Extraction + index both finish in the background.
    assert _wait_until(lambda: is_zip_extracted(str(zip_path)))
    assert _wait_until(
        lambda: api_client.get(
            "/api/backup/browse", params={"path": str(zip_path)}
        ).json()["total_files"]
        == 1
    )

    # Already extracted and running: the percent comes from the live ZIP task.
    with zip_lock:
        zip_tasks_state["active_tasks"][str(zip_path)] = {
            "type": "extract",
            "status": "running",
            "percent": 55.0,
            "started_at": time.time(),
        }
    try:
        running = api_client.get(
            "/api/backup/browse", params={"path": str(zip_path)}
        ).json()
        assert running["zip_extracting"] is True
        assert running["zip_extract_percent"] == 55.0
        assert running["total_files"] == 1
    finally:
        with zip_lock:
            zip_tasks_state["active_tasks"].pop(str(zip_path), None)

    # Second browse of the extracted archive re-uses the index (no re-extract).
    again = api_client.get("/api/backup/browse", params={"path": str(zip_path)}).json()
    assert again["zip_extracting"] is False
    assert again["files"][0]["name"] == "photo.jpg"

    cache_dir = get_zip_cache_dir(str(zip_path))
    assert is_zip_extracted(str(zip_path))

    # Cleanup the extraction cache so the sandbox temp dir stays small.
    import shutil

    shutil.rmtree(cache_dir, ignore_errors=True)


def test_browse_index_failure_in_background_thread_is_logged(api_client):
    """A crashing background index must not affect the HTTP response."""
    dest = Path(state["destination_dir"])
    sub = dest / "index_boom"
    sub.mkdir()

    with patch(
        "media_library.routers.library_browse.index_folder",
        side_effect=RuntimeError("indexer crashed"),
    ), patch(
        "media_library.routers.library_browse.thumbnail_worker_instance.enqueue_folder",
        side_effect=RuntimeError("thumb worker down"),
    ), patch(
        "media_library.routers.library_browse.transcode_worker_instance.enqueue_indexed_folder",
        side_effect=RuntimeError("transcoder down"),
    ):
        res = api_client.get("/api/backup/browse", params={"path": str(sub)})
        assert res.status_code == 200
        time.sleep(0.3)  # let the daemon thread hit its failure branch

    # A follow-up browse inside the debounce window still answers normally.
    follow = api_client.get("/api/backup/browse", params={"path": str(sub)})
    assert follow.status_code == 200


def test_browse_schedules_zip_optimization_for_archived_folders(api_client):
    """Browsing an already-indexed archive enqueues it for on-demand WebP work."""
    from media_library.services.zip import get_zip_cache_dir

    dest = Path(state["destination_dir"])
    zip_path = dest / "bundle.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("one.jpg", _jpeg_bytes("red"))

    api_client.get("/api/backup/browse", params={"path": str(zip_path)})
    assert _wait_until(lambda: _last_scanned(str(zip_path)) > 0)

    with patch(
        "media_library.routers.library_browse.webp_worker_instance.enqueue_folder"
    ) as mock_webp, patch(
        "media_library.routers.library_browse.thumbnail_worker_instance.enqueue_folder"
    ) as mock_thumbs, patch(
        "media_library.routers.library_browse.transcode_worker_instance.enqueue_indexed_folder"
    ) as mock_transcode:
        res = api_client.get("/api/backup/browse", params={"path": str(zip_path)})
        assert res.status_code == 200
        assert res.json()["total_files"] == 1

    # Browsing an indexed archive must enqueue WebP/thumbnail/transcode work.
    # The very first (unpatched) browse schedules a background index thread whose
    # enqueues can land inside this mock window, so assert at-least-once rather
    # than exactly-once to avoid a startup race.
    mock_webp.assert_called_with(str(zip_path))
    mock_thumbs.assert_called_with(str(zip_path))
    mock_transcode.assert_called_with(str(zip_path))

    import shutil

    shutil.rmtree(get_zip_cache_dir(str(zip_path)), ignore_errors=True)


def _last_scanned(path: str) -> float:
    from media_library.db.connection import db_conn

    with db_conn() as conn:
        row = conn.execute(
            "SELECT last_scanned FROM folders WHERE path = ?", (path,)
        ).fetchone()
    return float(row["last_scanned"] or 0) if row else 0.0


def test_folders_endpoint_schedules_index_for_unindexed_folder(api_client):
    dest = Path(state["destination_dir"])
    sub = dest / "tree_branch"
    sub.mkdir()

    res = api_client.get("/api/backup/folders", params={"path": str(sub)})
    assert res.status_code == 200
    body = res.json()
    assert body["is_root"] is False
    assert body["indexing"] is True
    assert _wait_until(lambda: _last_scanned(str(sub)) > 0)


# --------------------------------------------------------------------------
# ZIP router
# --------------------------------------------------------------------------


def test_zip_router_check_status_and_validation_errors(api_client):
    from media_library.db.indexer import index_folder

    dest = Path(state["destination_dir"])

    # Eligible folder: images only.
    leaf = dest / "photos"
    leaf.mkdir()
    for i in range(2):
        (leaf / f"p{i}.jpg").write_bytes(_jpeg_bytes("blue"))
    res = api_client.get("/api/backup/zip/check", params={"folder_path": str(leaf)})
    assert res.status_code == 200
    assert res.json()["eligible"] is True

    # Ineligible folder: contains a video.
    mixed = dest / "mixed"
    mixed.mkdir()
    (mixed / "clip.mp4").write_bytes(b"\x00" * 32)
    (mixed / "p.jpg").write_bytes(_jpeg_bytes())
    res = api_client.get("/api/backup/zip/check", params={"folder_path": str(mixed)})
    assert res.json()["eligible"] is False

    # Compressing an ineligible folder is rejected before any work starts.
    res = api_client.post("/api/backup/zip/compress", json={"folder_path": str(mixed)})
    assert res.status_code == 400
    assert res.json()["detail"]

    # Unpack requires a .zip path.
    res = api_client.post("/api/backup/zip/unpack", json={"zip_path": str(mixed)})
    assert res.status_code == 400
    assert "not a ZIP" in res.json()["detail"]

    # Status endpoints answer even when nothing is running.
    assert api_client.get("/api/backup/zip/status").json()["is_active"] is False
    assert api_client.get("/api/backup/zip/batch/status").status_code == 200
    assert (
        api_client.get("/api/backup/zip/batch-unpack/status").status_code == 200
    )
    assert api_client.get("/api/backup/zip/batch-unpack/candidates").status_code == 200

    # Batch candidates come straight from the index, so index the leaf first.
    index_folder(str(leaf), str(dest))
    candidates = api_client.get("/api/backup/zip/batch/candidates").json()
    assert candidates["total_candidates"] >= 1
    assert any(c["path"] == str(leaf) for c in candidates["candidates"])


def test_zip_router_batch_start_rejects_invalid_paths(api_client):
    res = api_client.post("/api/backup/zip/batch/start", json={"folder_paths": []})
    assert res.status_code == 400

    res = api_client.post(
        "/api/backup/zip/batch/start", json={"folder_paths": ["/etc", "/root"]}
    )
    assert res.status_code == 400
    assert "No valid folders" in res.json()["detail"]

    res = api_client.post(
        "/api/backup/zip/batch-unpack/start", json={"zip_paths": ["/etc/passwd"]}
    )
    assert res.status_code == 400
    assert "No valid ZIP" in res.json()["detail"]


def test_zip_router_batch_start_conflict_returns_409(api_client):
    import sys

    zip_router = sys.modules["media_library.routers.zip_router"]

    dest = Path(state["destination_dir"])
    leaf = dest / "conflict_batch"
    leaf.mkdir()
    (leaf / "a.jpg").write_bytes(_jpeg_bytes())
    (leaf / "b.jpg").write_bytes(_jpeg_bytes("purple"))

    with patch.object(
        zip_router, "start_batch_zip", side_effect=RuntimeError("busy")
    ):
        res = api_client.post(
            "/api/backup/zip/batch/start", json={"folder_paths": [str(leaf)]}
        )
    assert res.status_code == 409

    with patch.object(
        zip_router, "start_batch_unzip", side_effect=RuntimeError("busy")
    ):
        res = api_client.post(
            "/api/backup/zip/batch-unpack/start", json={"zip_paths": [str(leaf)]}
        )
    assert res.status_code == 409

    # Happy path: valid folder is reported as queued.
    with patch.object(zip_router, "start_batch_zip", return_value={"queued": 1}):
        res = api_client.post(
            "/api/backup/zip/batch/start", json={"folder_paths": [str(leaf)]}
        )
    assert res.status_code == 200
    assert res.json()["queued"] == 1


def test_zip_router_batch_controls_toggle_state(api_client):
    from media_library.services.zip import (
        batch_unzip_cancel_event,
        batch_unzip_state,
        batch_zip_cancel_event,
        batch_zip_state,
    )

    for state_obj, cancel_event, prefix in (
        (batch_zip_state, batch_zip_cancel_event, "/api/backup/zip/batch"),
        (batch_unzip_state, batch_unzip_cancel_event, "/api/backup/zip/batch-unpack"),
    ):
        cancel_event.clear()
        state_obj["status"] = "running"
        state_obj["is_paused"] = False

        assert api_client.post(f"{prefix}/pause").json()["status"] == "paused"
        assert api_client.post(f"{prefix}/pause").json()["status"] == "running"
        assert api_client.post(f"{prefix}/resume").json()["success"] is False
        assert api_client.post(f"{prefix}/cancel").json()["status"] == "canceled"
        # Cancelling twice is a harmless no-op.
        assert api_client.post(f"{prefix}/cancel").json()["success"] is False
        assert api_client.get(f"{prefix}/status").json()["status"] == "canceled"
        cancel_event.clear()

    # Reset global batch state so other tests start from idle.
    for state_obj in (batch_zip_state, batch_unzip_state):
        state_obj["status"] = "idle"
        state_obj["is_paused"] = False
        state_obj["queue"] = []
        state_obj["errors"] = []
        state_obj["overall_percent"] = 0.0
