"""Visual duplicate clustering (pHash) and group filtering."""

import logging
import os
import time
import urllib.parse
from collections import defaultdict
from typing import Any

from media_library.core.config import DEFAULT_DESTINATION_DIR
from media_library.core.constants import NATIVE_EXTENSIONS
from media_library.core.state import (
    format_size,
    state,
)
from media_library.db.connection import db_conn
from media_library.db.indexer import remove_file_from_index

logger = logging.getLogger(__name__)

from media_library.services.dedup_common import (
    normalize_pair,
    score_duplicate_file,
    visual_duplicates_cache,
)


def find_visual_duplicates(
    limit: int = 50, offset: int = 0, max_distance: int = 2
) -> dict[str, Any]:
    base_dir = os.path.realpath(state.get("destination_dir") or DEFAULT_DESTINATION_DIR)
    with db_conn() as conn:
        cur_hash_count_row = conn.execute(
            "SELECT COUNT(*) FROM files WHERE type = 'video' AND thumb_hash IS NOT NULL AND thumb_hash NOT IN ('no_thumb', 'blank_thumb')"
        ).fetchone()
        cur_hash_count = cur_hash_count_row[0] if cur_hash_count_row else 0
        cur_ignored_count_row = conn.execute(
            "SELECT COUNT(*) FROM ignored_duplicates"
        ).fetchone()
        cur_ignored_count = cur_ignored_count_row[0] if cur_ignored_count_row else 0

        now = time.time()
        if (
            now - visual_duplicates_cache["timestamp"] < 30.0
            and visual_duplicates_cache["db_hash_count"] == cur_hash_count
            and visual_duplicates_cache["ignored_count"] == cur_ignored_count
        ):
            all_groups = visual_duplicates_cache["groups"]
            paginated = all_groups[offset : offset + limit]
            paginated = filter_and_prune_groups(paginated)
            return {
                "success": True,
                "mode": "visual",
                "total_groups": visual_duplicates_cache["total_groups"],
                "reclaimable_bytes": visual_duplicates_cache["total_reclaimable"],
                "reclaimable_formatted": format_size(
                    visual_duplicates_cache["total_reclaimable"]
                ),
                "verified_groups_count": 0,
                "verified_reclaimable_bytes": 0,
                "verified_reclaimable_formatted": "0 B",
                "groups": paginated,
                "limit": limit,
                "offset": offset,
            }

        rows = conn.execute("""
            SELECT path, name, folder_path, type, ext, size, size_formatted, mtime, has_thumb, quick_hash, thumb_hash
            FROM files
            WHERE type = 'video'
              AND thumb_hash IS NOT NULL 
              AND thumb_hash NOT IN ('no_thumb', 'blank_thumb') 
              AND length(thumb_hash) = 16
        """).fetchall()

        ignored_rows = conn.execute(
            "SELECT path1, path2 FROM ignored_duplicates WHERE match_type = 'visual'"
        ).fetchall()
        ignored_pairs = {(r["path1"], r["path2"]) for r in ignored_rows}

    DEGENERATE_HASHES = {
        "0000000000000000",
        "ffffffffffffffff",
        "f0f0f0f0f0f0f0f0",
        "f0f0f0f0f0f8f0f0",
        "0f0f0f0f0f0f0f0f",
        "a55aa55aa55aa55a",
        "0000000000000006",
        "0000000000000004",
        "0000000000000002",
        "0000000000000001",
    }
    items = []
    for r in rows:
        th = r["thumb_hash"]
        if th in DEGENERATE_HASHES:
            continue
        try:
            val = int(th, 16)
            items.append((r, val))
        except (ValueError, TypeError):
            continue

    n = len(items)
    if n < 2:
        return {
            "success": True,
            "mode": "visual",
            "total_groups": 0,
            "reclaimable_bytes": 0,
            "reclaimable_formatted": "0 B",
            "verified_groups_count": 0,
            "verified_reclaimable_bytes": 0,
            "verified_reclaimable_formatted": "0 B",
            "groups": [],
            "limit": limit,
            "offset": offset,
        }

    masks_shifts = [
        (0xFFFF, 0),
        (0xFFFF, 16),
        (0xFFFF, 32),
        (0xFFFF, 48),
    ]
    buckets = [defaultdict(list) for _ in range(4)]
    for idx, (_, h_val) in enumerate(items):
        for b, (mask, shift) in enumerate(masks_shifts):
            buckets[b][(h_val >> shift) & mask].append(idx)

    checked_pairs = set()
    adj = defaultdict(set)
    pair_distances = {}

    for b in range(4):
        for idxs in buckets[b].values():
            if len(idxs) > 1:
                for i in range(len(idxs)):
                    idx_i = idxs[i]
                    for j in range(i + 1, len(idxs)):
                        idx_j = idxs[j]
                        u, v = (idx_i, idx_j) if idx_i < idx_j else (idx_j, idx_i)
                        if (u, v) in checked_pairs:
                            continue
                        checked_pairs.add((u, v))

                        p1, p2 = normalize_pair(
                            items[u][0]["path"], items[v][0]["path"]
                        )
                        if (p1, p2) in ignored_pairs:
                            continue

                        diff = (items[u][1] ^ items[v][1]).bit_count()
                        if diff <= max_distance:
                            adj[u].add(v)
                            adj[v].add(u)
                            pair_distances[(u, v)] = diff

    remaining = set(adj.keys())
    cliques = []
    while remaining:
        pivot = max(
            remaining,
            key=lambda x: (
                len(adj[x] & remaining),
                score_duplicate_file(
                    items[x][0]["name"],
                    items[x][0]["folder_path"],
                    items[x][0]["mtime"] or 0,
                    items[x][0]["size"] or 0,
                ),
            ),
        )
        candidates = adj[pivot] & remaining
        current_clique = [pivot]
        for c in sorted(
            candidates,
            key=lambda x: (
                len(adj[x] & candidates),
                score_duplicate_file(
                    items[x][0]["name"],
                    items[x][0]["folder_path"],
                    items[x][0]["mtime"] or 0,
                    items[x][0]["size"] or 0,
                ),
            ),
            reverse=True,
        ):
            if all(c in adj[member] for member in current_clique):
                current_clique.append(c)
        if len(current_clique) >= 2:
            cliques.append(current_clique)
            for m in current_clique:
                remaining.remove(m)
        else:
            remaining.remove(pivot)

    all_groups = []
    total_reclaimable = 0

    for root_idx, member_idxs in enumerate(cliques):
        file_list = []
        for idx in member_idxs:
            fr = items[idx][0]
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
                    "thumb_hash": fr["thumb_hash"],
                    "thumb_url": f"/api/backup/thumbnail?path={urllib.parse.quote(fpath)}",
                    "media_url": media_url,
                    "score": score_duplicate_file(
                        fr["name"], fr["folder_path"], fr["mtime"] or 0, fr["size"] or 0
                    ),
                }
            )

        best_idx = max(range(len(file_list)), key=lambda i: file_list[i]["score"])
        for i, f in enumerate(file_list):
            f["is_recommended"] = i == best_idx

        group_diffs = [
            pair_distances[(min(u, v), max(u, v))]
            for i, u in enumerate(member_idxs)
            for v in member_idxs[i + 1 :]
            if (min(u, v), max(u, v)) in pair_distances
        ]
        max_dist = max(group_diffs) if group_diffs else 0
        min_dist = min(group_diffs) if group_diffs else 0
        similarity = round((1.0 - (max_dist / 64.0)) * 100.0, 1)

        reclaim_bytes = sum(f["size"] for i, f in enumerate(file_list) if i != best_idx)
        total_reclaimable += reclaim_bytes

        all_groups.append(
            {
                "group_id": f"visual_{root_idx}",
                "name": file_list[best_idx]["name"],
                "size_formatted": file_list[best_idx]["size_formatted"],
                "match_type": "visual",
                "similarity": similarity,
                "min_distance": min_dist,
                "max_distance": max_dist,
                "files_count": len(file_list),
                "reclaimable_bytes": reclaim_bytes,
                "reclaimable_formatted": format_size(reclaim_bytes),
                "suggested_keep_index": best_idx,
                "is_verified_match": False,
                "files": file_list,
            }
        )

    all_groups.sort(key=lambda g: g["reclaimable_bytes"], reverse=True)

    visual_duplicates_cache["timestamp"] = time.time()
    visual_duplicates_cache["total_groups"] = len(all_groups)
    visual_duplicates_cache["total_reclaimable"] = total_reclaimable
    visual_duplicates_cache["groups"] = all_groups
    visual_duplicates_cache["db_hash_count"] = cur_hash_count
    visual_duplicates_cache["ignored_count"] = cur_ignored_count

    paginated = all_groups[offset : offset + limit]
    paginated = filter_and_prune_groups(paginated)

    return {
        "success": True,
        "mode": "visual",
        "total_groups": len(all_groups),
        "reclaimable_bytes": total_reclaimable,
        "reclaimable_formatted": format_size(total_reclaimable),
        "verified_groups_count": 0,
        "verified_reclaimable_bytes": 0,
        "verified_reclaimable_formatted": "0 B",
        "groups": paginated,
        "limit": limit,
        "offset": offset,
    }


