"""Unlock password-protected download pages before handing them to a downloader.

Some file hosts gate a folder or file page behind a password: the first GET
answers a "Password Required" page with a single form, and only after that form
is submitted (which sets a session cookie) does the page list its media. yt-dlp
has no notion of this and gallery-dl's own password handling is per-extractor
and brittle (its filester extractor decides by looking at the first 100 bytes of
the page, which the current layout has long outgrown), so both report "nothing
downloadable" while the folder is really just locked.

This module does the unlock itself and writes the resulting session as a
Netscape cookie jar, which both downloaders accept via ``--cookies``. That keeps
the flow generic: whatever extractor ends up running simply sees an authorised
page.

The password itself never goes on a command line (it would be visible in ``ps``)
and is never logged.
"""

import http.cookiejar
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from base64 import b64encode
from pathlib import Path

logger = logging.getLogger(__name__)

_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
_TIMEOUT_S = 25

# A locked page announces itself in the title ("Password Required - host.tld").
_PASSWORD_TITLE_RE = re.compile(
    r"<title[^>]*>[^<]*password[^<]*</title>", re.IGNORECASE
)
_FORM_RE = re.compile(r"<form\b([^>]*)>(.*?)</form>", re.IGNORECASE | re.DOTALL)
_INPUT_RE = re.compile(r"<input\b[^>]*>", re.IGNORECASE)
_ACTION_RE = re.compile(r"""\baction\s*=\s*["']([^"']*)["']""", re.IGNORECASE)
_NAME_RE = re.compile(r"""\bname\s*=\s*["']([^"']*)["']""", re.IGNORECASE)
_VALUE_RE = re.compile(r"""\bvalue\s*=\s*["']([^"']*)["']""", re.IGNORECASE)
_TYPE_RE = re.compile(r"""\btype\s*=\s*["']([^"']*)["']""", re.IGNORECASE)


def _attr(pattern: re.Pattern, text: str) -> str:
    match = pattern.search(text)
    return match.group(1) if match else ""


def is_password_protected(page: str) -> bool:
    """True when the page is a password gate rather than the folder listing."""
    if _PASSWORD_TITLE_RE.search(page):
        return True
    return bool(_password_form(page))


def _password_form(page: str) -> tuple[str, dict[str, str], str] | None:
    """Return ``(action, hidden_fields, password_field)`` for the first password form."""
    for attrs, body in _FORM_RE.findall(page):
        password_field = ""
        fields: dict[str, str] = {}
        for tag in _INPUT_RE.findall(body):
            name = _attr(_NAME_RE, tag)
            if not name:
                continue
            input_type = (_attr(_TYPE_RE, tag) or "text").lower()
            if input_type == "password":
                password_field = name
            elif input_type in ("hidden", "submit"):
                fields[name] = _attr(_VALUE_RE, tag)
        if password_field:
            return _attr(_ACTION_RE, attrs), fields, password_field
    return None


def _filester_payload(
    password: str, fields: dict[str, str], password_field: str
) -> dict[str, str] | None:
    """filester.me expects ``base64("password|epoch_ms|nonce")`` in the nonce field.

    That is the same encoding its own gallery-dl extractor submits; the site
    rejects a plain password (the POST answers 200 with an inline error instead
    of the 303 that grants the session cookie).
    """
    nonce = fields.get("nonce")
    if not nonce or password_field != "password":
        return None
    payload = f"{password}|{int(time.time() * 1000)}|{nonce}".encode()
    return {**fields, password_field: b64encode(payload).decode()}


# Encoders tried in order before the plain ``{password: value}`` submission.
_ENCODERS = (_filester_payload,)


def _openers(cookie_path: Path | None) -> tuple[urllib.request.OpenerDirector, object]:
    """Build an opener with a cookie jar seeded from an existing cookie file."""
    jar = http.cookiejar.MozillaCookieJar(str(cookie_path) if cookie_path else None)
    if cookie_path and cookie_path.is_file():
        try:
            jar.load(ignore_discard=True, ignore_expires=True)
        except (OSError, http.cookiejar.LoadError):
            pass
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    opener.addheaders = [("User-Agent", _USER_AGENT)]
    return opener, jar


def _get(opener: urllib.request.OpenerDirector, url: str) -> str:
    with opener.open(url, timeout=_TIMEOUT_S) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        return resp.read().decode(charset, "replace")


def unlock_page(
    url: str,
    password: str,
    cookie_path: Path,
    existing_cookies: str | None = None,
) -> bool:
    """Submit a page's password form and save the session cookies to ``cookie_path``.

    Returns True when ``cookie_path`` now holds cookies for an unlocked session,
    False when the page is not password protected, has no submit-able form, or
    the password was rejected. Never raises: a failure just means the downloader
    runs without the extra cookies.
    """
    if not password:
        return False
    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False

    seed = Path(existing_cookies) if existing_cookies else None
    cookie_path = Path(cookie_path)
    try:
        seed_path = seed if seed and seed.is_file() else cookie_path
        opener, jar = _openers(seed_path)
        page = _get(opener, url)
        if not is_password_protected(page):
            return False
        form = _password_form(page)
        if not form:
            logger.info(
                f"Password-protected page has no usable form: {parsed.hostname}"
            )
            return False
        action, fields, password_field = form
        target = urllib.parse.urljoin(url, action or url)
        target_parsed = urllib.parse.urlparse(target)
        # Never post the password to a different host than the page itself.
        if target_parsed.hostname != parsed.hostname:
            logger.warning(f"Refusing cross-host password form on {parsed.hostname}")
            return False

        candidates = [
            encoded
            for encoded in (
                encoder(password, fields, password_field) for encoder in _ENCODERS
            )
            if encoded
        ]
        candidates.append({**fields, password_field: password})

        for body in candidates:
            data = urllib.parse.urlencode(body).encode()
            request = urllib.request.Request(target, data=data, method="POST")
            try:
                with opener.open(request, timeout=_TIMEOUT_S):
                    pass
            except urllib.error.HTTPError as e:
                # A 4xx/5xx POST is a rejected password, not an unlock.
                if e.code >= 400:
                    continue
            except urllib.error.URLError as e:
                logger.info(f"Password form POST failed on {parsed.hostname}: {e}")
                return False

            if not is_password_protected(_get(opener, url)):
                cookie_path.parent.mkdir(parents=True, exist_ok=True)
                jar.save(str(cookie_path), ignore_discard=True, ignore_expires=True)
                logger.info(f"Unlocked password-protected page on {parsed.hostname}")
                return True

        logger.warning(f"Password rejected on {parsed.hostname}")
        return False
    except Exception as e:  # a locked page must never fail the whole job
        logger.warning(f"Could not unlock password-protected page: {e}")
        return False
