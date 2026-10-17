"""Index pruning, health reconciliation and the deep-clean sweep."""

import hashlib
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from media_library.core.config import (
    DEFAULT_DESTINATION_DIR,
    THUMBNAIL_CACHE_DIR,
)
from media_library.core.state import (
    state,
)
from media_library.db.connection import db_conn
from media_library.db.indexer_files import remove_file_from_index
from media_library.db.indexer_state import indexer_lock, indexer_state  # noqa: F401

logger = logging.getLogger(__name__)

# The animated-preview cache helpers live in ``services.anim_cache`` and are
# imported lazily inside the functions that use them. Importing that package at
# module level pulls in ``media_library.services.__init__``, which eagerly
# imports the dedup facades, which import back from ``media_library.db.indexer``
# — a cycle that turns ``import media_library.db.indexer`` into an ImportError
# as soon as the import order inside the indexer facade changes. Keeping the DB
# layer free of service imports at import time removes the cycle for good.


def _path_confirmed_gone(path: str, attempts: int = 3, delay: float = 0.15) -> bool:
    """Return True only when a path is consistently reported missing on disk.

    Remote FUSE mounts (pCloud, rclone, NFS) can transiently fail ``stat`` calls
    while reconnecting. A single failed existence check must never be treated as
    "the file was deleted", otherwise a brief mount hiccup wipes the index (this
    is exactly what emptied ~48k files once). Retry a few times before
    concluding the path is gone.
    """
    for attempt in range(attempts):
        try:
            if os.path.isdir(path) or os.path.isfile(path):
                return False
        except OSError:
            pass
        if attempt < attempts - 1:
            time.sleep(delay)
    return True


def _get_stale_folders(
    db_folders: list[str], resolved_base: str, visited_folders: set[str] | None
) -> tuple[list[str], list[str]]:
    candidate_stale = []
    under_base_folders = []
    for fp in db_folders:
        is_under_base = fp == resolved_base or fp.startswith(resolved_base + os.sep)
        if not is_under_base:
            continue
        under_base_folders.append(fp)
        if visited_folders is not None and fp in visited_folders:
            continue
        try:
            appears_gone = not (os.path.isdir(fp) or os.path.isfile(fp))
        except OSError:
            appears_gone = True
        if appears_gone:
            candidate_stale.append(fp)
    return candidate_stale, under_base_folders


def _remove_stale_folders(conn: Any, stale_folders: list[str]) -> tuple[int, int]:
    pruned_folders = 0
    pruned_files = 0
    thumb_dir = THUMBNAIL_CACHE_DIR
    for sf in stale_folders:
        cur = conn.execute(
            "SELECT path, mtime, size FROM files WHERE folder_path = ?", (sf,)
        )
        rows = cur.fetchall()
        for r in rows:
            k1 = f"{r['path']}_{r['mtime']}_{r['size']}"
            h1 = hashlib.sha256(k1.encode("utf-8")).hexdigest()[:24]
            try:
                (thumb_dir / f"thumb_{h1}.jpg").unlink(missing_ok=True)
            except OSError:
                pass

            k2 = r["path"]
            h2 = hashlib.sha256(k2.encode("utf-8")).hexdigest()[:24]
            try:
                (thumb_dir / f"thumb_{h2}.jpg").unlink(missing_ok=True)
            except OSError:
                pass

        with conn:
            conn.execute("DELETE FROM files WHERE folder_path = ?", (sf,))
            conn.execute("DELETE FROM folders WHERE path = ?", (sf,))
        from media_library.services.anim_cache import unlink_animated_previews

        unlink_animated_previews(rows)
        pruned_folders += 1
        pruned_files += len(rows)
    return pruned_folders, pruned_files


def _clean_orphaned_thumbnails(conn: Any, thumb_dir: Any) -> int:
    pruned_orphaned_thumbs = 0
    if thumb_dir.exists():
        cur = conn.execute("SELECT path, mtime, size FROM files")
        valid_thumb_names = set()
        for r in cur.fetchall():
            p, m, s = r["path"], r["mtime"], r["size"]
            k1 = f"{p}_{m}_{s}"
            h1 = hashlib.sha256(k1.encode("utf-8")).hexdigest()[:24]
            valid_thumb_names.add(f"thumb_{h1}.jpg")
            k2 = p
            h2 = hashlib.sha256(k2.encode("utf-8")).hexdigest()[:24]
            valid_thumb_names.add(f"thumb_{h2}.jpg")
            if s is not None:
                k3 = f"{p}_{s}"
                h3 = hashlib.sha256(k3.encode("utf-8")).hexdigest()[:24]
                valid_thumb_names.add(f"thumb_{h3}.jpg")

        for t_file in thumb_dir.glob("thumb_*.jpg"):
            if t_file.name not in valid_thumb_names:
                try:
                    t_file.unlink(missing_ok=True)
                    pruned_orphaned_thumbs += 1
                except OSError:
                    pass
    return pruned_orphaned_thumbs


