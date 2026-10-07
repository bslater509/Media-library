"""Integration tests for all major API routes and endpoints."""

import os
import pytest
from starlette.testclient import TestClient

from media_library.core.state import state
from media_library.db.schema import init_backup_db
from media_library.app_factory import create_app


@pytest.fixture
def api_client(tmp_path, monkeypatch):
    db_file = tmp_path / "test_api.db"
    dest_dir = tmp_path / "media_dest"
    source_dir = tmp_path / "media_src"
    dest_dir.mkdir()
    source_dir.mkdir()

    # Create dummy video file in source
    test_file = source_dir / "test_clip.mp4"
    test_file.write_bytes(b"\x00" * 1024)

    monkeypatch.setenv("DB_PATH", str(db_file))
    monkeypatch.setenv("LIBRARY_DIR", str(dest_dir))
    monkeypatch.setenv("INCOMING_DIR", str(source_dir))

    state["source_dir"] = str(source_dir)
    state["destination_dir"] = str(dest_dir)

    init_backup_db()

    app = create_app(
        source_dir=str(source_dir),
        destination_dir=str(dest_dir),
        run_startup_workers=False,
    )

    with TestClient(app) as client:
        yield client


def test_html_views(api_client):
    res_root = api_client.get("/")
    assert res_root.status_code == 200
    assert "text/html" in res_root.headers.get("content-type", "")

    res_sorter = api_client.get("/sorter")
    assert res_sorter.status_code == 200
    assert "text/html" in res_sorter.headers.get("content-type", "")

    res_lib = api_client.get("/library", follow_redirects=False)
    assert res_lib.status_code == 302


def test_config_and_destinations(api_client, tmp_path):
    res = api_client.get("/api/config")
    assert res.status_code == 200
    data = res.json()
    assert "source_dir" in data
    assert "destination_dir" in data

    res_dest = api_client.get("/api/destinations")
    assert res_dest.status_code == 200
    data_dest = res_dest.json()
    assert "folders" in data_dest
    assert "recent" in data_dest

    res_create = api_client.post(
        "/api/destinations/create",
        json={"folder_name": "NewSubfolder"},
    )
    assert res_create.status_code == 200
    assert res_create.json()["success"] is True


def test_scan_and_queue(api_client):
    res_scan = api_client.post("/api/source/scan", json={"source_dir": state["source_dir"]})
    assert res_scan.status_code == 200
    scan_data = res_scan.json()
    assert scan_data["success"] is True
    assert scan_data["count"] >= 1

    res_queue = api_client.get("/api/queue")
    assert res_queue.status_code == 200
    queue_data = res_queue.json()
    assert "videos" in queue_data
    assert len(queue_data["videos"]) >= 1


def test_backup_index_and_search(api_client):
    res_status = api_client.get("/api/backup/index/status")
    assert res_status.status_code == 200
    data = res_status.json()
    assert "status" in data
    assert "total_files" in data

    res_search = api_client.get("/api/backup/search?q=clip")
    assert res_search.status_code == 200
    search_data = res_search.json()
    assert "results" in search_data


def test_thumbnail_and_webp_status(api_client):
    res_thumb = api_client.get("/api/backup/thumbnails/status")
    assert res_thumb.status_code == 200
    assert "status" in res_thumb.json()

    res_webp = api_client.get("/api/backup/webp/status")
    assert res_webp.status_code == 200
    assert "status" in res_webp.json()

    res_pause = api_client.post("/api/backup/webp/pause")
    assert res_pause.status_code == 200
    assert "is_paused" in res_pause.json()

    res_resume = api_client.post("/api/backup/webp/resume")
    assert res_resume.status_code == 200
    assert res_resume.json()["is_paused"] is False


def test_duplicates_and_library_backup(api_client):
    res_dup_status = api_client.get("/api/backup/duplicates/hash-status")
    assert res_dup_status.status_code == 200
    assert "status" in res_dup_status.json()

    res_dups = api_client.get("/api/backup/duplicates")
    assert res_dups.status_code == 200
    assert "groups" in res_dups.json()

    res_backup_status = api_client.get("/api/backup/library-backup/status")
    assert res_backup_status.status_code == 200
    assert "status" in res_backup_status.json()
    assert "progress_percent" in res_backup_status.json()


def test_transcode_status(api_client):
    res = api_client.get("/api/transcode/status")
    assert res.status_code == 200
    data = res.json()
    assert "paused" in data
    assert "queue_length" in data


def test_background_tasks_modal_button_always_visible(api_client):
    res_root = api_client.get("/")
    assert res_root.status_code == 200
    root_html = res_root.text
    # In Library view (/), button must be present, visible (not hidden by default), and contain task icon
    assert 'id="transferBadgeBtn"' in root_html
    assert '<button id="transferBadgeBtn" title="View background activity" class="px-2' in root_html
    assert 'id="transferIcon"' in root_html
    assert 'id="transferDropdown"' in root_html
    assert 'id="closeTransferDropdownBtn"' in root_html

    res_sorter = api_client.get("/sorter")
    assert res_sorter.status_code == 200
    sorter_html = res_sorter.text
    # In Sorter view (/sorter), button must be present, visible (not hidden by default), and contain task icon
    assert 'id="transferBadgeBtn"' in sorter_html
    assert '<button id="transferBadgeBtn" title="View background activity" class="px-2' in sorter_html
    assert 'id="transferIcon"' in sorter_html
    assert 'id="transferDropdown"' in sorter_html
    assert 'id="closeTransferDropdownBtn"' in sorter_html
