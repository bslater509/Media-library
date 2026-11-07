"""Path validation and formatting helpers.

Every path that arrives from a request is resolved and clamped to the library
root before it reaches the database or the filesystem (AGENTS.md Rule 2).
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from urllib.parse import quote

from fastapi import HTTPException

from media_library.config import get_settings


def library_root() -> str:
    return str(Path(get_settings().library_dir))


def resolve_library_path(raw: str | None) -> str:
    """Resolve ``raw`` inside the library root, or clamp/deny it.

    ``None``/empty resolves to the root. Paths outside the root raise 403;
    a path equal to the root's parent or ``/`` clamps to the root, as
    does, so a stale bookmark can never escape the library.
    """
    root = os.path.abspath(os.path.normpath(library_root()))
    if not raw or not raw.strip():
        return root
    candidate = os.path.abspath(os.path.normpath(raw.strip()))
    if candidate in (root, os.path.dirname(root), "/"):
        return root
    try:
        if os.path.commonpath([root, candidate]) != root:
            raise HTTPException(status_code=403, detail="Path is outside the library")
    except ValueError:
        raise HTTPException(status_code=403, detail="Invalid path") from None
    return candidate


def resolve_existing_file(raw: str) -> str:
    """Validate a media path and make sure it names a real file."""
    if not raw or not raw.strip():
        raise HTTPException(status_code=400, detail="A path is required")
    path = resolve_library_path(raw)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="File not found")
    return path


def is_zip_member(path: str) -> bool:
    """True when ``path`` addresses a member of a ZIP virtual folder."""
    parts = Path(path).parts
    return any(part.lower().endswith(".zip") for part in parts[:-1])


def relative_to_root(path: str) -> str:
    root = library_root()
    try:
        rel = os.path.relpath(path, root)
    except ValueError:
        return ""
    return "" if rel == "." else rel


def breadcrumbs(target: str) -> list[dict[str, object]]:
    """Build the root-to-target crumb list, ZIP-aware."""
    root = library_root()
    crumbs: list[dict[str, object]] = [
        {
            "name": os.path.basename(root.rstrip("/")) or "Library",
            "path": root,
            "is_zip": False,
        }
    ]
    rel = relative_to_root(target)
    if not rel:
        return crumbs
    accum = root
    for part in Path(rel).parts:
        accum = os.path.join(accum, part)
        is_zip = part.lower().endswith(".zip")
        crumbs.append(
            {
                "name": Path(part).stem if is_zip else part,
                "raw_name": part,
                "path": accum,
                "is_zip": is_zip,
            }
        )
    return crumbs


def stable_id(path: str) -> str:
    return hashlib.sha1(path.encode("utf-8", "surrogatepass")).hexdigest()[:16]


def quote_path(path: str) -> str:
    return quote(path, safe="")


def format_size(size: int | None) -> str:
    """Human-readable byte count."""
    if size is None:
        return "0 B"
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}".replace(".0 ", " ")
        value /= 1024
    return f"{size} B"
