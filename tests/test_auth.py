"""Tests for the optional single shared-password authentication gate."""

from media_library.core.auth import AUTH_COOKIE_NAME


def test_auth_disabled_by_default(api_client):
    """With no APP_PASSWORD configured, all routes stay open and /login redirects."""
    assert api_client.get("/").status_code == 200
    assert api_client.get("/api/system/status").status_code == 200
    res_login = api_client.get("/login", follow_redirects=False)
    assert res_login.status_code == 302


def test_protected_routes_require_login(api_client, monkeypatch):
    monkeypatch.setenv("APP_PASSWORD", "s3cret")

    # API requests get a 401 rather than a redirect.
    res_api = api_client.get("/api/system/status", follow_redirects=False)
    assert res_api.status_code == 401

    # Page requests redirect to the login page, remembering the target.
    res_page = api_client.get("/", follow_redirects=False)
    assert res_page.status_code == 302
    assert res_page.headers["location"].startswith("/login")
    assert "next=%2F" in res_page.headers["location"]


def test_login_page_and_static_are_public(api_client, monkeypatch):
    monkeypatch.setenv("APP_PASSWORD", "s3cret")
    assert api_client.get("/login").status_code == 200
    assert (
        "/login" in api_client.get("/login").text
        or "password" in api_client.get("/login").text
    )
    # Static assets must load so the login page can render.
    assert api_client.get("/static/styles.css").status_code in (200, 304)


def test_wrong_password_is_rejected(api_client, monkeypatch):
    monkeypatch.setenv("APP_PASSWORD", "s3cret")
    res = api_client.post("/login", data={"password": "nope"}, follow_redirects=False)
    assert res.status_code == 200
    assert "Incorrect" in res.text
    assert AUTH_COOKIE_NAME not in res.cookies
    # Still locked out.
    assert (
        api_client.get("/api/system/status", follow_redirects=False).status_code == 401
    )


def test_correct_password_sets_persistent_cookie(api_client, monkeypatch):
    monkeypatch.setenv("APP_PASSWORD", "s3cret")
    res = api_client.post(
        "/login",
        data={"password": "s3cret", "next": "/api/system/status"},
        follow_redirects=False,
    )
    assert res.status_code == 302
    assert res.headers["location"] == "/api/system/status"
    assert AUTH_COOKIE_NAME in res.cookies

    # The cookie rides along automatically and grants access.
    assert api_client.get("/api/system/status").status_code == 200
    assert api_client.get("/").status_code == 200


def test_changing_password_invalidates_existing_cookie(api_client, monkeypatch):
    monkeypatch.setenv("APP_PASSWORD", "first")
    api_client.post("/login", data={"password": "first"}, follow_redirects=False)
    assert api_client.get("/api/system/status").status_code == 200

    monkeypatch.setenv("APP_PASSWORD", "second")
    assert (
        api_client.get("/api/system/status", follow_redirects=False).status_code == 401
    )


def test_login_rejects_open_redirect(api_client, monkeypatch):
    monkeypatch.setenv("APP_PASSWORD", "s3cret")
    res = api_client.post(
        "/login",
        data={"password": "s3cret", "next": "https://evil.example/"},
        follow_redirects=False,
    )
    assert res.status_code == 302
    assert res.headers["location"] == "/"
