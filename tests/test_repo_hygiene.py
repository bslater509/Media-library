"""Guards that nothing personal or secret reaches the public repository.

Two separate concerns, both checked against the *tracked* file list (so the
gitignored local state — ``.env``, ``config.json``, ``data/``, ``*.db`` — is
correctly out of scope):

1. No tracked file may contain this machine's or any user's real home path, a
   real email address, or a credential-shaped string. The version of this file
   you are reading is what keeps a one-off audit from decaying: the release
   checklist used to be run by hand.
2. Nothing that holds local state may become tracked, and the ignore rules that
   keep it out must stay in place — so a stray ``git add -f`` or a trimmed
   ``.gitignore`` fails here instead of publishing a real index or password.

Git *history* is deliberately not walked here (it is minutes of I/O and the
patterns are already covered by ``git log -p --all | grep`` in the release
checklist); these tests cover the committed content that ships.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------------------
# 1. Forbidden content
# ---------------------------------------------------------------------------

FORBIDDEN_PATTERNS = (
    # Concatenated so this file does not contain the literal it forbids —
    # otherwise the scanner matches its own source and can never pass.
    (re.compile("/" + "root/"), "this machine's root home directory"),
    (re.compile(r"/home/(?!<)[A-Za-z0-9_.-]{2,}"), "a real user home directory"),
    (re.compile(r"/Users/[A-Za-z][A-Za-z0-9_.-]*"), "a macOS user home directory"),
    (re.compile(r"C:\\\\Users\\\\", re.IGNORECASE), "a Windows user profile path"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"), "a GitHub token"),
    (re.compile(r"github_pat_" + r"[A-Za-z0-9_]{20,}"), "a GitHub fine-grained token"),
    (re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"), "an API key"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "an AWS access key id"),
    (re.compile("BEGIN " + r"[A-Z ]*PRIVATE KEY"), "a private key"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"), "a Slack token"),
)

# The one email the project is allowed to carry, plus the reserved example
# domains the test suite uses as deliberate dummies.
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
ALLOWED_EMAIL_DOMAINS = (
    "medialibrary.local",
    "example.com",
    "example.org",
    "example.net",
    "example",
)

# Local state that must never be tracked (see .gitignore).
IGNORED_LOCAL_PATHS = (
    ".env",
    "config.json",
    "media-library.service",
    "library.db",
    "backup_index.db",
    "backup_index.db-wal",
    "data/library.db",
    "data/thumbnails/thumb_deadbeef.jpg",
    "plans/some-ephemeral-plan.md",
)


def _git(*args: str) -> str:
    """Run git in the repo; skip the test if git is unavailable or unhappy."""
    if shutil.which("git") is None:
        pytest.skip("git is not available")
    result = subprocess.run(
        ["git", "-C", str(REPO_ROOT), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.skip(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def _git_ok(*args: str) -> bool:
    """Return True when git exits 0 (used for ``check-ignore``)."""
    result = subprocess.run(
        ["git", "-C", str(REPO_ROOT), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


@pytest.fixture(scope="module")
def tracked_files() -> list[str]:
    return [line for line in _git("ls-files").splitlines() if line.strip()]


def test_no_forbidden_patterns_in_tracked_files(tracked_files):
    offenders: list[str] = []
    for rel_path in tracked_files:
        path = REPO_ROOT / rel_path
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:  # pragma: no cover - unreadable tracked file
            continue
        for pattern, description in FORBIDDEN_PATTERNS:
            match = pattern.search(text)
            if match:
                offenders.append(f"{rel_path}: {description} ({match.group(0)!r})")
    assert not offenders, (
        "Personal or secret data found in tracked files:\n" + "\n".join(offenders)
    )


def test_only_generic_email_addresses_in_tracked_files(tracked_files):
    offenders: list[str] = []
    for rel_path in tracked_files:
        path = REPO_ROOT / rel_path
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:  # pragma: no cover - unreadable tracked file
            continue
        for email in EMAIL_RE.findall(text):
            if not email.lower().endswith(ALLOWED_EMAIL_DOMAINS):
                offenders.append(f"{rel_path}: {email}")
    assert not offenders, (
        "Non-generic email addresses found in tracked files:\n"
        + "\n".join(sorted(set(offenders)))
    )


def test_commit_identity_is_generic():
    """Authorship metadata must stay generic in the working copy's config."""
    if shutil.which("git") is None:
        pytest.skip("git is not available")
    for key, expected in (
        ("user.name", "Media Library"),
        ("user.email", "dev@medialibrary.local"),
    ):
        result = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "config", "--get", key],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0 or not result.stdout.strip():
            # No local identity configured: authorship comes from the global
            # config, which is not this repository's business to police.
            continue
        assert result.stdout.strip() == expected, (
            f"git {key} is not the generic project identity"
        )


# ---------------------------------------------------------------------------
# 2. Local state stays out of the repository
# ---------------------------------------------------------------------------


def test_local_state_is_not_tracked(tracked_files):
    tracked = set(tracked_files)
    offenders = [p for p in IGNORED_LOCAL_PATHS if p in tracked]
    assert not offenders, f"Local state is tracked: {offenders}"
    offenders = [p for p in tracked if p.startswith(("data/", "plans/"))]
    assert not offenders, f"Runtime directories are tracked: {offenders[:5]}"
    offenders = [p for p in tracked if Path(p).suffix in (".db", ".db-wal", ".db-shm")]
    assert not offenders, f"Database files are tracked: {offenders}"


@pytest.mark.parametrize("rel_path", IGNORED_LOCAL_PATHS)
def test_ignore_rules_still_cover_local_state(rel_path):
    assert _git_ok("check-ignore", "-q", rel_path), (
        f"{rel_path} is no longer gitignored — it could be committed by accident"
    )
