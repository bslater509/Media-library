"""Best-effort build identity (git commit) for the liveness probe.

The running process could not otherwise say *which* revision it is. The semver
in :mod:`media_library` only moves at releases, so it cannot tell a freshly
committed server from a stale one; a short commit SHA can.

Resolution order (first hit wins):

1. ``GIT_COMMIT`` / ``BUILD_COMMIT`` environment variables — what a container or
   packaged deploy should set, since it ships without a ``.git`` directory.
2. ``.git/HEAD`` read directly, following a linked worktree's ``gitdir:`` pointer
   and its ``commondir`` ``packed-refs``. Being file reads, this needs no git
   binary and never blocks the event loop, and the result is cached for the
   process lifetime.

Nothing here raises: an unpacked tarball, a stripped ``.git`` or an unreadable
ref yields ``None`` and the probe simply omits the field. Only a 40-hex object
id is ever returned, so an odd ``HEAD`` value cannot leak into a public,
credential-free response.
"""

import os
import re
from functools import lru_cache
from pathlib import Path

from media_library.core.config import BASE_DIR

_COMMIT_RE = re.compile(r"[0-9a-fA-F]{40}")
_HEAD_REF_PREFIX = "ref:"
_GITDIR_PREFIX = "gitdir:"
# Enough of the SHA to match against `git log` output, short of the full id.
_SHORT_LEN = 12


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="ignore").strip()
    except OSError:
        return None


def _resolve_git_dir(repo_root: Path) -> Path | None:
    """Return the real git directory, following a ``.git`` file indirection."""
    dot_git = repo_root / ".git"
    if dot_git.is_dir():
        return dot_git
    if dot_git.is_file():
        text = _read_text(dot_git) or ""
        if text.startswith(_GITDIR_PREFIX):
            target = Path(text[len(_GITDIR_PREFIX) :].strip())
            if not target.is_absolute():
                target = repo_root / target
            if target.is_dir():
                return target.resolve()
    return None


def _git_dirs(git_dir: Path) -> list[Path]:
    """The worktree's own git dir, plus its common dir when linked."""
    dirs = [git_dir]
    common = _read_text(git_dir / "commondir")
    if common:
        common_dir = Path(common)
        if not common_dir.is_absolute():
            common_dir = git_dir / common_dir
        if common_dir.is_dir() and common_dir.resolve() != git_dir:
            dirs.append(common_dir.resolve())
    return dirs


def _read_packed_ref(dirs: list[Path], ref: str) -> str | None:
    for directory in dirs:
        text = _read_text(directory / "packed-refs")
        if not text:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith(("#", "^")):
                continue
            parts = line.split(None, 1)
            if len(parts) == 2 and parts[1] == ref and _COMMIT_RE.fullmatch(parts[0]):
                return parts[0]
    return None


def _resolve_head(git_dir: Path) -> str | None:
    head = _read_text(git_dir / "HEAD")
    if not head:
        return None
    if not head.startswith(_HEAD_REF_PREFIX):
        return head  # detached HEAD: the value is the object id itself
    ref = head[len(_HEAD_REF_PREFIX) :].strip()
    dirs = _git_dirs(git_dir)
    for directory in dirs:
        value = _read_text(directory / ref)
        if value:
            return value
    return _read_packed_ref(dirs, ref)


def resolve_commit(repo_root: Path | None = None) -> str | None:
    """Return the short commit SHA of the checkout, or ``None`` if unknown."""
    for key in ("GIT_COMMIT", "BUILD_COMMIT"):
        value = (os.environ.get(key) or "").strip()
        if value:
            return value[:_SHORT_LEN]
    git_dir = _resolve_git_dir(repo_root or BASE_DIR)
    if git_dir is None:
        return None
    head = _resolve_head(git_dir)
    if head and _COMMIT_RE.fullmatch(head):
        return head[:_SHORT_LEN]
    return None


@lru_cache(maxsize=1)
def get_commit() -> str | None:
    """Cached :func:`resolve_commit` for the current process."""
    return resolve_commit()