def _count_files_in_folders(conn: Any, folders: list[str]) -> int:
    """Count indexed file rows belonging to the given folders (chunked for SQLite)."""
    total = 0
    for i in range(0, len(folders), 500):
        chunk = folders[i : i + 500]
        placeholders = ",".join("?" * len(chunk))
        total += conn.execute(
            f"SELECT COUNT(*) FROM files WHERE folder_path IN ({placeholders})", chunk
        ).fetchone()[0]
    return total


def _record_index_health(conn: Any, stale_folders: list[str]) -> None:
    """Record how many indexed folders (and files) no longer exist on the mount.

    Kept in ``indexer_state`` so ``/api/system/status`` can show a truthful
    "reachable vs indexed" split instead of a hardcoded 100%.
    """
    indexer_state["stale_folders"] = len(stale_folders)
    if stale_folders:
        try:
            indexer_state["stale_files"] = _count_files_in_folders(conn, stale_folders)
        except Exception as e:  # never let bookkeeping break the prune
            logger.error(f"[Prune] Error counting stale files: {e}")
            indexer_state["stale_files"] = 0
    else:
        indexer_state["stale_files"] = 0


def prune_stale_index_entries(
    base_dir: str | None = None,
    visited_folders: set[str] | None = None,
    clean_orphaned_thumbnails: bool = True,
    force: bool = False,
) -> dict[str, Any]:
    resolved_base = os.path.realpath(
        base_dir or state.get("destination_dir") or DEFAULT_DESTINATION_DIR
    )
    try:
        base_accessible = os.path.isdir(resolved_base)
    except OSError:
        base_accessible = False
    if not base_accessible:
        logger.warning(
            f"[Prune] Base directory {resolved_base} is not accessible. Aborting prune to prevent accidental data loss."
        )
        indexer_state["prune_aborted"] = True
        indexer_state["prune_reason"] = "Base directory unmounted or missing"
        return {
            "pruned_folders": 0,
            "pruned_files": 0,
            "pruned_orphaned_thumbs": 0,
            "pruned_orphaned_anims": 0,
            "pruned_stale_duplicates": 0,
            "aborted": True,
            "reason": "Base directory unmounted or missing",
        }

    pruned_folders = 0
    pruned_files = 0
    pruned_orphaned_thumbs = 0
    pruned_orphaned_anims = 0
    pruned_stale_duplicates = 0

    try:
        with db_conn() as conn:
            cur = conn.execute("SELECT path FROM folders")
            db_folders = [r["path"] for r in cur.fetchall()]

            candidate_stale, under_base_folders = _get_stale_folders(
                db_folders, resolved_base, visited_folders
            )

            max_safe_prune = max(25, int(len(under_base_folders) * 0.10))
            if len(candidate_stale) > max_safe_prune and not force:
                logger.warning(
                    f"[Prune] Aborting: {len(candidate_stale)} of {len(under_base_folders)} folders reported missing "
                    f"(safety limit {max_safe_prune}). Suspected transient mount failure; index left intact."
                )
                _record_index_health(conn, candidate_stale)
                indexer_state["prune_aborted"] = True
                indexer_state["prune_reason"] = (
                    f"{len(candidate_stale)} of {len(under_base_folders)} indexed folders are missing "
                    f"(safety limit {max_safe_prune})"
                )
                return {
                    "pruned_folders": 0,
                    "pruned_files": 0,
                    "pruned_orphaned_thumbs": 0,
                    "pruned_orphaned_anims": 0,
                    "pruned_stale_duplicates": 0,
                    "aborted": True,
                    "reason": f"Too many folders reported missing ({len(candidate_stale)}); aborted to protect the index",
                    "candidate_folders": len(candidate_stale),
                    "total_folders": len(under_base_folders),
                }

            stale_folders = [fp for fp in candidate_stale if _path_confirmed_gone(fp)]

            pruned_folders, pruned_files = _remove_stale_folders(conn, stale_folders)

            # Folders left behind because their absence could not be confirmed
            # still count as stale (they will be retried on the next prune).
            removed = set(stale_folders)
            _record_index_health(
                conn, [fp for fp in candidate_stale if fp not in removed]
            )
            indexer_state["prune_aborted"] = False
            indexer_state["prune_reason"] = ""

            with conn:
                cur = conn.execute("""
                    DELETE FROM ignored_duplicates
                    WHERE path1 NOT IN (SELECT path FROM files)
                       OR path2 NOT IN (SELECT path FROM files)
                """)
                pruned_stale_duplicates = cur.rowcount

            if clean_orphaned_thumbnails:
                pruned_orphaned_thumbs = _clean_orphaned_thumbnails(
                    conn, THUMBNAIL_CACHE_DIR
                )
                from media_library.services.anim_cache import (
                    clean_orphaned_animated_previews,
                )

                pruned_orphaned_anims = clean_orphaned_animated_previews(conn)

            try:
                from media_library.services.deduplication_service import (
                    invalidate_visual_cache,
                )

                invalidate_visual_cache()
            except ImportError:
                pass

            cur = conn.execute("SELECT COUNT(*) FROM files")
            indexer_state["total_files"] = cur.fetchone()[0]
            cur_folders = conn.execute("SELECT COUNT(*) FROM folders")
            indexer_state["total_folders"] = cur_folders.fetchone()[0]
            indexer_state["revision"] = indexer_state.get("revision", 0) + 1

            if (
                pruned_folders > 0
                or pruned_files > 0
                or pruned_orphaned_thumbs > 0
                or pruned_orphaned_anims > 0
                or pruned_stale_duplicates > 0
            ):
                logger.info(
                    f"[Prune] Removed {pruned_folders} deleted folders, {pruned_files} files, {pruned_orphaned_thumbs} orphaned thumbnails, {pruned_orphaned_anims} orphaned animated previews, and {pruned_stale_duplicates} stale duplicate records."
                )

    except Exception as e:
        logger.error(f"Error in prune_stale_index_entries: {e}")

    return {
        "pruned_folders": pruned_folders,
        "pruned_files": pruned_files,
        "pruned_orphaned_thumbs": pruned_orphaned_thumbs,
        "pruned_orphaned_anims": pruned_orphaned_anims,
        "pruned_stale_duplicates": pruned_stale_duplicates,
    }


