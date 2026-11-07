"""Session, sign-in and sign-out endpoints for the client."""

from __future__ import annotations

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
async def login(payload: AuthLoginRequest, response: Response) -> dict:
    password = get_app_password()
    if not password:
        return {"enabled": False, "authenticated": True}
    if not check_password(payload.password):
        raise HTTPException(status_code=401, detail="Incorrect password")
    set_auth_cookie(response, password)
    return {"enabled": True, "authenticated": True}


@router.post("/auth/logout", response_model=AuthSession)
async def logout(response: Response) -> dict:
    clear_auth_cookie(response)
    enabled = bool(get_app_password())
    return {"enabled": enabled, "authenticated": not enabled}
