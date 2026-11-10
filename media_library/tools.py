"""Downloader tool versions: PyPI update checks and in-place updates.

yt-dlp and gallery-dl run as isolated subprocesses and are never imported
in-process, so their installed versions are read from distribution metadata
(:mod:`importlib.metadata`) instead. Newer releases are looked up on PyPI at
most once a day in a daemon thread (never in a request path) and an update
runs ``python -m pip install --upgrade`` as one subprocess whose output is
parsed into a coarse progress state. Like the x265 queue this is in-memory:
one update at a time, a restart stops it and forgets the last result.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.request
from importlib import metadata
from typing import Any

from media_library.writes import WriteError

_log = logging.getLogger(__name__)

# Key -> distribution / PyPI project name. Keys match the tools reported by
# ``downloads.tools_available()`` so the client can join the two payloads.
TOOL_NAMES: dict[str, str] = {
    "ytdlp": "yt-dlp",
    "gallery_dl": "gallery-dl",
}

CHECK_INTERVAL_SECONDS = 24 * 3600
UPDATE_TIMEOUT_SECONDS = 30 * 60
LOG_LIMIT = 400

_check_lock = threading.Lock()
_check: dict[str, Any] = {"checked_at": 0.0, "results": {}}
_checking = threading.Event()

_job_lock = threading.Lock()
_job: dict[str, Any] | None = None
_process: subprocess.Popen[bytes] | None = None


def installed_versions() -> dict[str, str | None]:
    """Installed version per tool from distribution metadata (no imports)."""
    versions: dict[str, str | None] = {}
    for key, dist in TOOL_NAMES.items():
        try:
            versions[key] = metadata.version(dist)
        except metadata.PackageNotFoundError:
            versions[key] = None
    return versions


def version_key(version: str) -> tuple[tuple[int, int | str], ...]:
    """Numeric, dot-separated ordering key; non-numeric parts sort last."""
    parts: list[tuple[int, int | str]] = []
    for chunk in str(version).strip().split("."):
        try:
            parts.append((0, int(chunk)))
        except ValueError:
            digits = "".join(ch for ch in chunk if ch.isdigit())
            parts.append((1, digits))
    return tuple(parts)


def update_available(installed: str | None, latest: str | None) -> bool:
    """True when ``latest`` is a strictly newer release than ``installed``."""
    if not installed or not latest:
        return False
    return version_key(latest) > version_key(installed)


def fetch_latest_version(project: str, timeout: float = 10.0) -> str | None:
    """Latest release of a PyPI project, or None when the answer is missing."""
    url = f"https://pypi.org/pypi/{project}/json"
    with urllib.request.urlopen(url, timeout=timeout) as response:
        data = json.loads(response.read().decode("utf-8"))
    version = data.get("info", {}).get("version")
    return str(version) if version else None


def check_for_updates(force: bool = False) -> dict[str, Any]:
    """Return the cached PyPI check, refreshing it on a thread when stale."""
    with _check_lock:
        age = time.time() - float(_check["checked_at"])
        if not force and age < CHECK_INTERVAL_SECONDS:
            return dict(_check, checking=False)
        if _checking.is_set():
            return dict(_check, checking=True)
        _checking.set()
    threading.Thread(
        target=_refresh_latest, name="tool-update-check", daemon=True
    ).start()
    return dict(_check, checking=True)


def _refresh_latest() -> None:
    """Fetch every tool's latest release and cache it (runs off the loop)."""
    results: dict[str, dict[str, str | None]] = {}
    for key, project in TOOL_NAMES.items():
        try:
            results[key] = {
                "latest": fetch_latest_version(project),
                "error": None,
            }
        except Exception as exc:
            _log.warning("PyPI lookup for %s failed: %s", project, exc)
            # Raw exception text can carry proxy and DNS internals; report a
            # stable, readable reason instead.
            results[key] = {
                "latest": None,
                "error": f"Could not reach pypi.org ({type(exc).__name__})",
            }
    with _check_lock:
        _check["checked_at"] = time.time()
        _check["results"] = results
        _checking.clear()


def _public_job() -> dict[str, Any] | None:
    """A snapshot of the update job safe to serialize while it advances."""
    with _job_lock:
        if _job is None:
            return None
        return {
            key: (list(value) if key == "log" else value) for key, value in _job.items()
        }


def tools_status(force_check: bool = False) -> dict[str, Any]:
    """Versions, latest releases and the last update job for every tool."""
    check = check_for_updates(force_check)
    versions = installed_versions()
    tools = []
    for key, project in TOOL_NAMES.items():
        result = check["results"].get(key, {})
        latest = result.get("latest")
        tools.append(
            {
                "key": key,
                "name": project,
                "version": versions[key],
                "latest": latest,
                "update_available": update_available(versions[key], latest),
                "check_error": result.get("error"),
            }
        )
    return {
        "tools": tools,
        "checking": bool(check["checking"]),
        "update": _public_job(),
    }


