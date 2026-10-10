"""Deduplication service supporting exact chunk hash matching and perceptual visual clustering."""

import hashlib
import logging
import os
import re
import time
import urllib.parse
from collections import defaultdict
from pathlib import Path
from typing import Any

import imagehash
from fastapi import HTTPException
from PIL import Image, ImageStat

from media_library.core.config import DEFAULT_DESTINATION_DIR
from media_library.core.constants import NATIVE_EXTENSIONS
from media_library.core.state import (
    format_size,
    get_thumbnail_cache_path,
    state,
)
from media_library.db.connection import db_conn
from media_library.db.indexer import remove_file_from_index
from media_library.services.media_service import validate_backup_path

logger = logging.getLogger(__name__)

visual_duplicates_cache: dict[str, Any] = {
    "timestamp": 0,
    "total_groups": 0,
    "total_reclaimable": 0,
    "groups": [],
    "db_hash_count": 0,
    "ignored_count": 0,
}


def invalidate_visual_cache():
    """Invalidates the in-memory visual duplicates cache."""
    visual_duplicates_cache["timestamp"] = 0


def compute_quick_hash(filepath: str, chunk_size: int = 131072) -> str | None:
    """Computes a fast 128KB head SHA-256 hash incorporating exact byte size."""
    try:
        if not os.path.exists(filepath) or not os.path.isfile(filepath):
            return None
        size = os.path.getsize(filepath)
        if size == 0:
            return "empty_file"
        hasher = hashlib.sha256()
        with open(filepath, "rb") as f:
            hasher.update(f.read(chunk_size))
        hasher.update(str(size).encode("utf-8"))
        return hasher.hexdigest()
    except Exception as e:
        logger.error(f"Error computing quick_hash for {filepath}: {e}")
        return None


def compute_thumbnail_phash(thumb_path: Path) -> str | None:
    """Computes a 64-bit DCT perceptual hash (pHash) from a cached JPEG thumbnail."""
    try:
        if not thumb_path.exists() or thumb_path.stat().st_size == 0:
            return None
        with Image.open(thumb_path) as img:
            stat = ImageStat.Stat(img)
            avg_std = sum(stat.stddev) / len(stat.stddev)
            avg_mean = sum(stat.mean) / len(stat.mean)
            if avg_std < 8.0 or avg_mean < 8.0:
                return "blank_thumb"
            return str(imagehash.phash(img))
    except Exception:
        return None


compute_thumbnail_dhash = compute_thumbnail_phash


def _files_have_identical_content(a: str, b: str) -> bool:
    """Full-content byte comparison.

    ``quick_hash`` only reads a 128 KB head chunk, so two same-size files can
    share a quick_hash while differing in the remainder. Before auto-deleting
    anything we compare the full contents to guarantee genuine duplicates.
    """
    try:
        if os.path.getsize(a) != os.path.getsize(b):
            return False
        chunk = 1024 * 1024
        with open(a, "rb") as fa, open(b, "rb") as fb:
            while True:
                ba = fa.read(chunk)
                bb = fb.read(chunk)
                if ba != bb:
                    return False
                if not ba:
                    return True
    except OSError:
        return False
    return False


def normalize_pair(p1: str, p2: str) -> tuple[str, str]:
    return (p1, p2) if p1 < p2 else (p2, p1)


def get_ignored_pairs_set(match_type: str | None = None) -> set[tuple[str, str]]:
    with db_conn() as conn:
        if match_type:
            rows = conn.execute(
                "SELECT path1, path2 FROM ignored_duplicates WHERE match_type = ?",
                (match_type,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT path1, path2 FROM ignored_duplicates"
            ).fetchall()
        return {(r["path1"], r["path2"]) for r in rows}


def score_duplicate_file(
    name: str, folder_path: str, mtime: float, size: int = 0
) -> float:
    score = 0.0
    if size > 0:
        score += size / (1024 * 1024 * 10)  # +1 point per 10MB
    if re.search(r"(_\d+|\(\d+\)|\bcopy\b|\.mp4\.mp4)", name, re.IGNORECASE):
        score -= 50.0
    if re.search(r"^[0-9a-z]{15,}_source", name, re.IGNORECASE):
        score -= 20.0
    if re.search(r"\d{4}-\d{2}-\d{2}", name):
        score += 15.0
    if " " in name or "-" in name:
        score += 5.0
    depth = len(Path(folder_path).parts)
    score += depth * 2.0
    score += mtime / 1e10
    return score


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
                "groups": all_groups[offset : offset + limit],
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

    return {
        "success": True,
        "mode": "visual",
        "total_groups": len(all_groups),
        "reclaimable_bytes": total_reclaimable,
        "reclaimable_formatted": format_size(total_reclaimable),
        "verified_groups_count": 0,
        "verified_reclaimable_bytes": 0,
        "verified_reclaimable_formatted": "0 B",
        "groups": all_groups[offset : offset + limit],
        "limit": limit,
        "offset": offset,
    }


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
