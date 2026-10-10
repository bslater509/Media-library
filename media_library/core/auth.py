"""Single shared-password authentication helpers.

The whole web UI and API can be gated behind one password supplied via the
``APP_PASSWORD`` environment variable. Login issues a stateless signed cookie
(no server-side session store, so it survives restarts and needs no DB writes);
because the signing key is derived from the password, changing the password
automatically invalidates every previously issued cookie. When ``APP_PASSWORD``
is empty or unset, authentication is disabled entirely.
"""

import base64
import hashlib
import hmac
import os
import time

from media_library.core import config

# Name of the signed session cookie set after a successful login.
AUTH_COOKIE_NAME = "ml_auth"

# Browsers cap cookie lifetimes at roughly 400 days; the cookie is refreshed well
# before that (sliding expiry) so it effectively never lapses while in use.
AUTH_COOKIE_MAX_AGE = 400 * 24 * 60 * 60

# Re-issue the cookie on requests whose token is older than this (30 days).
AUTH_COOKIE_REFRESH_INTERVAL = 30 * 24 * 60 * 60


def get_app_password() -> str:
    """Return the configured shared password (empty string disables auth).

    The live environment is preferred so runtime ``.env``/env changes and tests
    are honoured; the import-time config snapshot is used as a fallback.
    """
    env_val = os.environ.get("APP_PASSWORD")
    if env_val is not None:
        return env_val.strip()
    return getattr(config, "APP_PASSWORD", "").strip()


def is_auth_enabled() -> bool:
    return bool(get_app_password())


def _signing_key(password: str) -> bytes:
    # Deriving the key from the password means changing the password
    # automatically invalidates every previously issued cookie.
    return hashlib.sha256(
        ("media_library_auth_v1:" + password).encode("utf-8")
    ).digest()


def create_auth_token(password: str, issued_at: int | None = None) -> str:
    """Build a URL-safe signed token embedding the issue time."""
    issued = int(issued_at if issued_at is not None else time.time())
    payload = str(issued)
    sig = hmac.new(
        _signing_key(password), payload.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    raw = f"{payload}.{sig}"
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")


def parse_auth_token(password: str, token: str) -> int | None:
    """Return the token's issued-at timestamp if it is valid, else ``None``."""
    if not token:
        return None
    try:
        padded = token + "=" * (-len(token) % 4)
        raw = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
        payload, sig = raw.split(".", 1)
        expected = hmac.new(
            _signing_key(password), payload.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        return int(payload)
    except ValueError:
        # Tampered/malformed cookie (bad base64, missing dot, non-int timestamp):
        # treat as unauthenticated. binascii/Unicode decode errors are ValueError
        # subclasses, so this covers all decode+parse failures.
        return None


def check_password(provided: str) -> bool:
    """Constant-time password comparison (always True when auth is disabled)."""
    password = get_app_password()
    if not password:
        return True
    return hmac.compare_digest(provided or "", password)
