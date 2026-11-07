"""The application version must have exactly one source of truth.

``media_library.__version__`` is it: the FastAPI metadata, the health and
info payloads and the backup snapshot metadata all read it. These tests fail the
moment a second hardcoded literal is introduced, so a release bump cannot leave
half the app reporting an older version.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

from media_library import __version__
from media_library.app import create_app
from media_library.routers import system as system_router

REPO_ROOT = Path(__file__).resolve().parents[1]

# Modules that report the version to the outside world must import it rather
# than restating it: a literal here is exactly the drift this guards against.
VERSION_REPORTERS = (
    "media_library/app.py",
    "media_library/backups.py",
    "media_library/routers/system.py",
)


def test_version_is_the_release_version():
    assert __version__ == "3.0.0"


def test_package_json_matches_python_version():
    package = json.loads((REPO_ROOT / "package.json").read_text(encoding="utf-8"))
    assert package["version"] == __version__


def test_readme_advertises_the_current_release():
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert f"Current release: v{__version__}" in readme, (
        "README.md must state the current release version"
    )


def test_changelog_has_an_entry_for_the_version():
    changelog = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert re.search(rf"^## \[{re.escape(__version__)}\]", changelog, re.MULTILINE), (
        "CHANGELOG.md has no section for the current version"
    )


def test_reporters_import_the_version_instead_of_hardcoding_it():
    for rel_path in VERSION_REPORTERS:
        text = (REPO_ROOT / rel_path).read_text(encoding="utf-8")
        assert "__version__" in text, f"{rel_path} does not import __version__"
        assert not re.search(r"[\"']\d+\.\d+\.\d+[\"']", text), (
            f"{rel_path} hardcodes a version literal"
        )


def test_fastapi_metadata_advertises_the_version():
    app = create_app()
    assert app.version == __version__
    assert app.openapi()["info"]["version"] == __version__


def test_health_and_info_payloads_report_the_version():
    from starlette.responses import Response

    health = asyncio.run(system_router.health(Response()))
    assert health["version"] == __version__
    info = asyncio.run(system_router.info())
    assert info["version"] == __version__
