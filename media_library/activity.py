"""Local activity telemetry and cooperative, process-wide background scheduling.

No filesystem or database access: safe for frequent header polling. Priority is
deliberately session-only and never interrupts an operation already in progress.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from functools import wraps
from typing import Any

_lock = threading.RLock()
_priority = False
_waiting: dict[str, int] = {}
_local = threading.local()
_work: dict[str, dict[str, Any]] = {}

WORKERS = {
    "metadata": ("Metadata", "/tools"),
    "posters": ("Posters", "/tools"),
    "webp": ("WebP conversion", "/tools"),
    "content-hashes": ("Content hashes", "/tools/dedupe"),
    "visual-hashes": ("Visual hashes", "/tools/dedupe"),
    "backups": ("Snapshots", "/tools/backup"),
    "verification": ("Library verification", "/tools"),
    "growth": ("Growth history", "/analytics"),
}


def priority() -> bool:
    with _lock:
        return _priority


def set_priority(enabled: bool) -> dict[str, bool]:
    global _priority
    with _lock:
        _priority = enabled
    return {"prioritize_browsing": enabled}


@contextmanager
def waiting(key: str) -> Iterator[None]:
    with _lock:
        _waiting[key] = _waiting.get(key, 0) + 1
    try:
        yield
    finally:
        with _lock:
            _waiting[key] -= 1
            if not _waiting[key]:
                del _waiting[key]


def wait_turn(key: str, cancelled: Callable[[], bool] = lambda: False) -> bool:
    """Wait outside locks/transactions; return False when the caller must stop."""
    with waiting(key):
        while priority():
            if cancelled():
                return False
            time.sleep(0.1)
    return not cancelled()


async def wait_turn_async(
    key: str, cancelled: Callable[[], bool] = lambda: False
) -> bool:
    with waiting(key):
        while priority():
            if cancelled():
                return False
            await asyncio.sleep(0.1)
    return not cancelled()


@contextmanager
def background(stop: threading.Event) -> Iterator[None]:
    """Only owned sweeps defer; explicit synchronous user actions remain usable."""
    previous = getattr(_local, "stop", None)
    _local.stop = stop
    try:
        yield
    finally:
        _local.stop = previous


def checkpoint(key: str, path: str | None = None) -> bool:
    stop = getattr(_local, "stop", None)
    if stop is not None and not wait_turn(key, stop.is_set):
        return False
    report_current(key, path)
    return True


def report_current(key: str, path: str | None) -> None:
    """Telemetry only: safe inside an in-flight operation; never schedules."""
    with _lock:
        if key in _work:
            _work[key]["current"] = path


def tracked(key: str) -> Callable:
    """Keep last outcome and active item for otherwise status-less sweeps."""

    def decorate(fn: Callable) -> Callable:
        @wraps(fn)
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            with _lock:
                state = _work.setdefault(key, {"active": 0})
                state.update(started_at=time.time())
                state["active"] += 1
            try:
                result = fn(*args, **kwargs)
                with _lock:
                    state["detail"] = ", ".join(
                        f"{name.replace('_', ' ')}: {value}"
                        for name, value in (result or {}).items()
                        if name
                        in (
                            "probed",
                            "generated",
                            "converted",
                            "hashed",
                            "failed",
                            "remaining",
                            "scanned",
                            "flagged",
                            "pruned",
                            "files",
                            "folders",
                            "thumbnails",
                            "files_count",
                        )
                    )
                    if (result or {}).get("failed"):
                        state["error"] = (
                            f"{result['failed']} item(s) failed; see feature details."
                        )
                    elif key in ("backups", "growth") or any(
                        (result or {}).get(name)
                        for name in ("probed", "generated", "converted", "hashed")
                    ):
                        state["error"] = None
                return result
            except Exception as exc:
                with _lock:
                    state["error"] = str(exc)
                raise
            finally:
                with _lock:
                    state["active"] -= 1
                    state.update(current=None, finished_at=time.time())

        return wrapped

    return decorate


def item(
    key: str,
    label: str,
    href: str,
    state: str = "idle",
    *,
    current: str | None = None,
    detail: str = "",
    error: str | None = None,
    percent: float | None = None,
    controls: list[str] | None = None,
    job_id: str | None = None,
    updated_at: float | None = None,
) -> dict[str, Any]:
    with _lock:
        deferred = bool(_priority and _waiting.get(key))
    return {
        "id": key,
        "label": label,
        "href": href,
        "state": "deferred" if deferred else state,
        "current": current,
        "detail": detail,
        "error": None if state == "cancelled" and error == "Cancelled" else error,
        "percent": max(0, min(100, percent)) if percent is not None else None,
        "controls": controls or [],
        "job_id": job_id,
        "updated_at": updated_at,
    }


def summary() -> dict[str, Any]:
    """Combine cheap memory snapshots; never call feature filesystem status APIs."""
    from media_library import animated, dedupe, indexer, tools, zip_batch
    from media_library.routers.downloads import manager
    from media_library.routers.web_mp4 import manager as web_mp4_manager
    from media_library.routers.x265 import manager as x265_manager

    with _lock:
        work = {key: dict(value) for key, value in _work.items()}
    items = []
    for key, (label, href) in WORKERS.items():
        entry = work.get(key, {})
        items.append(
            item(
                key,
                label,
                href,
                "running" if entry.get("active") else "idle",
                current=entry.get("current"),
                detail=entry.get("detail", ""),
                error=entry.get("error"),
                updated_at=entry.get("finished_at"),
            )
        )
    crawl = indexer.status()
    items.append(
        item(
            "indexing",
            "Indexing",
            "/settings",
            "running" if crawl["status"] == "crawling" else "idle",
            current=crawl.get("current_folder"),
            error=crawl.get("error"),
            detail=f"{crawl.get('scanned_folders', 0)} folders scanned · {crawl.get('skipped_folders', 0)} skipped",
        )
    )
    anim = animated.activity_status()
    items.append(
        item(
            "animated",
            "Animated previews",
            "/tools",
            "running" if anim["active"] else "queued" if anim["queued"] else "idle",
            current=anim["active"],
            detail=f"{anim['queued']} queued · {anim['completed']} completed · {anim['failed']} failed",
            error=f"{anim['failed']} preview(s) failed" if anim["failed"] else None,
            controls=["cancel"] if anim["queued"] else [],
        )
    )
    visual = dedupe.visual_check_status()
    items.append(
        item(
            "duplicate-checks",
            "Duplicate frame checks",
            "/tools/dedupe",
            visual["status"] if visual["status"] in ("running", "queued") else "idle",
            current=visual.get("active_file"),
            detail=f"{visual.get('completed_groups', 0)} / {visual.get('total_groups', 0)} groups · frame {visual.get('active_frame', 0)} / {visual.get('active_frames', 0)}",
            error=f"{visual['errors']} check(s) failed"
            if visual.get("errors")
            else None,
        )
    )
    batch = zip_batch.runner.status()
    items.append(
        item(
            "zip",
            "ZIP batch",
            "/tools/zip",
            batch["status"],
            current=batch["current_path"],
            percent=batch["percent"],
            detail=f"{batch['processed']} / {batch['total']} archives",
            error=batch["errors"][-1]["error"] if batch["errors"] else None,
            controls=(
                ["pause", "cancel"]
                if batch["status"] == "running"
                else ["resume", "cancel"]
                if batch["status"] == "paused"
                else []
            ),
        )
    )
    mp4 = web_mp4_manager.snapshot()
    items.append(
        item(
            "web-mp4",
            "Web MP4 replacement",
            "/tools",
            mp4["status"],
            current=mp4["current"],
            detail=f"{mp4['checked']} / {mp4['total']} checked · {mp4['conversion_pending']} awaiting replacement (including active) · {mp4['converted']} replaced · {mp4['failed']} failed",
            percent=mp4["percent"]
            if mp4["current"] and mp4["phase"] in ("converting", "verifying")
            else None,
            error=mp4["errors"][-1]["error"] if mp4["errors"] else None,
        )
    )
    conversions = x265_manager.status()
    jobs = (
        ([conversions["active"]] if conversions["active"] else [])
        + conversions["queued"]
        + conversions["history"]
    )
    for job in jobs:
        items.append(
            item(
                f"x265:{job['id']}",
                "HEVC conversion",
                "/tools",
                job["status"],
                job_id=job["id"],
                current=job["source_path"],
                error=job.get("error"),
                percent=job.get("percent"),
                controls=["cancel"] if job["status"] in ("queued", "running") else [],
            )
        )
    # Manager is owned by this event loop; do not call status(), which discovers
    # downloader executables on disk and serializes every album member.
    for job in list(manager.jobs.values()) + list(manager.history)[-20:]:
        items.append(
            item(
                f"download:{job['id']}",
                "Download",
                "/downloads",
                job["status"],
                job_id=job["id"],
                current=job.get("title") or job["url"],
                detail=job.get("speed") or "",
                error=job.get("error"),
                percent=job.get("percent"),
                controls=(
                    ["cancel"]
                    if job["status"] in ("queued", "downloading")
                    else ["retry"]
                    if job["status"] in ("failed", "cancelled")
                    else []
                ),
            )
        )
    update = tools._public_job()
    if update:
        items.append(
            item(
                "tool-update",
                "Downloader tool update",
                "/tools",
                update["status"],
                detail=update.get("phase") or "",
                error=update.get("error"),
                percent=update.get("percent"),
                controls=["cancel"] if update["status"] == "running" else [],
            )
        )
    # Queued async jobs use their individual identifiers in the scheduler.
    active_states = {
        "running",
        "downloading",
        "working",
        "queued",
        "deferred",
        "paused",
    }
    return {
        "prioritize_browsing": priority(),
        "items": items,
        "active_count": sum(row["state"] in active_states for row in items),
        "failure_count": sum(
            bool(row["error"]) or row["state"] in ("failed", "error") for row in items
        ),
    }
