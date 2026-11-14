"""Private, append-only diagnostic history, independent of the current batch."""

from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from media_library.config import get_settings

_lock = threading.Lock()


def log_path() -> Path:
    return get_settings().data_dir / "web_mp4_failures.jsonl"


def event(
    path: str,
    stage: str,
    error: str,
    *,
    diagnostic: str | None = None,
    fingerprint: list[int] | None = None,
    origin: str = "live",
) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "recorded_at": now,
        "occurred_at": now if origin == "live" else None,
        "path": path,
        "stage": stage,
        "error": error,
        "diagnostic": diagnostic or error,
        "fingerprint": fingerprint,
        "origin": origin,
    }


def _write(path: Path, records: list[dict[str, Any]], flags: int) -> None:
    fd = os.open(path, flags | os.O_WRONLY | os.O_CREAT, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def seed_recent(errors: list[dict[str, str]]) -> None:
    """Backfill recoverable recent errors once; never invent old diagnostics."""
    with _lock:
        path = log_path()
        if path.exists():
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
        try:
            _write(
                temp,
                [
                    event(
                        row["path"],
                        "previous_run",
                        row["error"],
                        origin="restored_recent",
                    )
                    for row in errors
                ],
                os.O_EXCL,
            )
            os.replace(temp, path)
        finally:
            temp.unlink(missing_ok=True)


def append(record: dict[str, Any]) -> None:
    with _lock:
        path = log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        _write(path, [record], os.O_APPEND)
