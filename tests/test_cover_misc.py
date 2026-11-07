"""Line coverage for the small core modules and the thin mutation routers.

Kept deliberately small and deterministic: error branches are driven by
monkeypatching, the query-validation branches by boundary payloads, and the
lifespan/startup branches by a fresh TestClient context. No network, no
filesystem walks, no real snapshot writes.
"""

from __future__ import annotations

import asyncio
import dataclasses
import sqlite3
import time
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from media_library import auth as auth_core
from media_library import backups, indexer, library, writes
from media_library import config as v2_config
from media_library import db as db_module
from media_library.app import create_app
from media_library.routers import auth as auth_router_mod
from media_library.routers import favorites as favorites_router_mod
from media_library.routers import library as library_router_mod
from media_library.routers import manage as manage_router_mod
from media_library.routers import system as system_router_mod
from media_library.routers import uploads as uploads_router_mod
from media_library.views import build_router


def _router_app(router):
    """A TestClient hosting one router under ``/api``."""
    app = FastAPI()
    app.include_router(router, prefix="/api")
    return TestClient(app)
    app = FastAPI()
    app.include_router(router, prefix="/api")
    return app


def _boom(*args, **kwargs):
    raise sqlite3.OperationalError("boom")


def _patch_settings_static_dir(monkeypatch, tmp_path: Path) -> None:
    """Point ``static_dir`` at a throwaway folder for one test."""
    static = tmp_path / "static"
    static.mkdir()
    settings = dataclasses.replace(v2_config.get_settings(), static_dir=static)
    monkeypatch.setattr(v2_config, "_settings", settings)
    return static


# --- auth core ---------------------------------------------------------------


def test_is_auth_enabled_and_disabled_password(monkeypatch):
    """Auth switches on the configured password alone."""
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    assert auth_core.is_auth_enabled() is False
    # Auth off: every password is accepted.
    assert auth_core.check_password("anything") is True


def test_middleware_refreshes_a_stale_cookie(monkeypatch):
    """A cookie older than the refresh interval is re-issued on a request."""
    monkeypatch.setenv("APP_PASSWORD", "pw")
    stale = auth_core.create_auth_token(
        "pw", issued_at=int(time.time()) - 40 * 24 * 3600
    )
    client = TestClient(create_app())
    response = client.get("/api/system/info", headers={"Cookie": f"ml_auth={stale}"})
    assert response.status_code == 200
    assert "ml_auth" in response.headers.get("set-cookie", "")


# --- auth router back-off bookkeeping ---------------------------------------


def test_backoff_resets_after_the_window(monkeypatch):
    store: dict = {}
    monkeypatch.setattr(auth_router_mod, "_failures", store)
    store["ip"] = (time.monotonic() - auth_router_mod._FAILURE_WINDOW - 1, 5)
    assert auth_router_mod._backoff("ip") == 0.0
    assert store["ip"][1] == 0


def test_backoff_is_capped_at_the_maximum(monkeypatch):
    store: dict = {}
    monkeypatch.setattr(auth_router_mod, "_failures", store)
    store["ip"] = (time.monotonic(), 1000)
    assert auth_router_mod._backoff("ip") == auth_router_mod._MAX_BACKOFF


def test_record_failure_resets_after_the_window(monkeypatch):
    store: dict = {}
    monkeypatch.setattr(auth_router_mod, "_failures", store)
    store["ip"] = (time.monotonic() - auth_router_mod._FAILURE_WINDOW - 1, 5)
    floor = time.monotonic()
    auth_router_mod._record_failure("ip")
    started, count = store["ip"]
    assert count == 1
    assert started >= floor


def test_record_failure_evicts_stale_entries(monkeypatch):
    store: dict = {}
    monkeypatch.setattr(auth_router_mod, "_failures", store)
    stale = time.monotonic() - auth_router_mod._FAILURE_WINDOW - 1
    for i in range(1001):
        store[f"client-{i}"] = (stale, 3)
    auth_router_mod._record_failure("live")
    assert set(store) == {"live"}
    assert store["live"][1] == 1


def test_login_sleeps_through_the_backoff(monkeypatch):
    monkeypatch.setenv("APP_PASSWORD", "pw")
    monkeypatch.setattr(auth_router_mod, "_failures", {})
    monkeypatch.setattr(auth_router_mod, "_backoff", lambda ip: 0.01)
    client = _router_app(auth_router_mod.router)
    assert client.post("/api/auth/login", json={"password": "wrong"}).status_code == 401


