"""Backup-facing duplicate listing (content + visual)."""

import logging
import os
import urllib.parse
from typing import Any

from media_library.core.config import DEFAULT_DESTINATION_DIR
from media_library.core.constants import NATIVE_EXTENSIONS
from media_library.core.state import (
    format_size,
    state,
)
from media_library.db.connection import db_conn

logger = logging.getLogger(__name__)

from media_library.services.dedup_common import (
    get_ignored_pairs_set,
    normalize_pair,
    score_duplicate_file,
)
from media_library.services.dedup_visual import (
    filter_and_prune_groups,
    find_visual_duplicates,
)


def get_backup_duplicates(
    mode: str = "content", limit: int = 50, offset: int = 0
) -> dict[str, Any]:
    if mode == "visual":
        return find_visual_duplicates(limit=limit, offset=offset)

    with db_conn() as conn:
        base_dir = os.path.realpath(
            state.get("destination_dir") or DEFAULT_DESTINATION_DIR
        )
        ignored_pairs = get_ignored_pairs_set()

        all_candidate_groups = []
        hash_rows = conn.execute("""
            SELECT size, quick_hash, COUNT(*) as cnt
            FROM files
            WHERE size > 0 
              AND quick_hash IS NOT NULL 
              AND quick_hash != '' 
              AND quick_hash != 'unhashable'
            GROUP BY size, quick_hash
            HAVING cnt > 1
            ORDER BY size DESC
        """).fetchall()

        for h_row in hash_rows:
            target_size = h_row["size"]
            target_hash = h_row["quick_hash"]
            file_rows = conn.execute(
                """
                SELECT path, name, folder_path, type, ext, size, size_formatted, mtime, has_thumb, quick_hash
                FROM files
                WHERE size = ? AND quick_hash = ?
                ORDER BY mtime DESC
            """,
                (target_size, target_hash),
            ).fetchall()

            file_list = []
            for fr in file_rows:
                fpath = fr["path"]
                is_vid = fr["type"] == "video"
                ext = fr["ext"]
                is_native = ext in NATIVE_EXTENSIONS
                media_url = (
                    f"/api/backup/media?path={urllib.parse.quote(fpath)}"
                    if (not is_vid or is_native)
                    else f"/api/backup/transcode_stream?path={urllib.parse.quote(fpath)}"
                )
                try:
                    rel_folder = os.path.relpath(fr["folder_path"], base_dir)
                except Exception:
                    rel_folder = fr["folder_path"]
                if rel_folder == ".":
                    rel_folder = "Root"

                file_list.append(
                    {
                        "path": fpath,
                        "name": fr["name"],
                        "folder_path": fr["folder_path"],
                        "folder_display": rel_folder,
                        "type": fr["type"],
                        "ext": ext,
                        "size": fr["size"],
                        "size_formatted": fr["size_formatted"],
                        "mtime": fr["mtime"],
                        "has_thumb": bool(fr["has_thumb"]),
                        "quick_hash": fr["quick_hash"],
                        "thumb_url": f"/api/backup/thumbnail?path={urllib.parse.quote(fpath)}",
                        "media_url": media_url,
                        "score": score_duplicate_file(
                            fr["name"],
                            fr["folder_path"],
                            fr["mtime"] or 0,
                            fr["size"] or 0,
                        ),
                    }
                )

            if file_list:
                if len(file_list) >= 2 and ignored_pairs:
                    all_ignored = True
                    for i in range(len(file_list)):
                        for j in range(i + 1, len(file_list)):
                            pair = normalize_pair(
                                file_list[i]["path"], file_list[j]["path"]
                            )
                            if pair not in ignored_pairs:
                                all_ignored = False
                                break
                        if not all_ignored:
                            break
                    if all_ignored:
                        continue

                best_idx = max(
                    range(len(file_list)), key=lambda i: file_list[i]["score"]
                )
                for i, f in enumerate(file_list):
                    f["is_recommended"] = i == best_idx

                all_candidate_groups.append(
                    {
                        "group_id": f"content_{target_size}_{target_hash[:12]}",
                        "size": target_size,
                        "size_formatted": format_size(target_size),
                        "match_type": "content",
                        "files_count": len(file_list),
                        "reclaimable_bytes": (len(file_list) - 1) * target_size,
                        "reclaimable_formatted": format_size(
                            (len(file_list) - 1) * target_size
                        ),
                        "suggested_keep_index": best_idx,
                        "is_verified_match": True,
                        "files": file_list,
                    }
                )

    total_groups = len(all_candidate_groups)
    total_reclaimable = sum(g["reclaimable_bytes"] for g in all_candidate_groups)
    groups = all_candidate_groups[offset : offset + limit]
    groups = filter_and_prune_groups(groups)

    return {
        "success": True,
        "mode": "content",
        "total_groups": total_groups,
        "reclaimable_bytes": total_reclaimable,
        "reclaimable_formatted": format_size(total_reclaimable),
        "verified_groups_count": total_groups,
        "verified_reclaimable_bytes": total_reclaimable,
        "verified_reclaimable_formatted": format_size(total_reclaimable),
        "groups": groups,
        "limit": limit,
        "offset": offset,
    }
