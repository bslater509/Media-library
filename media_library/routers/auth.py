"""Authentication routes and HTTP middleware: single shared-password gate."""

import html
import time
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from media_library.core.auth import (
    AUTH_COOKIE_MAX_AGE,
    AUTH_COOKIE_NAME,
    AUTH_COOKIE_REFRESH_INTERVAL,
    check_password,
    create_auth_token,
    get_app_password,
    parse_auth_token,
)
from media_library.core.templating import SafeHTML, load_template, render

router = APIRouter()

LOGIN_PATH = "/login"

# Paths that must stay reachable without a password so the login page can render.
_PUBLIC_PATHS = {LOGIN_PATH, "/favicon.ico", "/sw.js"}
_PUBLIC_PREFIXES = ("/static/",)

_FALLBACK_LOGIN_HTML = (
    "<!doctype html><html><head><meta charset='utf-8'>"
    "<meta name='viewport' content='width=device-width,initial-scale=1'>"
    "<title>Sign in</title></head>"
    "<body style='font-family:system-ui;background:#0f1115;color:#e6e6e6;"
    "display:flex;min-height:100vh;align-items:center;justify-content:center'>"
    "<form method='post' action='/login' style='background:#171a21;padding:2rem;border-radius:12px'>"
    "{{ERROR_BLOCK}}<h1 style='font-size:1.1rem'>Sign in</h1>"
    "<input type='password' name='password' autofocus style='padding:.6rem;width:16rem'>"
    "<input type='hidden' name='next' value='{{NEXT}}'>"
    "<button style='padding:.6rem 1rem;margin-left:.5rem'>Enter</button></form>"
    "</body></html>"
)


def _is_public(path: str) -> bool:
    if path in _PUBLIC_PATHS:
        return True
    return any(path.startswith(prefix) for prefix in _PUBLIC_PREFIXES)


def _safe_next(next_url: str) -> str:
    """Only allow same-site, absolute-path redirects (blocks open redirects)."""
    if next_url and next_url.startswith("/") and not next_url.startswith("//"):
        return next_url
    return "/"


def _set_auth_cookie(response, password: str) -> None:
    response.set_cookie(
        key=AUTH_COOKIE_NAME,
        value=create_auth_token(password),
        max_age=AUTH_COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        path="/",
    )


async def auth_middleware(request: Request, call_next):
    """Gate every page and API route behind the shared password when enabled."""
    password = get_app_password()
    if not password:
        return await call_next(request)

    path = request.url.path
    if _is_public(path):
        return await call_next(request)

    issued = parse_auth_token(password, request.cookies.get(AUTH_COOKIE_NAME, ""))
    if issued is not None:
        response = await call_next(request)
        # Sliding expiry: refresh the cookie occasionally so an active browser
        # stays logged in indefinitely without a Set-Cookie on every request.
        if time.time() - issued > AUTH_COOKIE_REFRESH_INTERVAL:
            _set_auth_cookie(response, password)
        return response

    if path.startswith("/api/"):
        return JSONResponse({"detail": "Unauthorized"}, status_code=401)

    target = path
    if request.url.query:
        target = f"{target}?{request.url.query}"
    return RedirectResponse(
        url=f"{LOGIN_PATH}?next={quote(target, safe='')}", status_code=302
    )


def _render_login(next_url: str, error: str = "") -> HTMLResponse:
    template = load_template("login.html")
    body = template if template is not None else _FALLBACK_LOGIN_HTML
    error_block = (
        SafeHTML(f'<p class="login-error">{html.escape(error)}</p>') if error else ""
    )
    return HTMLResponse(
        content=render(body, ERROR_BLOCK=error_block, NEXT=_safe_next(next_url))
    )


@router.api_route(LOGIN_PATH, methods=["GET", "HEAD"], response_class=HTMLResponse)
async def login_page(request: Request):
    password = get_app_password()
    next_url = _safe_next(request.query_params.get("next", "/"))
    if not password:
        return RedirectResponse(url=next_url, status_code=302)
    if (
        parse_auth_token(password, request.cookies.get(AUTH_COOKIE_NAME, ""))
        is not None
    ):
        return RedirectResponse(url=next_url, status_code=302)
    return _render_login(next_url)


@router.post(LOGIN_PATH, response_class=HTMLResponse)
async def login_submit(request: Request):
    password = get_app_password()
    form = await request.form()
    next_url = _safe_next(str(form.get("next", "/")))
    if not password:
        return RedirectResponse(url=next_url, status_code=302)
    if not check_password(str(form.get("password", ""))):
        return _render_login(next_url, error="Incorrect password. Try again.")
    response = RedirectResponse(url=next_url, status_code=302)
    _set_auth_cookie(response, password)
    return response