# --- system router -----------------------------------------------------------


def test_system_info_and_status_503_when_index_broken(monkeypatch):
    monkeypatch.setattr(library, "counts", _boom)
    client = _router_app(system_router_mod.router)
    assert client.get("/api/system/info").status_code == 503
    assert client.get("/api/system/status").status_code == 503


def test_index_sync_reports_a_started_crawl(monkeypatch):
    monkeypatch.setenv("OWN_WORKERS", "true")
    v2_config.reload_settings()
    calls: list[bool] = []

    def fake_start(force_full=True):
        calls.append(force_full)
        return True

    monkeypatch.setattr(indexer, "start_crawl", fake_start)
    client = _router_app(system_router_mod.router)
    body = client.post("/api/system/index/sync").json()
    assert body["started"] is True
    assert calls == [True]


# --- library router: 503 guard branches --------------------------------------


def test_library_routes_503_when_index_broken(monkeypatch):
    for name in ("browse", "list_folders", "search_folders", "search", "favorites"):
        monkeypatch.setattr(library, name, _boom)
    client = _router_app(library_router_mod.router)
    assert client.get("/api/library/browse").status_code == 503
    assert client.get("/api/library/folders").status_code == 503
    assert (
        client.get("/api/library/folders/search", params={"q": "x"}).status_code == 503
    )
    assert client.get("/api/library/search", params={"q": "x"}).status_code == 503
    assert client.get("/api/library/favorites").status_code == 503


# --- mutation routers: 503 guard branches ------------------------------------


def test_favorite_batch_503_on_sqlite_error(monkeypatch):
    monkeypatch.setattr(writes, "set_favorites", _boom)
    client = _router_app(favorites_router_mod.router)
    response = client.post(
        "/api/favorites/batch",
        json={"paths": ["/a/b.jpg"], "action": "add"},
    )
    assert response.status_code == 503


def test_manage_bulk_rename_and_delete_503_on_sqlite_error(monkeypatch):
    monkeypatch.setattr(writes, "bulk_rename", _boom)
    monkeypatch.setattr(writes, "delete_files", _boom)
    client = _router_app(manage_router_mod.router)
    bulk = client.post(
        "/api/manage/bulk-rename", json={"renames": [{"path": "/a", "name": "b"}]}
    )
    assert bulk.status_code == 503
    delete = client.post("/api/manage/delete", json={"paths": ["/a"]})
    assert delete.status_code == 503


def test_upload_status_and_cancel_503_on_sqlite_error(monkeypatch):
    monkeypatch.setattr(writes, "staged_size", _boom)
    monkeypatch.setattr(writes, "cancel_upload", _boom)
    client = _router_app(uploads_router_mod.router)
    assert (
        client.get(
            "/api/uploads/status", params={"upload_id": "abcdef1234"}
        ).status_code
        == 503
    )
    cancel = client.post("/api/uploads/cancel", json={"upload_id": "abcdef1234"})
    assert cancel.status_code == 503


# --- uploads router: chunk boundary validation -------------------------------


class _FakeUpload:
    """Minimal stand-in for a FastAPI ``UploadFile``."""

    def __init__(self, size: int):
        self.size = size

    async def read(self, limit: int) -> bytes:
        return b"\x00" * self.size


def test_upload_chunk_too_large():
    oversize = 64 * 1024 * 1024 + 1
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            uploads_router_mod.upload_chunk("abcdef1234", 0, _FakeUpload(oversize))
        )
    assert exc_info.value.status_code == 413


def test_upload_chunk_rejects_a_negative_offset():
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(uploads_router_mod.upload_chunk("abcdef1234", -1, _FakeUpload(10)))
    assert exc_info.value.status_code == 400


# --- config ------------------------------------------------------------------


def test_env_int_and_float_fall_back_to_defaults_on_garbage(monkeypatch):
    monkeypatch.setenv("WEBP_QUALITY", "not-a-number")
    monkeypatch.setenv("BACKUP_INTERVAL_HOURS", "not-a-number")
    settings = v2_config.reload_settings()
    assert settings.webp_quality == 85
    assert settings.backup_interval_hours == 6.0


