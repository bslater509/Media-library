"""Fixtures for the test suite.

Every test gets its own throwaway index with this package's own schema, pinned
through ``DB_PATH`` before the application settings are read, so no test can
reach the developer's real library or database. Auth and the owned background
runtime are off by default; tests that exercise either switch them on.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from media_library import config as v2_config
from media_library.schema import init_schema


def pytest_configure(config):
    """Create the parent of ``--basetemp`` before pytest does.

    ``pytest.ini`` pins the base temp directory to ``data/pytest_tmp``, and
    pytest creates it with a non-recursive ``mkdir``. ``data/`` is git-ignored,
    so on a fresh checkout (CI included) it does not exist yet.
    """
    basetemp = config.getoption("basetemp", None)
    if basetemp:
        Path(basetemp).resolve().parent.mkdir(parents=True, exist_ok=True)


@pytest.fixture(autouse=True)
def isolated_index(tmp_path, monkeypatch):
    """A fresh, schema-complete index and data directory for every test.

    The library and incoming roots are pinned too: the repo ``.env`` points
    them at a real remote mount, and a test that starts the app would otherwise
    read that mount (and its snapshot) instead of the fixture, making results
    depend on the developer's machine.
    """
    db_path = tmp_path / "isolated_index.db"
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    library_dir = tmp_path / "isolated_library"
    library_dir.mkdir()
    incoming_dir = tmp_path / "isolated_incoming"
    incoming_dir.mkdir()

    monkeypatch.setenv("DB_PATH", str(db_path))
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("LIBRARY_DIR", str(library_dir))
    monkeypatch.setenv("INCOMING_DIR", str(incoming_dir))
    monkeypatch.setenv("SOURCE_DIR", str(incoming_dir))
    # Never start the developer's owned workers (crawler, probes, snapshots).
    monkeypatch.setenv("OWN_WORKERS", "false")

    init_schema(db_path)
    v2_config.reload_settings()
    yield
    v2_config.reload_settings()


@pytest.fixture(autouse=True)
def disable_auth_by_default(monkeypatch):
    """Keep auth off regardless of the developer's real ``.env``.

    ``get_app_password`` reads the environment first and the settings snapshot
    loads the repo ``.env``, so without this an ``APP_PASSWORD`` there would gate
    every TestClient in the suite. Auth tests set the variable themselves.
    """
    monkeypatch.delenv("APP_PASSWORD", raising=False)
