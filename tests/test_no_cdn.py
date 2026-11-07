"""The client must not fetch anything from a CDN at runtime.

Fonts, icons and the lightbox are bundled (``web/vite.config.ts`` inlines
``lucide-react`` and the ``@fontsource`` faces into ``static/dist``). A remote
``<script>``/``<link>``/font URL would make the app depend on a third party at
runtime, leak a request per visit and break an offline install — the previous
app shipped a Tailwind Play CDN, and this guard keeps that class of mistake out.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DIST = REPO_ROOT / "static" / "dist"

REMOTE_URL = re.compile(r"""(?:src|href)\s*=\s*["'](?:https?:)?//""", re.IGNORECASE)
KNOWN_CDNS = (
    "cdn.tailwindcss.com",
    "cdn.jsdelivr.net",
    "unpkg.com",
    "cdnjs.cloudflare.com",
    "fonts.googleapis.com",
    "fonts.gstatic.com",
)


def _shells() -> list[Path]:
    return sorted(DIST.rglob("index.html"))


def test_a_built_client_shell_exists():
    shells = _shells()
    assert shells, "no built client shell under static/dist — run the frontend build"
    for shell in shells:
        assert "static/dist/" in shell.read_text(encoding="utf-8"), shell


def test_shell_loads_only_same_origin_assets():
    for shell in _shells():
        html = shell.read_text(encoding="utf-8")
        assert not REMOTE_URL.search(html), f"{shell} loads a remote asset"
        for cdn in KNOWN_CDNS:
            assert cdn not in html, f"{shell} references {cdn}"


def test_no_known_cdn_in_the_bundled_client():
    """Bundled JS may carry documentation links, but not a runtime CDN host."""
    for path in DIST.rglob("assets/*.js"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        for cdn in KNOWN_CDNS:
            assert cdn not in text, f"{path.name} references {cdn}"


def test_fonts_ship_with_the_bundle():
    faces = list((DIST).rglob("assets/*.woff2"))
    assert faces, "the bundled client carries no local font files"
    for shell in _shells():
        assert "fonts.googleapis.com" not in shell.read_text(encoding="utf-8")