def deep_clean_database():
    from media_library.services.zip.core import split_zip_path

    logger.info("[Deep Clean] Starting deep scan of database for missing files...")
    indexer_state["status"] = "crawling"
    indexer_state["current_folder"] = "Deep cleaning DB (checking missing files)..."
    try:
        with db_conn() as conn:
            rows = conn.execute("SELECT path FROM files").fetchall()
            paths = [r["path"] for r in rows]

        logger.info(
            f"[Deep Clean] Checking existence of {len(paths)} files concurrently..."
        )
        missing = []
        checked = 0
        lock = threading.Lock()

        def _check(p):
            nonlocal checked
            zip_p, inner_p = split_zip_path(p)
            target = zip_p if (zip_p and inner_p) else p
            is_missing = False
            try:
                if not (os.path.isdir(target) or os.path.isfile(target)):
                    is_missing = True
            except OSError:
                is_missing = True
            with lock:
                if is_missing:
                    missing.append(p)
                checked += 1
                if checked % 1000 == 0:
                    indexer_state["current_folder"] = (
                        f"Deep cleaning DB (checked {checked}/{len(paths)})..."
                    )

        with ThreadPoolExecutor(max_workers=16) as ex:
            ex.map(_check, paths)

        logger.info(f"[Deep Clean] Found {len(missing)} potentially missing files.")
        indexer_state["current_folder"] = (
            f"Deep cleaning DB (verifying {len(missing)} missing files)..."
        )

        removed = 0
        for i, p in enumerate(missing):
            zip_p, inner_p = split_zip_path(p)
            target = zip_p if (zip_p and inner_p) else p
            if _path_confirmed_gone(target, attempts=2, delay=0.1):
                remove_file_from_index(p)
                removed += 1
            if i % 50 == 0:
                indexer_state["current_folder"] = (
                    f"Deep cleaning DB (verified {i}/{len(missing)} missing files)..."
                )

        logger.info(
            f"[Deep Clean] Removed {removed} confirmed missing files. Running stale index prune..."
        )
        indexer_state["current_folder"] = "Deep cleaning DB (pruning stale entries)..."
        prune_stale_index_entries(clean_orphaned_thumbnails=True, force=True)
        logger.info("[Deep Clean] Deep clean finished.")
    finally:
        indexer_state["status"] = "idle"
        indexer_state["current_folder"] = ""
