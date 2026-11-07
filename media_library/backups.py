"""Library snapshots: a compacted SQLite copy, a poster tarball and metadata.

Snapshots are written into ``<library>/.media_library_backup/`` (or
``LIBRARY_BACKUP_DIR``), which is what the startup auto-restore reads when the
local index or posters are missing. Restore is disabled by default: replacing
the database under running writers is a service-stopped operation
(``python -m media_library.restore``). Set ``ALLOW_LIVE_RESTORE=true`` to allow
it from the API.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tarfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from media_library import __version__, writes
from media_library.config import get_settings
from media_library.db import reader

_lock = threading.Lock()
_META = "meta.json"
_DB = "library.db"
_TAR = "thumbnails.tar"


def snapshot_dir() -> Path:
    return get_settings().backup_dir


def _paths() -> tuple[Path, Path, Path]:
    root = snapshot_dir()
    return root / _DB, root / _TAR, root / _META


def _counts(conn: sqlite3.Connection) -> tuple[int, int]:
    try:
        files = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        folders = conn.execute("SELECT COUNT(*) FROM folders").fetchone()[0]
        return int(files or 0), int(folders or 0)
    except sqlite3.Error:
        return 0, 0


def status() -> dict[str, Any]:
    db_path, tar_path, meta_path = _paths()
    exists = db_path.is_file() and tar_path.is_file() and meta_path.is_file()
    meta: dict[str, Any] = {}
    if meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            meta = {}
    db_bytes = db_path.stat().st_size if db_path.is_file() else 0
    tar_bytes = tar_path.stat().st_size if tar_path.is_file() else 0
    return {
        "exists": exists,
        "directory": str(snapshot_dir()),
        "created_at": meta.get("created_at"),
        "created_at_str": meta.get("created_at_str"),
        "version": meta.get("version"),
        "files": meta.get("files"),
        "folders": meta.get("folders"),
        "thumbnails": meta.get("thumbnails"),
        "db_bytes": db_bytes,
        "db_size_formatted": _size(db_bytes),
        "tar_bytes": tar_bytes,
        "tar_size_formatted": _size(tar_bytes),
        "allow_live_restore": get_settings().allow_live_restore,
    }


def _size(value: int) -> str:
    from media_library.paths import format_size

    return format_size(value)


def _thumbnail_files() -> list[Path]:
    root = get_settings().thumbnail_dir
    if not root.is_dir():
        return []
    return sorted(root.glob("thumb_*.jpg"))


def create_snapshot() -> dict[str, Any]:
    """Write a consistent DB copy and a poster tarball, then swap them in."""
    if not _lock.acquire(blocking=False):
        raise writes.WriteError("A snapshot is already being created", 409)
    try:
        settings = get_settings()
        staging = settings.backup_staging_dir
        staging.mkdir(parents=True, exist_ok=True)
        target_dir = snapshot_dir()
        target_dir.mkdir(parents=True, exist_ok=True)

        staged_db = staging / f"{_DB}.{uuid.uuid4().hex}.tmp"
        staged_tar = staging / f"{_TAR}.{uuid.uuid4().hex}.tmp"

        source = sqlite3.connect(settings.db_path, timeout=30.0)
        try:
            destination = sqlite3.connect(staged_db)
            try:
                source.backup(destination)
                # The live index can carry a large freelist (removed features);
                # compact the copy so a snapshot is not hundreds of MB of holes.
                destination.execute("VACUUM")
            finally:
                destination.close()
        finally:
            source.close()

        check = sqlite3.connect(staged_db)
        try:
            integrity = check.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise writes.WriteError(
                    f"Snapshot database failed its check: {integrity}", 500
                )
            files, folders = _counts(check)
        finally:
            check.close()

        thumbnails = _thumbnail_files()
        with tarfile.open(staged_tar, "w") as archive:
            for path in thumbnails:
                try:
                    archive.add(path, arcname=path.name)
                except OSError:
                    continue

        meta = {
            "created_at": time.time(),
            "created_at_str": time.strftime("%Y-%m-%d %H:%M:%S"),
            "version": __version__,
            "files": files,
            "folders": folders,
            "thumbnails": len(thumbnails),
        }

        def commit(staged: Path, final: Path) -> None:
            temp = final.with_name(f".tmp-{uuid.uuid4().hex}-{final.name}")
            shutil.copyfile(staged, temp)
            if temp.stat().st_size != staged.stat().st_size:
                temp.unlink(missing_ok=True)
                raise writes.WriteError(
                    f"Snapshot copy was truncated: {final.name}", 500
                )
            os.replace(temp, final)

        commit(staged_db, target_dir / _DB)
        commit(staged_tar, target_dir / _TAR)
        meta_temp = target_dir / f".tmp-{uuid.uuid4().hex}-{_META}"
        meta_temp.write_text(json.dumps(meta, indent=2), encoding="utf-8")
        os.replace(meta_temp, target_dir / _META)
        return {
            "success": True,
            "created_at": meta["created_at"],
            "files": files,
            "folders": folders,
            "thumbnails": len(thumbnails),
            "db_bytes": (target_dir / _DB).stat().st_size,
            "tar_bytes": (target_dir / _TAR).stat().st_size,
            "directory": str(target_dir),
        }
    finally:
        for leftover in get_settings().backup_staging_dir.glob("*.tmp"):
            try:
                leftover.unlink()
            except OSError:
                pass
        _lock.release()


def verify_snapshot() -> dict[str, Any]:
    db_path, tar_path, meta_path = _paths()
    if not (db_path.is_file() and tar_path.is_file()):
        raise writes.WriteError("No snapshot found", 404)
    with reader() as current:
        integrity = current.execute("PRAGMA integrity_check").fetchone()[0]
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        snapshot_integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        files, folders = _counts(conn)
    finally:
        conn.close()
    members = 0
    with tarfile.open(tar_path) as archive:
        for member in archive:
            if member.isfile():
                members += 1
    return {
        "snapshot_integrity": snapshot_integrity,
        "live_integrity": integrity,
        "files": files,
        "folders": folders,
        "thumbnails": members,
        "meta_present": meta_path.is_file(),
    }


def _restore_from(
    db_source: Path, tar_source: Path, db_target: Path, thumbs_target: Path
) -> dict[str, Any]:
    """Copy a validated snapshot database into place and unpack its posters."""
    if not db_source.is_file() or not tar_source.is_file():
        raise writes.WriteError("No snapshot found", 404)

    check = sqlite3.connect(f"file:{db_source}?mode=ro", uri=True)
    try:
        if check.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise writes.WriteError("Snapshot database is corrupt", 400)
        files, folders = _counts(check)
    finally:
        check.close()

    db_target.parent.mkdir(parents=True, exist_ok=True)
    temp_db = db_target.with_name(f".restore-{uuid.uuid4().hex}.db")
    shutil.copyfile(db_source, temp_db)
    os.replace(temp_db, db_target)

    thumbs_target.mkdir(parents=True, exist_ok=True)
    restored = 0
    with tarfile.open(tar_source) as archive:
        for member in archive:
            if not member.isfile() or not os.path.basename(member.name).startswith(
                "thumb_"
            ):
                continue
            if os.path.dirname(member.name):
                continue
            source = archive.extractfile(member)
            if source is None:
                continue
            target = thumbs_target / member.name
            temp = thumbs_target / f".restore-{uuid.uuid4().hex}-{member.name}"
            with source, open(temp, "wb") as output:
                shutil.copyfileobj(source, output, 1024 * 1024)
            os.replace(temp, target)
            restored += 1

    return {
        "restored": True,
        "database": str(db_target),
        "thumbnails": restored,
        "files": files,
        "folders": folders,
        "restart_required": True,
    }


def auto_restore_if_missing() -> dict[str, Any]:
    """Restore the index on startup when it is missing or empty.

    ``<library backup>/library.db`` + ``thumbnails.tar`` is the only snapshot
    location now, so a deployment whose local index was lost recovers from it on
    the next start. A non-empty index is never touched.
    """
    settings = get_settings()
    present, files, folders = _index_health(settings.db_path)
    if present and (files or folders):
        return {"restored": False, "reason": "index present", "files": files}
    candidates = [
        (snapshot_dir() / _DB, snapshot_dir() / _TAR, "snapshot"),
    ]
    for db_source, tar_source, label in candidates:
        if db_source.is_file() and tar_source.is_file():
            result = _restore_from(
                db_source, tar_source, settings.db_path, settings.thumbnail_dir
            )
            return {**result, "source": label}
    return {"restored": False, "reason": "no usable snapshot"}


def _index_health(path: Path) -> tuple[bool, int, int]:
    if not path.is_file():
        return False, 0, 0
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        return False, 0, 0
    try:
        files, folders = _counts(conn)
    except sqlite3.Error:
        return False, 0, 0
    finally:
        conn.close()
    return True, files, folders


def restore_snapshot(
    target_db: Path | None = None,
    target_thumbnails: Path | None = None,
    *,
    allow_live: bool | None = None,
) -> dict[str, Any]:
    """Restore the snapshot into explicit targets (service-stopped operation)."""
    settings = get_settings()
    allowed = settings.allow_live_restore if allow_live is None else allow_live
    db_target = Path(target_db) if target_db else settings.db_path
    thumbs_target = (
        Path(target_thumbnails) if target_thumbnails else settings.thumbnail_dir
    )
    if not allowed and db_target.resolve() == settings.db_path.resolve():
        raise writes.WriteError(
            "Live restore is disabled: stop the service and run "
            "`python -m media_library.restore`, or set ALLOW_LIVE_RESTORE=true.",
            409,
        )

    db_path, tar_path, _meta_path = _paths()
    if not db_path.is_file() or not tar_path.is_file():
        raise writes.WriteError("No snapshot found", 404)

    return _restore_from(db_path, tar_path, db_target, thumbs_target)
