"""Single shared-password authentication.

The cookie is ``base64url(issued_epoch "." hmac_sha256)``, keyed by
``sha256("media_library_auth_v1:" + password)``, with a 400-day lifetime and a
30-day sliding refresh, so a browser that signed in to the app before the
rewrite stays signed in. Changing ``APP_PASSWORD`` invalidates every issued
cookie.

Only ``os.environ`` is read here, never the package's ``.env`` snapshot: the
entrypoint owns dotenv loading, and a test that removes the variable must really
remove it.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time

from fastapi import Request
from fastapi.responses import JSONResponse

AUTH_COOKIE_NAME = "ml_auth"

# Browsers cap cookie lifetimes at roughly 400 days; the cookie is refreshed well
# before that (sliding expiry) so it effectively never lapses while in use.
AUTH_COOKIE_MAX_AGE = 400 * 24 * 60 * 60

# Re-issue the cookie on requests whose token is older than this (30 days).
AUTH_COOKIE_REFRESH_INTERVAL = 30 * 24 * 60 * 60

# Reachable without a password. The session/login/logout endpoints are how the
# client finds out whether it must ask for one at all; ``/api/health`` is a
# credential-free liveness probe; ``/static`` serves the client bundle so the
# sign-in screen can render.
_PUBLIC_PATHS = {
    "/favicon.ico",
    "/api/health",
    "/api/auth/session",
    "/api/auth/login",
    "/api/auth/logout",
}
_PUBLIC_PREFIXES = ("/static/",)


def get_app_password() -> str:
    """Return the configured shared password (empty string disables auth)."""
    return os.environ.get("APP_PASSWORD", "").strip()


def is_auth_enabled() -> bool:
    return bool(get_app_password())


def _signing_key(password: str) -> bytes:
    # Derived from the password, so changing it invalidates every cookie.
    return hashlib.sha256(
        ("media_library_auth_v1:" + password).encode("utf-8")
    ).digest()


def create_auth_token(password: str, issued_at: int | None = None) -> str:
    """Build a URL-safe signed token embedding the issue time."""
    issued = int(issued_at if issued_at is not None else time.time())
    payload = str(issued)
    signature = hmac.new(
        _signing_key(password), payload.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    raw = f"{payload}.{signature}"
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")


def parse_auth_token(password: str, token: str) -> int | None:
    """Return the token's issued-at timestamp if it is valid, else ``None``."""
    if not token:
        return None
    try:
        padded = token + "=" * (-len(token) % 4)
        raw = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
        payload, signature = raw.split(".", 1)
        expected = hmac.new(
            _signing_key(password), payload.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return None
        return int(payload)
    except ValueError:
        # Tampered or malformed cookie: treat as unauthenticated. binascii and
        # Unicode decode errors are ValueError subclasses, so this covers every
        # decode and parse failure.
        return None


def check_password(provided: str) -> bool:
    """Constant-time password comparison (always True when auth is disabled)."""
    password = get_app_password()
    if not password:
        return True
    # Compare bytes: str compare_digest rejects non-ASCII input with TypeError.
    return hmac.compare_digest(
        (provided or "").encode("utf-8"), password.encode("utf-8")
    )


def set_auth_cookie(response, password: str) -> None:
    response.set_cookie(
        key=AUTH_COOKIE_NAME,
        value=create_auth_token(password),
        max_age=AUTH_COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        path="/",
    )


def clear_auth_cookie(response) -> None:
    response.delete_cookie(key=AUTH_COOKIE_NAME, path="/", samesite="lax")


def is_public_path(path: str) -> bool:
    if path in _PUBLIC_PATHS:
        return True
    return any(path.startswith(prefix) for prefix in _PUBLIC_PREFIXES)


async def auth_middleware(request: Request, call_next):
    """Gate the API behind the shared password when one is configured.

    Unauthenticated API calls get JSON ``401`` so the client can show its sign-in
    screen. Page requests still receive the SPA shell: every data call the app
    makes is an API call, so nothing is disclosed, and the client renders the
    sign-in screen instead of a server-side HTML login page.
    """
    password = get_app_password()
    if not password or is_public_path(request.url.path):
        return await call_next(request)

    issued = parse_auth_token(password, request.cookies.get(AUTH_COOKIE_NAME, ""))
    if issued is None:
        if request.url.path.startswith("/api/"):
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)
        return await call_next(request)

    response = await call_next(request)
    # Sliding expiry: refresh occasionally rather than on every request.
    if time.time() - issued > AUTH_COOKIE_REFRESH_INTERVAL:
        set_auth_cookie(response, password)
    return response
