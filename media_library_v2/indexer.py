"""v2's own library crawler.

While v1 runs, its crawler owns the index and v2 only reads. This module is the
replacement: an incremental, per-folder scan keyed on ``dir_mtime`` that also
indexes ZIP archives as virtual folders and prunes deleted entries with
mount-safety guards. It writes only through short transactions in a worker
thread, never on the event loop.

Safety rules mirrored from v1:
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

from media_library_v2 import writes, zip_ops
from media_library_v2.config import get_settings
from media_library_v2.db import reader, writer
from media_library_v2.paths import format_size

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
}
_state_lock = threading.Lock()
_crawl_lock = threading.Lock()


def _update(**changes: Any) -> None:
    with _state_lock:
        state.update(changes)


def status() -> dict[str, Any]:
    with _state_lock:
        return dict(state)


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


def _scan_folder(conn, folder: str, stat: os.stat_result) -> list[str]:
    """Scan one folder, upsert entries, prune vanished ones. Returns child dirs."""
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
                path = entry.path
                dirs.append(path)
                child = entry.stat(follow_symlinks=False)
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
                    (path, entry.name, folder),
                )
            elif entry.is_file(follow_symlinks=False):
                child = entry.stat(follow_symlinks=False)
                if entry.name.lower().endswith(".zip"):
                    archives[entry.path] = child
                else:
                    files[entry.path] = child
                    _upsert_file(conn, entry.path, child)
        except OSError:
            continue

    for path, child in archives.items():
        existing = conn.execute(
            "SELECT is_zip, dir_mtime FROM folders WHERE path = ?", (path,)
        ).fetchone()
        if (
            existing is None
            or not existing["is_zip"]
            or existing["dir_mtime"] is None
            or abs(float(existing["dir_mtime"]) - child.st_mtime) > 0.001
        ):
            _index_zip(conn, path, child)
        else:
            conn.execute(
                "UPDATE folders SET parent_path = ? WHERE path = ?", (folder, path)
            )

    # Prune files that vanished from a folder we could read successfully.
    indexed_files = conn.execute(
        "SELECT path FROM files WHERE folder_path = ?", (folder,)
    ).fetchall()
    for row in indexed_files:
        if row["path"] not in files:
            conn.execute("DELETE FROM files WHERE path = ?", (row["path"],))
            conn.execute("DELETE FROM favorites WHERE path = ?", (row["path"],))

    # Prune child folders/archives that vanished, with mount-safety.
    children = conn.execute(
        "SELECT path, is_zip FROM folders WHERE parent_path = ?", (folder,)
    ).fetchall()
    present = set(dirs) | set(archives)
    missing = [row["path"] for row in children if row["path"] not in present]
    aborted = 0
    stale_folders = 0
    stale_files = 0
    if missing and len(children) >= 5 and len(missing) > len(children) / 2:
        aborted = 1
        stale_folders = len(missing)
        stale_files = 0
    else:
        for path in missing:
            if not _confirm_gone(path):
                stale_folders += 1
                stale_files += int(
                    conn.execute(
                        "SELECT COUNT(*) AS n FROM files WHERE folder_path = ?",
                        (path,),
                    ).fetchone()["n"]
                    or 0
                )
                continue
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
    if aborted:
        _update(
            prune_aborted=state["prune_aborted"] + 1,
            stale_folders=state["stale_folders"] + stale_folders,
            stale_files=state["stale_files"] + stale_files,
        )
    elif stale_folders:
        _update(
            stale_folders=state["stale_folders"] + stale_folders,
            stale_files=state["stale_files"] + stale_files,
        )
    return dirs


def crawl(force_full: bool = False) -> dict[str, Any]:
    """Crawl the library from the root (incremental unless ``force_full``)."""
    if not _crawl_lock.acquire(blocking=False):
        raise writes.WriteError("A crawl is already running", 409)
    settings = get_settings()
    root = str(settings.library_dir)
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
                    ):
                        with writer() as conn, conn:
                            _index_zip(conn, archive["path"], archive_stat)
                continue

            scanned += 1
            _update(
                scanned_folders=scanned,
                total_folders=len(seen),
                current_folder=folder,
            )
            with writer() as conn, conn:
                children = _scan_folder(conn, folder, stat)
            queue.extend(children)

        _update(
            status="idle",
            last_synced=time.time(),
            revision=state["revision"] + 1,
            current_folder="",
            total_folders=len(seen),
        )
        return status()
    except Exception as exc:
        _update(status="error", error=str(exc), current_folder="")
        raise
    finally:
        _crawl_lock.release()


class CrawlerThread:
    """Periodic crawler, used only when v2 owns the workers."""

    def __init__(self, interval_seconds: float = 1800.0) -> None:
        self.interval = interval_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="v2-crawler", daemon=True
        )
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
