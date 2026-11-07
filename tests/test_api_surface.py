"""API surface tests: error translation, download/dedupe/system routes, restore CLI.

The feature suites cover the happy paths of each area; this module pins the
shared error mapping, the endpoints that mostly exist to translate input into a
job, and the recovery CLI, so a change cannot quietly turn a 404 into a 500.
"""

from __future__ import annotations

import json
import sqlite3

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from media_library import restore as restore_cli
from media_library.app import create_app, load_environment
from media_library.db import DatabaseReadOnlyError
from media_library.routers.common import as_http_error
from media_library.writes import WriteError


@pytest.fixture
def client():
    from media_library import config as api_config

    api_config.reload_settings()
    return TestClient(create_app())


# ---------------------------------------------------------------------------
# Error translation
# ---------------------------------------------------------------------------


def test_http_exception_passes_through_unchanged():
    original = HTTPException(status_code=418, detail="teapot")
    assert as_http_error(original) is original


def test_write_error_keeps_its_status_and_detail():
    translated = as_http_error(WriteError("Nope", 409))
    assert translated.status_code == 409
    assert translated.detail == "Nope"


def test_known_exception_types_map_to_their_status():
    assert as_http_error(DatabaseReadOnlyError("read only")).status_code == 503
    assert as_http_error(PermissionError("outside")).status_code == 403
    assert as_http_error(FileNotFoundError()).status_code == 404
    assert as_http_error(sqlite3.OperationalError("locked")).status_code == 503
    fallback = as_http_error(ValueError("unexpected"))
    assert fallback.status_code == 500
    # Raw internal error text (paths and internals) must never leak to the
    # client; unexpected exceptions only get a generic message.
    assert fallback.detail == "Internal error"


# ---------------------------------------------------------------------------
# Downloads
# ---------------------------------------------------------------------------


def test_download_status_and_history_start_empty(client):
    assert client.get("/api/downloads/status").status_code == 200
    history = client.get("/api/downloads/history").json()
    assert history["items"] == []
    assert history["total"] == 0


def test_cancel_and_retry_report_unknown_jobs_as_404(client):
    assert (
        client.post("/api/downloads/cancel", json={"job_id": "nope"}).status_code == 404
    )
    assert (
        client.post("/api/downloads/retry", json={"job_id": "nope"}).status_code == 404
    )


def test_clear_history_is_safe_when_empty(client):
    assert client.post("/api/downloads/clear-history").json() == {"cleared": 0}


def test_queueing_without_a_downloader_or_a_real_folder_fails_cleanly(client):
    response = client.post(
        "/api/downloads/queue",
        json={"urls": ["https://example.com/watch?v=1"], "destination": "/nope"},
    )
    assert response.status_code in (400, 403, 404, 503)
    assert "detail" in response.json()


# ---------------------------------------------------------------------------
# Dedupe
# ---------------------------------------------------------------------------


def test_dedupe_status_and_groups_start_empty(client):
    status = client.get("/api/dedupe/status")
    assert status.status_code == 200
    assert status.json()["ignored"] == 0
    groups = client.get("/api/dedupe/groups", params={"mode": "content"})
    assert groups.status_code == 200
    assert groups.json()["groups"] == []


def test_hash_rejects_an_unknown_mode(client):
    response = client.post("/api/dedupe/hash", json={"mode": "sideways"})
    assert response.status_code == 400


def test_verify_reports_a_missing_file_as_404(client):
    from media_library.config import get_settings

    root = get_settings().library_dir
    response = client.post(
        "/api/dedupe/verify",
        json={"keep": str(root / "a.mp4"), "path": str(root / "b.mp4")},
    )
    assert response.status_code == 404


def test_ignore_list_and_unignore_round_trip(client):
    from media_library.config import get_settings

    root = get_settings().library_dir
    a, b = str(root / "a.mp4"), str(root / "b.mp4")
    pair = {"path1": a, "path2": b, "match_type": "content"}
    assert client.post("/api/dedupe/ignore", json=pair).status_code == 200

    page = client.get("/api/dedupe/ignored").json()
    assert page["total"] == 1
    assert page["items"][0]["path1"] == a

    undo = client.post("/api/dedupe/unignore", json={"path1": a, "path2": b})
    assert undo.status_code == 200
    assert client.get("/api/dedupe/ignored").json()["total"] == 0


