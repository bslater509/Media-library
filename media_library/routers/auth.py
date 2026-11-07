"""Session, sign-in and sign-out endpoints for the client."""

from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, HTTPException, Request, Response

from media_library.auth import (
    AUTH_COOKIE_NAME,
    AUTH_COOKIE_REFRESH_INTERVAL,
    check_password,
    clear_auth_cookie,
    get_app_password,
    parse_auth_token,
    set_auth_cookie,
)
from media_library.schemas import AuthLoginRequest, AuthSession

router = APIRouter()

# Brute-force throttle for the single shared password: an exponential delay
# per failing client IP, capped at 8s. Kept in memory — it only has to make
# request-speed guessing expensive, not to survive a restart.
_failures: dict[str, tuple[float, int]] = {}
_FAILURE_WINDOW = 600.0
_MAX_BACKOFF = 8.0


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "??"


def _backoff(ip: str) -> float:
    now = time.monotonic()
    started, count = _failures.get(ip, (now, 0))
    if now - started > _FAILURE_WINDOW:
        _failures[ip] = (now, 0)
        return 0.0
    if count <= 0:
        return 0.0
    if count >= 1000:
        return _MAX_BACKOFF
    # 1s after the first failure, then x2 per failure, capped at 8s.
    return min(2.0 ** (count - 1), _MAX_BACKOFF)


def _record_failure(ip: str) -> None:
    now = time.monotonic()
    started, count = _failures.get(ip, (now, 0))
    if now - started > _FAILURE_WINDOW:
        started = now
        count = 0
    _failures[ip] = (started, count + 1)
    if len(_failures) > 1000:
        # Evict stale/leftover entries so the map follows recent clients only.
        cutoff = now - _FAILURE_WINDOW
        for key, (stamp, _count) in list(_failures.items()):
            if stamp < cutoff:
                _failures.pop(key, None)


@router.get("/auth/session", response_model=AuthSession)
async def session(request: Request, response: Response) -> dict:
    """Whether a password is required, and whether this browser has one.

    Also the sliding-expiry touch point: the session endpoint is public, so the
    middleware never sees an authenticated request to this path, and the client
    calls it on every app load.
    """
    password = get_app_password()
    if not password:
        return {"enabled": False, "authenticated": True}
    issued = parse_auth_token(password, request.cookies.get(AUTH_COOKIE_NAME, ""))
    if issued is not None and time.time() - issued > AUTH_COOKIE_REFRESH_INTERVAL:
        set_auth_cookie(response, password)
    return {"enabled": True, "authenticated": issued is not None}


@router.post("/auth/login", response_model=AuthSession)
async def login(
    payload: AuthLoginRequest, request: Request, response: Response
) -> dict:
    password = get_app_password()
    if not password:
        return {"enabled": False, "authenticated": True}
    delay = _backoff(_client_ip(request))
    if not check_password(payload.password):
        _record_failure(_client_ip(request))
        # Await off the event loop (a plain sleep would stall every request).
        if delay > 0:
            await asyncio.sleep(delay)
        raise HTTPException(status_code=401, detail="Incorrect password")
    _failures.pop(_client_ip(request), None)
    set_auth_cookie(response, password)
    return {"enabled": True, "authenticated": True}


@router.post("/auth/logout", response_model=AuthSession)
async def logout(response: Response) -> dict:
    clear_auth_cookie(response)
    enabled = bool(get_app_password())
    return {"enabled": enabled, "authenticated": not enabled}
