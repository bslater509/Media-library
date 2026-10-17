"""Resolving duplicate groups: keep one, delete the rest."""

import logging
import os
from collections import defaultdict
from typing import Any

from fastapi import HTTPException

from media_library.core.state import (
    format_size,
    get_thumbnail_cache_path,
)
from media_library.db.connection import db_conn
from media_library.db.indexer import remove_file_from_index
from media_library.services.media_service import validate_backup_path

logger = logging.getLogger(__name__)

from media_library.services.dedup_common import (
    _files_have_identical_content,
    get_ignored_pairs_set,
    normalize_pair,
    score_duplicate_file,
)


def resolve_duplicate_group(keep_path: str, delete_paths: list[str]) -> dict[str, Any]:
    """Keeps the specified file and safely removes selected duplicate files."""
    keep_real = validate_backup_path(keep_path)
    if not os.path.exists(keep_real):
        raise HTTPException(status_code=404, detail="The file to keep was not found.")

    deleted = []
    failed = []
    for dp in delete_paths:
        try:
            d_real = validate_backup_path(dp)
            if d_real == keep_real:
                continue
            if os.path.exists(d_real) and os.path.isfile(d_real):
                thumb = get_thumbnail_cache_path(d_real)
                if thumb.exists():
                    try:
                        thumb.unlink()
                    except OSError:
                        pass
                os.remove(d_real)
                remove_file_from_index(d_real)
                deleted.append(dp)
            else:
                remove_file_from_index(d_real)
                deleted.append(dp)
        except Exception as e:
            failed.append({"path": dp, "error": str(e)})

    return {"success": True, "kept": keep_path, "deleted": deleted, "failed": failed}


def autodelete_verified_duplicates() -> dict[str, Any]:
    with db_conn() as conn:
        rows = conn.execute("""
            SELECT path, name, folder_path, size, size_formatted, mtime, quick_hash
            FROM files
            WHERE size > 0 
              AND quick_hash IS NOT NULL 
              AND quick_hash != '' 
              AND quick_hash != 'unhashable'
            ORDER BY size DESC
        """).fetchall()

    hash_groups = defaultdict(list)
    for r in rows:
        hash_groups[(r["size"], r["quick_hash"])].append(dict(r))

    ignored_pairs = get_ignored_pairs_set()

    resolved_groups = 0
    deleted_count = 0
    reclaimed_bytes = 0
    deleted_paths = []
    failed_deletions = []

    for (sz, qh), files in hash_groups.items():
        if len(files) < 2:
            continue

        scored = []
        for f in files:
            score = score_duplicate_file(f["name"], f["folder_path"], f["mtime"] or 0)
            scored.append((score, f))
        scored.sort(key=lambda x: x[0], reverse=True)

        keep_file = scored[0][1]
        keep_real = validate_backup_path(keep_file["path"])

        if not os.path.exists(keep_real):
            continue

        group_deleted = 0
        for _, file_to_delete in scored[1:]:
            dp = file_to_delete["path"]
            # Honour the user's ignore-list: never auto-delete a pair they marked false-positive.
            if normalize_pair(keep_file["path"], dp) in ignored_pairs:
                continue
            try:
                d_real = validate_backup_path(dp)
                if d_real == keep_real:
                    continue
                if os.path.exists(d_real) and os.path.isfile(d_real):
                    # quick_hash is a head-only hash; verify full contents before deleting.
                    if not _files_have_identical_content(keep_real, d_real):
                        continue
                    thumb = get_thumbnail_cache_path(d_real)
                    if thumb.exists():
                        try:
                            thumb.unlink()
                        except OSError:
                            pass
                    os.remove(d_real)
                    remove_file_from_index(d_real)
                    group_deleted += 1
                    deleted_count += 1
                    reclaimed_bytes += sz
                    deleted_paths.append(dp)
                else:
                    remove_file_from_index(d_real)
                    group_deleted += 1
                    deleted_count += 1
                    deleted_paths.append(dp)
            except Exception as e:
                failed_deletions.append({"path": dp, "error": str(e)})

        if group_deleted > 0:
            resolved_groups += 1

    return {
        "success": True,
        "resolved_groups": resolved_groups,
        "deleted_count": deleted_count,
        "reclaimed_bytes": reclaimed_bytes,
        "reclaimed_formatted": format_size(reclaimed_bytes),
        "deleted_paths": deleted_paths,
        "failed": failed_deletions,
    }
