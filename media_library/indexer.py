"""The library crawler.

This module is the
replacement: an incremental, per-folder scan keyed on ``dir_mtime`` that also
indexes ZIP archives as virtual folders and prunes deleted entries with
mount-safety guards. It writes only through short transactions in a worker
thread, never on the event loop.

Safety rules:
- a folder whose mtime is unchanged is skipped (its known child folders are
  still traversed);
- a missing child is confirmed with retries before its rows are deleted;
- if more than half of a folder's indexed children look missing at once (and
  there are at least 5), pruning is aborted for that folder and reported as
  stale, because that is far more likely to be a flaky mount than a real
  deletion.
"""

from __future__ import annotations

import os
import threading
import time
import zipfile
from typing import Any

from media_library import cache, writes, zip_ops
from media_library.config import get_settings
from media_library.db import reader, writer
from media_library.paths import format_size

_HIDDEN = {".media_library_backup", ".git", "__pycache__", ".thumbnails"}

state: dict[str, Any] = {
    "status": "idle",
    "scanned_folders": 0,
    "skipped_folders": 0,
    "total_folders": 0,
    "current_folder": "",
    "last_synced": 0.0,
    "revision": 0,
    "prune_aborted": 0,
    "stale_folders": 0,
    "stale_files": 0,
    "error": "",
    "artwork_swept": {},
    # Per-run progress, reset by every crawl. ``files_*`` counts loose files
    # in the folders this run actually scanned; skipped folders are not read.
    "started_at": 0.0,
    "finished_at": 0.0,
    "duration_seconds": 0.0,
    "force_full": False,
    "files_seen": 0,
    "files_added": 0,
    "files_updated": 0,
    "files_removed": 0,
    "archives_indexed": 0,
}
_state_lock = threading.Lock()
_crawl_lock = threading.Lock()


def _update(**changes: Any) -> None:
    with _state_lock:
        state.update(changes)


def _bump(**addends: Any) -> None:
    """Atomic read-modify-write on the counters, under the state lock."""
    with _state_lock:
        for key, value in addends.items():
            state[key] = state[key] + value


def status() -> dict[str, Any]:
    with _state_lock:
        snapshot = dict(state)
    started = float(snapshot.get("started_at") or 0)
    if snapshot.get("status") == "crawling" and started:
        snapshot["elapsed_seconds"] = max(0.0, time.time() - started)
    else:
        snapshot["elapsed_seconds"] = float(snapshot.get("duration_seconds") or 0)
    return snapshot


def _confirm_gone(path: str, retries: int = 3) -> bool:
    for attempt in range(retries):
        try:
            os.stat(path)
            return False
        except FileNotFoundError:
            time.sleep(0.05 * (attempt + 1))
        except OSError:
            return False
    return True


def _delete_subtree_index(conn, target: str) -> None:
    prefix = target + os.sep
    length = len(prefix)
    conn.execute(
        "DELETE FROM folders WHERE path = ? OR substr(path, 1, ?) = ?",
        (target, length, prefix),
    )
    conn.execute(
        "DELETE FROM files WHERE folder_path = ? OR substr(folder_path, 1, ?) = ? "
        "OR path = ? OR substr(path, 1, ?) = ?",
        (target, length, prefix, target, length, prefix),
    )
    conn.execute(
        "DELETE FROM favorites WHERE path = ? OR substr(path, 1, ?) = ?",
        (target, length, prefix),
    )
    conn.execute(
        "DELETE FROM ignored_duplicates WHERE path1 = ? OR path2 = ? "
        "OR substr(path1, 1, ?) = ? OR substr(path2, 1, ?) = ?",
        (target, target, length, prefix, length, prefix),
    )
    conn.execute(
        "DELETE FROM visual_frames WHERE path = ? OR substr(path, 1, ?) = ?",
        (target, length, prefix),
    )


def _file_type(ext: str) -> str:
    settings = get_settings()
    if ext in settings.video_exts:
        return "video"
    if ext in settings.image_exts:
        return "image"
    return "other"


