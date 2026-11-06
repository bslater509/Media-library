"""v2 auth: token contract, cookie gate and the standalone app's routes."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from media_library_v2 import auth
from media_library_v2.standalone import build_standalone_app


@pytest.fixture
def secured_client(monkeypatch):
    monkeypatch.setenv("APP_PASSWORD", "hunter2")
    return TestClient(build_standalone_app())


@pytest.fixture
def open_client(monkeypatch):
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    return TestClient(build_standalone_app())


def test_token_round_trip_and_wrong_password():
    token = auth.create_auth_token("secret", issued_at=1_700_000_000)
    assert auth.parse_auth_token("secret", token) == 1_700_000_000
    assert auth.parse_auth_token("different", token) is None


def test_tampered_and_malformed_tokens_are_rejected():
    token = auth.create_auth_token("secret", issued_at=1_700_000_000)
    assert (
        auth.parse_auth_token("secret", token[:-1] + ("A" if token[-1] != "A" else "B"))
        is None
    )
    for bad in ("", "not-base64!!", "abcd", "MTIz", "@@@"):
        assert auth.parse_auth_token("secret", bad) is None


def test_cookie_is_compatible_with_the_current_app_format():
    """Same name, payload and key derivation, so a login carries over."""
    assert auth.AUTH_COOKIE_NAME == "ml_auth"
    token = auth.create_auth_token("hunter2", issued_at=1_700_000_000)
    import base64
    import hashlib
    import hmac

    raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)).decode()
    payload, signature = raw.split(".", 1)
    key = hashlib.sha256(b"media_library_auth_v1:hunter2").digest()
    expected = hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()
    assert signature == expected


def test_session_is_public_and_reports_enabled(secured_client):
    response = secured_client.get("/api/v2/auth/session")
    assert response.status_code == 200
    assert response.json() == {"enabled": True, "authenticated": False}


def test_api_requires_the_cookie_when_enabled(secured_client):
    assert secured_client.get("/api/v2/system/info").status_code == 401
    assert secured_client.get("/api/v2/library/browse").status_code == 401
    assert secured_client.get("/api/health").status_code == 200


def test_pages_still_serve_the_client_shell(secured_client):
    """No server-side login page: the SPA renders the sign-in screen itself."""
    for path in ("/", "/library", "/settings"):
        response = secured_client.get(path)
        assert response.status_code in (200, 503)
        assert "text/html" in response.headers["content-type"]


def test_login_sets_cookie_and_opens_the_api(secured_client):
    wrong = secured_client.post("/api/v2/auth/login", json={"password": "nope"})
    assert wrong.status_code == 401

    login = secured_client.post("/api/v2/auth/login", json={"password": "hunter2"})
    assert login.status_code == 200
    assert login.json() == {"enabled": True, "authenticated": True}
    assert auth.AUTH_COOKIE_NAME in login.cookies or secured_client.cookies.get(
        auth.AUTH_COOKIE_NAME
    )

    assert secured_client.get("/api/v2/auth/session").json()["authenticated"] is True
    assert secured_client.get("/api/v2/system/info").status_code == 200


def test_logout_clears_the_cookie(secured_client):
    secured_client.post("/api/v2/auth/login", json={"password": "hunter2"})
    assert secured_client.get("/api/v2/system/info").status_code == 200

    logout = secured_client.post("/api/v2/auth/logout")
    assert logout.status_code == 200
    assert logout.json() == {"enabled": True, "authenticated": False}
    assert secured_client.get("/api/v2/system/info").status_code == 401


def test_malformed_cookie_is_treated_as_signed_out(secured_client):
    secured_client.cookies.set(auth.AUTH_COOKIE_NAME, "garbage")
    assert secured_client.get("/api/v2/system/info").status_code == 401


def test_old_cookie_is_refreshed_on_use(secured_client):
    issued = int(time.time()) - auth.AUTH_COOKIE_REFRESH_INTERVAL - 60
    secured_client.cookies.set(
        auth.AUTH_COOKIE_NAME, auth.create_auth_token("hunter2", issued_at=issued)
    )
    response = secured_client.get("/api/v2/auth/session")
    assert response.status_code == 200
    assert response.cookies.get(auth.AUTH_COOKIE_NAME)


def test_auth_disabled_leaves_everything_open(open_client):
    assert open_client.get("/api/v2/auth/session").json() == {
        "enabled": False,
        "authenticated": True,
    }
    assert open_client.get("/api/v2/system/info").status_code == 200
    assert open_client.post("/api/v2/auth/login", json={"password": "x"}).json() == {
        "enabled": False,
        "authenticated": True,
    }


def test_standalone_spa_mounts_at_the_root(open_client):
    root = open_client.get("/")
    assert root.status_code in (200, 503)
    assert "text/html" in root.headers["content-type"]
    deep = open_client.get("/tools/dedupe")
    assert deep.status_code in (200, 503)
    assert "text/html" in deep.headers["content-type"]


def test_unknown_api_paths_stay_json_404(open_client):
    response = open_client.get("/api/v2/does-not-exist")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")


def test_preview_mount_still_serves_under_v2(open_client):
    """The same client bundle also serves the side-by-side /v2 mount."""
    from media_library_v2.views import build_router

    app = build_standalone_app()
    app.include_router(build_router("/v2"))
    client = TestClient(app)
    response = client.get("/v2/library")
    assert response.status_code in (200, 503)
    assert "text/html" in response.headers["content-type"]


def test_old_v2_urls_redirect_to_the_root_app(open_client):
    root = open_client.get("/v2", follow_redirects=False)
    assert root.status_code == 307
    assert root.headers["location"] == "/"

    deep = open_client.get("/v2/library?path=/x%20y", follow_redirects=False)
    assert deep.status_code == 307
    assert deep.headers["location"] == "/library?path=/x%20y"
