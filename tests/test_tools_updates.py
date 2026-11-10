"""Downloader tool update checks and the in-place update job.

The module talks to PyPI and runs pip as subprocesses; every test keeps that
off the network by faking the version lookup or replacing the pip command
with a small inline script whose output mimics pip's.
"""

from __future__ import annotations

import json
import sys
import threading
import time

import pytest
from fastapi.testclient import TestClient

from media_library import tools
from media_library.app import create_app
from media_library.writes import WriteError


@pytest.fixture(autouse=True)
def reset_tools_state():
    """Start every test from the module's empty in-memory state."""
    tools._check.update({"checked_at": 0.0, "results": {}})
    tools._checking.clear()
    tools._job = None
    tools._process = None
    yield
    tools._job = None
    tools._process = None


@pytest.fixture
def client():
    from media_library import config as api_config

    api_config.reload_settings()
    return TestClient(create_app())


def wait_for(predicate, timeout: float = 10.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


class FakeResponse:
    """A ``urllib`` response returning one JSON payload."""

    def __init__(self, payload: dict):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


# ---------------------------------------------------------------------------
# Versions and comparisons
# ---------------------------------------------------------------------------


def test_installed_versions_read_metadata_and_tolerate_missing_tools(monkeypatch):
    def fake_version(dist):
        if dist == "yt-dlp":
            return "2026.8.19"
        raise tools.metadata.PackageNotFoundError

    monkeypatch.setattr(tools.metadata, "version", fake_version)
    assert tools.installed_versions() == {"ytdlp": "2026.8.19", "gallery_dl": None}


def test_version_key_orders_numerically():
    assert tools.version_key("2026.10.26") > tools.version_key("2026.8.19")
    assert tools.version_key("1.32.19") > tools.version_key("1.32.16")
    assert tools.version_key("1.32.19") == tools.version_key("1.32.19")
    assert tools.version_key("1.2.post1") > tools.version_key("1.2")


def test_update_available_needs_both_versions():
    assert tools.update_available("2026.8.19", "2026.10.26") is True
    assert tools.update_available("2026.10.26", "2026.10.26") is False
    assert tools.update_available(None, "2026.10.26") is False
    assert tools.update_available("2026.8.19", None) is False


def test_fetch_latest_version_parses_the_pypi_payload(monkeypatch):
    def fake_urlopen(url, timeout):
        assert "yt-dlp" in url
        return FakeResponse({"info": {"version": "2026.10.26"}})

    monkeypatch.setattr(tools.urllib.request, "urlopen", fake_urlopen)
    assert tools.fetch_latest_version("yt-dlp") == "2026.10.26"

    missing = FakeResponse({"info": {}})
    monkeypatch.setattr(tools.urllib.request, "urlopen", lambda url, timeout: missing)
    assert tools.fetch_latest_version("yt-dlp") is None


# ---------------------------------------------------------------------------
# The cached PyPI check
# ---------------------------------------------------------------------------


def test_check_for_updates_caches_and_refreshes(monkeypatch):
    looked_up: list[str] = []

    def fake_fetch(project, timeout=10.0):
        looked_up.append(project)
        return "9.9.9"

    monkeypatch.setattr(tools, "fetch_latest_version", fake_fetch)

    # A fresh cache is returned as-is, without a new lookup.
    tools._check["checked_at"] = time.time()
    status = tools.tools_status()
    assert status["checking"] is False
    assert looked_up == []

    # A stale cache (or refresh=true) fetches on a background thread.
    tools._check["checked_at"] = 0.0
    status = tools.tools_status()
    assert status["checking"] is True
    assert wait_for(lambda: tools._checking.is_set() is False and len(looked_up) == 2)
    status = tools.tools_status()
    assert status["checking"] is False
    assert [tool["latest"] for tool in status["tools"]] == ["9.9.9", "9.9.9"]


def test_check_in_flight_reports_checking_without_a_second_thread(monkeypatch):
    release = threading.Event()
    started = threading.Event()

    def slow_fetch(project, timeout=10.0):
        started.set()
        assert release.wait(timeout=5)
        return "1.0.0"

    monkeypatch.setattr(tools, "fetch_latest_version", slow_fetch)
    first = tools.tools_status()
    assert first["checking"] is True
    assert started.wait(timeout=5)
    second = tools.tools_status(force_check=True)
    assert second["checking"] is True
    release.set()
    assert wait_for(lambda: tools.tools_status()["checking"] is False)


def test_a_failed_pypi_lookup_is_reported_per_tool(monkeypatch):
    def boom(project, timeout=10.0):
        raise TimeoutError("timed out")

    monkeypatch.setattr(tools, "fetch_latest_version", boom)
    tools.tools_status()  # stale cache: starts the refresh thread
    assert wait_for(lambda: tools._checking.is_set() is False)
    status = tools.tools_status()
    assert all(tool["latest"] is None for tool in status["tools"])
    assert all(
        tool["check_error"] == "Could not reach pypi.org (TimeoutError)"
        for tool in status["tools"]
    )


def test_status_joins_versions_releases_and_the_last_job(monkeypatch):
    tools._check.update(
        {
            "checked_at": time.time(),
            "results": {
                "ytdlp": {"latest": "2026.10.26", "error": None},
                "gallery_dl": {"latest": "1.32.16", "error": None},
            },
        }
    )
    monkeypatch.setattr(
        tools,
        "installed_versions",
        lambda: {"ytdlp": "2026.8.19", "gallery_dl": "1.32.16"},
    )
    tools._job = {
        "status": "success",
        "tools": ["ytdlp"],
        "phase": "done",
        "progress": 100.0,
        "log": ["Successfully installed yt-dlp-2026.10.26"],
        "error": None,
        "versions": {"ytdlp": "2026.10.26"},
        "cancel_requested": False,
        "started_at": 1.0,
        "finished_at": 2.0,
    }
    status = tools.tools_status()
    by_key = {tool["key"]: tool for tool in status["tools"]}
    assert by_key["ytdlp"]["update_available"] is True
    assert by_key["gallery_dl"]["update_available"] is False
    assert status["update"]["log"] == ["Successfully installed yt-dlp-2026.10.26"]


# ---------------------------------------------------------------------------
# The update job
# ---------------------------------------------------------------------------

SUCCESS_SCRIPT = (
    "print('')\n"
    "print('Collecting yt-dlp')\n"
    "print('Downloading yt_dlp-2026.10.26-py3-none-any.whl (2.9 MB)')\n"
    "print('Installing collected packages: yt-dlp')\n"
    "print('Successfully installed yt-dlp-2026.10.26')\n"
)
FAIL_SCRIPT = (
    "import sys\n"
    "print('Collecting yt-dlp')\n"
    "print('ERROR: No matching distribution found for yt-dlp')\n"
    "sys.exit(1)\n"
)
SLOW_SCRIPT = "import time\nprint('Collecting yt-dlp', flush=True)\ntime.sleep(60)\n"


def fake_pip(monkeypatch, script: str) -> None:
    monkeypatch.setattr(
        tools, "_update_command", lambda keys: [sys.executable, "-c", script]
    )
    monkeypatch.setattr(
        tools,
        "installed_versions",
        lambda: {"ytdlp": "2026.10.26", "gallery_dl": "1.32.16"},
    )


def test_the_update_command_targets_pip_and_the_chosen_tools():
    command = tools._update_command(["ytdlp", "gallery_dl"])
    assert command[:3] == [sys.executable, "-m", "pip"]
    assert "install" in command and "--upgrade" in command
    assert command[-2:] == ["yt-dlp", "gallery-dl"]


def test_the_log_is_trimmed_to_its_limit(monkeypatch):
    monkeypatch.setattr(tools, "LOG_LIMIT", 5)
    fake_pip(monkeypatch, "print('Collecting yt-dlp')\n" * 10)
    tools.start_update(["ytdlp"])
    assert wait_for(lambda: tools._job["status"] != "running")
    assert len(tools._job["log"]) == 5
    assert tools._job["log"][-1] == "Collecting yt-dlp"


def test_successful_update_streams_progress_and_rereads_versions(monkeypatch):
    fake_pip(monkeypatch, SUCCESS_SCRIPT)
    job = tools.start_update(["ytdlp"])
    assert job["status"] == "running"
    assert wait_for(lambda: tools._job["status"] != "running")
    job = tools._public_job()
    assert job["status"] == "success"
    assert job["phase"] == "done"
    assert job["progress"] == 100.0
    assert job["versions"] == {"ytdlp": "2026.10.26"}
    assert job["log"][-1] == "Successfully installed yt-dlp-2026.10.26"


def test_progress_parsing_moves_through_the_phases():
    phase, progress, downloaded = "starting", 0.0, 0
    for line, want_phase, want_progress, want_downloaded in [
        ("Collecting yt-dlp", "resolving", 5.0, 0),
        (
            "Downloading yt_dlp-2026.10.26-py3-none-any.whl (2.9 MB)",
            "downloading",
            70.0,
            1,
        ),
        ("Installing collected packages: yt-dlp", "installing", 75.0, 1),
        ("Successfully installed yt-dlp-2026.10.26", "verifying", 90.0, 1),
        ("Unrelated notice", "verifying", 90.0, 1),
    ]:
        phase, progress, downloaded = tools._progress_for(
            line, phase, progress, downloaded, 1
        )
        assert phase == want_phase
        assert progress == want_progress
        assert downloaded == want_downloaded


def test_a_failed_pip_run_surfaces_its_error(monkeypatch):
    fake_pip(monkeypatch, FAIL_SCRIPT)
    tools.start_update(["ytdlp"])
    assert wait_for(lambda: tools._job["status"] != "running")
    job = tools._public_job()
    assert job["status"] == "failed"
    assert job["error"] == "ERROR: No matching distribution found for yt-dlp"
    assert job["finished_at"] is not None


def test_a_silent_pip_failure_reports_the_exit_code(monkeypatch):
    fake_pip(monkeypatch, "import sys\nsys.exit(3)\n")
    tools.start_update(["ytdlp"])
    assert wait_for(lambda: tools._job["status"] != "running")
    assert tools._job["error"] == "pip exited with code 3"


def test_an_unstartable_update_fails_cleanly(monkeypatch):
    monkeypatch.setattr(
        tools, "_update_command", lambda keys: ["/no/such/binary-for-tests"]
    )
    tools.start_update(["ytdlp"])
    assert wait_for(lambda: tools._job["status"] != "running")
    assert tools._job["status"] == "failed"
    assert tools._job["error"] == "Could not start pip"


def test_a_second_update_is_refused_while_one_runs(monkeypatch):
    fake_pip(monkeypatch, SLOW_SCRIPT)
    tools.start_update(["ytdlp"])
    assert wait_for(lambda: tools._job["phase"] == "resolving")
    with pytest.raises(WriteError) as excinfo:
        tools.start_update(["gallery_dl"])
    assert excinfo.value.status == 409
    assert tools.cancel_update()["cancelled"] is True
    assert wait_for(lambda: tools._job["status"] == "cancelled")


def test_cancel_terminates_the_running_update(monkeypatch):
    fake_pip(monkeypatch, SLOW_SCRIPT)
    tools.start_update(["ytdlp"])
    assert wait_for(lambda: tools._job["phase"] == "resolving")
    assert tools.cancel_update() == {"cancelled": True}
    assert wait_for(lambda: tools._job["status"] == "cancelled")
    assert tools._job["error"] is None


def test_cancelling_without_a_running_update_is_a_404():
    with pytest.raises(WriteError) as excinfo:
        tools.cancel_update()
    assert excinfo.value.status == 404


def test_kill_group_falls_back_to_terminate(monkeypatch):
    terminated: list[object] = []

    class FakeProcess:
        pid = 1234

        def terminate(self):
            terminated.append(self.pid)

    def refused_killpg(pid, sig):
        raise ProcessLookupError

    monkeypatch.setattr(tools.os, "killpg", refused_killpg)
    tools._kill_group(FakeProcess())
    assert terminated == [1234]


def test_an_update_past_its_deadline_times_out(monkeypatch):
    fake_pip(monkeypatch, SLOW_SCRIPT)
    monkeypatch.setattr(tools, "UPDATE_TIMEOUT_SECONDS", 0.3)
    tools.start_update(["ytdlp"])
    assert wait_for(lambda: tools._job["status"] != "running", timeout=15)
    assert tools._job["status"] == "failed"
    assert "timed out" in tools._job["error"]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


def test_tools_status_route_reports_versions_and_releases(client, monkeypatch):
    tools._check.update(
        {
            "checked_at": time.time(),
            "results": {
                "ytdlp": {"latest": "2026.10.26", "error": None},
                "gallery_dl": {"latest": "1.32.19", "error": None},
            },
        }
    )
    # Pinned so the assertion does not depend on the venv the suite runs in.
    monkeypatch.setattr(
        tools,
        "installed_versions",
        lambda: {"ytdlp": "2026.8.19", "gallery_dl": "1.32.16"},
    )
    body = client.get("/api/tools/status").json()
    assert [tool["key"] for tool in body["tools"]] == ["ytdlp", "gallery_dl"]
    ytdlp = body["tools"][0]
    assert ytdlp["version"]
    assert ytdlp["update_available"] is True
    assert body["update"] is None


def test_update_route_rejects_unknown_tools_and_read_only_mode(client, monkeypatch):
    assert client.post("/api/tools/update", json={"tools": ["pipx"]}).status_code == 422

    from media_library.routers import tools as tools_router

    monkeypatch.setattr(tools_router, "writes_available", lambda: False)
    response = client.post("/api/tools/update", json={"tools": ["ytdlp"]})
    assert response.status_code == 503
    assert "read-only" in response.json()["detail"]


def test_update_route_starts_and_translates_conflicts(client, monkeypatch):
    from media_library.routers import tools as tools_router

    seen: list[list[str]] = []

    def fake_start(keys):
        seen.append(keys)
        return {
            "status": "running",
            "tools": list(keys),
            "phase": "starting",
            "progress": 0.0,
            "log": [],
            "error": None,
            "versions": {"ytdlp": None},
            "cancel_requested": False,
            "started_at": 1.0,
            "finished_at": None,
        }

    monkeypatch.setattr(tools_router.tools, "start_update", fake_start)
    response = client.post("/api/tools/update", json={"tools": ["ytdlp"]})
    assert response.status_code == 200
    assert response.json()["status"] == "running"
    assert seen == [["ytdlp"]]

    def conflict(keys):
        raise WriteError("An update is already running", 409)

    monkeypatch.setattr(tools_router.tools, "start_update", conflict)
    again = client.post("/api/tools/update", json={"tools": ["ytdlp"]})
    assert again.status_code == 409
    assert again.json()["detail"] == "An update is already running"

    monkeypatch.setattr(
        tools_router.tools, "cancel_update", lambda: {"cancelled": True}
    )
    assert client.post("/api/tools/update/cancel").json() == {"cancelled": True}


def test_cancel_route_translates_the_404(client):
    response = client.post("/api/tools/update/cancel")
    assert response.status_code == 404
    assert response.json()["detail"] == "No update is running"