def _upsert_file(conn, path: str, stat: os.stat_result) -> None:
    settings = get_settings()
    ext = os.path.splitext(path)[1].lower()
    file_type = _file_type(ext)
    needs_transcode = int(
        file_type == "video" and ext not in settings.native_video_exts
    )
    name = os.path.basename(path)
    folder = os.path.dirname(path)
    existing = conn.execute(
        "SELECT size, mtime, type FROM files WHERE path = ?", (path,)
    ).fetchone()
    if (
        existing is not None
        and existing["size"] == stat.st_size
        and abs(float(existing["mtime"] or 0) - stat.st_mtime) < 0.001
    ):
        conn.execute(
            "UPDATE files SET name = ?, ext = ?, type = ?, needs_transcode = ? "
            "WHERE path = ?",
            (name, ext, file_type, needs_transcode, path),
        )
        return
    conn.execute(
        """
        INSERT INTO files
            (path, name, folder_path, type, ext, size, size_formatted, mtime,
             needs_transcode, has_thumb, width, height, duration, video_codec,
             audio_codec, fps, capture_time, quick_hash, thumb_hash,
             has_animated, webp_optimized)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, NULL, NULL, NULL, NULL,
                NULL, NULL, NULL, NULL, NULL, 0, 0)
        ON CONFLICT(path) DO UPDATE SET
            name = excluded.name, folder_path = excluded.folder_path,
            type = excluded.type, ext = excluded.ext, size = excluded.size,
            size_formatted = excluded.size_formatted, mtime = excluded.mtime,
            needs_transcode = excluded.needs_transcode,
            has_thumb = 0, width = NULL, height = NULL, duration = NULL,
            video_codec = NULL, audio_codec = NULL, fps = NULL,
            capture_time = NULL, quick_hash = NULL, thumb_hash = NULL,
            has_animated = 0, webp_optimized = 0
        """,
        (
            path,
            name,
            folder,
            file_type,
            ext,
            stat.st_size,
            format_size(stat.st_size),
            stat.st_mtime,
            needs_transcode,
        ),
    )


def _index_zip(conn, path: str, stat: os.stat_result) -> int:
    """Index (or re-index) one archive as a virtual folder."""
    try:
        with zipfile.ZipFile(path) as bundle:
            infos = bundle.infolist()
    except (zipfile.BadZipFile, OSError):
        return 0
    zip_ops.clear_archive_members(conn, path)
    count = zip_ops.index_archive(conn, path, infos)
    conn.execute(
        "UPDATE folders SET dir_mtime = ? WHERE path = ?", (stat.st_mtime, path)
    )
    return count


def _read_zip_infos(path: str) -> list[zipfile.ZipInfo] | None:
    """Central directory of an archive; None when it cannot be read."""
    try:
        with zipfile.ZipFile(path) as bundle:
            return bundle.infolist()
    except (zipfile.BadZipFile, OSError):
        return None


def _index_zip_path(path: str, stat: os.stat_result) -> bool:
    """Re-index a changed archive, keeping remote reads outside the write txn.

    Returns True when the member rows were rebuilt.
    """
    infos = _read_zip_infos(path)
    if infos is None:
        return False
    with writer() as conn, conn:
        # Re-stat inside the txn: if the archive was replaced while we read
        # its central directory (slow remote mount, concurrent WebP rewrite),
        # ``infos`` is stale — skip and let the next crawl use the new stat.
        try:
            current = os.stat(path)
        except OSError:
            return False
        if current.st_mtime != stat.st_mtime or current.st_size != stat.st_size:
            return False
        row = conn.execute(
            "SELECT is_zip, dir_mtime FROM folders WHERE path = ?", (path,)
        ).fetchone()
        if (
            row is not None
            and row["is_zip"]
            and row["dir_mtime"] is not None
            and abs(float(row["dir_mtime"]) - stat.st_mtime) <= 0.001
        ):
            return False
        zip_ops.clear_archive_members(conn, path)
        zip_ops.index_archive(conn, path, infos)
        conn.execute(
            "UPDATE folders SET dir_mtime = ? WHERE path = ?", (stat.st_mtime, path)
        )
    return True