def test_library_dir_defaults_to_data_dir_media(monkeypatch):
    monkeypatch.delenv("LIBRARY_DIR", raising=False)
    # The machine's .env keeps a real LIBRARY_DIR; hide it for this assertion.
    monkeypatch.setattr(
        v2_config,
        "_DOTENV",
        {k: v for k, v in v2_config._DOTENV.items() if k != "LIBRARY_DIR"},
    )
    settings = v2_config.reload_settings()
    assert settings.library_dir == settings.data_dir / "media"


def test_db_path_falls_back_to_a_root_backup_index(tmp_path, monkeypatch):
    """Without DB_PATH, a root ``backup_index.db`` wins over the data dir."""
    monkeypatch.delenv("DB_PATH", raising=False)
    monkeypatch.setattr(
        v2_config,
        "_DOTENV",
        {k: v for k, v in v2_config._DOTENV.items() if k != "DB_PATH"},
    )
    with_db = tmp_path / "with"
    with_db.mkdir()
    (with_db / "backup_index.db").write_bytes(b"placeholder")
    monkeypatch.setattr(v2_config, "BASE_DIR", with_db)
    settings = v2_config.reload_settings()
    assert settings.db_path == (with_db / "backup_index.db").resolve()


def test_db_path_defaults_to_the_data_dir_when_the_root_backup_is_absent(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("DB_PATH", raising=False)
    monkeypatch.setattr(
        v2_config,
        "_DOTENV",
        {k: v for k, v in v2_config._DOTENV.items() if k != "DB_PATH"},
    )
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setattr(v2_config, "BASE_DIR", empty)
    settings = v2_config.reload_settings()
    assert settings.db_path == (settings.data_dir / "library.db").resolve()


# --- db ----------------------------------------------------------------------


def test_is_available_false_without_a_database(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "missing.db"))
    v2_config.reload_settings()
    assert db_module.is_available() is False


def test_is_available_false_on_a_garbage_file(tmp_path, monkeypatch):
    garbage = tmp_path / "garbage.db"
    garbage.write_bytes(b"this is not a database")
    monkeypatch.setenv("DB_PATH", str(garbage))
    v2_config.reload_settings()
    assert db_module.is_available() is False


# --- views and static delivery ------------------------------------------------


def test_missing_client_build_serves_the_placeholder_page(tmp_path, monkeypatch):
    _patch_settings_static_dir(monkeypatch, tmp_path)
    app = FastAPI()
    app.include_router(build_router(""))
    client = TestClient(app)
    response = client.get("/library")
    assert response.status_code == 503
    assert "not built" in response.text


def test_favicon_serves_the_icon_file(tmp_path, monkeypatch):
    static = _patch_settings_static_dir(monkeypatch, tmp_path)
    (static / "favicon.ico").write_bytes(b"icon")
    client = TestClient(create_app())
    response = client.get("/favicon.ico")
    assert response.status_code == 200
    assert response.content == b"icon"


# --- startup and shutdown lifespan branches ----------------------------------


def test_lifespan_drops_obsolete_indexes(monkeypatch):
    db_path = Path(v2_config.get_settings().db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE INDEX idx_files_name ON files(name)")
    monkeypatch.setattr(backups, "auto_restore_if_missing", lambda: {"restored": False})
    ran: dict = {}
    monkeypatch.setattr(backups, "create_snapshot", lambda: ran.setdefault("ok", True))
    with TestClient(create_app()) as client:
        assert client.get("/api/health").status_code == 200
    with sqlite3.connect(db_path) as conn:
        names = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            )
        }
    assert "idx_files_name" not in names
    assert ran["ok"]


def test_lifespan_report_s_read_only_index_and_shutdown_snapshot_failure(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "missing.db"))
    monkeypatch.setenv("DB_READ_ONLY", "true")
    v2_config.reload_settings()
    died: dict = {}

    def failing_snapshot():
        died["called"] = True
        raise RuntimeError("no snapshot for you")

    monkeypatch.setattr(backups, "auto_restore_if_missing", lambda: {"restored": False})
    monkeypatch.setattr(backups, "create_snapshot", failing_snapshot)
    with TestClient(create_app()) as probe:
        probe.get("/api/health")  # The missing index still answers 503.
    assert died["called"]