def start_update(keys: list[str]) -> dict[str, Any]:
    """Start one ``pip install --upgrade`` for the selected tools."""
    global _job
    with _job_lock:
        if _job is not None and _job["status"] == "running":
            raise WriteError("An update is already running", 409)
        job = {
            "status": "running",
            "tools": list(keys),
            "phase": "starting",
            "progress": 0.0,
            "log": [],
            "error": None,
            "versions": {key: None for key in keys},
            "cancel_requested": False,
            "started_at": time.time(),
            "finished_at": None,
        }
        _job = job
    threading.Thread(
        target=_run_update, args=(list(keys), job), name="tool-update", daemon=True
    ).start()
    return _public_job() or {}


def cancel_update() -> dict[str, Any]:
    """Terminate the running update's process group."""
    with _job_lock:
        job = _job
        if job is None or job["status"] != "running":
            raise WriteError("No update is running", 404)
        job["cancel_requested"] = True
        process = _process
    if process is not None and process.poll() is None:
        _kill_group(process)
    return {"cancelled": True}


def _kill_group(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        process.terminate()


def _update_command(keys: list[str]) -> list[str]:
    """The pip command an update runs (monkeypatched by tests)."""
    return [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--upgrade",
        "--disable-pip-version-check",
        "--progress-bar",
        "off",
        *[TOOL_NAMES[key] for key in keys],
    ]


def _progress_for(
    line: str, phase: str, progress: float, downloaded: int, expected: int
) -> tuple[str, float, int]:
    """Map one pip output line to (phase, progress, downloaded-wheels)."""
    if line.startswith("Collecting "):
        return ("resolving", max(progress, 5.0), downloaded)
    if line.startswith("Downloading "):
        downloaded += 1
        share = min(downloaded / max(expected, 1), 1.0)
        return ("downloading", 10.0 + 60.0 * share, downloaded)
    if line.startswith("Installing collected packages"):
        return ("installing", max(progress, 75.0), downloaded)
    if line.startswith("Successfully installed"):
        return ("verifying", max(progress, 90.0), downloaded)
    return (phase, progress, downloaded)


def _error_from_log(log: list[str], code: int) -> str:
    """The last pip error line, or the exit code when pip was silent."""
    for line in reversed(log):
        if line.startswith(("ERROR", "error")):
            return line[:300]
    return f"pip exited with code {code}"


def _run_update(keys: list[str], job: dict[str, Any]) -> None:
    """Run the pip upgrade, streaming its output into the job state."""
    global _process
    expected = len(keys)
    downloaded = 0
    timed_out = threading.Event()
    try:
        process = subprocess.Popen(
            _update_command(keys),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            bufsize=1,
            start_new_session=True,
        )
    except OSError as exc:
        _log.warning("Could not start the tool update: %s", exc)
        with _job_lock:
            job["status"] = "failed"
            job["phase"] = "failed"
            job["error"] = "Could not start pip"
            job["finished_at"] = time.time()
        return
    with _job_lock:
        _process = process
        job["phase"] = "resolving"
        job["progress"] = 5.0

    def watchdog() -> None:
        timed_out.set()
        _kill_group(process)

    timer = threading.Timer(UPDATE_TIMEOUT_SECONDS, watchdog)
    timer.daemon = True
    timer.start()
    try:
        assert process.stdout is not None
        for raw in process.stdout:
            line = raw.strip()
            if not line:
                continue
            job["phase"], job["progress"], downloaded = _progress_for(
                line, job["phase"], job["progress"], downloaded, expected
            )
            with _job_lock:
                job["log"].append(line)
                if len(job["log"]) > LOG_LIMIT:
                    del job["log"][: len(job["log"]) - LOG_LIMIT]
        code = process.wait()
        with _job_lock:
            job["finished_at"] = time.time()
            if timed_out.is_set():
                job["status"] = "failed"
                job["phase"] = "failed"
                job["error"] = (
                    f"The update timed out after {UPDATE_TIMEOUT_SECONDS // 60} minutes"
                )
            elif job["cancel_requested"]:
                job["status"] = "cancelled"
                job["phase"] = "cancelled"
            elif code == 0:
                job["status"] = "success"
                job["phase"] = "done"
                job["progress"] = 100.0
                fresh = installed_versions()
                job["versions"] = {key: fresh.get(key) for key in job["tools"]}
            else:
                job["status"] = "failed"
                job["phase"] = "failed"
                job["error"] = _error_from_log(job["log"], code)
    finally:
        timer.cancel()
        with _job_lock:
            _process = None
