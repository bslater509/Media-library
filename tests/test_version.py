"""The application version must have exactly one source of truth.

``media_library.__version__`` is it: the FastAPI app metadata, the backup
snapshot metadata, the ``/api/system/status`` payload and the served pages all
read it. These tests fail the moment a second hardcoded literal is introduced,
so a release bump cannot leave half the app reporting an older version.
"""

import json
import re
from pathlib import Path

from media_library import __version__

REPO_ROOT = Path(__file__).resolve().parents[1]

# Modules that report the version to the outside world must import it rather
# than restating it: a literal here is exactly the drift this guards against.
VERSION_REPORTERS = (
    "media_library/app_factory.py",
    "media_library/services/backup_service.py",
    "media_library/routers/system.py",
)

# Pages served through ``routers/views._serve_page`` (the version token is
# substituted for all of them, even where a template does not use it yet).
SERVED_PAGES = ("/", "/sorter", "/downloads", "/analytics", "/people", "/people/review")


def test_version_is_the_release_version():
    assert __version__ == "1.2.5"


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


def test_no_stale_version_literals_anywhere_in_the_package():
    """No shipped module may still claim the pre-release version (2.0.0)."""
    for path in (REPO_ROOT / "media_library").rglob("*.py"):
        assert "2.0.0" not in path.read_text(encoding="utf-8"), (
            f"{path.relative_to(REPO_ROOT)} still reports the old version"
        )


def test_fastapi_metadata_and_openapi_advertise_the_version(api_client):
    assert api_client.app.version == __version__
    assert api_client.get("/openapi.json").json()["info"]["version"] == __version__


def test_status_payload_reports_the_version(api_client):
    payload = api_client.get("/api/system/status").json()
    assert payload["version"] == __version__


def test_served_pages_resolve_the_version_token(api_client):
    for path in SERVED_PAGES:
        res = api_client.get(path)
        assert res.status_code == 200, path
        assert "{{VERSION}}" not in res.text, f"{path} leaked an unsubstituted token"
    # The Library page shows the version in its sidebar footer.
    assert f"v{__version__}" in api_client.get("/").text


def test_login_page_shows_the_version(api_client, monkeypatch):
    monkeypatch.setenv("APP_PASSWORD", "s3cret")
    res = api_client.get("/login")
    assert res.status_code == 200
    assert f"v{__version__}" in res.text
    assert "{{VERSION}}" not in res.text