def _prune_missing_files_bg(missing_paths: list[str]):
    from media_library.db.indexer import _path_confirmed_gone
    from media_library.services.zip.core import split_zip_path

    for p in missing_paths:
        zip_p, inner_p = split_zip_path(p)
        target = zip_p if (zip_p and inner_p) else p
        if _path_confirmed_gone(target, attempts=2, delay=0.1):
            remove_file_from_index(p)


def filter_and_prune_groups(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from media_library.services.zip.core import split_zip_path

    valid_groups = []
    missing_paths = []

    for g in groups:
        valid_files = []
        for f in g.get("files", []):
            fpath = f["path"]
            zip_p, inner_p = split_zip_path(fpath)
            target = zip_p if (zip_p and inner_p) else fpath
            try:
                exists = os.path.exists(target)
            except OSError:
                exists = False

            if exists:
                valid_files.append(f)
            else:
                missing_paths.append(fpath)

        if len(valid_files) >= 2:
            g["files"] = valid_files
            g["files_count"] = len(valid_files)

            # Recompute reclaimable bytes
            target_size = g.get("size", 0)
            if target_size > 0:
                g["reclaimable_bytes"] = (len(valid_files) - 1) * target_size
                from media_library.core.state import format_size

                g["reclaimable_formatted"] = format_size(g["reclaimable_bytes"])

            # Recompute best index
            best_idx = max(
                range(len(valid_files)), key=lambda i: valid_files[i].get("score", 0)
            )
            for i, f in enumerate(valid_files):
                f["is_recommended"] = i == best_idx
            g["suggested_keep_index"] = best_idx

            valid_groups.append(g)

    if missing_paths:
        import threading

        threading.Thread(
            target=_prune_missing_files_bg, args=(missing_paths,), daemon=True
        ).start()

    return valid_groups
