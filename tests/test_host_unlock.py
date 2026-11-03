"""Tests for password-protected download pages (``services/host_unlock``).

A local HTTP server stands in for a file host: it answers a "Password Required"
page until the password form is submitted, then sets a session cookie and lists
the media. No external network is used.
"""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

import pytest

from media_library.services.host_unlock import (
    is_password_protected,
    unlock_page,
)

PASSWORD = "simp"


class _HostHandler(BaseHTTPRequestHandler):
    """Minimal password-gated host: /f/ID locked until POST /f/ID."""

    def log_message(self, *args):  # keep pytest output quiet
        pass

    def _page(self, unlocked: bool) -> bytes:
        if unlocked:
            return (
                b"<html><head><title>Folder</title></head><body>"
                b"<div class='file-item' data-name='a.mp4'></div></body></html>"
            )
        return (
            b"<html><head><meta name='x' content='padding padding padding padding "
            b"padding padding padding padding'><title>Password Required - host"
            b"</title></head><body><form action='/f/ID' method='POST'>"
            b"<input type='hidden' name='nonce' value='abc123'>"
            b"<input type='password' name='password'>"
            b"</form></body></html>"
        )

    def do_GET(self):
        cookie = self.headers.get("Cookie") or ""
        unlocked = "session=ok" in cookie
        body = self._page(unlocked)
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        fields = parse_qs(self.rfile.read(length).decode())
        submitted = (fields.get("password") or [""])[0]
        if submitted == PASSWORD:
            self.send_response(303)
            self.send_header("Set-Cookie", "session=ok; Path=/")
            self.send_header("Location", "/f/ID")
            self.end_headers()
            return
        body = self._page(False)
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def host():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _HostHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/f/ID"
    server.shutdown()
    server.server_close()


def test_is_password_protected_detects_title_far_into_the_page():
    page = (
        "<html><head><meta charset='utf-8'>"
        + "<meta name='x' content='y'>" * 12
        + "<title>Password Required - host</title></head></html>"
    )
    assert is_password_protected(page)


def test_is_password_protected_detects_password_form_without_title():
    page = (
        "<html><body><form action='/f/1'><input type='hidden' name='nonce' value='n'>"
        "<input type='password' name='password'></form></body></html>"
    )
    assert is_password_protected(page)


def test_is_password_protected_is_false_for_a_listing():
    assert not is_password_protected(
        "<html><title>Folder</title><body>hi</body></html>"
    )


def test_unlock_page_saves_session_cookies(host, tmp_path):
    cookie_path = tmp_path / "job.cookies.txt"
    assert unlock_page(host, PASSWORD, cookie_path) is True
    assert cookie_path.is_file()
    assert "session" in cookie_path.read_text()


def test_unlock_page_rejects_a_wrong_password(host, tmp_path):
    cookie_path = tmp_path / "job.cookies.txt"
    assert unlock_page(host, "wrong", cookie_path) is False
    assert not cookie_path.exists()


def test_unlock_page_without_a_password_does_nothing(host, tmp_path):
    cookie_path = tmp_path / "job.cookies.txt"
    assert unlock_page(host, "", cookie_path) is False
    assert not cookie_path.exists()


def test_unlock_page_returns_false_for_an_open_page(tmp_path):
    """A folder that needs no password must not produce a jar."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _OpenHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/f/OPEN"
        cookie_path = tmp_path / "job.cookies.txt"
        assert unlock_page(url, PASSWORD, cookie_path) is False
        assert not cookie_path.exists()
    finally:
        server.shutdown()
        server.server_close()


class _OpenHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        body = b"<html><title>Folder</title><body>public</body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
