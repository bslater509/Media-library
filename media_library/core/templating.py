"""Tiny HTML-template helper: cached template loads + escaped token substitution.

Deliberately no Jinja2 (per AGENTS.md): pages are static HTML with ``{{TOKEN}}``
placeholders, assembled with a single escaping helper so double-escaping and
missing-token bugs stay impossible.
"""

import html
from functools import cache

from media_library.core.config import TEMPLATES_DIR


class SafeHTML(str):
    """Marker for already-safe HTML: :func:`render` substitutes it verbatim."""


@cache
def load_template(name: str) -> str | None:
    """Read a template file once and cache it for the process lifetime.

    Frontend template edits therefore take effect on restart, matching the
    "backend changes need a restart" model documented in AGENTS.md §14.
    """
    path = TEMPLATES_DIR / name
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def render(template: str, /, **tokens: str) -> str:
    """Substitute ``{{KEY}}`` placeholders with HTML-escaped token values.

    Values wrapped in :class:`SafeHTML` are inserted verbatim (used for
    pre-built markup such as the login error paragraph).
    """
    for key, value in tokens.items():
        if isinstance(value, SafeHTML):
            replacement = str(value)
        else:
            replacement = html.escape(value, quote=True)
        template = template.replace("{{" + key + "}}", replacement)
    return template