def _scan_folder(folder: str, stat: os.stat_result) -> list[str]:
    """Scan one folder, upsert entries, prune vanished ones. Returns child dirs.

    Every remote filesystem read (scandir, child stats, ZIP central
    directories, confirm-gone retries) happens in a read-only phase; the
    write transaction at the end only runs SQL.
    """
    try:
        entries = list(os.scandir(folder))
    except OSError as exc:
        raise writes.WriteError(f"Cannot read {folder}: {exc}", 500) from exc

    files: dict[str, os.stat_result] = {}
    dirs: list[str] = []
    archives: dict[str, os.stat_result] = {}
    for entry in entries:
        if entry.name.startswith(".") or entry.name in _HIDDEN:
            continue
        try:
            if entry.is_dir(follow_symlinks=False):
                dirs.append(entry.path)
            elif entry.is_file(follow_symlinks=False):
                child = entry.stat(follow_symlinks=False)
                if entry.name.lower().endswith(".zip"):
                    archives[entry.path] = child
                else:
                    files[entry.path] = child
        except OSError:
            continue

    # Archives this scan confirms fresh (only their parent may need an update),
    # versus ones that changed and need their member rows rebuilt. When a
    # newly-changed archive cannot be read, nothing is written and the next
    # crawl retries it, exactly as before.
    fresh_archives: list[tuple[str, int]] = []
    zip_payloads: dict[str, tuple[list[zipfile.ZipInfo], os.stat_result]] = {}
    with reader() as conn:
        archive_rows = {
            row["path"]: (row["is_zip"], row["dir_mtime"])
            for row in conn.execute(
                "SELECT path, is_zip, dir_mtime FROM folders WHERE parent_path = ?",
                (folder,),
            ).fetchall()
        }
    for path, child in archives.items():
        row = archive_rows.get(path)
        if (
            row is not None
            and row[0]
            and row[1] is not None
            and abs(float(row[1]) - child.st_mtime) <= 0.001
        ):
            fresh_archives.append((path, child.st_mtime))
            continue
        infos = _read_zip_infos(path)
        if infos is not None:
            zip_payloads[path] = (infos, child)

    # Missing-child decisions, including the mount-safety confirm probes.
    with reader() as conn:
        indexed_files = conn.execute(
            "SELECT path, size, mtime FROM files WHERE folder_path = ?", (folder,)
        ).fetchall()
        child_rows = conn.execute(
            "SELECT path FROM folders WHERE parent_path = ?", (folder,)
        ).fetchall()
    present = set(dirs) | set(archives)
    missing = [row["path"] for row in child_rows if row["path"] not in present]
    prune_aborted = False
    stale_folders = 0
    stale_files = 0
    doomed: list[str] = []
    if missing and len(child_rows) >= 5 and len(missing) > len(child_rows) / 2:
        prune_aborted = True
        stale_folders = len(missing)
    else:
        for path in missing:
            if not _confirm_gone(path):
                stale_folders += 1
                with reader() as conn:
                    stale_files += int(
                        conn.execute(
                            "SELECT COUNT(*) AS n FROM files WHERE folder_path = ?",
                            (path,),
                        ).fetchone()["n"]
                        or 0
                    )
                continue
            doomed.append(path)

    # Write phase: SQL only, the lock is held for milliseconds.
    with writer() as conn, conn:
        conn.execute(
            """
            INSERT INTO folders
                (path, name, parent_path, file_count, subfolder_count, last_scanned,
                 is_zip, total_size, dir_mtime)
            VALUES (?, ?, ?, 0, 0, 0, 0, 0, NULL)
            ON CONFLICT(path) DO UPDATE SET
                name = excluded.name, parent_path = excluded.parent_path, is_zip = 0
            """,
            (folder, os.path.basename(folder) or folder, os.path.dirname(folder)),
        )
        for path in dirs:
            conn.execute(
                """
                INSERT INTO folders
                    (path, name, parent_path, file_count, subfolder_count,
                     last_scanned, is_zip, total_size, dir_mtime)
                VALUES (?, ?, ?, 0, 0, 0, 0, 0, NULL)
                ON CONFLICT(path) DO UPDATE SET
                    name = excluded.name, parent_path = excluded.parent_path,
                    is_zip = 0
                """,
                (path, os.path.basename(path), folder),
            )
        for path, child in files.items():
            _upsert_file(conn, path, child)
        for path, mtime in fresh_archives:
            conn.execute(
                "UPDATE folders SET parent_path = ? WHERE path = ?", (folder, path)
            )
        archives_rebuilt = 0
        for path, (infos, stat_at_read) in zip_payloads.items():
            # Re-stat before writing: the archive may have been replaced
            # (ZIP WebP rewrite / compress) between the read and write
            # phases, and the old member list would resurrect stale rows.
            try:
                current = os.stat(path)
            except OSError:
                continue
            if (
                current.st_mtime != stat_at_read.st_mtime
                or current.st_size != stat_at_read.st_size
            ):
                continue
            zip_ops.clear_archive_members(conn, path)
            zip_ops.index_archive(conn, path, infos)
            conn.execute(
                "UPDATE folders SET dir_mtime = ? WHERE path = ?",
                (stat_at_read.st_mtime, path),
            )
            archives_rebuilt += 1

        # Prune files that vanished from a folder we could read successfully.
        for row in indexed_files:
            if row["path"] not in files:
                conn.execute("DELETE FROM files WHERE path = ?", (row["path"],))
                conn.execute("DELETE FROM favorites WHERE path = ?", (row["path"],))
                conn.execute("DELETE FROM visual_frames WHERE path = ?", (row["path"],))

        for path in doomed:
            _delete_subtree_index(conn, path)

        total_size = sum(child.st_size for child in files.values())
        conn.execute(
            """
            UPDATE folders SET file_count = ?, subfolder_count = ?, last_scanned = ?,
                total_size = ?, dir_mtime = ?, parent_path = ?
            WHERE path = ?
            """,
            (
                len(files),
                len(dirs) + len(archives),
                time.time(),
                total_size,
                stat.st_mtime,
                os.path.dirname(folder),
                folder,
            ),
        )
    indexed_by_path = {row["path"]: row for row in indexed_files}
    files_added = sum(1 for path in files if path not in indexed_by_path)
    files_updated = sum(
        1
        for path, child in files.items()
        if path in indexed_by_path
        and (
            indexed_by_path[path]["size"] != child.st_size
            or abs(float(indexed_by_path[path]["mtime"] or 0) - child.st_mtime) >= 0.001
        )
    )
    files_removed = sum(1 for row in indexed_files if row["path"] not in files)
    _bump(
        files_seen=len(files),
        files_added=files_added,
        files_updated=files_updated,
        files_removed=files_removed,
        archives_indexed=archives_rebuilt,
    )
    if prune_aborted:
        _bump(prune_aborted=1, stale_folders=stale_folders, stale_files=stale_files)
    elif stale_folders:
        _bump(stale_folders=stale_folders, stale_files=stale_files)
    return dirs


