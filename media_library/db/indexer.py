"""Media library filesystem scanning, folder indexing, and pruning."""

import os
import time
import hashlib
import asyncio
from typing import Optional, List, Dict, Any, Set
from pathlib import Path

from media_library.core.constants import VIDEO_EXTENSIONS, IMAGE_EXTENSIONS
from media_library.core.config import (
    DEFAULT_DESTINATION_DIR,
    THUMBNAIL_CACHE_DIR,
)
from media_library.core.state import (
    state,
    format_size,
    get_thumbnail_cache_path,
)
from media_library.db.connection import get_db

# Indexer State & Background Crawler
indexer_state: Dict[str, Any] = {
    "status": "idle",  # "crawling" | "idle"
    "scanned_folders": 0,
    "total_folders": 0,
    "scanned_files": 0,
    "total_files": 0,
    "current_folder": "",
    "last_synced": None,
}
indexer_lock = asyncio.Lock()

def _scan_root_dirs(path: str) -> List[str]:
    root_dirs = []
    try:
        with os.scandir(path) as entries:
            for entry in entries:
                if entry.is_dir(follow_symlinks=False) and not entry.name.startswith("."):
                    root_dirs.append(entry.path)
        root_dirs.sort(key=lambda x: os.path.basename(x).lower())
    except Exception as e:
        print(f"Error scanning root dirs from {path}: {e}")
    return root_dirs

def index_folder(folder_path: str, base_dir: Optional[str] = None) -> Dict[str, Any]:
    if not base_dir:
        base_dir = os.path.realpath(state.get("destination_dir") or DEFAULT_DESTINATION_DIR)
    real_folder = os.path.realpath(folder_path)
    if not os.path.exists(real_folder) or not os.path.isdir(real_folder):
        return {"file_count": 0, "subfolder_count": 0, "subfolders": []}

    folder_name = os.path.basename(real_folder)
    parent_path = os.path.dirname(real_folder) if real_folder != base_dir else None

    subfolders = []
    files = []
    try:
        with os.scandir(real_folder) as entries:
            for entry in entries:
                if entry.name.startswith("."):
                    continue
                try:
                    if entry.is_dir(follow_symlinks=False):
                        subfolders.append({
                            "path": entry.path,
                            "name": entry.name
                        })
                    elif entry.is_file(follow_symlinks=False):
                        ext = os.path.splitext(entry.name)[1].lower()
                        is_vid = ext in VIDEO_EXTENSIONS
                        is_img = ext in IMAGE_EXTENSIONS
                        if is_vid or is_img:
                            st = entry.stat()
                            key = f"{entry.path}_{st.st_mtime}_{st.st_size}"
                            h = hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
                            has_thumb = 1 if (THUMBNAIL_CACHE_DIR / f"thumb_{h}.jpg").exists() else 0
                            files.append((
                                entry.path,
                                entry.name,
                                real_folder,
                                "video" if is_vid else "image",
                                ext,
                                st.st_size,
                                format_size(st.st_size),
                                st.st_mtime,
                                1 if (is_vid and ext != ".mp4") else 0,
                                has_thumb
                            ))
                except (PermissionError, OSError):
                    continue
    except Exception as e:
        print(f"Error scanning folder for index {real_folder}: {e}")
        return {"file_count": 0, "subfolder_count": 0, "subfolders": []}

    conn = get_db()
    try:
        existing_opt = {}
        try:
            cur = conn.execute("SELECT path, webp_optimized FROM files WHERE folder_path = ?", (real_folder,))
            for r in cur.fetchall():
                existing_opt[r[0]] = r[1]
        except Exception:
            pass

        with conn:
            conn.execute("""
            INSERT INTO folders (path, name, parent_path, file_count, subfolder_count, last_scanned)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(path) DO UPDATE SET
                name=excluded.name,
                parent_path=excluded.parent_path,
                file_count=excluded.file_count,
                subfolder_count=excluded.subfolder_count,
                last_scanned=excluded.last_scanned
            """, (real_folder, folder_name, parent_path, len(files), len(subfolders), time.time()))

            conn.execute("DELETE FROM files WHERE folder_path = ?", (real_folder,))
            if files:
                file_rows = []
                for f in files:
                    fpath, fname, fdir, ftype, fext, fsize, fsize_fmt, fmtime, ftrans, fthumb = f
                    prev_val = existing_opt.get(fpath)
                    if prev_val is not None and prev_val != 0:
                        opt_val = prev_val
                    elif fext == ".webp":
                        opt_val = 1
                    else:
                        opt_val = 0
                    file_rows.append((fpath, fname, fdir, ftype, fext, fsize, fsize_fmt, fmtime, ftrans, fthumb, opt_val))

                conn.executemany("""
                INSERT INTO files (path, name, folder_path, type, ext, size, size_formatted, mtime, needs_transcode, has_thumb, webp_optimized)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, file_rows)
    except Exception as e:
        print(f"DB error indexing {real_folder}: {e}")
    finally:
        conn.close()

    return {"file_count": len(files), "subfolder_count": len(subfolders), "subfolders": subfolders}

def add_file_to_index(file_path: str):
    try:
        if not os.path.exists(file_path) or not os.path.isfile(file_path):
            return
        ext = os.path.splitext(file_path)[1].lower()
        is_vid = ext in VIDEO_EXTENSIONS
        is_img = ext in IMAGE_EXTENSIONS
        if not is_vid and not is_img:
            return
        folder_path = os.path.dirname(file_path)
        fname = os.path.basename(file_path)
        st = os.stat(file_path)

        conn = get_db()
        with conn:
            has_thumb = 1 if get_thumbnail_cache_path(file_path).exists() else 0
            conn.execute("""
            INSERT OR REPLACE INTO files (path, name, folder_path, type, ext, size, size_formatted, mtime, needs_transcode, has_thumb)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                file_path,
                fname,
                folder_path,
                "video" if is_vid else "image",
                ext,
                st.st_size,
                format_size(st.st_size),
                st.st_mtime,
                1 if (is_vid and ext != ".mp4") else 0,
                has_thumb
            ))
            cur = conn.execute("SELECT COUNT(*) FROM files WHERE folder_path = ?", (folder_path,))
            cnt = cur.fetchone()[0]
            conn.execute("UPDATE folders SET file_count = ?, last_scanned = ? WHERE path = ?", (cnt, time.time(), folder_path))
        conn.close()
    except Exception as e:
        print(f"Error adding {file_path} to index: {e}")

