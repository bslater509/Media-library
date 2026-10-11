"""Tiny HTML-template helper: cached template loads + escaped token substitution.

Deliberately no Jinja2 (per AGENTS.md): pages are static HTML with ``{{TOKEN}}``
placeholders, assembled with a single escaping helper so double-escaping and
missing-token bugs stay impossible. Now supports simple includes like ``{{include 'partial.html'}}``.
"""

import html
import re
from functools import cache

from media_library.core.config import TEMPLATES_DIR


class SafeHTML(str):
    """Marker for already-safe HTML: :func:`render` substitutes it verbatim."""


def _resolve_includes(template_str: str, current_dir) -> str:
    def repl(m):
        inc_file = m.group(1).strip("'\"")
        inc_path = current_dir / inc_file
        if inc_path.exists():
            return _resolve_includes(inc_path.read_text(encoding="utf-8"), inc_path.parent)
        return ""
    
    # Matches {{include 'file.html'}} or {{ include "file.html" }}
    pattern = r"\{\{\s*include\s+(['\"].*?['\"])\s*\}\}"
    return re.sub(pattern, repl, template_str)


@cache
def load_template(name: str) -> str | None:
    """Read a template file once and cache it for the process lifetime.

    Frontend template edits therefore take effect on restart, matching the
    "backend changes need a restart" model documented in AGENTS.md §14.
    """
    path = TEMPLATES_DIR / name
    if not path.exists():
        return None
    raw = path.read_text(encoding="utf-8")
    return _resolve_includes(raw, path.parent)


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
