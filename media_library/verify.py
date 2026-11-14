"""Media verification queue: index-only detection of damaged files.

The owned runtime runs one daemon thread (:class:`VerifyWorker`) that pages the
index in small batches and flags rows that show damage the *index* already
knows about — no directory walks and no per-file ``stat`` storm on a slow
remote mount (AGENTS.md Rule 2):

- ``zero_size``  — ``files.size = 0`` for a video or image;
- ``probe_failed`` — definitely failed metadata probes, stored as ``0 x 0``
  (``width IS NULL`` only means "not probed yet");
- ``thumb_failed`` — poster generation failed (``has_thumb = -1``).

ZIP archive members are skipped for all three: the probe worker and the poster
sweep deliberately write ``0 x 0`` / ``-1`` for them because their dimensions
are backfilled from the cached cover poster and posters generate on demand.

Findings live in ``file_issues`` — one row per (path, issue). A row stays
stalked by its first detection: the sweep uses ``INSERT OR IGNORE`` so
re-detection keeps the original ``detected_at`` and never resets a dismissal.
A later pass or an explicit recheck deletes a row whose issue no longer
derives, and a pass also prunes issue rows whose ``files`` row is gone.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any

from media_library import activity
from media_library.db import reader, writer
from media_library.paths import is_zip_member
from media_library.writes import WriteError

logger = logging.getLogger(__name__)

ZERO_SIZE = "zero_size"
PROBE_FAILED = "probe_failed"
THUMB_FAILED = "thumb_failed"

ISSUES = (ZERO_SIZE, PROBE_FAILED, THUMB_FAILED)

# Small statements everywhere: SQLite stops at ~999 bound variables, and the
# package already chunks at 400 (see dedupe.py). The pass keeps its batches
# under that too.
_BATCH_PATHS = 200
_CHUNK = 400


def derive_issues(row) -> set[str]:
    """The issues one indexed ``files`` row shows, from the index alone."""
    if row["type"] not in ("video", "image"):
        return set()
    issues: set[str] = set()
    if int(row["size"] or 0) == 0:
        issues.add(ZERO_SIZE)
    width, height = row["width"], row["height"]
    # ``width IS NULL`` means "not probed yet"; ``0 x 0`` is decisive.
    if (
        width is not None
        and height is not None
        and int(width) == 0
        and int(height) == 0
    ):
        issues.add(PROBE_FAILED)
    if int(row["has_thumb"] or 0) == -1:
        issues.add(THUMB_FAILED)
    return issues


def _existing_issues(conn, paths: list[str]) -> dict[str, set[str]]:
    """What ``file_issues`` currently holds for these paths, chunked."""
    result: dict[str, set[str]] = {}
    if not paths:
        return result
    for start in range(0, len(paths), _CHUNK):
        chunk = paths[start : start + _CHUNK]
        placeholders = ",".join("?" for _ in chunk)
        rows = conn.execute(
            f"SELECT path, issue FROM file_issues WHERE path IN ({placeholders})",
            chunk,
        ).fetchall()
        for row in rows:
            result.setdefault(row["path"], set()).add(row["issue"])
    return result


def _reconcile_rows(
    conn, desired: dict[str, set[str]], existing: dict[str, set[str]]
) -> tuple[int, list[tuple[str, str]]]:
    """Make ``file_issues`` match the derived sets inside one transaction.

    Returns the number of issues newly recorded and the stale (path, issue)
    pairs that were deleted. A path absent from ``desired`` loses every row
    its issues had — that is how a sweep drops a file that vanished.
    """
    inserts: list[tuple[str, str, float]] = []
    deletes: list[tuple[str, str]] = []
    now = time.time()
    for path in set(desired) | set(existing):
        want = desired.get(path, set())
        have = existing.get(path, set())
        for issue in want - have:
            inserts.append((path, issue, now))
        for issue in have - want:
            deletes.append((path, issue))
    if inserts:
        conn.executemany(
            "INSERT OR IGNORE INTO file_issues (path, issue, detected_at) "
            "VALUES (?, ?, ?)",
            inserts,
        )
    if deletes:
        conn.executemany(
            "DELETE FROM file_issues WHERE path = ? AND issue = ?", deletes
        )
    return len(inserts), deletes


def _batch(after_path: str, limit: int) -> list:
    """The next ``limit`` media rows past ``after_path``, index only."""
    with reader() as conn:
        return conn.execute(
            "SELECT path, name, type, size, mtime, width, height, has_thumb "
            "FROM files WHERE path > ? ORDER BY path LIMIT ?",
            (after_path, limit),
        ).fetchall()


def _archive_members(rows) -> set[str]:
    """Rows among ``rows`` that address a ZIP virtual-folder member.

    Mirrors :func:`media_library.paths.is_zip_member`: the first ``.zip/``
    component in the (case-insensitive) path demarcates a virtual archive
    folder unless that archive has an ``is_zip = 0`` folders row. Resolved
    with one query per call instead of one per member.
    """
    archives: dict[str, None] = {}
    for row in rows:
        path = row["path"]
        marker = path.lower().find(f".zip{os.sep}")
        if marker != -1:
            archives.setdefault(path[: marker + len(".zip")], None)
    virtual = {archive: True for archive in archives}
    archive_list = list(archives)
    for start in range(0, len(archive_list), _CHUNK):
        chunk = archive_list[start : start + _CHUNK]
        placeholders = ",".join("?" for _ in chunk)
        with reader() as conn:
            found = conn.execute(
                f"SELECT path, is_zip FROM folders WHERE path IN ({placeholders})",
                chunk,
            ).fetchall()
        for found_row in found:
            virtual[found_row["path"]] = bool(found_row["is_zip"])
    members: set[str] = set()
    for row in rows:
        path = row["path"]
        marker = path.lower().find(f".zip{os.sep}")
        if marker == -1:
            continue
        archive = path[: marker + len(".zip")]
        if virtual.get(archive, True):
            members.add(path)
    return members


def _prune_missing_rows() -> int:
    """Drop issue rows whose ``files`` row is gone (delete, move, unzip).

    Pure SQLite: untouched issue rows would otherwise point at paths the
    index no longer knows. The sweep calls this at the end of a pass.
    """
    with writer() as conn, conn:
        cursor = conn.execute(
            "DELETE FROM file_issues WHERE path NOT IN (SELECT path FROM files)"
        )
        return cursor.rowcount or 0


# --- Owned worker ----------------------------------------------------------


class VerifyWorker:
    """Pages the whole index in small batches, recording issue rows."""

    def __init__(
        self,
        batch: int = _BATCH_PATHS,
        batch_pause: float = 0.5,
        idle_wait: float = 1800.0,
    ) -> None:
        self.batch = max(1, batch)
        self.batch_pause = batch_pause
        self.idle_wait = idle_wait
        self._stop = threading.Event()
        self._rescan = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._state: dict[str, Any] = {
            "pass_running": False,
            "passes": 0,
            "scanned": 0,
            "flagged": 0,
            "pruned": 0,
            "last_pass": None,
            "last_pass_seconds": None,
        }

    # -- lifecycle ----------------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="verify", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._rescan.set()  # wake an idle pass wait
        if self._thread:
            self._thread.join(timeout=5)

    def _loop(self) -> None:
        """A full pass, then idle; a rescan short-circuits the idle wait."""
        while not self._stop.is_set():
            try:
                self.run_pass()
            except Exception as exc:
                logger.warning("[verify] verification pass failed: %s", exc)
                with self._lock:
                    self._state["pass_running"] = False
            # Sleep until a rescan arrives (or ``stop`` wakes us), the idle
            # cadence elapses, or we are stopped.
            if self._rescan.wait(self.idle_wait):
                self._rescan.clear()
            if self._stop.is_set():
                break

    # -- the pass -----------------------------------------------------------
    @activity.tracked("verification")
    def run_pass(self) -> dict[str, Any]:
        """Cover the library once: flag, clear and prune issue rows."""
        started = time.time()
        with self._lock:
            self._state["pass_running"] = True
            self._state["scanned"] = 0
            self._state["flagged"] = 0
            self._state["pruned"] = 0
        scanned = 0
        flagged = 0
        pruned = 0
        after = ""
        try:
            while True:
                if not activity.checkpoint("verification"):
                    break
                rows = _batch(after, self.batch)
                if not rows:
                    break
                members = _archive_members(rows)
                desired = {
                    row["path"]: derive_issues(row)
                    for row in rows
                    if row["path"] not in members
                }
                with writer() as conn, conn:
                    existing = _existing_issues(conn, list(desired))
                    inserted, deleted = _reconcile_rows(conn, desired, existing)
                scanned += len(rows)
                flagged += inserted
                pruned += len(deleted)
                after = rows[-1]["path"]
                # A FUSE-heavy host: park briefly between two SQL batches.
                if self._stop.wait(self.batch_pause):
                    break
            pruned += _prune_missing_rows()
        finally:
            with self._lock:
                self._state["pass_running"] = False
                self._state["passes"] += 1
                self._state["scanned"] = scanned
                self._state["flagged"] = flagged
                self._state["pruned"] = pruned
                self._state["last_pass"] = time.time()
                self._state["last_pass_seconds"] = round(time.time() - started, 3)
        return {
            "scanned": scanned,
            "flagged": flagged,
            "pruned": pruned,
            "seconds": round(time.time() - started, 3),
        }

    # -- rescan request -----------------------------------------------------
    def request_rescan(self) -> None:
        """Ask the loop for another full pass; thread-safe."""
        self._rescan.set()

    def rescan_pending(self) -> bool:
        return self._rescan.is_set()

    # -- shared state -------------------------------------------------------
    def state(self) -> dict[str, Any]:
        with self._lock:
            snapshot = dict(self._state)
        snapshot["running"] = bool(self._thread is not None and self._thread.is_alive())
        snapshot["rescan_pending"] = self._rescan.is_set()
        return snapshot


# Module-owned worker, shared by the runtime and the API router.

_worker: VerifyWorker | None = None
_worker_lock = threading.Lock()


def worker() -> VerifyWorker:
    """The one verification worker for this process, created on demand."""
    global _worker
    with _worker_lock:
        if _worker is None:
            _worker = VerifyWorker()
        return _worker


def start_worker() -> VerifyWorker:
    """Start the owned verification worker (runtime calls this)."""
    thread = worker()
    thread.start()
    return thread


def stop_worker() -> None:
    """Stop the owned verification worker, leaving its state readable."""
    with _worker_lock:
        if _worker is not None:
            _worker.stop()


def request_rescan() -> None:
    worker().request_rescan()


def rescan_pending() -> bool:
    return worker().rescan_pending()


def worker_state() -> dict[str, Any]:
    return worker().state()


# --- API-facing queries and single-file actions ----------------------------


def issue_counts() -> list[dict[str, Any]]:
    """Open and total counts per known issue, every issue always present."""
    with reader() as conn:
        rows = conn.execute(
            "SELECT issue, COUNT(*) AS total, "
            "SUM(CASE WHEN dismissed_at IS NULL THEN 1 ELSE 0 END) AS open "
            "FROM file_issues GROUP BY issue"
        ).fetchall()
    found = {
        row["issue"]: (int(row["open"] or 0), int(row["total"] or 0)) for row in rows
    }
    return [
        {
            "issue": name,
            "open": found.get(name, (0, 0))[0],
            "total": found.get(name, (0, 0))[1],
        }
        for name in ISSUES
    ]


def totals() -> dict[str, int]:
    """Open (undismissed) and dismissed issue rows in one round trip."""
    with reader() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS total, "
            "SUM(CASE WHEN dismissed_at IS NULL THEN 1 ELSE 0 END) AS open "
            "FROM file_issues"
        ).fetchone()
    total = int(row["total"] or 0)
    open_count = int(row["open"] or 0)
    return {"open": open_count, "dismissed": total - open_count}


def list_issues(
    issue: str | None = None,
    include_dismissed: bool = False,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    """Issue rows newest first, joined with the files row for display."""
    if issue is not None and issue not in ISSUES:
        raise WriteError("Unknown issue", 400)
    where = ["1 = 1"]
    params: list[Any] = []
    if issue is not None:
        where.append("fi.issue = ?")
        params.append(issue)
    if not include_dismissed:
        where.append("fi.dismissed_at IS NULL")
    base = (
        " FROM file_issues fi JOIN files f ON f.path = fi.path "
        "WHERE " + " AND ".join(where)
    )
    with reader() as conn:
        total = int(conn.execute(f"SELECT COUNT(*){base}", params).fetchone()[0])
        rows = conn.execute(
            "SELECT fi.path, fi.issue, fi.detected_at, fi.dismissed_at, "
            "f.name, f.type, f.ext, f.size, f.size_formatted, f.mtime"
            f"{base} ORDER BY fi.detected_at DESC, fi.path LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "issues": [dict(row) for row in rows],
    }


def recheck_file(path: str) -> dict[str, Any]:
    """Re-derive one file's issues from the filesystem and the index.

    An explicit, single-file action may touch the filesystem: one ``stat``
    for the size, and the real prober when the index holds a ``0 x 0``. A
    successful probe clears the issue; the ``files`` row itself is left to
    the owned MetadataWorker, which owns that column's lifecycle.
    """
    with reader() as conn:
        row = conn.execute(
            "SELECT path, name, type, ext, size, mtime, width, height, has_thumb "
            "FROM files WHERE path = ?",
            (path,),
        ).fetchone()
    if row is None:
        raise WriteError("File not found in the index", 404)

    issues: set[str] = set()
    probe_status = "skipped"
    if row["type"] in ("video", "image") and not is_zip_member(path):
        # The filesystem decides the size: one stat of the one file.
        try:
            size = os.stat(path).st_size
        except OSError:
            size = int(row["size"] or 0)
        if size == 0:
            issues.add(ZERO_SIZE)
        width, height = row["width"], row["height"]
        if (
            width is not None
            and height is not None
            and int(width) == 0
            and int(height) == 0
        ):
            from media_library import probe

            result = probe.probe_media(path)
            if result is None:
                probe_status = "failed"
                issues.add(PROBE_FAILED)
            elif result is probe.TRANSIENT:
                # Cannot confirm either way: keep the recorded issue.
                probe_status = "transient"
                issues.add(PROBE_FAILED)
            else:
                probe_status = "ok"
        if int(row["has_thumb"] or 0) == -1:
            issues.add(THUMB_FAILED)

    with writer() as conn, conn:
        existing = _existing_issues(conn, [path])
        _inserted, deleted = _reconcile_rows(conn, {path: issues}, existing)
    return {
        "path": path,
        "issues": sorted(issues),
        "cleared": sorted(issue for _gone_path, issue in deleted),
        "probe_status": probe_status,
    }


def dismiss_issues(path: str, issue: str | None = None) -> dict[str, Any]:
    """Dismiss one issue, or every issue of a path; dismissed rows stay.

    Read-only indexes refuse politely: :class:`db.DatabaseReadOnlyError`
    maps to 503 in the router, the same as every other mutation.
    """
    if issue is not None and issue not in ISSUES:
        raise WriteError("Unknown issue", 400)
    now = time.time()
    with writer() as conn, conn:
        if issue is not None:
            cursor = conn.execute(
                "UPDATE file_issues SET dismissed_at = ? "
                "WHERE path = ? AND issue = ? AND dismissed_at IS NULL",
                (now, path, issue),
            )
            dismissed = cursor.rowcount or 0
        else:
            cursor = conn.execute(
                "UPDATE file_issues SET dismissed_at = ? "
                "WHERE path = ? AND dismissed_at IS NULL",
                (now, path),
            )
            dismissed = cursor.rowcount or 0
        remaining = conn.execute(
            "SELECT issue FROM file_issues WHERE path = ? ORDER BY issue", (path,)
        ).fetchall()
    return {
        "path": path,
        "dismissed": int(dismissed),
        "issues": [remaining_row["issue"] for remaining_row in remaining],
    }