def remove_file_from_index(file_path: str):
    try:
        folder_path = os.path.dirname(file_path)
        conn = get_db()
        with conn:
            conn.execute("DELETE FROM files WHERE path = ?", (file_path,))
            conn.execute("DELETE FROM ignored_duplicates WHERE path1 = ? OR path2 = ?", (file_path, file_path))
            cur = conn.execute("SELECT COUNT(*) FROM files WHERE folder_path = ?", (folder_path,))
            cnt = cur.fetchone()[0]
            conn.execute("UPDATE folders SET file_count = ?, last_scanned = ? WHERE path = ?", (cnt, time.time(), folder_path))
        conn.close()
    except Exception as e:
        print(f"Error removing {file_path} from index: {e}")

def update_file_in_index(old_path: str, new_path: str, new_name: str, new_size: Optional[int] = None, new_ext: Optional[str] = None):
    try:
        folder_path = os.path.dirname(new_path)
        ext = new_ext or os.path.splitext(new_name)[1].lower()
        is_vid = ext in VIDEO_EXTENSIONS
        st_size = new_size if new_size is not None else (os.path.getsize(new_path) if os.path.exists(new_path) else 0)
        mtime = os.path.getmtime(new_path) if os.path.exists(new_path) else time.time()

        conn = get_db()
        with conn:
            conn.execute("DELETE FROM files WHERE path = ?", (old_path,))
            has_thumb = 1 if get_thumbnail_cache_path(new_path).exists() else 0
            conn.execute("""
            INSERT OR REPLACE INTO files (path, name, folder_path, type, ext, size, size_formatted, mtime, needs_transcode, has_thumb)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                new_path,
                new_name,
                folder_path,
                "video" if is_vid else "image",
                ext,
                st_size,
                format_size(st_size),
                mtime,
                1 if (is_vid and ext != ".mp4") else 0,
                has_thumb
            ))
            cur = conn.execute("SELECT COUNT(*) FROM files WHERE folder_path = ?", (folder_path,))
            cnt = cur.fetchone()[0]
            conn.execute("UPDATE folders SET file_count = ?, last_scanned = ? WHERE path = ?", (cnt, time.time(), folder_path))
        conn.close()
    except Exception as e:
        print(f"Error updating {old_path} -> {new_path} in index: {e}")

def prune_stale_index_entries(
    base_dir: Optional[str] = None,
    visited_folders: Optional[Set[str]] = None,
    clean_orphaned_thumbnails: bool = True
) -> Dict[str, Any]:
    resolved_base = os.path.realpath(base_dir or state.get("destination_dir") or DEFAULT_DESTINATION_DIR)
    if not os.path.exists(resolved_base):
        print(f"[Prune] Base directory {resolved_base} is not accessible. Aborting prune to prevent accidental data loss.")
        return {
            "pruned_folders": 0,
            "pruned_files": 0,
            "pruned_orphaned_thumbs": 0,
            "pruned_stale_duplicates": 0,
            "aborted": True,
            "reason": "Base directory unmounted or missing"
        }

    conn = get_db()
    pruned_folders = 0
    pruned_files = 0
    pruned_orphaned_thumbs = 0
    pruned_stale_duplicates = 0

    try:
        cur = conn.execute("SELECT path FROM folders")
        db_folders = [r["path"] for r in cur.fetchall()]

        stale_folders = []
        for fp in db_folders:
            is_under_base = (fp == resolved_base or fp.startswith(resolved_base + os.sep))
            if is_under_base:
                if visited_folders is not None:
                    if fp not in visited_folders:
                        if not os.path.exists(fp):
                            stale_folders.append(fp)
                else:
                    if not os.path.exists(fp):
                        stale_folders.append(fp)

        thumb_dir = THUMBNAIL_CACHE_DIR
        for sf in stale_folders:
            cur = conn.execute("SELECT path, mtime, size FROM files WHERE folder_path = ?", (sf,))
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
            pruned_folders += 1
            pruned_files += len(rows)

        with conn:
            cur = conn.execute("""
                DELETE FROM ignored_duplicates
                WHERE path1 NOT IN (SELECT path FROM files)
                   OR path2 NOT IN (SELECT path FROM files)
            """)
            pruned_stale_duplicates = cur.rowcount

        if clean_orphaned_thumbnails and thumb_dir.exists():
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

            for t_file in thumb_dir.glob("thumb_*.jpg"):
                if t_file.name not in valid_thumb_names:
                    try:
                        t_file.unlink(missing_ok=True)
                        pruned_orphaned_thumbs += 1
                    except OSError:
                        pass

        # Invalidate visual duplicates cache if available
        try:
            from media_library.services.deduplication_service import invalidate_visual_cache
            invalidate_visual_cache()
        except ImportError:
            pass

        cur = conn.execute("SELECT COUNT(*) FROM files")
        indexer_state["total_files"] = cur.fetchone()[0]
        cur_folders = conn.execute("SELECT COUNT(*) FROM folders")
        indexer_state["total_folders"] = cur_folders.fetchone()[0]

        if pruned_folders > 0 or pruned_files > 0 or pruned_orphaned_thumbs > 0 or pruned_stale_duplicates > 0:
            print(f"[Prune] Removed {pruned_folders} deleted folders, {pruned_files} files, {pruned_orphaned_thumbs} orphaned thumbnails, and {pruned_stale_duplicates} stale duplicate records.")

    except Exception as e:
        print(f"Error in prune_stale_index_entries: {e}")
    finally:
        conn.close()

    return {
        "pruned_folders": pruned_folders,
        "pruned_files": pruned_files,
        "pruned_orphaned_thumbs": pruned_orphaned_thumbs,
        "pruned_stale_duplicates": pruned_stale_duplicates,
    }

async def run_background_crawler():
    async with indexer_lock:
        indexer_state["status"] = "crawling"
        indexer_state["scanned_folders"] = 0
        indexer_state["scanned_files"] = 0
        base_dir = os.path.realpath(state.get("destination_dir") or DEFAULT_DESTINATION_DIR)
        if not os.path.exists(base_dir):
            indexer_state["status"] = "idle"
            return

        try:
            root_dirs = await asyncio.to_thread(_scan_root_dirs, base_dir)
            indexer_state["total_folders"] = len(root_dirs)

            dirs_queue = list(root_dirs)
            visited_folders: Set[str] = set()
            crawler_sem = asyncio.Semaphore(4)

            async def _crawl_single(folder_path: str):
                async with crawler_sem:
                    indexer_state["current_folder"] = os.path.basename(folder_path)
                    res = await asyncio.to_thread(index_folder, folder_path, base_dir)
                    indexer_state["scanned_folders"] += 1
                    if isinstance(res, dict):
                        indexer_state["scanned_files"] += res.get("file_count", 0)
                    return res

            while dirs_queue:
                batch = []
                while dirs_queue and len(batch) < 8:
                    f = dirs_queue.pop(0)
                    batch.append(f)
                    visited_folders.add(os.path.realpath(f))

                results = await asyncio.gather(*[_crawl_single(f) for f in batch], return_exceptions=True)
                for res in results:
                    if isinstance(res, dict) and res.get("subfolders"):
                        for sub in res["subfolders"]:
                            dirs_queue.append(sub["path"])
                            indexer_state["total_folders"] += 1

                await asyncio.sleep(0.04)

            # Auto-prune stale entries, deleted folders & orphaned thumbnails
            indexer_state["current_folder"] = "Pruning deleted folders & cache..."
            await asyncio.to_thread(prune_stale_index_entries, base_dir, visited_folders, True)

            conn = get_db()
            cur = conn.execute("SELECT COUNT(*) FROM files")
            indexer_state["total_files"] = cur.fetchone()[0]
            cur_folders = conn.execute("SELECT COUNT(*) FROM folders")
            indexer_state["total_folders"] = cur_folders.fetchone()[0]
            conn.close()

            indexer_state["last_synced"] = time.time()
        except Exception as e:
            print(f"Error in background crawler: {e}")
        finally:
            indexer_state["status"] = "idle"
            indexer_state["current_folder"] = ""
