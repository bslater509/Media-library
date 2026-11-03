"""Tailwind is compiled at build time, never in the browser.

The pages used to load `https://cdn.tailwindcss.com`, a runtime JIT compiler
that re-scans the document on every DOM mutation — the status poll alone was
enough to make it recompile several times a minute. It was replaced by a
`static/dist/tailwind.css` built by the Tailwind CLI, with the theme palettes
reaching the compiled utilities through CSS variables so a theme switch is just
a variable swap.
"""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
TAILWIND_BUNDLE = REPO_ROOT / "static" / "dist" / "tailwind.css"
PAGES = ("/", "/sorter", "/downloads", "/analytics")


@pytest.mark.parametrize("path", PAGES)
def test_page_links_the_compiled_tailwind_stylesheet(api_client, path):
    res = api_client.get(path)
    assert res.status_code == 200
    assert re.search(r'href="/static/dist/tailwind\.css\?v=\d+"', res.text)


@pytest.mark.parametrize("path", PAGES)
def test_page_never_loads_the_tailwind_cdn(api_client, path):
    res = api_client.get(path)
    assert "cdn.tailwindcss.com" not in res.text
    # The runtime config hook only made sense for the CDN's JIT.
    assert "window.tailwind" not in res.text


def test_theme_engine_does_not_generate_runtime_tailwind_overrides(api_client):
    res = api_client.get("/")
    assert res.status_code == 200
    # The old `!important` override sheet regenerated on every theme switch.
    assert "dynamicThemeOverrides" not in res.text
    # The compiled utilities must resolve through the variables the theme
    # engine sets, or a theme switch would stop recolouring anything.
    assert "--surface-950" in res.text or "applyCssVars" in res.text


def test_compiled_tailwind_bundle_exists_and_is_variable_themed():
    assert TAILWIND_BUNDLE.is_file(), (
        "static/dist/tailwind.css is missing — run `npm run build` "
        "(it runs the Tailwind CLI after Vite)"
    )
    css = TAILWIND_BUNDLE.read_text(encoding="utf-8")
    assert len(css) > 20_000, "the Tailwind bundle looks truncated"
    # Utilities must reference the CSS variables rather than a baked palette.
    assert "rgb(var(--surface-950)" in css
    assert "rgb(var(--accent-600)" in css
    # And the accent namespace is shared by the names the templates use.
    for name in ("emerald", "teal", "slate"):
        assert f".bg-{name}-" in css or f".text-{name}-" in css