def start_crawl(force_full: bool = False) -> bool:
    """Start one crawl on a daemon thread if none is running.

    The crawl lock is taken here, in the request handler, so a second
    concurrent request gets a definitive 409 instead of racing the
    thread's own advisory check. Returns False when one is running.
    """
    if not _crawl_lock.acquire(blocking=False):
        return False
    try:
        threading.Thread(
            target=crawl,
            kwargs={"force_full": force_full, "lock_held": True},
            daemon=True,
        ).start()
        return True
    except Exception:
        _crawl_lock.release()
        raise


def crawl(force_full: bool = False, *, lock_held: bool = False) -> dict[str, Any]:
    """Crawl the library from the root (incremental unless ``force_full``)."""
    if not (lock_held or _crawl_lock.acquire(blocking=False)):
        raise writes.WriteError("A crawl is already running", 409)
    settings = get_settings()
    root = str(settings.library_dir)
    started = time.time()
    _update(
        status="crawling",
        scanned_folders=0,
        skipped_folders=0,
        total_folders=0,
        current_folder="",
        prune_aborted=0,
        stale_folders=0,
        stale_files=0,
        error="",
        started_at=started,
        finished_at=0.0,
        duration_seconds=0.0,
        force_full=force_full,
        files_seen=0,
        files_added=0,
        files_updated=0,
        files_removed=0,
        archives_indexed=0,
    )
    try:
        if not os.path.isdir(root):
            raise writes.WriteError("Library root is not available", 503)

        queue = [root]
        seen: set[str] = set()
        scanned = 0
        skipped = 0
        while queue:
            folder = queue.pop()
            if folder in seen:
                continue
            seen.add(folder)
            try:
                stat = os.stat(folder)
            except OSError:
                continue
            try:
                with reader() as conn:
                    row = conn.execute(
                        "SELECT dir_mtime FROM folders WHERE path = ? AND is_zip = 0",
                        (folder,),
                    ).fetchone()
            except Exception:
                row = None
            if (
                not force_full
                and row is not None
                and row["dir_mtime"] is not None
                and abs(float(row["dir_mtime"]) - stat.st_mtime) < 0.001
            ):
                skipped += 1
                _update(skipped_folders=skipped, total_folders=len(seen))
                with reader() as conn:
                    children = conn.execute(
                        "SELECT path FROM folders WHERE parent_path = ? AND is_zip = 0",
                        (folder,),
                    ).fetchall()
                    archives = conn.execute(
                        "SELECT path, dir_mtime FROM folders "
                        "WHERE parent_path = ? AND is_zip = 1",
                        (folder,),
                    ).fetchall()
                queue.extend(child["path"] for child in children)
                # A changed archive does not touch its parent's mtime, so check
                # each known ZIP even when the folder itself is unchanged.
                for archive in archives:
                    try:
                        archive_stat = os.stat(archive["path"])
                    except OSError:
                        continue
                    if (
                        archive["dir_mtime"] is None
                        or abs(float(archive["dir_mtime"]) - archive_stat.st_mtime)
                        > 0.001
                    ) and _index_zip_path(archive["path"], archive_stat):
                        _bump(archives_indexed=1)
                continue

            scanned += 1
            _update(
                scanned_folders=scanned,
                total_folders=len(seen),
                current_folder=folder,
            )
            try:
                children = _scan_folder(folder, stat)
            except writes.WriteError:
                # One unreadable folder on a flaky mount must not kill the
                # whole crawl (the artwork sweep below depends on finishing).
                _bump(prune_aborted=1, stale_folders=1)
                continue
            queue.extend(children)

        artwork: dict[str, int] = {}
        if get_settings().own_workers:
            # Removing a file outside the app leaves its artwork in the local
            # cache; the index prune swept those keys and the owned runtime must too.
            try:
                artwork = cache.sweep_orphaned_artwork()
            except Exception:
                artwork = {}

        _bump(revision=1)
        finished = time.time()
        _update(
            status="idle",
            last_synced=finished,
            current_folder="",
            total_folders=len(seen),
            artwork_swept=artwork,
            finished_at=finished,
            duration_seconds=max(0.0, finished - started),
        )
        return status()
    except Exception as exc:
        failed = time.time()
        _update(
            status="error",
            error=str(exc),
            current_folder="",
            finished_at=failed,
            duration_seconds=max(0.0, failed - started),
        )
        raise
    finally:
        _crawl_lock.release()


class CrawlerThread:
    """Periodic crawler, used only when the owned runtime is enabled."""

    def __init__(self, interval_seconds: float = 1800.0) -> None:
        self.interval = interval_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="crawler", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.wait(5):
            try:
                crawl()
            except Exception:
                pass
            if self._stop.wait(self.interval):
                break

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