# ---------------------------------------------------------------------------
# System
# ---------------------------------------------------------------------------


def test_info_reports_the_library_root_and_features(client, monkeypatch):
    from media_library.config import get_settings

    body = client.get("/api/system/info").json()
    assert body["library_root"] == str(get_settings().library_dir)
    assert body["features"]["browse"] is True
    assert body["features"]["workers"] is False


def test_onboarding_returns_paths_and_tools(client):
    body = client.get("/api/system/onboarding").json()
    assert "library_root" in body
    assert "tools" in body
    assert body["counts"]["files"] == 0


def test_index_status_and_sync_refusal(client):
    body = client.get("/api/system/index/status").json()
    assert body["owned"] is False
    refused = client.post("/api/system/index/sync")
    assert refused.status_code == 409
    assert "OWN_WORKERS" in refused.json()["detail"]


def test_status_payload_describes_the_standalone_runtime(client):
    body = client.get("/api/system/status").json()
    assert body["workers"]["mode"] == "standalone"
    assert body["workers"]["indexer"] == "off"
    assert body["index"]["files"] == 0


def test_health_is_public_and_reports_the_database(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"


def test_health_degrades_when_the_index_is_unreadable(client, monkeypatch):
    from media_library.routers import system as system_router

    monkeypatch.setattr(system_router, "is_available", lambda: False)
    response = client.get("/api/health")
    assert response.status_code == 503
    assert response.json()["status"] == "degraded"


def test_load_environment_reads_the_repo_env_file():
    load_environment()
    from media_library.config import get_settings

    assert get_settings().base_dir.exists()


# ---------------------------------------------------------------------------
# Restore CLI
# ---------------------------------------------------------------------------


def test_restore_cli_requires_confirmation(capsys):
    assert restore_cli.main([]) == 2
    assert "--yes" in capsys.readouterr().err


def test_restore_cli_reports_a_missing_snapshot(tmp_path, monkeypatch, capsys):
    from media_library import backups

    monkeypatch.setattr(
        backups,
        "restore_snapshot",
        lambda **kw: (_ for _ in ()).throw(RuntimeError("no snapshot")),
    )
    assert restore_cli.main(["--yes"]) == 1
    assert "Restore failed" in capsys.readouterr().err


def test_restore_cli_restores_a_snapshot(tmp_path, monkeypatch, capsys):
    from media_library import backups

    result = {"success": True, "files": 3, "folders": 1}
    monkeypatch.setattr(backups, "restore_snapshot", lambda **kw: result)
    assert restore_cli.main(["--yes"]) == 0
    assert json.loads(capsys.readouterr().out)["files"] == 3


# ---------------------------------------------------------------------------
# Application assembly
# ---------------------------------------------------------------------------


def test_missing_favicon_is_a_json_404(client):
    response = client.get("/favicon.ico")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")


def test_startup_survives_a_failed_snapshot_check(monkeypatch):
    """A broken backup directory must not stop the server from starting."""
    from media_library import app as app_module
    from media_library import backups

    def boom():
        raise RuntimeError("mount unavailable")

    monkeypatch.setattr(backups, "auto_restore_if_missing", boom)
    with TestClient(app_module.create_app()) as client:
        assert client.get("/api/health").status_code == 200


def test_startup_survives_a_failed_schema_creation(monkeypatch):
    from media_library import app as app_module

    def boom(*args, **kwargs):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(app_module, "init_schema", boom)
    with TestClient(app_module.create_app()) as client:
        assert client.get("/api/health").status_code == 200


def test_main_builds_the_app_and_starts_uvicorn(monkeypatch):
    import sys

    import uvicorn

    from media_library import app as app_module

    captured: dict[str, object] = {}

    def fake_run(app, *, host, port, log_level):
        captured.update(app=app, host=host, port=port, log_level=log_level)

    monkeypatch.setattr(uvicorn, "run", fake_run)
    monkeypatch.setattr(
        sys, "argv", ["app.py", "--port", "9999", "--host", "127.0.0.1"]
    )
    app_module.main()

    assert type(captured["app"]).__name__ == "FastAPI"
    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 9999
