import os
import shutil
import mimetypes
import asyncio
import socket
import re
import json
import time
import threading
import uuid
import hashlib
import sqlite3
from typing import Optional, List, Dict, Any, Set, Tuple, Union
from collections import defaultdict
from pathlib import Path
from PIL import Image
import imagehash
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException, Query, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse, Response, FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import urllib.parse
from dotenv import load_dotenv

# Constants & Paths
BASE_DIR = Path(__file__).resolve().parent

# Load environment configuration (.env) if present
load_dotenv(BASE_DIR / ".env")

STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"
CONFIG_FILE = BASE_DIR / "config.json"

STATIC_DIR.mkdir(exist_ok=True)
TEMPLATES_DIR.mkdir(exist_ok=True)

# Runtime data & cache directories (configurable via DATA_DIR, TRANSCODE_DIR, THUMBNAIL_DIR)
DATA_DIR = Path(os.environ.get("DATA_DIR", BASE_DIR / "data")).resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)

TRANSCODE_TEMP_DIR = Path(os.environ.get("TRANSCODE_DIR", DATA_DIR / "transcodes")).resolve()
TRANSCODE_TEMP_DIR.mkdir(parents=True, exist_ok=True)

THUMBNAIL_CACHE_DIR = Path(os.environ.get("THUMBNAIL_DIR", DATA_DIR / "thumbnails")).resolve()
THUMBNAIL_CACHE_DIR.mkdir(parents=True, exist_ok=True)

WEBP_STAGING_DIR = Path(os.environ.get("WEBP_STAGING_DIR", DATA_DIR / "webp_staging")).resolve()
WEBP_STAGING_DIR.mkdir(parents=True, exist_ok=True)
WEBP_WORKER_CONCURRENCY = max(1, int(os.environ.get("WEBP_WORKER_CONCURRENCY", "1")))
WEBP_QUALITY = max(1, min(100, int(os.environ.get("WEBP_QUALITY", "85"))))
WEBP_SKIP_LARGER = os.environ.get("WEBP_SKIP_LARGER", "false").lower() in ("true", "1", "yes")
WEBP_AUTO_OPTIMIZE = os.environ.get("WEBP_AUTO_OPTIMIZE", "true").lower() in ("true", "1", "yes")

# Supported video extensions
VIDEO_EXTENSIONS = {
    ".mp4", ".m4v", ".mkv", ".webm", ".avi", ".mov", ".wmv",
    ".flv", ".ts", ".m2ts", ".mpg", ".mpeg", ".vob", ".3gp", ".asf"
}

# Supported image extensions
IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".avif", ".svg"
}

# Browser-native streaming extensions
NATIVE_EXTENSIONS = {".mp4", ".m4v", ".webm", ".mov"}

def get_thumbnail_cache_path(file_path: str, mtime: Optional[float] = None, size: Optional[int] = None) -> Path:
    if mtime is not None and size is not None:
        key = f"{file_path}_{mtime}_{size}"
    else:
        try:
            st = os.stat(file_path)
            key = f"{file_path}_{st.st_mtime}_{st.st_size}"
        except OSError:
            key = file_path
    h = hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
    return THUMBNAIL_CACHE_DIR / f"thumb_{h}.jpg"

# Potential default destination directories (fallback detection)
CANDIDATE_DESTINATIONS = [
    str(Path.home() / "Videos"),
    str(Path.home() / "Movies"),
    "/media/library",
]

def detect_default_destination() -> str:
    env_dest = os.environ.get("LIBRARY_DIR", "").strip()
    if env_dest:
        dest_path = Path(env_dest).resolve()
        try:
            dest_path.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            print(f"Warning: could not create or stat destination directory {dest_path}: {e}")
        return str(dest_path)
    for path in CANDIDATE_DESTINATIONS:
        if os.path.exists(path) and os.path.isdir(path):
            return path
    fallback = DATA_DIR / "media"
    fallback.mkdir(parents=True, exist_ok=True)
    return str(fallback)

DEFAULT_DESTINATION_DIR = detect_default_destination()

# Persistent configuration
def load_config_data() -> Dict[str, Any]:
    env_source = os.environ.get("INCOMING_DIR", "").strip()
    config = {
        "recent_destinations": [],
        "source_dir": env_source,
    }
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                config["recent_destinations"] = data.get("recent_destinations", [])
                if not env_source and data.get("source_dir"):
                    config["source_dir"] = data.get("source_dir", "")
        except Exception as e:
            print(f"Error loading config.json: {e}")
    return config

def save_config_data():
    try:
        data = {
            "recent_destinations": state.get("recent_destinations", [])[:15],
            "source_dir": state.get("source_dir", ""),
        }
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"Error saving config.json: {e}")

saved_cfg = load_config_data()

# Application state
state = {
    "source_dir": saved_cfg.get("source_dir", ""),
    "destination_dir": DEFAULT_DESTINATION_DIR,
    "video_queue": [],  # List of dicts: { id, filename, path, size, mtime, ext, needs_transcode }
    "undo_stack": [],   # List of dicts: { type, task_id, source_path, target_path, filename, destination_folder }
    "recent_destinations": saved_cfg.get("recent_destinations", []),
}

# Transfer Queue & Background Worker
transfer_queue: asyncio.Queue = asyncio.Queue()
active_transfer: Optional[Dict[str, Any]] = None
transfers_history: List[Dict[str, Any]] = []
transfer_items_map: Dict[str, Dict[str, Any]] = {}

# Pre-Transcode Queue & Background Worker
transcode_queue: asyncio.Queue = asyncio.Queue()
active_transcode: Optional[Dict[str, Any]] = None
transcode_history: List[Dict[str, Any]] = []
transcode_items_map: Dict[str, Dict[str, Any]] = {}
transcode_paused: bool = False
transcode_pause_event: asyncio.Event = asyncio.Event()
transcode_pause_event.set()

def format_size(size_bytes: Union[int, float]) -> str:
    try:
        size_val = float(size_bytes)
    except (TypeError, ValueError):
        return "0 B"
    is_neg = size_val < 0
    size_val = abs(size_val)
    prefix = "-" if is_neg else ""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size_val < 1024.0:
            return f"{prefix}{size_val:.1f} {unit}"
        size_val /= 1024.0
    return f"{prefix}{size_val:.1f} PB"

def make_video_meta(path: str) -> Optional[Dict[str, Any]]:
    try:
        if not os.path.exists(path):
            return None
        name = os.path.basename(path)
        ext = os.path.splitext(name)[1].lower()
        stat = os.stat(path)
        return {
            "id": name,
            "filename": name,
            "path": path,
            "size": stat.st_size,
            "size_formatted": format_size(stat.st_size),
            "mtime": stat.st_mtime,
            "ext": ext,
            "needs_transcode": ext not in NATIVE_EXTENSIONS,
        }
    except Exception:
        return None

# ==========================================
# SQLite Search Index for Media Library
# ==========================================
env_db = os.environ.get("DB_PATH", "").strip()
if env_db:
    DB_PATH = Path(env_db).resolve()
elif (BASE_DIR / "backup_index.db").exists():
    DB_PATH = BASE_DIR / "backup_index.db"
else:
    DB_PATH = DATA_DIR / "library.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

def init_backup_db():
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("""
    CREATE TABLE IF NOT EXISTS folders (
        path TEXT PRIMARY KEY,
        name TEXT,
        parent_path TEXT,
        file_count INTEGER DEFAULT 0,
        subfolder_count INTEGER DEFAULT 0,
        last_scanned REAL DEFAULT 0
    )""")
    conn.execute("""
    CREATE TABLE IF NOT EXISTS files (
        path TEXT PRIMARY KEY,
        name TEXT,
        folder_path TEXT,
        type TEXT,
        ext TEXT,
        size INTEGER,
        size_formatted TEXT,
        mtime REAL,
        needs_transcode INTEGER DEFAULT 0,
        has_thumb INTEGER DEFAULT 0
    )""")
    try:
        conn.execute("ALTER TABLE files ADD COLUMN has_thumb INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE files ADD COLUMN quick_hash TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE files ADD COLUMN thumb_hash TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE files ADD COLUMN webp_optimized INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    conn.execute("CREATE INDEX IF NOT EXISTS idx_files_folder ON files(folder_path)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_files_name ON files(name)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_files_type ON files(type)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_files_has_thumb ON files(has_thumb)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_files_size ON files(size)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_files_quick_hash ON files(quick_hash)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_files_thumb_hash ON files(thumb_hash)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_files_webp_optimized ON files(webp_optimized)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_folders_parent ON folders(parent_path)")

    conn.execute("""
    CREATE TABLE IF NOT EXISTS webp_stats (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        saved_bytes INTEGER DEFAULT 0,
        converted_count INTEGER DEFAULT 0,
        skipped_count INTEGER DEFAULT 0
    )""")
    conn.execute("INSERT OR IGNORE INTO webp_stats (id, saved_bytes, converted_count, skipped_count) VALUES (1, 0, 0, 0)")
    conn.execute("UPDATE files SET webp_optimized = 1 WHERE ext = '.webp' AND webp_optimized = 0")

    conn.execute("""
    CREATE TABLE IF NOT EXISTS ignored_duplicates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        path1 TEXT NOT NULL,
        path2 TEXT NOT NULL,
        match_type TEXT NOT NULL,
        created_at REAL NOT NULL,
        UNIQUE(path1, path2)
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ignored_p1_p2 ON ignored_duplicates(path1, path2)")

    conn.commit()
    conn.close()

def compute_quick_hash(filepath: str, chunk_size: int = 131072) -> Optional[str]:
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
        print(f"Error computing quick_hash for {filepath}: {e}")
        return None

def compute_thumbnail_dhash(thumb_path: Path) -> Optional[str]:
    """Computes a 64-bit difference hash (dHash) from a cached JPEG thumbnail."""
    try:
        if not thumb_path.exists() or thumb_path.stat().st_size == 0:
            return None
        with Image.open(thumb_path) as img:
            return str(imagehash.dhash(img))
    except Exception:
        return None

def normalize_pair(p1: str, p2: str) -> Tuple[str, str]:
    return (p1, p2) if p1 < p2 else (p2, p1)

def get_ignored_pairs_set() -> Set[Tuple[str, str]]:
    conn = get_db()
    try:
        rows = conn.execute("SELECT path1, path2 FROM ignored_duplicates").fetchall()
        return {(r["path1"], r["path2"]) for r in rows}
    finally:
        conn.close()

def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    return conn

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

# Indexer State & Background Crawler
indexer_state = {
    "status": "idle", # "crawling" | "idle"
    "scanned_folders": 0,
    "total_folders": 0,
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

async def run_background_crawler():
    async with indexer_lock:
        indexer_state["status"] = "crawling"
        indexer_state["scanned_folders"] = 0
        base_dir = os.path.realpath(state.get("destination_dir") or DEFAULT_DESTINATION_DIR)
        if not os.path.exists(base_dir):
            indexer_state["status"] = "idle"
            return

        try:
            root_dirs = await asyncio.to_thread(_scan_root_dirs, base_dir)
            indexer_state["total_folders"] = len(root_dirs)

            dirs_queue = list(root_dirs)
            while dirs_queue:
                folder = dirs_queue.pop(0)
                indexer_state["current_folder"] = os.path.basename(folder)
                
                res = await asyncio.to_thread(index_folder, folder, base_dir)
                indexer_state["scanned_folders"] += 1

                if res.get("subfolders"):
                    for sub in res["subfolders"]:
                        dirs_queue.append(sub["path"])
                        indexer_state["total_folders"] += 1

                await asyncio.sleep(0.04)

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

# ==============================================================================
# Background Thumbnail Generator & Worker
# ==============================================================================
thumbnail_worker_state = {
    "status": "idle",  # "generating" | "idle" | "paused"
    "total_files": 0,
    "cached_count": 0,
    "pending_count": 0,
    "percent": 0.0,
    "current_file": "",
    "current_folder": "",
    "processed_count": 0,
}
thumbnail_pause_event = asyncio.Event()
thumbnail_pause_event.set()
priority_thumbnail_queue: asyncio.Queue = asyncio.Queue()

def sync_thumbnail_db_cache():
    """Fast check: cross-reference existing cached thumbnail files against files in SQLite."""
    try:
        if not THUMBNAIL_CACHE_DIR.exists():
            return
        existing_thumbs = set(p.name for p in THUMBNAIL_CACHE_DIR.glob("thumb_*.jpg"))
        if not existing_thumbs:
            return

        conn = get_db()
        cur = conn.execute("SELECT path, mtime, size FROM files WHERE has_thumb = 0")
        rows = cur.fetchall()
        matched = []
        for r in rows:
            key = f"{r['path']}_{r['mtime']}_{r['size']}"
            h = hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
            if f"thumb_{h}.jpg" in existing_thumbs:
                matched.append((r["path"],))
        if matched:
            with conn:
                conn.executemany("UPDATE files SET has_thumb = 1 WHERE path = ?", matched)
        conn.close()
    except Exception as e:
        print(f"Error syncing thumbnail cache: {e}")

def enqueue_folder_thumbnails(folder_path: str):
    """Enqueue missing thumbnails for a specific folder with priority."""
    try:
        conn = get_db()
        cur = conn.execute(
            "SELECT path, type, name, folder_path FROM files WHERE folder_path = ? AND has_thumb = 0",
            (folder_path,)
        )
        rows = cur.fetchall()
        conn.close()
        for r in rows:
            priority_thumbnail_queue.put_nowait({
                "path": r["path"],
                "type": r["type"],
                "name": r["name"],
                "folder_path": r["folder_path"]
            })
    except Exception as e:
        print(f"Error enqueuing folder thumbnails: {e}")

async def thumbnail_worker():
    await asyncio.to_thread(sync_thumbnail_db_cache)
    try:
        conn = get_db()
        c_tot = conn.execute("SELECT count(*) FROM files").fetchone()[0]
        c_pending = conn.execute("SELECT count(*) FROM files WHERE has_thumb = 0").fetchone()[0]
        c_cached = conn.execute("SELECT count(*) FROM files WHERE has_thumb = 1").fetchone()[0]
        conn.close()
        thumbnail_worker_state["total_files"] = c_tot
        thumbnail_worker_state["cached_count"] = c_cached
        thumbnail_worker_state["pending_count"] = c_pending
        thumbnail_worker_state["percent"] = round(((c_tot - c_pending) / c_tot * 100.0), 1) if c_tot > 0 else 100.0
    except Exception as e:
        print(f"Error initializing thumbnail stats: {e}")

    concurrency_sem = asyncio.Semaphore(5)

    async def process_item(item: Dict[str, Any]) -> tuple:
        async with concurrency_sem:
            fpath = item["path"]
            is_vid = (item["type"] == "video")
            thumbnail_worker_state["current_file"] = item.get("name", os.path.basename(fpath))
            thumbnail_worker_state["current_folder"] = os.path.basename(item.get("folder_path", os.path.dirname(fpath)))
            ok = await generate_thumbnail(fpath, is_video=is_vid, update_db=False)
            return fpath, bool(ok)

    async def execute_batch(items: List[Dict[str, Any]]):
        if not items:
            return
        thumbnail_worker_state["status"] = "generating"
        results = await asyncio.gather(*[process_item(it) for it in items], return_exceptions=True)

        successes = []
        failures = []
        for res in results:
            if isinstance(res, tuple):
                fpath, ok = res
                if ok:
                    successes.append((fpath,))
                else:
                    failures.append((fpath,))
            elif isinstance(res, Exception):
                print(f"Exception processing thumbnail item: {res}")

        if successes or failures:
            conn = get_db()
            try:
                with conn:
                    if successes:
                        conn.executemany("UPDATE files SET has_thumb = 1 WHERE path = ?", successes)
                        thumbnail_worker_state["processed_count"] += len(successes)
                        thumbnail_worker_state["cached_count"] += len(successes)
                        thumbnail_worker_state["pending_count"] = max(0, thumbnail_worker_state["pending_count"] - len(successes))
                    if failures:
                        # Mark as 2 (unparseable / failed) so we don't spin endlessly on broken files
                        conn.executemany("UPDATE files SET has_thumb = 2 WHERE path = ?", failures)
                        thumbnail_worker_state["pending_count"] = max(0, thumbnail_worker_state["pending_count"] - len(failures))
                tot = thumbnail_worker_state["total_files"]
                pend = thumbnail_worker_state["pending_count"]
                if tot > 0:
                    thumbnail_worker_state["percent"] = round(((tot - pend) / tot * 100.0), 1)
            except Exception as e:
                print(f"Error bulk updating thumbnail status: {e}")
            finally:
                conn.close()

    while True:
        try:
            if not thumbnail_pause_event.is_set():
                thumbnail_worker_state["status"] = "paused"
                await thumbnail_pause_event.wait()
                thumbnail_worker_state["status"] = "generating"

            # Check priority queue first - drain up to 25 items at once
            priority_items = []
            while len(priority_items) < 25:
                try:
                    p_item = priority_thumbnail_queue.get_nowait()
                    priority_items.append(p_item)
                except asyncio.QueueEmpty:
                    break

            if priority_items:
                await execute_batch(priority_items)
                for _ in priority_items:
                    priority_thumbnail_queue.task_done()
                continue

            # Background batch from SQLite
            conn = get_db()
            batch = []
            try:
                cur = conn.execute(
                    "SELECT path, type, name, folder_path FROM files WHERE has_thumb = 0 LIMIT 25"
                )
                for r in cur.fetchall():
                    batch.append({
                        "path": r["path"],
                        "type": r["type"],
                        "name": r["name"],
                        "folder_path": r["folder_path"]
                    })
            except Exception as e:
                print(f"Error fetching thumbnail batch: {e}")
            finally:
                conn.close()

            if not batch:
                thumbnail_worker_state["status"] = "idle"
                thumbnail_worker_state["current_file"] = ""
                thumbnail_worker_state["current_folder"] = ""
                thumbnail_worker_state["pending_count"] = 0
                thumbnail_worker_state["percent"] = 100.0
                await asyncio.sleep(4.0)
                continue

            await execute_batch(batch)
            await asyncio.sleep(0.04)

        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"Error in thumbnail worker: {e}")
            await asyncio.sleep(2.0)

# ==============================================================================
# Duplicate Hash Pre-Verification Background Worker
# ==============================================================================
duplicate_hash_state = {
    "status": "idle",  # "idle" | "hashing" | "paused"
    "total_candidates": 0,
    "hashed_count": 0,
    "pending_count": 0,
    "percent": 100.0,
    "current_file": "",
    "processed_count": 0,
    "is_paused": False,
    "last_synced": None,
}
duplicate_hash_pause_event = asyncio.Event()
duplicate_hash_pause_event.set()

async def duplicate_hash_worker():
    """Background worker that continuously pre-verifies and caches 128KB chunk hashes for all duplicate candidates."""
    # Brief initial pause on boot so Uvicorn and other workers settle smoothly
    await asyncio.sleep(3.0)

    concurrency_sem = asyncio.Semaphore(2)

    async def hash_single_candidate(row) -> tuple:
        async with concurrency_sem:
            if not duplicate_hash_pause_event.is_set():
                await duplicate_hash_pause_event.wait()
            fpath = row["path"]
            duplicate_hash_state["current_file"] = row["name"]
            # Offload file read & sha256 to background threadpool
            q_hash = await asyncio.to_thread(compute_quick_hash, fpath)
            return fpath, q_hash

    while True:
        try:
            if not duplicate_hash_pause_event.is_set():
                duplicate_hash_state["status"] = "paused"
                duplicate_hash_state["is_paused"] = True
                await duplicate_hash_pause_event.wait()
                duplicate_hash_state["status"] = "hashing"
                duplicate_hash_state["is_paused"] = False

            # Query duplicate candidates in SQLite
            conn = get_db()
            try:
                c_tot_row = conn.execute("""
                    SELECT COUNT(*) FROM files 
                    WHERE size > 0 AND size IN (SELECT size FROM files WHERE size > 0 GROUP BY size HAVING COUNT(*) > 1)
                """).fetchone()
                c_tot = c_tot_row[0] if c_tot_row else 0

                c_hashed_row = conn.execute("""
                    SELECT COUNT(*) FROM files 
                    WHERE quick_hash IS NOT NULL AND size > 0 AND size IN (SELECT size FROM files WHERE size > 0 GROUP BY size HAVING COUNT(*) > 1)
                """).fetchone()
                c_hashed = c_hashed_row[0] if c_hashed_row else 0

                # Fetch candidates needing hashes (limit 50 per cycle)
                pending_rows = conn.execute("""
                    SELECT path, name FROM files 
                    WHERE quick_hash IS NULL AND size > 0 AND size IN (SELECT size FROM files WHERE size > 0 GROUP BY size HAVING COUNT(*) > 1)
                    ORDER BY size DESC
                    LIMIT 50
                """).fetchall()
            except Exception as e:
                print(f"Error querying duplicate candidates: {e}")
                c_tot, c_hashed, pending_rows = 0, 0, []
            finally:
                conn.close()

            duplicate_hash_state["total_candidates"] = c_tot
            duplicate_hash_state["hashed_count"] = c_hashed
            duplicate_hash_state["pending_count"] = max(0, c_tot - c_hashed)
            duplicate_hash_state["percent"] = round((c_hashed / c_tot * 100.0), 1) if c_tot > 0 else 100.0

            if not pending_rows:
                duplicate_hash_state["status"] = "idle"
                duplicate_hash_state["current_file"] = ""
                duplicate_hash_state["last_synced"] = time.time()
                # Re-check every 30s for new files added or moved
                await asyncio.sleep(30.0)
                continue

            duplicate_hash_state["status"] = "hashing"

            # Execute batch of candidates
            batch = list(pending_rows)
            results = await asyncio.gather(*[hash_single_candidate(r) for r in batch], return_exceptions=True)

            updates = []
            for res in results:
                if isinstance(res, tuple):
                    fpath, q_hash = res
                    # If file is unreadable or deleted, record 'unhashable' sentinel so it doesn't spin endlessly
                    val = q_hash if q_hash else "unhashable"
                    updates.append((val, fpath))

            if updates:
                conn = get_db()
                try:
                    with conn:
                        conn.executemany("UPDATE files SET quick_hash = ? WHERE path = ?", updates)
                except Exception as e:
                    print(f"Error updating candidate hashes in DB: {e}")
                finally:
                    conn.close()

                duplicate_hash_state["processed_count"] += len(updates)
                duplicate_hash_state["hashed_count"] += len(updates)
                duplicate_hash_state["pending_count"] = max(0, duplicate_hash_state["total_candidates"] - duplicate_hash_state["hashed_count"])
                tot = duplicate_hash_state["total_candidates"]
                if tot > 0:
                    duplicate_hash_state["percent"] = round((duplicate_hash_state["hashed_count"] / tot * 100.0), 1)

            # Gentle pause between batches
            await asyncio.sleep(0.1)

        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"Error in duplicate hash worker: {e}")
            await asyncio.sleep(5.0)

# ==============================================================================
# Thumbnail Difference Hash (dHash) Background Worker
# ==============================================================================
thumb_hash_state = {
    "status": "idle",  # "idle" | "hashing"
    "total_thumbnails": 0,
    "hashed_count": 0,
    "pending_count": 0,
    "percent": 100.0,
    "current_file": "",
    "processed_count": 0,
}

async def thumb_hash_worker():
    """Background worker that continuously computes 64-bit dHash for cached thumbnails."""
    await asyncio.sleep(4.0)
    concurrency_sem = asyncio.Semaphore(4)

    async def hash_single_thumb(row) -> tuple:
        async with concurrency_sem:
            fpath = row["path"]
            thumb_hash_state["current_file"] = row["name"]
            tpath = get_thumbnail_cache_path(fpath, mtime=row["mtime"], size=row["size"])
            dh = await asyncio.to_thread(compute_thumbnail_dhash, tpath)
            return fpath, dh

    while True:
        try:
            conn = get_db()
            try:
                tot_row = conn.execute("SELECT COUNT(*) FROM files WHERE has_thumb = 1").fetchone()
                tot_thumbs = tot_row[0] if tot_row else 0

                hashed_row = conn.execute("SELECT COUNT(*) FROM files WHERE has_thumb = 1 AND thumb_hash IS NOT NULL AND thumb_hash != ''").fetchone()
                hashed_thumbs = hashed_row[0] if hashed_row else 0

                pending_rows = conn.execute("""
                    SELECT path, name, mtime, size FROM files 
                    WHERE has_thumb = 1 AND (thumb_hash IS NULL OR thumb_hash = '')
                    ORDER BY size DESC
                    LIMIT 100
                """).fetchall()
            except Exception as e:
                print(f"Error querying thumbnail hash candidates: {e}")
                tot_thumbs, hashed_thumbs, pending_rows = 0, 0, []
            finally:
                conn.close()

            thumb_hash_state["total_thumbnails"] = tot_thumbs
            thumb_hash_state["hashed_count"] = hashed_thumbs
            thumb_hash_state["pending_count"] = max(0, tot_thumbs - hashed_thumbs)
            thumb_hash_state["percent"] = round((hashed_thumbs / tot_thumbs * 100.0), 1) if tot_thumbs > 0 else 100.0

            if not pending_rows:
                thumb_hash_state["status"] = "idle"
                thumb_hash_state["current_file"] = ""
                await asyncio.sleep(30.0)
                continue

            thumb_hash_state["status"] = "hashing"

            batch = list(pending_rows)
            results = await asyncio.gather(*[hash_single_thumb(r) for r in batch], return_exceptions=True)

            updates = []
            for res in results:
                if isinstance(res, tuple):
                    fpath, dh = res
                    val = dh if dh else "no_thumb"
                    updates.append((val, fpath))

            if updates:
                conn = get_db()
                try:
                    with conn:
                        conn.executemany("UPDATE files SET thumb_hash = ? WHERE path = ?", updates)
                except Exception as e:
                    print(f"Error updating thumbnail hashes in DB: {e}")
                finally:
                    conn.close()

                thumb_hash_state["processed_count"] += len(updates)
                thumb_hash_state["hashed_count"] += len(updates)
                thumb_hash_state["pending_count"] = max(0, thumb_hash_state["total_thumbnails"] - thumb_hash_state["hashed_count"])
                tot = thumb_hash_state["total_thumbnails"]
                if tot > 0:
                    thumb_hash_state["percent"] = round((thumb_hash_state["hashed_count"] / tot * 100.0), 1)

            await asyncio.sleep(0.05)

        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"Error in thumb_hash_worker: {e}")
            await asyncio.sleep(5.0)


# ==============================================================================
# WebP Image Optimizer Engine & Background Worker
# ==============================================================================

def convert_image_file_to_webp(file_path: str, quality: Optional[int] = None) -> dict:
    fpath = Path(file_path).resolve()
    try:
        if not fpath.exists():
            return {"status": "error", "error": "File not found", "path": str(fpath)}
    except OSError as e:
        return {"status": "error", "error": str(e), "path": str(fpath)}

    if quality is None:
        quality = WEBP_QUALITY

    ext = fpath.suffix.lower()
    if ext == ".webp":
        conn = get_db()
        try:
            with conn:
                conn.execute("UPDATE files SET webp_optimized = 1 WHERE path = ?", (str(fpath),))
        except Exception:
            pass
        finally:
            conn.close()
        return {"status": "already_webp", "path": str(fpath)}
    if ext not in (".jpg", ".jpeg", ".png", ".bmp", ".tiff"):
        conn = get_db()
        try:
            with conn:
                conn.execute("UPDATE files SET webp_optimized = -2 WHERE path = ?", (str(fpath),))
        except Exception:
            pass
        finally:
            conn.close()
        return {"status": "unsupported", "path": str(fpath)}

    orig_size = fpath.stat().st_size
    temp_name = f"temp_{uuid.uuid4().hex}.webp"
    temp_path = WEBP_STAGING_DIR / temp_name

    try:
        with Image.open(fpath) as img:
            orig_width, orig_height = img.size
            orig_mode = img.mode
            icc_profile = img.info.get("icc_profile")
            exif = img.getexif()

            is_png = ext == ".png"
            save_kwargs = {
                "format": "WEBP",
                "method": 4,
            }
            if is_png:
                save_kwargs["lossless"] = True
            else:
                save_kwargs["quality"] = quality
                save_kwargs["lossless"] = False

            if icc_profile:
                save_kwargs["icc_profile"] = icc_profile
            if len(exif) > 0:
                save_kwargs["exif"] = exif

            if orig_mode == "CMYK":
                work_img = img.convert("RGB")
            elif orig_mode not in ("RGB", "RGBA", "L", "LA"):
                work_img = img.convert("RGBA" if "transparency" in img.info or orig_mode in ("P", "PA") else "RGB")
            else:
                work_img = img

            work_img.save(temp_path, **save_kwargs)

        # Integrity verification
        with Image.open(temp_path) as v_img:
            v_img.verify()
        with Image.open(temp_path) as v_img:
            v_img.load()
            v_w, v_h = v_img.size
            if v_w != orig_width or v_h != orig_height:
                raise ValueError(f"Dimension mismatch: expected {orig_width}x{orig_height}, got {v_w}x{v_h}")

        webp_size = temp_path.stat().st_size

        # Only skip if WEBP_SKIP_LARGER is explicitly enabled
        if WEBP_SKIP_LARGER and webp_size >= orig_size:
            temp_path.unlink(missing_ok=True)
            conn = get_db()
            try:
                with conn:
                    # Mark as skipped (-1) so it won't be queried again by worker
                    conn.execute("UPDATE files SET webp_optimized = -1 WHERE path = ?", (str(fpath),))
                    conn.execute("UPDATE webp_stats SET skipped_count = skipped_count + 1 WHERE id = 1")
            except Exception as e:
                print(f"Error updating SQLite index for skipped WebP: {e}")
            finally:
                conn.close()

            return {
                "status": "skipped",
                "reason": "webp_larger_or_equal",
                "path": str(fpath),
                "orig_size": orig_size,
                "webp_size": webp_size,
                "difference": webp_size - orig_size,
            }

        # Target file resolution
        target_path = fpath.with_suffix(".webp")
        if target_path.exists() and target_path != fpath:
            counter = 1
            while (fpath.parent / f"{fpath.stem}_opt{counter}.webp").exists():
                counter += 1
            target_path = fpath.parent / f"{fpath.stem}_opt{counter}.webp"

        shutil.copy2(temp_path, target_path)
        temp_path.unlink(missing_ok=True)

        if not target_path.exists() or target_path.stat().st_size != webp_size:
            raise IOError("Target WebP verification failed after copy")

        # Safely remove original
        try:
            fpath.unlink()
        except Exception as e:
            print(f"Warning: could not unlink original {fpath}: {e}")

        saved_bytes = orig_size - webp_size
        new_mtime = target_path.stat().st_mtime
        new_size_fmt = format_size(webp_size)

        # Update SQLite index
        conn = get_db()
        try:
            with conn:
                conn.execute("""
                    UPDATE files
                    SET path = ?, name = ?, ext = '.webp', size = ?, size_formatted = ?, mtime = ?, webp_optimized = 1, has_thumb = 1
                    WHERE path = ?
                """, (str(target_path), target_path.name, webp_size, new_size_fmt, new_mtime, str(fpath)))
                conn.execute("UPDATE webp_stats SET saved_bytes = saved_bytes + ?, converted_count = converted_count + 1 WHERE id = 1", (saved_bytes,))
        except Exception as e:
            print(f"Error updating SQLite index for WebP: {e}")
        finally:
            conn.close()

        # Update thumbnail cache: create thumbnail for target_path directly
        try:
            new_thumb = get_thumbnail_cache_path(str(target_path), new_mtime, webp_size)
            if not new_thumb.exists() or new_thumb.stat().st_size == 0:
                with Image.open(target_path) as t_img:
                    t_img.thumbnail((360, 360), Image.Resampling.BILINEAR)
                    if t_img.mode not in ("RGB", "L"):
                        t_img = t_img.convert("RGB")
                    t_img.save(new_thumb, format="JPEG", quality=80)
        except Exception as e:
            print(f"Thumbnail generation error for {target_path}: {e}")

        # Clean old thumbnail if exists
        try:
            old_thumb = get_thumbnail_cache_path(str(fpath))
            if old_thumb.exists() and old_thumb != get_thumbnail_cache_path(str(target_path), new_mtime, webp_size):
                old_thumb.unlink(missing_ok=True)
        except Exception:
            pass

        return {
            "status": "converted",
            "orig_path": str(fpath),
            "new_path": str(target_path),
            "orig_size": orig_size,
            "webp_size": webp_size,
            "saved_bytes": saved_bytes,
            "savings_pct": round(saved_bytes / orig_size * 100, 2),
        }

    except Exception as e:
        temp_path.unlink(missing_ok=True)
        # Mark unoptimizable so we don't spin endlessly on corrupted file
        conn = get_db()
        try:
            with conn:
                conn.execute("UPDATE files SET webp_optimized = -2 WHERE path = ?", (str(fpath),))
        except Exception:
            pass
        finally:
            conn.close()
        return {"status": "error", "error": str(e), "path": str(fpath)}


webp_worker_state = {
    "status": "idle",  # "idle" | "converting" | "paused" | "disabled"
    "total_images": 0,
    "converted_count": 0,
    "skipped_count": 0,
    "pending_count": 0,
    "percent": 100.0,
    "saved_bytes": 0,
    "saved_formatted": "0 B",
    "current_file": "",
    "current_folder": "",
    "is_paused": False,
    "concurrency": WEBP_WORKER_CONCURRENCY,
    "last_synced": None,
}
webp_pause_event = asyncio.Event()
webp_pause_event.set()
priority_webp_queue: asyncio.Queue = asyncio.Queue()


async def webp_converter_worker():
    """Background worker that continuously converts library images to WebP without quality loss."""
    if not WEBP_AUTO_OPTIMIZE:
        webp_worker_state["status"] = "disabled"
        return

    # Initial settling delay
    await asyncio.sleep(5.0)

    concurrency_sem = asyncio.Semaphore(WEBP_WORKER_CONCURRENCY)

    async def optimize_single_file(fpath: str, fname: str) -> dict:
        async with concurrency_sem:
            if not webp_pause_event.is_set():
                await webp_pause_event.wait()
            webp_worker_state["current_file"] = fname
            webp_worker_state["current_folder"] = os.path.basename(os.path.dirname(fpath))
            return await asyncio.to_thread(convert_image_file_to_webp, fpath)

    while True:
        try:
            if not webp_pause_event.is_set():
                webp_worker_state["status"] = "paused"
                webp_worker_state["is_paused"] = True
                await webp_pause_event.wait()
                webp_worker_state["status"] = "converting"
                webp_worker_state["is_paused"] = False

            conn = get_db()
            try:
                tot_row = conn.execute("SELECT COUNT(*) FROM files WHERE type = 'image'").fetchone()
                total_imgs = tot_row[0] if tot_row else 0

                opt_row = conn.execute("SELECT COUNT(*) FROM files WHERE type = 'image' AND (webp_optimized != 0 OR ext = '.webp')").fetchone()
                optimized_imgs = opt_row[0] if opt_row else 0

                stats_row = conn.execute("SELECT saved_bytes, converted_count, skipped_count FROM webp_stats WHERE id = 1").fetchone()
                if stats_row:
                    webp_worker_state["saved_bytes"] = stats_row[0]
                    webp_worker_state["saved_formatted"] = format_size(stats_row[0])
                    webp_worker_state["converted_count"] = stats_row[1]
                    webp_worker_state["skipped_count"] = stats_row[2]

                # Check priority queue first
                priority_items = []
                batch_limit = max(1, WEBP_WORKER_CONCURRENCY * 2)
                while not priority_webp_queue.empty() and len(priority_items) < batch_limit:
                    try:
                        item = priority_webp_queue.get_nowait()
                        priority_items.append(item)
                    except asyncio.QueueEmpty:
                        break

                if priority_items:
                    pending_rows = priority_items
                else:
                    pending_rows = conn.execute(f"""
                        SELECT path, name FROM files 
                        WHERE type = 'image' AND ext != '.webp' AND webp_optimized = 0
                        ORDER BY size DESC
                        LIMIT {batch_limit}
                    """).fetchall()

            except Exception as e:
                print(f"Error querying webp images: {e}")
                pending_rows = []
            finally:
                conn.close()

            if total_imgs > 0:
                webp_worker_state["total_images"] = total_imgs
                webp_worker_state["pending_count"] = max(0, total_imgs - optimized_imgs)
                webp_worker_state["percent"] = round((optimized_imgs / total_imgs * 100.0), 1)

            if not pending_rows:
                if webp_worker_state["pending_count"] == 0 and webp_worker_state["total_images"] > 0:
                    webp_worker_state["status"] = "idle"
                    webp_worker_state["current_file"] = ""
                webp_worker_state["last_synced"] = time.time()
                await asyncio.sleep(5.0)
                continue

            webp_worker_state["status"] = "converting"

            # Execute batch
            results = await asyncio.gather(*[optimize_single_file(r["path"], r["name"]) for r in pending_rows], return_exceptions=True)

            has_progress = False
            for res in results:
                if isinstance(res, dict) and res.get("status") == "converted":
                    has_progress = True
                    webp_worker_state["saved_bytes"] += res.get("saved_bytes", 0)
                    webp_worker_state["saved_formatted"] = format_size(webp_worker_state["saved_bytes"])
                elif isinstance(res, dict) and res.get("status") == "skipped":
                    has_progress = True
                    webp_worker_state["skipped_count"] = webp_worker_state.get("skipped_count", 0) + 1

            if not has_progress:
                await asyncio.sleep(2.0)
            else:
                await asyncio.sleep(0.1)

        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"Error in webp_converter_worker: {e}")
            await asyncio.sleep(5.0)


def search_backup_index(query: str, type_filter: Optional[str] = None, sort_by: str = "name_asc", limit: int = 150) -> List[Dict[str, Any]]:
    conn = get_db()
    try:
        words = query.strip().lower().split()
        if not words:
            return []

        where_clauses = ["LOWER(name) LIKE ?" for _ in words]
        sql = f"SELECT * FROM files WHERE {' AND '.join(where_clauses)}"
        params = [f"%{w}%" for w in words]

        if type_filter and type_filter in ("video", "image"):
            sql += " AND type = ?"
            params.append(type_filter)

        sort_sql = "name ASC"
        if sort_by == "name_desc":
            sort_sql = "name DESC"
        elif sort_by == "date_desc":
            sort_sql = "mtime DESC"
        elif sort_by == "date_asc":
            sort_sql = "mtime ASC"
        elif sort_by == "size_desc":
            sort_sql = "size DESC"
        elif sort_by == "size_asc":
            sort_sql = "size ASC"

        sql += f" ORDER BY {sort_sql} LIMIT ?"
        params.append(limit)

        cur = conn.execute(sql, params)
        rows = cur.fetchall()

        results = []
        base_dir = os.path.realpath(state.get("destination_dir") or DEFAULT_DESTINATION_DIR)
        for r in rows:
            fpath = r["path"]
            fname = r["name"]
            folder_path = r["folder_path"]
            rel_folder = os.path.relpath(folder_path, base_dir)
            folder_name = os.path.basename(folder_path)
            is_vid = (r["type"] == "video")
            ext = r["ext"]
            is_native = ext in NATIVE_EXTENSIONS

            media_url = (
                f"/api/backup/media?path={urllib.parse.quote(fpath)}"
                if (not is_vid or is_native)
                else f"/api/backup/transcode_stream?path={urllib.parse.quote(fpath)}"
            )

            results.append({
                "name": fname,
                "path": fpath,
                "folder_name": folder_name,
                "folder_path": folder_path,
                "rel_folder": rel_folder,
                "type": r["type"],
                "ext": ext,
                "size": r["size"],
                "size_formatted": r["size_formatted"],
                "mtime": r["mtime"],
                "needs_transcode": bool(r["needs_transcode"]),
                "has_thumb": bool(r["has_thumb"]) if "has_thumb" in r.keys() else False,
                "thumb_url": f"/api/backup/thumbnail?path={urllib.parse.quote(fpath)}",
                "media_url": media_url,
            })
        return results
    except Exception as e:
        print(f"Error querying backup search: {e}")
        return []
    finally:
        conn.close()

def resolve_video_path(filename: str) -> Optional[str]:
    if not state["source_dir"] or not os.path.isdir(state["source_dir"]):
        return None

    clean_filename = urllib.parse.unquote(filename).strip()
    candidates_to_try = [clean_filename]
    if clean_filename != filename:
        candidates_to_try.append(filename)

    for fname in candidates_to_try:
        # 1. Exact match in source directory
        direct_path = os.path.join(state["source_dir"], fname)
        if os.path.exists(direct_path) and not os.path.basename(direct_path).startswith("."):
            return direct_path

        # 2. Check transcode items map for completed target paths
        for t in transcode_items_map.values():
            if t.get("filename") == fname and t.get("target_path") and os.path.exists(t["target_path"]):
                return t["target_path"]

        # 3. Check if file was converted from an older container to .mp4
        base, _ = os.path.splitext(fname)
        mp4_name = f"{base}.mp4"
        mp4_path = os.path.join(state["source_dir"], mp4_name)
        if os.path.exists(mp4_path):
            return mp4_path

        # 4. Check if file was renamed with a converted counter (e.g. base_converted_1.mp4)
        try:
            with os.scandir(state["source_dir"]) as entries:
                for entry in entries:
                    if entry.is_file() and not entry.name.startswith("."):
                        if entry.name.startswith(base) and entry.name.endswith(".mp4"):
                            return entry.path
        except Exception:
            pass

    return None

def scan_videos(directory: str) -> List[Dict[str, Any]]:
    if not os.path.isdir(directory):
        return []
    
    videos = []
    try:
        with os.scandir(directory) as entries:
            for entry in entries:
                if entry.is_file():
                    # Strictly ignore hidden or temporary staging files
                    if entry.name.startswith("."):
                        continue
                    ext = os.path.splitext(entry.name)[1].lower()
                    if ext in VIDEO_EXTENSIONS:
                        try:
                            stat = entry.stat()
                            needs_transcode = ext not in NATIVE_EXTENSIONS
                            videos.append({
                                "id": entry.name,
                                "filename": entry.name,
                                "path": entry.path,
                                "size": stat.st_size,
                                "size_formatted": format_size(stat.st_size),
                                "mtime": stat.st_mtime,
                                "ext": ext,
                                "needs_transcode": needs_transcode,
                            })
                        except Exception:
                            continue
    except Exception as e:
        print(f"Error scanning directory {directory}: {e}")
        return []

    # Sort alphabetically by filename
    videos.sort(key=lambda x: x["filename"].lower())
    return videos

def get_lan_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        s.connect(("1.1.1.1", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


# Background Transfer Execution
def do_transfer(item: Dict[str, Any]):
    source_path = item["source_path"]
    target_path = item["target_path"]
    abort_event: threading.Event = item["abort_event"]

    # If waiting on background pre-transcode to finish
    wait_transcode_id = item.get("waiting_on_transcode")
    if wait_transcode_id and wait_transcode_id in transcode_items_map:
        t_task = transcode_items_map[wait_transcode_id]
        while t_task.get("status") in ("queued", "transcoding"):
            if abort_event.is_set():
                item["status"] = "cancelled"
                return
            time.sleep(0.5)

        # If transcode completed, use the converted MP4 as the source file!
        if t_task.get("status") == "completed" and t_task.get("target_path") and os.path.exists(t_task["target_path"]):
            source_path = t_task["target_path"]
            item["source_path"] = source_path
            converted_name = os.path.basename(source_path)
            item["filename"] = converted_name
            target_dir = os.path.dirname(target_path)
            c_base, c_ext = os.path.splitext(converted_name)
            final_dest_name = converted_name
            cnt = 1
            while os.path.exists(os.path.join(target_dir, final_dest_name)):
                final_dest_name = f"{c_base}_{cnt}{c_ext}"
                cnt += 1
            target_path = os.path.join(target_dir, final_dest_name)
            item["target_path"] = target_path
            item["final_filename"] = final_dest_name
            try:
                item["total_bytes"] = os.path.getsize(source_path)
            except Exception:
                pass

    if not os.path.exists(source_path):
        item["status"] = "failed"
        item["error"] = "Source file not found"
        return

    target_dir = os.path.dirname(target_path)
    os.makedirs(target_dir, exist_ok=True)

    # Check if same filesystem (device ID match)
    try:
        s_stat = os.stat(source_path)
        d_stat = os.stat(target_dir)
        if s_stat.st_dev == d_stat.st_dev:
            # Atomic rename within same filesystem
            os.rename(source_path, target_path)
            item["bytes_copied"] = item["total_bytes"]
            item["percent"] = 100.0
            item["status"] = "completed"
            item["completed_at"] = time.time()
            add_file_to_index(target_path)
            return
    except Exception:
        pass

    # Across filesystem copy (chunked with abort support)
    temp_target = target_path + f".tmp.{item['id']}"
    total_size = item["total_bytes"]
    bytes_copied = 0
    chunk_size = 4 * 1024 * 1024  # 4MB chunks

    try:
        with open(source_path, "rb") as fsrc, open(temp_target, "wb") as fdst:
            while True:
                if abort_event.is_set():
                    raise InterruptedError("Transfer cancelled by user")
                chunk = fsrc.read(chunk_size)
                if not chunk:
                    break
                fdst.write(chunk)
                bytes_copied += len(chunk)
                item["bytes_copied"] = bytes_copied
                if total_size > 0:
                    item["percent"] = round((bytes_copied / total_size) * 100.0, 1)

        # Atomic rename into final file once fully written
        os.replace(temp_target, target_path)

        # Remove source file only after destination is safely committed
        try:
            os.remove(source_path)
        except Exception as e:
            print(f"Warning: Failed to delete source file {source_path}: {e}")

        item["status"] = "completed"
        item["completed_at"] = time.time()
        item["percent"] = 100.0
        add_file_to_index(target_path)
    except InterruptedError:
        if os.path.exists(temp_target):
            try:
                os.remove(temp_target)
            except Exception:
                pass
        item["status"] = "cancelled"
    except Exception as e:
        if os.path.exists(temp_target):
            try:
                os.remove(temp_target)
            except Exception:
                pass
        item["status"] = "failed"
        item["error"] = str(e)
        print(f"Transfer failed for {item['filename']}: {e}")

async def transfer_worker():
    global active_transfer
    while True:
        item = await transfer_queue.get()
        try:
            if item.get("status") == "cancelled":
                transfer_queue.task_done()
                continue

            item["status"] = "transferring"
            item["started_at"] = time.time()
            active_transfer = item

            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, do_transfer, item)
        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"Error in transfer worker: {e}")
            item["status"] = "failed"
            item["error"] = str(e)
        finally:
            active_transfer = None
            transfer_queue.task_done()


# ==============================================================================
# Automatic Pre-Transcoder Background Worker
# ==============================================================================

def format_eta_str(seconds: float) -> str:
    if seconds <= 0:
        return "0s"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h}h {m}m"
    if m > 0:
        return f"{m}m {s}s"
    return f"{s}s"

async def probe_video_meta(file_path: str) -> Dict[str, Any]:
    try:
        cmd = [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration:stream=codec_name,codec_type",
            "-of", "json",
            file_path
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL
        )
        stdout, _ = await proc.communicate()
        if proc.returncode == 0:
            data = json.loads(stdout.decode("utf-8", errors="replace"))
            duration_str = data.get("format", {}).get("duration", "0")
            try:
                duration = float(duration_str)
            except (ValueError, TypeError):
                duration = 0.0
            video_codec = ""
            audio_codec = ""
            for s in data.get("streams", []):
                ctype = s.get("codec_type")
                if ctype == "video" and not video_codec:
                    video_codec = s.get("codec_name", "").lower()
                elif ctype == "audio" and not audio_codec:
                    audio_codec = s.get("codec_name", "").lower()
            return {
                "duration": duration,
                "video_codec": video_codec,
                "audio_codec": audio_codec,
            }
    except Exception as e:
        print(f"Probe error on {file_path}: {e}")
    return {"duration": 0.0, "video_codec": "", "audio_codec": ""}

async def run_transcode_task(item: Dict[str, Any]):
    source_path = item["source_path"]
    if not os.path.exists(source_path):
        item["status"] = "failed"
        item["error"] = "Source file missing"
        return

    source_dir = os.path.dirname(source_path)
    base_name = os.path.splitext(os.path.basename(source_path))[0]

    target_name = f"{base_name}.mp4"
    target_path = os.path.join(source_dir, target_name)
    if os.path.exists(target_path) and target_path != source_path:
        cnt = 1
        while os.path.exists(os.path.join(source_dir, f"{base_name}_converted_{cnt}.mp4")):
            cnt += 1
        target_path = os.path.join(source_dir, f"{base_name}_converted_{cnt}.mp4")

    temp_path = str(TRANSCODE_TEMP_DIR / f"temp_{item['id']}_{target_name}")
    item["target_path"] = target_path

    # Probe file for duration and codecs
    probe = await probe_video_meta(source_path)
    duration = probe.get("duration", 0.0)
    video_codec = probe.get("video_codec", "")
    audio_codec = probe.get("audio_codec", "")

    # Can we smart remux? (H.264 video, AAC or MP3 or no audio)
    can_remux = (video_codec == "h264" and audio_codec in ("aac", "mp3", ""))

    success = False
    abort_event: threading.Event = item["abort_event"]

    # Attempt 1: Smart Remux or Balanced Re-encode
    if can_remux:
        item["method"] = "remux"
        remux_cmd = [
            "nice", "-n", "10",
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", source_path,
            "-c", "copy",
            "-movflags", "+faststart",
            "-f", "mp4",
            temp_path
        ]
        proc = await asyncio.create_subprocess_exec(*remux_cmd)
        while proc.returncode is None:
            if abort_event.is_set():
                try: proc.terminate()
                except Exception: pass
                if os.path.exists(temp_path):
                    try: os.remove(temp_path)
                    except Exception: pass
                item["status"] = "cancelled"
                return
            try:
                await asyncio.wait_for(proc.wait(), timeout=0.5)
            except asyncio.TimeoutError:
                pass

        if proc.returncode == 0 and os.path.exists(temp_path) and os.path.getsize(temp_path) > 1024:
            success = True

    if not success and not abort_event.is_set():
        # Balanced Re-encode with progress pipe
        item["method"] = "reencode"
        if os.path.exists(temp_path):
            try: os.remove(temp_path)
            except Exception: pass

        encode_cmd = [
            "nice", "-n", "10",
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", source_path,
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "22",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", "192k",
            "-movflags", "+faststart",
            "-progress", "pipe:1",
            "-f", "mp4",
            temp_path
        ]
        proc = await asyncio.create_subprocess_exec(
            *encode_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL
        )

        async def read_progress():
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").strip()
                if "=" in text:
                    key, val = text.split("=", 1)
                    if key in ("out_time_us", "out_time_ms"):
                        try:
                            out_us = int(val)
                            out_sec = out_us / 1_000_000.0
                            if duration > 0:
                                pct = min(99.0, max(0.0, (out_sec / duration) * 100.0))
                                item["percent"] = round(pct, 1)
                        except ValueError:
                            pass
                    elif key == "fps":
                        try:
                            item["fps"] = round(float(val), 1)
                        except ValueError:
                            pass
                    elif key == "speed":
                        item["speed"] = val
                        try:
                            spd = float(val.replace("x", "").strip())
                            if spd > 0 and duration > 0:
                                rem_sec = max(0.0, duration - (item["percent"] / 100.0 * duration)) / spd
                                item["eta"] = format_eta_str(rem_sec)
                        except ValueError:
                            pass

        progress_task = asyncio.create_task(read_progress())
        while proc.returncode is None:
            if abort_event.is_set():
                try: proc.terminate()
                except Exception: pass
                progress_task.cancel()
                if os.path.exists(temp_path):
                    try: os.remove(temp_path)
                    except Exception: pass
                item["status"] = "cancelled"
                return
            try:
                await asyncio.wait_for(proc.wait(), timeout=0.5)
            except asyncio.TimeoutError:
                pass

        await progress_task
        if proc.returncode == 0 and os.path.exists(temp_path) and os.path.getsize(temp_path) > 1024:
            success = True

    # Fallback retry pass with safe generic compatibility flags
    if not success and not abort_event.is_set():
        item["method"] = "fallback_reencode"
        if os.path.exists(temp_path):
            try: os.remove(temp_path)
            except Exception: pass

        fallback_cmd = [
            "nice", "-n", "10",
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", source_path,
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", "128k",
            "-ac", "2",
            "-movflags", "+faststart",
            "-f", "mp4",
            temp_path
        ]
        proc = await asyncio.create_subprocess_exec(*fallback_cmd)
        while proc.returncode is None:
            if abort_event.is_set():
                try: proc.terminate()
                except Exception: pass
                if os.path.exists(temp_path):
                    try: os.remove(temp_path)
                    except Exception: pass
                item["status"] = "cancelled"
                return
            try:
                await asyncio.wait_for(proc.wait(), timeout=0.5)
            except asyncio.TimeoutError:
                pass

        if proc.returncode == 0 and os.path.exists(temp_path) and os.path.getsize(temp_path) > 1024:
            success = True

    # Finalize commit
    if success:
        try:
            shutil.move(temp_path, target_path)
        except Exception as e:
            try:
                os.replace(temp_path, target_path)
            except Exception as e2:
                print(f"Error moving transcoded file to {target_path}: {e2}")
                item["status"] = "failed"
                item["error"] = f"Failed to commit transcoded file: {e2}"
                if os.path.exists(temp_path):
                    try: os.remove(temp_path)
                    except Exception: pass
                transcode_history.append(item)
                return

        if os.path.exists(source_path) and os.path.abspath(source_path) != os.path.abspath(target_path):
            try:
                os.remove(source_path)
            except Exception as e:
                print(f"Warning: Failed to remove original file {source_path}: {e}")

        # Invalidate thumbnail cache of the old source file
        try:
            old_thumb = get_thumbnail_cache_path(source_path)
            if old_thumb.exists():
                old_thumb.unlink()
        except Exception:
            pass

        target_size = os.path.getsize(target_path) if os.path.exists(target_path) else 0
        item["target_size"] = target_size
        item["percent"] = 100.0
        item["status"] = "completed"
        item["completed_at"] = time.time()
        transcode_history.append(item)

        # Update SQLite backup index if applicable
        update_file_in_index(source_path, target_path, os.path.basename(target_path), target_size, ".mp4")

        # Update in-memory video queue
        new_meta = make_video_meta(target_path)
        if new_meta:
            old_name = os.path.basename(source_path)
            state["video_queue"] = [
                new_meta if (v["id"] == old_name or v.get("filename") == old_name) else v
                for v in state["video_queue"]
            ]
    else:
        if os.path.exists(temp_path):
            try: os.remove(temp_path)
            except Exception: pass
        item["status"] = "failed"
        item["error"] = "Conversion failed (safe fallback preserved original file)"
        transcode_history.append(item)

async def transcode_worker():
    global active_transcode
    while True:
        try:
            await transcode_pause_event.wait()
            item = await transcode_queue.get()
            try:
                if item.get("status") in ("cancelled", "completed"):
                    continue

                if not os.path.exists(item["source_path"]):
                    item["status"] = "failed"
                    item["error"] = "Source file missing"
                    continue

                item["status"] = "transcoding"
                item["started_at"] = time.time()
                active_transcode = item

                await run_transcode_task(item)
            except asyncio.CancelledError:
                break
            except Exception as e:
                item["status"] = "failed"
                item["error"] = str(e)
                print(f"Error in transcode task: {e}")
            finally:
                active_transcode = None
                try:
                    transcode_queue.task_done()
                except (ValueError, Exception):
                    pass
        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"Fatal error in transcode worker outer loop: {e}")
            await asyncio.sleep(1.0)

def enqueue_pretranscode_for_source(source_dir: str):
    if not source_dir or not os.path.isdir(source_dir):
        return

    videos = scan_videos(source_dir)
    for v in videos:
        ext = v.get("ext", "").lower()
        needs_transcode = v.get("needs_transcode", False) or ext != ".mp4"
        if needs_transcode:
            existing = None
            for t in transcode_items_map.values():
                if t["filename"] == v["filename"] and t.get("status") in ("queued", "transcoding", "completed"):
                    existing = t
                    break
            if not existing:
                task_id = uuid.uuid4().hex[:10]
                item = {
                    "id": task_id,
                    "file_id": v["filename"],
                    "filename": v["filename"],
                    "source_path": v["path"],
                    "target_path": None,
                    "status": "queued",
                    "percent": 0.0,
                    "fps": 0.0,
                    "speed": "",
                    "eta": "",
                    "method": "pending",
                    "created_at": time.time(),
                    "started_at": None,
                    "completed_at": None,
                    "error": None,
                    "abort_event": threading.Event(),
                }
                transcode_items_map[task_id] = item
                transcode_queue.put_nowait(item)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize SQLite backup index
    init_backup_db()

    # Start background transfer, pre-transcode, index crawler, thumbnail, duplicate hash, thumb hash, and webp workers
    worker_task = asyncio.create_task(transfer_worker())
    transcode_task = asyncio.create_task(transcode_worker())
    crawler_task = asyncio.create_task(run_background_crawler())
    thumb_task = asyncio.create_task(thumbnail_worker())
    dup_hash_task = asyncio.create_task(duplicate_hash_worker())
    thumb_hash_task = asyncio.create_task(thumb_hash_worker())
    webp_task = asyncio.create_task(webp_converter_worker())

    # Auto-enqueue pre-transcoding for configured source directory
    if state["source_dir"] and os.path.isdir(state["source_dir"]):
        asyncio.create_task(asyncio.to_thread(enqueue_pretranscode_for_source, state["source_dir"]))

    yield
    worker_task.cancel()
    transcode_task.cancel()
    crawler_task.cancel()
    thumb_task.cancel()
    dup_hash_task.cancel()
    thumb_hash_task.cancel()
    webp_task.cancel()
    try:
        await asyncio.gather(worker_task, transcode_task, crawler_task, thumb_task, dup_hash_task, thumb_hash_task, webp_task, return_exceptions=True)
    except asyncio.CancelledError:
        pass

# Initialize FastAPI app
app = FastAPI(title="Video Sorter", version="2.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Request Models
class ScanSourceRequest(BaseModel):
    source_dir: str

class MoveRequest(BaseModel):
    file_id: str
    destination_folder: str

class RenameRequest(BaseModel):
    file_id: str
    new_name: str

class DeleteRequest(BaseModel):
    file_id: str

class CreateFolderRequest(BaseModel):
    folder_name: str

class CancelTransferRequest(BaseModel):
    task_id: str

class BackupRenameRequest(BaseModel):
    path: str
    new_name: str

class BackupDeleteRequest(BaseModel):
    path: str

class BackupTranscodeRequest(BaseModel):
    folder_path: Optional[str] = None
    file_path: Optional[str] = None


# Routes
@app.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def serve_library():
    library_file = TEMPLATES_DIR / "library.html"
    if not library_file.exists():
        return HTMLResponse("<h1>Media Library page loading...</h1>", status_code=200)
    return HTMLResponse(content=library_file.read_text(encoding="utf-8"))


@app.api_route("/sorter", methods=["GET", "HEAD"], response_class=HTMLResponse)
@app.api_route("/sort", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def serve_sorter():
    sorter_file = TEMPLATES_DIR / "index.html"
    if not sorter_file.exists():
        return HTMLResponse("<h1>Video Sorter UI loading...</h1>", status_code=200)
    return HTMLResponse(content=sorter_file.read_text(encoding="utf-8"))


@app.api_route("/library", methods=["GET", "HEAD"])
async def redirect_library():
    return RedirectResponse(url="/", status_code=302)


@app.get("/api/config")
async def get_config():
    lan_ip = get_lan_ip()
    queued_count = sum(1 for t in transfer_items_map.values() if t.get("status") == "queued")
    active_count = 1 if active_transfer else 0
    return {
        "lan_ip": lan_ip,
        "source_dir": state["source_dir"],
        "destination_dir": state["destination_dir"],
        "recent_destinations": state["recent_destinations"][:6],
        "queue_count": len(state["video_queue"]),
        "can_undo": len(state["undo_stack"]) > 0,
        "last_undo_item": state["undo_stack"][-1]["filename"] if state["undo_stack"] else None,
        "transfers_in_progress": active_count + queued_count,
    }


_last_queue_scan_time = 0.0

@app.post("/api/source/scan")
async def scan_source(req: ScanSourceRequest):
    global _last_queue_scan_time
    source_path = os.path.abspath(req.source_dir.strip())
    if not os.path.exists(source_path):
        raise HTTPException(status_code=400, detail=f"Directory does not exist: {source_path}")
    if not os.path.isdir(source_path):
        raise HTTPException(status_code=400, detail=f"Path is not a directory: {source_path}")

    state["source_dir"] = source_path
    save_config_data()

    # Filter out files that are currently queued or transferring in background
    in_flight_names = {
        t["filename"] for t in transfer_items_map.values()
        if t.get("status") in ("queued", "transferring") and t.get("source_path", "").startswith(source_path)
    }

    all_videos = await asyncio.to_thread(scan_videos, source_path)
    state["video_queue"] = [v for v in all_videos if v["filename"] not in in_flight_names]
    _last_queue_scan_time = time.time()

    # Auto-enqueue pre-transcoding in background
    asyncio.create_task(asyncio.to_thread(enqueue_pretranscode_for_source, source_path))

    return {
        "success": True,
        "source_dir": source_path,
        "count": len(state["video_queue"]),
        "videos": state["video_queue"],
    }


@app.get("/api/queue")
async def get_queue(refresh: Optional[bool] = Query(False)):
    global _last_queue_scan_time
    now = time.time()
    in_flight_names = {
        t["filename"] for t in transfer_items_map.values()
        if t.get("status") in ("queued", "transferring")
    }

    if state["source_dir"] and os.path.isdir(state["source_dir"]):
        # Re-scan if explicitly requested, if queue is empty, or if cache is older than 8s
        if refresh or not state["video_queue"] or (now - _last_queue_scan_time > 8.0):
            all_videos = await asyncio.to_thread(scan_videos, state["source_dir"])
            state["video_queue"] = [v for v in all_videos if v["filename"] not in in_flight_names]
            _last_queue_scan_time = now
        else:
            # Sub-millisecond in-memory filtering
            state["video_queue"] = [v for v in state["video_queue"] if v["filename"] not in in_flight_names]

    return {
        "source_dir": state["source_dir"],
        "count": len(state["video_queue"]),
        "videos": state["video_queue"],
    }


@app.get("/api/browse")
def browse_directory(path: Optional[str] = Query(None)):
    target_path = os.path.abspath(path.strip()) if path and path.strip() else (state["source_dir"] or state["destination_dir"] or "/")
    if not os.path.exists(target_path):
        target_path = "/"
    if not os.path.isdir(target_path):
        target_path = os.path.dirname(target_path) or "/"

    folders = []
    video_count = 0
    try:
        with os.scandir(target_path) as entries:
            for entry in entries:
                try:
                    if entry.is_dir(follow_symlinks=False):
                        if not entry.name.startswith("."):
                            folders.append({
                                "name": entry.name,
                                "path": entry.path,
                            })
                    elif entry.is_file(follow_symlinks=False):
                        ext = os.path.splitext(entry.name)[1].lower()
                        if ext in VIDEO_EXTENSIONS:
                            video_count += 1
                except (PermissionError, OSError):
                    continue
    except PermissionError:
        raise HTTPException(status_code=403, detail="Permission denied")

    folders.sort(key=lambda x: x["name"].lower())
    parent_path = str(Path(target_path).parent) if target_path != "/" else None

    return {
        "current_path": target_path,
        "parent_path": parent_path,
        "folders": folders,
        "video_count_here": video_count,
    }


@app.get("/api/quick-nav")
def get_quick_nav():
    shortcuts = []
    seen = set()

    def add_shortcut(label: str, path_str: str):
        if not path_str:
            return
        try:
            p = os.path.abspath(os.path.expanduser(path_str.strip()))
            if p not in seen and os.path.exists(p) and os.path.isdir(p):
                seen.add(p)
                shortcuts.append({"label": label, "path": p})
        except Exception:
            pass

    # 1. User Home Directory
    try:
        home_path = str(Path.home())
        add_shortcut("Home", home_path)
    except Exception:
        pass

    # 2. Configured Destination Library Directory
    if state.get("destination_dir"):
        add_shortcut("Media Library", state["destination_dir"])

    # 3. Configured Incoming / Source Directory
    if state.get("source_dir"):
        add_shortcut("Incoming", state["source_dir"])

    # 4. Custom QUICK_NAV_DIRS from environment / .env
    env_quick = os.environ.get("QUICK_NAV_DIRS", "")
    if env_quick:
        for item in env_quick.split(","):
            item = item.strip()
            if item:
                label = os.path.basename(item) or item
                add_shortcut(label, item)

    # 5. Common system mount paths if present
    for sys_path in ["/media", "/mnt", "/Volumes"]:
        if os.path.exists(sys_path) and os.path.isdir(sys_path):
            add_shortcut(sys_path, sys_path)

    # 6. Fallback Root
    if "/" not in seen:
        shortcuts.append({"label": "Root", "path": "/"})

    return {"shortcuts": shortcuts}


@app.get("/api/destinations")
def list_destinations():
    dest_dir = state["destination_dir"]
    if not os.path.exists(dest_dir):
        detected = detect_default_destination()
        if os.path.exists(detected):
            state["destination_dir"] = detected
            dest_dir = detected
        else:
            return {
                "base_path": dest_dir,
                "folders": [],
                "recent": state["recent_destinations"][:6],
            }

    subfolders = []
    # 1. Query indexed categories directly from SQLite (0.5ms vs 6+ seconds over FUSE)
    try:
        conn = get_db()
        cur = conn.execute("SELECT name FROM folders WHERE parent_path = ? ORDER BY LOWER(name)", (dest_dir,))
        subfolders = [r["name"] for r in cur.fetchall()]
        conn.close()
    except Exception as e:
        print(f"Error reading destinations from DB: {e}")

    # 2. Fallback to filesystem if not yet in SQLite index
    if not subfolders:
        try:
            with os.scandir(dest_dir) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False) and not entry.name.startswith("."):
                            subfolders.append(entry.name)
                    except (PermissionError, OSError):
                        continue
            subfolders.sort(key=lambda x: x.lower())
        except Exception as e:
            print(f"Error reading destinations from {dest_dir}: {e}")

    return {
        "base_path": dest_dir,
        "folders": subfolders,
        "recent": state["recent_destinations"][:6],
    }


@app.post("/api/destinations/create")
def create_destination_subfolder(req: CreateFolderRequest):
    folder_name = req.folder_name.strip()
    if not folder_name:
        raise HTTPException(status_code=400, detail="Folder name cannot be empty")
    
    sanitized = re.sub(r'[\/\\:\*\?"<>\|]', '', folder_name).strip()
    if not sanitized or sanitized.startswith("."):
        raise HTTPException(status_code=400, detail="Invalid folder name")

    dest_dir = state["destination_dir"]
    new_folder_path = os.path.join(dest_dir, sanitized)

    try:
        os.makedirs(new_folder_path, exist_ok=True)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create folder: {str(e)}")

    try:
        conn = get_db()
        with conn:
            conn.execute(
                "INSERT OR IGNORE INTO folders (path, name, parent_path, file_count, subfolder_count, last_scanned) VALUES (?, ?, ?, 0, 0, ?)",
                (new_folder_path, sanitized, dest_dir, time.time())
            )
        conn.close()
    except Exception as e:
        print(f"Error adding new folder to DB index: {e}")

    if sanitized not in state["recent_destinations"]:
        state["recent_destinations"].insert(0, sanitized)
        save_config_data()

    return {
        "success": True,
        "folder_name": sanitized,
        "folder_path": new_folder_path,
    }


# Non-Blocking Move Endpoint
@app.post("/api/move")
def move_video(req: MoveRequest):
    if not state["source_dir"]:
        raise HTTPException(status_code=400, detail="No source directory configured")

    source_path = resolve_video_path(req.file_id)
    if not source_path or not os.path.exists(source_path):
        raise HTTPException(status_code=404, detail="Source file not found")

    dest_folder = req.destination_folder.strip()
    if not dest_folder:
        raise HTTPException(status_code=400, detail="Destination folder cannot be empty")

    target_dir = os.path.join(state["destination_dir"], dest_folder)
    try:
        os.makedirs(target_dir, exist_ok=True)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to prepare destination: {e}")

    filename = os.path.basename(source_path)
    base, ext = os.path.splitext(filename)

    # Collision resolution considering disk and in-flight queue
    occupied_names = set()
    if os.path.exists(target_dir):
        try:
            occupied_names = set(os.listdir(target_dir))
        except Exception:
            pass

    for t in transfer_items_map.values():
        if t.get("status") in ("queued", "transferring") and t.get("destination_folder") == dest_folder:
            occupied_names.add(os.path.basename(t["target_path"]))

    final_name = filename
    collision_renamed = False
    if final_name in occupied_names:
        collision_renamed = True
        counter = 1
        while f"{base}_{counter}{ext}" in occupied_names:
            counter += 1
        final_name = f"{base}_{counter}{ext}"

    target_path = os.path.join(target_dir, final_name)
    file_size = os.path.getsize(source_path)

    # Check if this file has an active or queued pre-transcode task
    waiting_transcode_id = None
    for tid, t in transcode_items_map.items():
        if (t.get("filename") in (filename, req.file_id)) and t.get("status") in ("queued", "transcoding"):
            waiting_transcode_id = tid
            break

    # Create transfer task
    task_id = uuid.uuid4().hex[:10]
    transfer_item = {
        "id": task_id,
        "file_id": req.file_id,
        "filename": filename,
        "final_filename": final_name,
        "source_path": source_path,
        "target_path": target_path,
        "destination_folder": dest_folder,
        "total_bytes": file_size,
        "bytes_copied": 0,
        "percent": 0.0,
        "size_formatted": format_size(file_size),
        "status": "queued",
        "created_at": time.time(),
        "started_at": None,
        "completed_at": None,
        "error": None,
        "collision_renamed": collision_renamed,
        "waiting_on_transcode": waiting_transcode_id,
        "abort_event": threading.Event(),
    }

    transfer_items_map[task_id] = transfer_item
    transfers_history.append(transfer_item)
    transfer_queue.put_nowait(transfer_item)

    # Update recent destinations
    if dest_folder in state["recent_destinations"]:
        state["recent_destinations"].remove(dest_folder)
    state["recent_destinations"].insert(0, dest_folder)
    save_config_data()

    # Record in Undo Stack
    state["undo_stack"].append({
        "type": "move",
        "task_id": task_id,
        "source_path": source_path,
        "target_path": target_path,
        "filename": filename,
        "destination_folder": dest_folder,
    })

    # Optimistically remove from in-memory queue
    state["video_queue"] = [v for v in state["video_queue"] if v["id"] not in (req.file_id, filename)]

    return {
        "success": True,
        "task_id": task_id,
        "original_file": filename,
        "final_filename": final_name,
        "target_path": target_path,
        "destination_folder": dest_folder,
        "collision_renamed": collision_renamed,
        "remaining_count": len(state["video_queue"]),
        "status": "queued",
    }


# Smart Undo Endpoint
@app.post("/api/undo")
async def undo_last_move():
    if not state["undo_stack"]:
        raise HTTPException(status_code=400, detail="Nothing to undo")

    last_op = state["undo_stack"].pop()
    task_id = last_op.get("task_id")
    source_path = last_op["source_path"]
    target_path = last_op["target_path"]
    filename = last_op["filename"]

    item = transfer_items_map.get(task_id) if task_id else None

    # Case A: Task is still queued (0 wait cancellation!)
    if item and item.get("status") == "queued":
        item["status"] = "cancelled"
        cur_src = item.get("source_path") or source_path
        meta = make_video_meta(cur_src) or make_video_meta(source_path)
        if meta:
            state["video_queue"].insert(0, meta)
        return {
            "success": True,
            "action": "cancelled_queued",
            "restored_filename": filename,
            "message": f"Instantly cancelled transfer of {filename}",
            "remaining_undo_count": len(state["undo_stack"]),
        }

    # Case B: Task is actively transferring (abort clean!)
    if item and item.get("status") == "transferring":
        item["abort_event"].set()
        cur_src = item.get("source_path") or source_path
        meta = make_video_meta(cur_src) or make_video_meta(source_path)
        if meta:
            state["video_queue"].insert(0, meta)
        return {
            "success": True,
            "action": "aborted_active",
            "restored_filename": filename,
            "message": f"Aborted active transfer of {filename}",
            "remaining_undo_count": len(state["undo_stack"]),
        }

    # Case C: Transfer completed, reverse-move file back from destination
    if not os.path.exists(target_path):
        raise HTTPException(status_code=404, detail="File no longer exists at destination")

    restore_target = source_path
    if os.path.exists(restore_target):
        base, ext = os.path.splitext(source_path)
        counter = 1
        while os.path.exists(f"{base}_restored_{counter}{ext}"):
            counter += 1
        restore_target = f"{base}_restored_{counter}{ext}"

    try:
        await asyncio.to_thread(shutil.move, target_path, restore_target)
    except Exception as e:
        state["undo_stack"].append(last_op)
        raise HTTPException(status_code=500, detail=f"Failed to restore file: {str(e)}")

    meta = make_video_meta(restore_target)
    if meta:
        state["video_queue"].insert(0, meta)

    return {
        "success": True,
        "action": "restored",
        "restored_filename": os.path.basename(restore_target),
        "restored_path": restore_target,
        "remaining_undo_count": len(state["undo_stack"]),
    }


# Live Transfers & Pre-Transcoding Status Endpoint
@app.get("/api/transfers")
async def get_transfers_status():
    active_data = None
    if active_transfer:
        active_data = {
            "id": active_transfer["id"],
            "filename": active_transfer["filename"],
            "destination_folder": active_transfer["destination_folder"],
            "bytes_copied": active_transfer.get("bytes_copied", 0),
            "total_bytes": active_transfer.get("total_bytes", 0),
            "percent": active_transfer.get("percent", 0.0),
            "status": "transferring",
        }

    queued_list = []
    for t in transfer_items_map.values():
        if t.get("status") == "queued":
            queued_list.append({
                "id": t["id"],
                "filename": t["filename"],
                "destination_folder": t["destination_folder"],
                "size_formatted": t.get("size_formatted", ""),
                "status": "queued",
                "created_at": t.get("created_at"),
            })

    # Sort queued list by created_at
    queued_list.sort(key=lambda x: x.get("created_at", 0))

    recent_history = []
    for t in reversed(transfers_history[-20:]):
        if t.get("status") in ("completed", "failed", "cancelled"):
            recent_history.append({
                "id": t["id"],
                "filename": t["filename"],
                "destination_folder": t["destination_folder"],
                "status": t["status"],
                "error": t.get("error"),
                "completed_at": t.get("completed_at"),
            })

    total_pending = (1 if active_data else 0) + len(queued_list)

    # Pre-transcode status
    transcode_active_data = None
    if active_transcode:
        transcode_active_data = {
            "id": active_transcode["id"],
            "filename": active_transcode["filename"],
            "percent": active_transcode.get("percent", 0.0),
            "fps": active_transcode.get("fps", 0.0),
            "speed": active_transcode.get("speed", ""),
            "eta": active_transcode.get("eta", ""),
            "method": active_transcode.get("method", "reencode"),
            "status": "transcoding",
        }

    transcode_queued_list = []
    for t in transcode_items_map.values():
        if t.get("status") == "queued":
            transcode_queued_list.append({
                "id": t["id"],
                "filename": t["filename"],
                "status": "queued",
                "created_at": t.get("created_at"),
            })
    transcode_queued_list.sort(key=lambda x: x.get("created_at", 0))

    transcode_history_list = []
    for t in reversed(transcode_history[-15:]):
        if t.get("status") in ("completed", "failed", "cancelled"):
            transcode_history_list.append({
                "id": t["id"],
                "filename": t["filename"],
                "target_filename": os.path.basename(t["target_path"]) if t.get("target_path") else None,
                "status": t["status"],
                "error": t.get("error"),
                "method": t.get("method"),
                "completed_at": t.get("completed_at"),
            })

    return {
        "active": active_data,
        "queued": queued_list,
        "history": recent_history,
        "active_count": 1 if active_data else 0,
        "queued_count": len(queued_list),
        "total_pending": total_pending,
        "transcode": {
            "active": transcode_active_data,
            "queued": transcode_queued_list,
            "queued_count": len(transcode_queued_list),
            "history": transcode_history_list,
            "is_paused": transcode_paused,
            "total_pending": (1 if transcode_active_data else 0) + len(transcode_queued_list),
        }
    }


@app.post("/api/transfers/cancel")
async def cancel_transfer(req: CancelTransferRequest):
    item = transfer_items_map.get(req.task_id)
    if not item:
        raise HTTPException(status_code=404, detail="Transfer task not found")

    if item.get("status") == "queued":
        item["status"] = "cancelled"
        # Restore to queue if in current source dir
        if state["source_dir"] and item["source_path"].startswith(state["source_dir"]):
            meta = make_video_meta(item["source_path"])
            if meta:
                state["video_queue"].insert(0, meta)
        return {"success": True, "action": "cancelled"}

    if item.get("status") == "transferring":
        item["abort_event"].set()
        if state["source_dir"] and item["source_path"].startswith(state["source_dir"]):
            meta = make_video_meta(item["source_path"])
            if meta:
                state["video_queue"].insert(0, meta)
        return {"success": True, "action": "aborted"}

    return {"success": False, "detail": f"Cannot cancel transfer in state {item.get('status')}"}


@app.post("/api/transcode/pause")
async def toggle_transcode_pause():
    global transcode_paused
    transcode_paused = not transcode_paused
    if transcode_paused:
        transcode_pause_event.clear()
    else:
        transcode_pause_event.set()
    return {
        "success": True,
        "is_paused": transcode_paused,
    }


@app.post("/api/transcode/cancel")
async def cancel_transcode_task(req: CancelTransferRequest):
    item = transcode_items_map.get(req.task_id)
    if not item:
        raise HTTPException(status_code=404, detail="Transcode task not found")

    if item.get("status") == "queued":
        item["status"] = "cancelled"
        return {"success": True, "action": "cancelled"}

    if item.get("status") == "transcoding":
        item["abort_event"].set()
        return {"success": True, "action": "aborted"}

    return {"success": False, "detail": f"Cannot cancel transcode in state {item.get('status')}"}


@app.post("/api/transcode/cancel_all")
async def cancel_all_transcode_tasks():
    cancelled_count = 0
    for item in transcode_items_map.values():
        if item.get("status") == "queued":
            item["status"] = "cancelled"
            cancelled_count += 1
    while not transcode_queue.empty():
        try:
            it = transcode_queue.get_nowait()
            it["status"] = "cancelled"
            transcode_queue.task_done()
        except (asyncio.QueueEmpty, ValueError, Exception):
            break
    return {
        "success": True,
        "cancelled_count": cancelled_count,
    }


@app.get("/api/transcode/status")
async def get_transcode_status():
    active_data = None
    if active_transcode:
        active_data = {
            "id": active_transcode["id"],
            "filename": active_transcode["filename"],
            "source_path": active_transcode.get("source_path"),
            "target_path": active_transcode.get("target_path"),
            "percent": active_transcode.get("percent", 0.0),
            "fps": active_transcode.get("fps", 0.0),
            "speed": active_transcode.get("speed", ""),
            "eta": active_transcode.get("eta", ""),
            "method": active_transcode.get("method", "reencode"),
            "status": "transcoding",
        }

    queued_list = []
    for t in transcode_items_map.values():
        if t.get("status") == "queued":
            queued_list.append({
                "id": t["id"],
                "filename": t["filename"],
                "source_path": t.get("source_path"),
                "status": "queued",
                "created_at": t.get("created_at"),
            })
    queued_list.sort(key=lambda x: x.get("created_at", 0))

    recent_history = []
    for t in reversed(transcode_history[-20:]):
        if t.get("status") in ("completed", "failed", "cancelled"):
            t_size = t.get("target_size", 0)
            recent_history.append({
                "id": t["id"],
                "filename": t["filename"],
                "source_path": t.get("source_path"),
                "target_path": t.get("target_path"),
                "target_filename": os.path.basename(t["target_path"]) if t.get("target_path") else None,
                "target_size": t_size,
                "target_size_formatted": format_size(t_size) if t_size else "",
                "status": t["status"],
                "error": t.get("error"),
                "method": t.get("method"),
                "completed_at": t.get("completed_at"),
            })

    return {
        "active": active_data,
        "queued": queued_list,
        "history": recent_history,
        "is_paused": transcode_paused,
        "queued_count": len(queued_list),
        "total_pending": (1 if active_data else 0) + len(queued_list),
    }


@app.post("/api/rename")
def rename_video(req: RenameRequest):
    if not state["source_dir"]:
        raise HTTPException(status_code=400, detail="No source directory configured")

    source_path = resolve_video_path(req.file_id)
    if not source_path or not os.path.exists(source_path):
        raise HTTPException(status_code=404, detail="File not found")

    actual_file_id = os.path.basename(source_path)
    new_name = req.new_name.strip()
    if not new_name:
        raise HTTPException(status_code=400, detail="New name cannot be empty")

    _, orig_ext = os.path.splitext(actual_file_id)
    _, new_ext = os.path.splitext(new_name)
    if not new_ext:
        new_name += orig_ext

    sanitized_name = re.sub(r'[\/\\:\*\?"<>\|]', '', new_name).strip()
    target_path = os.path.join(state["source_dir"], sanitized_name)

    if os.path.exists(target_path) and target_path != source_path:
        raise HTTPException(status_code=400, detail="A file with this name already exists")

    # Cancel any active/queued transcode for the old file
    for t in transcode_items_map.values():
        if t.get("filename") in (req.file_id, actual_file_id) and t.get("status") in ("queued", "transcoding"):
            t["abort_event"].set()
            t["status"] = "cancelled"

    try:
        os.rename(source_path, target_path)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to rename file: {str(e)}")

    # Update in queue
    meta = make_video_meta(target_path)
    state["video_queue"] = [meta if v["id"] in (req.file_id, actual_file_id) else v for v in state["video_queue"]]

    return {
        "success": True,
        "old_id": req.file_id,
        "new_id": sanitized_name,
        "new_filename": sanitized_name,
    }


@app.post("/api/delete")
def delete_video(req: DeleteRequest):
    if not state["source_dir"]:
        raise HTTPException(status_code=400, detail="No source directory configured")

    source_path = resolve_video_path(req.file_id)
    if not source_path or not os.path.exists(source_path):
        raise HTTPException(status_code=404, detail="File not found")

    actual_file_id = os.path.basename(source_path)

    # Cancel any active/queued transcode for this file
    for t in transcode_items_map.values():
        if t.get("filename") in (req.file_id, actual_file_id) and t.get("status") in ("queued", "transcoding"):
            t["abort_event"].set()
            t["status"] = "cancelled"

    try:
        os.remove(source_path)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete file: {str(e)}")

    state["video_queue"] = [v for v in state["video_queue"] if v["id"] not in (req.file_id, actual_file_id)]

    return {
        "success": True,
        "deleted_file": req.file_id,
        "remaining_count": len(state["video_queue"]),
    }


# Video Streaming
@app.api_route("/api/stream/{file_id}", methods=["GET", "HEAD"])
async def stream_video(file_id: str, request: Request):
    if not state["source_dir"]:
        raise HTTPException(status_code=400, detail="No source directory configured")

    file_path = resolve_video_path(file_id)
    if not file_path or not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Video file not found")

    file_size = os.path.getsize(file_path)
    mime_type, _ = mimetypes.guess_type(file_path)
    if not mime_type:
        mime_type = "video/mp4"

    if request.method == "HEAD":
        headers = {
            "Content-Length": str(file_size),
            "Content-Type": mime_type,
            "Accept-Ranges": "bytes",
        }
        return Response(status_code=200, headers=headers)

    range_header = request.headers.get("Range")

    if not range_header:
        async def full_file_sender():
            with open(file_path, "rb") as f:
                while chunk := f.read(1024 * 1024):
                    yield chunk

        headers = {
            "Content-Length": str(file_size),
            "Content-Type": mime_type,
            "Accept-Ranges": "bytes",
        }
        return StreamingResponse(full_file_sender(), status_code=200, headers=headers)

    # Parse Range: bytes=start-end
    try:
        range_val = range_header.strip().replace("bytes=", "")
        parts = range_val.split("-")
        start = int(parts[0]) if parts[0] else 0
        end = int(parts[1]) if len(parts) > 1 and parts[1] else file_size - 1

        if start >= file_size or end >= file_size or start > end:
            return Response(
                status_code=416,
                headers={"Content-Range": f"bytes */{file_size}"}
            )

        chunk_max = 2 * 1024 * 1024
        if (end - start + 1) > chunk_max:
            end = start + chunk_max - 1

        content_length = end - start + 1

        def chunk_generator(filepath, start_pos, length):
            with open(filepath, "rb") as f:
                f.seek(start_pos)
                bytes_left = length
                while bytes_left > 0:
                    read_size = min(bytes_left, 64 * 1024)
                    data = f.read(read_size)
                    if not data:
                        break
                    bytes_left -= len(data)
                    yield data

        headers = {
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Accept-Ranges": "bytes",
            "Content-Length": str(content_length),
            "Content-Type": mime_type,
        }
        return StreamingResponse(
            chunk_generator(file_path, start, content_length),
            status_code=206,
            headers=headers
        )
    except Exception as e:
        print(f"Error handling Range request for {file_id}: {e}")
        raise HTTPException(status_code=500, detail="Error streaming video range")


# Transcoding
@app.get("/api/transcode/{file_id}")
async def transcode_video(file_id: str, request: Request, ss: Optional[float] = Query(0.0)):
    if not state["source_dir"]:
        raise HTTPException(status_code=400, detail="No source directory configured")

    file_path = resolve_video_path(file_id)
    if not file_path or not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Video file not found")

    ffmpeg_cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
    ]

    if ss and ss > 0:
        ffmpeg_cmd.extend(["-ss", str(ss)])

    ffmpeg_cmd.extend([
        "-i", file_path,
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-tune", "zerolatency",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "128k",
        "-ac", "2",
        "-movflags", "frag_keyframe+empty_moov+default_base_moof",
        "-f", "mp4",
        "pipe:1"
    ])

    process = await asyncio.create_subprocess_exec(
        *ffmpeg_cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL
    )

    async def transcode_stream():
        try:
            while True:
                chunk = await process.stdout.read(64 * 1024)
                if not chunk:
                    break
                yield chunk
        finally:
            try:
                process.terminate()
                await process.wait()
            except Exception:
                pass

    return StreamingResponse(transcode_stream(), media_type="video/mp4")


# ==========================================
# Media Library & Media Viewer Endpoints
# ==========================================

def validate_backup_path(path_str: Optional[str]) -> str:
    """Validates that a path resides strictly within the destination directory."""
    base_dir = os.path.abspath(os.path.normpath(state.get("destination_dir") or DEFAULT_DESTINATION_DIR))
    if not path_str or not path_str.strip():
        return base_dir
    clean_path = urllib.parse.unquote(path_str.strip())
    norm_path = os.path.abspath(os.path.normpath(clean_path))
    try:
        common = os.path.commonpath([base_dir, norm_path])
        if common != base_dir:
            raise HTTPException(status_code=403, detail="Access denied: path is outside Backup directory")
    except ValueError:
        raise HTTPException(status_code=403, detail="Invalid path")
    return norm_path


async def generate_thumbnail(file_path: str, is_video: bool, update_db: bool = False) -> Optional[Path]:
    thumb_path = get_thumbnail_cache_path(file_path)
    if thumb_path.exists() and thumb_path.stat().st_size > 0:
        return thumb_path

    temp_thumb = thumb_path.with_suffix(f".{uuid.uuid4().hex[:8]}.tmp.jpg")
    try:
        if is_video:
            cmd = [
                "nice", "-n", "15",
                "ffmpeg", "-y", "-loglevel", "error",
                "-ss", "00:00:01",
                "-i", file_path,
                "-an", "-sn", "-dn",
                "-frames:v", "1",
                "-update", "1",
                "-vf", "scale=360:-2:flags=fast_bilinear",
                "-q:v", "3",
                str(temp_thumb)
            ]
        else:
            cmd = [
                "nice", "-n", "15",
                "ffmpeg", "-y", "-loglevel", "error",
                "-i", file_path,
                "-frames:v", "1",
                "-update", "1",
                "-vf", "scale=360:-2:flags=fast_bilinear",
                "-q:v", "3",
                str(temp_thumb)
            ]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL
        )
        try:
            await asyncio.wait_for(proc.communicate(), timeout=8.0)
        except asyncio.TimeoutError:
            try:
                proc.kill()
                await proc.communicate()
            except Exception:
                pass

        # If video is shorter than 1s or ss 1 failed, retry at 00:00:00
        if is_video and (proc.returncode != 0 or not temp_thumb.exists() or temp_thumb.stat().st_size == 0):
            retry_cmd = [
                "nice", "-n", "15",
                "ffmpeg", "-y", "-loglevel", "error",
                "-ss", "00:00:00",
                "-i", file_path,
                "-an", "-sn", "-dn",
                "-frames:v", "1",
                "-update", "1",
                "-vf", "scale=360:-2:flags=fast_bilinear",
                "-q:v", "3",
                str(temp_thumb)
            ]
            proc = await asyncio.create_subprocess_exec(
                *retry_cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL
            )
            try:
                await asyncio.wait_for(proc.communicate(), timeout=8.0)
            except asyncio.TimeoutError:
                try:
                    proc.kill()
                    await proc.communicate()
                except Exception:
                    pass

        if temp_thumb.exists() and temp_thumb.stat().st_size > 0:
            temp_thumb.rename(thumb_path)
            if update_db:
                try:
                    conn = get_db()
                    with conn:
                        conn.execute("UPDATE files SET has_thumb = 1 WHERE path = ?", (file_path,))
                    conn.close()
                except Exception:
                    pass
            return thumb_path
    except Exception as e:
        print(f"Thumbnail generation error for {file_path}: {e}")
    finally:
        if temp_thumb.exists():
            try:
                temp_thumb.unlink()
            except OSError:
                pass
    return None


def enqueue_transcode_for_backup(folder_path: Optional[str] = None, file_path: Optional[str] = None) -> List[Dict[str, Any]]:
    enqueued = []
    files_to_check = []
    if file_path:
        real_file = validate_backup_path(file_path)
        if os.path.exists(real_file) and os.path.isfile(real_file):
            files_to_check.append(real_file)
    elif folder_path:
        real_folder = validate_backup_path(folder_path)
        if os.path.exists(real_folder) and os.path.isdir(real_folder):
            try:
                with os.scandir(real_folder) as entries:
                    for entry in entries:
                        if entry.is_file(follow_symlinks=False) and not entry.name.startswith("."):
                            files_to_check.append(entry.path)
            except Exception as e:
                print(f"Error scanning backup folder for transcode: {e}")

    for fpath in files_to_check:
        ext = os.path.splitext(fpath)[1].lower()
        if ext in VIDEO_EXTENSIONS and ext != ".mp4":
            fname = os.path.basename(fpath)
            existing = False
            for t in transcode_items_map.values():
                if t.get("source_path") == fpath and t.get("status") in ("queued", "transcoding", "completed"):
                    existing = True
                    break
            if not existing:
                task_id = uuid.uuid4().hex[:10]
                item = {
                    "id": task_id,
                    "file_id": fname,
                    "filename": fname,
                    "source_path": fpath,
                    "target_path": None,
                    "status": "queued",
                    "percent": 0.0,
                    "fps": 0.0,
                    "speed": "",
                    "eta": "",
                    "method": "pending",
                    "created_at": time.time(),
                    "started_at": None,
                    "completed_at": None,
                    "error": None,
                    "abort_event": threading.Event(),
                }
                transcode_items_map[task_id] = item
                transcode_queue.put_nowait(item)
                enqueued.append({
                    "id": task_id,
                    "filename": fname,
                    "source_path": fpath,
                })

    return enqueued


def enqueue_all_transcodes_for_backup() -> List[Dict[str, Any]]:
    conn = get_db()
    try:
        cur = conn.execute(
            "SELECT path, name, size FROM files WHERE type = 'video' AND LOWER(path) NOT LIKE '%.mp4' ORDER BY size ASC"
        )
        rows = cur.fetchall()
    except Exception as e:
        print(f"Error querying non-mp4 files for bulk transcode: {e}")
        rows = []
    finally:
        conn.close()

    enqueued = []
    for r in rows:
        fpath = r["path"]
        if not os.path.exists(fpath):
            continue

        existing = False
        for t in transcode_items_map.values():
            if t.get("source_path") == fpath and t.get("status") in ("queued", "transcoding", "completed"):
                existing = True
                break

        if not existing:
            fname = r["name"]
            task_id = uuid.uuid4().hex[:10]
            item = {
                "id": task_id,
                "file_id": fname,
                "filename": fname,
                "source_path": fpath,
                "target_path": None,
                "status": "queued",
                "percent": 0.0,
                "fps": 0.0,
                "speed": "",
                "eta": "",
                "method": "pending",
                "created_at": time.time(),
                "started_at": None,
                "completed_at": None,
                "error": None,
                "abort_event": threading.Event(),
            }
            transcode_items_map[task_id] = item
            transcode_queue.put_nowait(item)
            enqueued.append({
                "id": task_id,
                "filename": fname,
                "source_path": fpath,
            })

    return enqueued






@app.get("/api/backup/browse")
async def browse_backup_library(path: Optional[str] = Query(None), refresh: Optional[bool] = Query(False)):
    base_dir = os.path.realpath(state.get("destination_dir") or DEFAULT_DESTINATION_DIR)
    target_path = validate_backup_path(path)
    
    if not os.path.exists(target_path):
        target_path = base_dir

    if not os.path.isdir(target_path):
        target_path = os.path.dirname(target_path) or base_dir

    rel_path = os.path.relpath(target_path, base_dir)
    is_root = (rel_path == ".")

    breadcrumbs = [{"name": os.path.basename(base_dir) or "Library", "path": base_dir}]
    if not is_root:
        parts = Path(rel_path).parts
        accum = base_dir
        for p in parts:
            accum = os.path.join(accum, p)
            breadcrumbs.append({"name": p, "path": accum})

    # Read top-level root folders for sidebar with item counts directly from SQLite (0.8ms vs 6+s over FUSE)
    root_folders = []
    conn = get_db()
    try:
        cur = conn.execute("SELECT name, path, file_count, subfolder_count FROM folders WHERE parent_path = ? ORDER BY LOWER(name)", (base_dir,))
        for r in cur.fetchall():
            root_folders.append({
                "name": r["name"],
                "path": r["path"],
                "file_count": r["file_count"],
                "subfolder_count": r["subfolder_count"]
            })
    except Exception as e:
        print(f"Error reading root folders from DB: {e}")
    finally:
        conn.close()

    # Fallback to filesystem only if SQLite index is empty
    if not root_folders:
        try:
            def _scan_roots():
                items = []
                with os.scandir(base_dir) as entries:
                    for entry in entries:
                        if entry.is_dir(follow_symlinks=False) and not entry.name.startswith("."):
                            items.append({
                                "name": entry.name,
                                "path": entry.path,
                                "file_count": 0,
                                "subfolder_count": 0
                            })
                items.sort(key=lambda x: x["name"].lower())
                return items
            root_folders = await asyncio.to_thread(_scan_roots)
        except Exception as e:
            print(f"Error reading root folders from {base_dir}: {e}")

    subfolders = []
    files = []

    if not is_root:
        is_indexed = False
        if not refresh:
            conn = get_db()
            try:
                cur = conn.execute("SELECT last_scanned FROM folders WHERE path = ?", (target_path,))
                row = cur.fetchone()
                if row and row["last_scanned"] > 0:
                    f_cur = conn.execute("SELECT * FROM files WHERE folder_path = ? ORDER BY LOWER(name) ASC", (target_path,))
                    for r in f_cur.fetchall():
                        ext = r["ext"]
                        is_vid = (r["type"] == "video")
                        is_native = ext in NATIVE_EXTENSIONS
                        fpath = r["path"]
                        media_url = (
                            f"/api/backup/media?path={urllib.parse.quote(fpath)}"
                            if (not is_vid or is_native)
                            else f"/api/backup/transcode_stream?path={urllib.parse.quote(fpath)}"
                        )
                        files.append({
                            "name": r["name"],
                            "path": fpath,
                            "type": r["type"],
                            "ext": ext,
                            "size": r["size"],
                            "size_formatted": r["size_formatted"],
                            "mtime": r["mtime"],
                            "needs_transcode": bool(r["needs_transcode"]),
                            "has_thumb": bool(r["has_thumb"]) if "has_thumb" in r.keys() else False,
                            "thumb_url": f"/api/backup/thumbnail?path={urllib.parse.quote(fpath)}",
                            "media_url": media_url,
                        })

                    sf_cur = conn.execute("SELECT path, name FROM folders WHERE parent_path = ? ORDER BY LOWER(name) ASC", (target_path,))
                    for sr in sf_cur.fetchall():
                        subfolders.append({
                            "name": sr["name"],
                            "path": sr["path"],
                        })
                    is_indexed = True
            except Exception as e:
                print(f"DB read error for {target_path}: {e}")
            finally:
                conn.close()

        # If not indexed yet or force refresh requested, index it on demand!
        if not is_indexed:
            await asyncio.to_thread(index_folder, target_path, base_dir)
            conn = get_db()
            try:
                f_cur = conn.execute("SELECT * FROM files WHERE folder_path = ? ORDER BY LOWER(name) ASC", (target_path,))
                for r in f_cur.fetchall():
                    ext = r["ext"]
                    is_vid = (r["type"] == "video")
                    is_native = ext in NATIVE_EXTENSIONS
                    fpath = r["path"]
                    media_url = (
                        f"/api/backup/media?path={urllib.parse.quote(fpath)}"
                        if (not is_vid or is_native)
                        else f"/api/backup/transcode_stream?path={urllib.parse.quote(fpath)}"
                    )
                    files.append({
                        "name": r["name"],
                        "path": fpath,
                        "type": r["type"],
                        "ext": ext,
                        "size": r["size"],
                        "size_formatted": r["size_formatted"],
                        "mtime": r["mtime"],
                        "needs_transcode": bool(r["needs_transcode"]),
                        "has_thumb": bool(r["has_thumb"]) if "has_thumb" in r.keys() else False,
                        "thumb_url": f"/api/backup/thumbnail?path={urllib.parse.quote(fpath)}",
                        "media_url": media_url,
                    })

                sf_cur = conn.execute("SELECT path, name FROM folders WHERE parent_path = ? ORDER BY LOWER(name) ASC", (target_path,))
                for sr in sf_cur.fetchall():
                    subfolders.append({
                        "name": sr["name"],
                        "path": sr["path"],
                    })
            except Exception as e:
                print(f"DB read error after indexing {target_path}: {e}")
            finally:
                conn.close()

        # Auto-enqueue non-MP4 videos for background conversion if browsing a folder
        enqueue_transcode_for_backup(folder_path=target_path)
        # Prioritize thumbnail generation for this folder
        enqueue_folder_thumbnails(folder_path=target_path)
    else:
        # At root, subfolders are the root folders
        subfolders = [{"name": rf["name"], "path": rf["path"]} for rf in root_folders]

    return {
        "base_path": base_dir,
        "current_path": target_path,
        "relative_path": "" if is_root else rel_path,
        "is_root": is_root,
        "breadcrumbs": breadcrumbs,
        "root_folders": root_folders,
        "subfolders": subfolders,
        "files": files,
        "total_files": len(files),
        "total_subfolders": len(subfolders),
    }


@app.api_route("/api/backup/thumbnail", methods=["GET", "HEAD"])
async def get_backup_thumbnail(path: str = Query(...), request: Request = None):
    real_path = validate_backup_path(path)
    if not os.path.exists(real_path) or not os.path.isfile(real_path):
        raise HTTPException(status_code=404, detail="File not found")

    ext = os.path.splitext(real_path)[1].lower()
    is_video = ext in VIDEO_EXTENSIONS
    is_img = ext in IMAGE_EXTENSIONS
    if not is_video and not is_img:
        raise HTTPException(status_code=400, detail="Unsupported media file type")

    thumb = await generate_thumbnail(real_path, is_video=is_video, update_db=True)
    if thumb and thumb.exists():
        if request and request.method == "HEAD":
            return Response(
                status_code=200,
                headers={"Content-Length": str(thumb.stat().st_size), "Content-Type": "image/jpeg"}
            )
        return FileResponse(
            str(thumb),
            media_type="image/jpeg",
            headers={"Cache-Control": "public, max-age=86400"}
        )

    if is_img:
        mime, _ = mimetypes.guess_type(real_path)
        mime_type = mime or "image/jpeg"
        if request and request.method == "HEAD":
            return Response(
                status_code=200,
                headers={"Content-Length": str(os.path.getsize(real_path)), "Content-Type": mime_type}
            )
        return FileResponse(
            real_path,
            media_type=mime_type,
            headers={"Cache-Control": "public, max-age=86400"}
        )

    raise HTTPException(status_code=500, detail="Could not generate thumbnail")


@app.api_route("/api/backup/media", methods=["GET", "HEAD"])
async def stream_backup_media(path: str = Query(...), request: Request = None):
    real_path = validate_backup_path(path)
    if not os.path.exists(real_path) or not os.path.isfile(real_path):
        raise HTTPException(status_code=404, detail="File not found")

    ext = os.path.splitext(real_path)[1].lower()
    mime_type, _ = mimetypes.guess_type(real_path)
    file_size = os.path.getsize(real_path)

    if ext in IMAGE_EXTENSIONS:
        if not mime_type:
            mime_type = "image/jpeg"
        if request.method == "HEAD":
            return Response(
                status_code=200,
                headers={"Content-Length": str(file_size), "Content-Type": mime_type}
            )
        return FileResponse(
            real_path,
            media_type=mime_type,
            headers={"Cache-Control": "public, max-age=3600"}
        )

    if not mime_type:
        mime_type = "video/mp4"

    if request.method == "HEAD":
        headers = {
            "Content-Length": str(file_size),
            "Content-Type": mime_type,
            "Accept-Ranges": "bytes",
        }
        return Response(status_code=200, headers=headers)

    range_header = request.headers.get("Range")
    if not range_header:
        async def full_file_sender():
            with open(real_path, "rb") as f:
                while chunk := f.read(1024 * 1024):
                    yield chunk

        headers = {
            "Content-Length": str(file_size),
            "Content-Type": mime_type,
            "Accept-Ranges": "bytes",
        }
        return StreamingResponse(full_file_sender(), status_code=200, headers=headers)

    try:
        range_val = range_header.strip().replace("bytes=", "")
        parts = range_val.split("-")
        start = int(parts[0]) if parts[0] else 0
        end = int(parts[1]) if len(parts) > 1 and parts[1] else file_size - 1

        if start >= file_size or end >= file_size or start > end:
            return Response(
                status_code=416,
                headers={"Content-Range": f"bytes */{file_size}"}
            )

        chunk_max = 2 * 1024 * 1024
        if (end - start + 1) > chunk_max:
            end = start + chunk_max - 1

        content_length = end - start + 1

        def chunk_generator(filepath, start_pos, length):
            with open(filepath, "rb") as f:
                f.seek(start_pos)
                bytes_left = length
                while bytes_left > 0:
                    read_size = min(bytes_left, 64 * 1024)
                    data = f.read(read_size)
                    if not data:
                        break
                    bytes_left -= len(data)
                    yield data

        headers = {
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Accept-Ranges": "bytes",
            "Content-Length": str(content_length),
            "Content-Type": mime_type,
        }
        return StreamingResponse(
            chunk_generator(real_path, start, content_length),
            status_code=206,
            headers=headers
        )
    except Exception as e:
        print(f"Error streaming range for {real_path}: {e}")
        raise HTTPException(status_code=500, detail="Error streaming media")


@app.post("/api/backup/rename")
def backup_rename(req: BackupRenameRequest):
    real_path = validate_backup_path(req.path)
    if not os.path.exists(real_path) or not os.path.isfile(real_path):
        raise HTTPException(status_code=404, detail="File not found")

    new_name = req.new_name.strip()
    if not new_name or "/" in new_name or "\\" in new_name or new_name.startswith("."):
        raise HTTPException(status_code=400, detail="Invalid filename")

    dir_path = os.path.dirname(real_path)
    target_path = os.path.join(dir_path, new_name)

    if target_path == real_path:
        return {"success": True, "path": target_path, "name": new_name}

    if os.path.exists(target_path):
        raise HTTPException(status_code=400, detail="A file with this name already exists")

    try:
        os.rename(real_path, target_path)
        update_file_in_index(real_path, target_path, new_name)
        old_thumb = get_thumbnail_cache_path(real_path)
        if old_thumb.exists():
            try:
                old_thumb.unlink()
            except OSError:
                pass
        return {
            "success": True,
            "old_path": real_path,
            "new_path": target_path,
            "new_name": new_name,
            "thumb_url": f"/api/backup/thumbnail?path={urllib.parse.quote(target_path)}",
            "media_url": f"/api/backup/media?path={urllib.parse.quote(target_path)}"
        }
    except Exception as e:
        print(f"Error renaming {real_path} to {target_path}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to rename: {str(e)}")


@app.post("/api/backup/delete")
def backup_delete(req: BackupDeleteRequest):
    real_path = validate_backup_path(req.path)
    if not os.path.exists(real_path):
        raise HTTPException(status_code=404, detail="File not found")

    if not os.path.isfile(real_path):
        raise HTTPException(status_code=400, detail="Only files can be deleted")

    try:
        thumb = get_thumbnail_cache_path(real_path)
        if thumb.exists():
            try:
                thumb.unlink()
            except OSError:
                pass

        os.remove(real_path)
        remove_file_from_index(real_path)
        return {"success": True, "deleted_path": real_path}
    except Exception as e:
        print(f"Error deleting {real_path}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to delete: {str(e)}")


@app.post("/api/backup/transcode/enqueue")
async def backup_enqueue_transcode(req: BackupTranscodeRequest):
    enqueued = enqueue_transcode_for_backup(folder_path=req.folder_path, file_path=req.file_path)
    return {
        "success": True,
        "enqueued_count": len(enqueued),
        "enqueued": enqueued,
    }


@app.get("/api/backup/transcode/summary")
async def get_backup_transcode_summary():
    conn = get_db()
    try:
        cur = conn.execute(
            "SELECT path, name, size FROM files WHERE type = 'video' AND LOWER(path) NOT LIKE '%.mp4'"
        )
        rows = cur.fetchall()
    except Exception as e:
        print(f"Error querying transcode summary: {e}")
        rows = []
    finally:
        conn.close()

    active_or_queued_paths = set()
    for t in transcode_items_map.values():
        if t.get("status") in ("queued", "transcoding", "completed"):
            active_or_queued_paths.add(t.get("source_path"))

    pending_files = []
    breakdown = {}
    total_bytes = 0

    for r in rows:
        p = r["path"]
        if p in active_or_queued_paths:
            continue
        ext = os.path.splitext(p)[1].lower()
        breakdown[ext] = breakdown.get(ext, 0) + 1
        total_bytes += (r["size"] or 0)
        pending_files.append(r)

    sorted_breakdown = dict(sorted(breakdown.items(), key=lambda item: item[1], reverse=True))

    return {
        "pending_count": len(pending_files),
        "total_bytes": total_bytes,
        "total_size_formatted": format_size(total_bytes),
        "breakdown": sorted_breakdown,
        "already_queued_count": len(active_or_queued_paths),
    }


@app.post("/api/backup/transcode/enqueue_all")
async def backup_enqueue_all_transcode():
    enqueued = enqueue_all_transcodes_for_backup()
    return {
        "success": True,
        "enqueued_count": len(enqueued),
    }


@app.get("/api/backup/transcode_stream")
async def stream_backup_transcode(path: str = Query(...), ss: Optional[float] = Query(0.0)):
    real_path = validate_backup_path(path)
    if not os.path.exists(real_path) or not os.path.isfile(real_path):
        raise HTTPException(status_code=404, detail="Video file not found")

    ffmpeg_cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
    ]

    if ss and ss > 0:
        ffmpeg_cmd.extend(["-ss", str(ss)])

    ffmpeg_cmd.extend([
        "-i", real_path,
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-tune", "zerolatency",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "128k",
        "-ac", "2",
        "-movflags", "frag_keyframe+empty_moov+default_base_moof",
        "-f", "mp4",
        "pipe:1"
    ])

    process = await asyncio.create_subprocess_exec(
        *ffmpeg_cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL
    )

    async def transcode_stream():
        try:
            while True:
                chunk = await process.stdout.read(64 * 1024)
                if not chunk:
                    break
                yield chunk
        finally:
            try:
                process.terminate()
                await process.wait()
            except Exception:
                pass

    return StreamingResponse(transcode_stream(), media_type="video/mp4")


# ==============================================================================
# Media Library SQLite Index Endpoints
# ==============================================================================

@app.get("/api/backup/index/status")
async def get_backup_index_status():
    conn = get_db()
    total_files = 0
    total_folders = 0
    try:
        cur_files = conn.execute("SELECT COUNT(*) FROM files")
        total_files = cur_files.fetchone()[0]
        cur_folders = conn.execute("SELECT COUNT(*) FROM folders")
        total_folders = cur_folders.fetchone()[0]
    except Exception as e:
        print(f"Error reading index counts: {e}")
        total_files = indexer_state.get("total_files", 0)
        total_folders = indexer_state.get("total_folders", 0)
    finally:
        conn.close()

    return {
        "status": indexer_state["status"],
        "scanned_folders": indexer_state["scanned_folders"],
        "total_folders": total_folders or indexer_state["total_folders"],
        "total_files": total_files,
        "current_folder": indexer_state["current_folder"],
        "last_synced": indexer_state["last_synced"],
    }


@app.post("/api/backup/index/sync")
async def trigger_backup_sync():
    if indexer_state["status"] == "crawling":
        return {"success": False, "message": "Crawler is already running", "status": indexer_state["status"]}
    asyncio.create_task(run_background_crawler())
    return {"success": True, "message": "Index crawl started", "status": "crawling"}


@app.get("/api/backup/search")
async def search_backup(
    q: str = Query(..., min_length=1),
    type: Optional[str] = Query(None),
    sort: Optional[str] = Query("name_asc"),
    limit: Optional[int] = Query(150),
):
    results = search_backup_index(query=q, type_filter=type, sort_by=sort, limit=limit)
    return {
        "query": q,
        "total": len(results),
        "results": results,
    }


# ==============================================================================
# Thumbnail Generator API Endpoints
# ==============================================================================

class ThumbnailGenerateRequest(BaseModel):
    folder_path: Optional[str] = None


@app.get("/api/backup/thumbnails/status")
async def get_thumbnails_status():
    return {
        "status": thumbnail_worker_state["status"],
        "total_files": thumbnail_worker_state["total_files"],
        "cached_count": thumbnail_worker_state["cached_count"],
        "pending_count": thumbnail_worker_state["pending_count"],
        "percent": thumbnail_worker_state["percent"],
        "current_file": thumbnail_worker_state["current_file"],
        "current_folder": thumbnail_worker_state["current_folder"],
        "processed_count": thumbnail_worker_state["processed_count"],
        "is_paused": not thumbnail_pause_event.is_set(),
    }


@app.post("/api/backup/thumbnails/pause")
async def toggle_thumbnail_pause():
    if thumbnail_pause_event.is_set():
        thumbnail_pause_event.clear()
        thumbnail_worker_state["status"] = "paused"
        return {"success": True, "is_paused": True, "message": "Thumbnail generator paused"}
    else:
        thumbnail_pause_event.set()
        thumbnail_worker_state["status"] = "generating"
        return {"success": True, "is_paused": False, "message": "Thumbnail generator resumed"}


@app.post("/api/backup/thumbnails/generate")
async def trigger_thumbnail_generate(req: Optional[ThumbnailGenerateRequest] = None):
    folder_path = req.folder_path if req else None
    if folder_path:
        real_path = validate_backup_path(folder_path)
        enqueue_folder_thumbnails(real_path)
        thumbnail_pause_event.set()
        return {"success": True, "message": f"Enqueued thumbnails for {os.path.basename(real_path)}"}
    else:
        conn = get_db()
        try:
            with conn:
                conn.execute("UPDATE files SET has_thumb = 0 WHERE has_thumb = 2")
        except Exception:
            pass
        finally:
            conn.close()
        thumbnail_pause_event.set()
        return {"success": True, "message": "Thumbnail generator active for all files"}


# ==============================================================================
# WebP Image Optimizer Endpoints
# ==============================================================================

class WebPOptimizeFolderRequest(BaseModel):
    folder_path: str


@app.get("/api/backup/webp/status")
async def get_webp_status():
    return {
        "status": webp_worker_state["status"],
        "total_images": webp_worker_state["total_images"],
        "converted_count": webp_worker_state["converted_count"],
        "skipped_count": webp_worker_state["skipped_count"],
        "pending_count": webp_worker_state["pending_count"],
        "percent": webp_worker_state["percent"],
        "saved_bytes": webp_worker_state["saved_bytes"],
        "saved_formatted": webp_worker_state["saved_formatted"],
        "current_file": webp_worker_state["current_file"],
        "current_folder": webp_worker_state["current_folder"],
        "is_paused": not webp_pause_event.is_set(),
        "concurrency": webp_worker_state["concurrency"],
        "last_synced": webp_worker_state["last_synced"]
    }


@app.post("/api/backup/webp/pause")
async def toggle_webp_pause():
    if webp_pause_event.is_set():
        webp_pause_event.clear()
        webp_worker_state["status"] = "paused"
        webp_worker_state["is_paused"] = True
        return {"success": True, "is_paused": True, "message": "WebP optimizer paused"}
    else:
        webp_pause_event.set()
        webp_worker_state["status"] = "converting"
        webp_worker_state["is_paused"] = False
        return {"success": True, "is_paused": False, "message": "WebP optimizer resumed"}


@app.post("/api/backup/webp/resume")
async def resume_webp():
    webp_pause_event.set()
    webp_worker_state["status"] = "converting"
    webp_worker_state["is_paused"] = False
    return {"success": True, "is_paused": False, "message": "WebP optimizer resumed"}


@app.post("/api/backup/webp/optimize-folder")
async def optimize_folder_webp(req: WebPOptimizeFolderRequest):
    real_path = validate_backup_path(req.folder_path)
    conn = get_db()
    count = 0
    try:
        rows = conn.execute("SELECT path, name FROM files WHERE folder_path = ? AND type = 'image' AND webp_optimized = 0", (real_path,)).fetchall()
        for r in rows:
            priority_webp_queue.put_nowait({"path": r["path"], "name": r["name"]})
            count += 1
    except Exception as e:
        print(f"Error enqueuing folder webp: {e}")
    finally:
        conn.close()
    webp_pause_event.set()
    return {"success": True, "enqueued": count, "message": f"Enqueued {count} images for WebP optimization"}


# ==============================================================================
# Duplicate Detection & Management API Endpoints
# ==============================================================================

class DuplicateResolveRequest(BaseModel):
    keep_path: str
    delete_paths: List[str]

class DuplicateVerifyRequest(BaseModel):
    paths: List[str]

class DuplicateIgnoreRequest(BaseModel):
    paths: List[str]
    match_type: str = "visual"

class DuplicateUnignoreRequest(BaseModel):
    id: Optional[int] = None
    paths: Optional[List[str]] = None

def score_duplicate_file(name: str, folder_path: str, mtime: float, size: int = 0) -> float:
    score = 0.0
    # Favor higher file size (higher resolution/bitrate) for visual duplicates
    if size > 0:
        score += (size / (1024 * 1024 * 10))  # +1 point per 10MB
    # Penalize duplicate indicator suffixes like _1, (1), -copy, .mp4.mp4
    if re.search(r'(_\d+|\(\d+\)|\bcopy\b|\.mp4\.mp4)', name, re.IGNORECASE):
        score -= 50.0
    # Penalize generic/random looking hash titles
    if re.search(r'^[0-9a-z]{15,}_source', name, re.IGNORECASE):
        score -= 20.0
    # Favor dates or readable words
    if re.search(r'\d{4}-\d{2}-\d{2}', name):
        score += 15.0
    if ' ' in name or '-' in name:
        score += 5.0
    # Favor subfolder organization
    depth = len(Path(folder_path).parts)
    score += depth * 2.0
    # Slight favor for newer mtime
    score += (mtime / 1e10)
    return score

# ==============================================================================
# Duplicate Hash Pre-Verification Endpoints
# ==============================================================================

@app.get("/api/backup/duplicates/hash-status")
async def get_duplicate_hash_status():
    return {
        "status": duplicate_hash_state["status"],
        "total_candidates": duplicate_hash_state["total_candidates"],
        "hashed_count": duplicate_hash_state["hashed_count"],
        "pending_count": duplicate_hash_state["pending_count"],
        "percent": duplicate_hash_state["percent"],
        "current_file": duplicate_hash_state["current_file"],
        "processed_count": duplicate_hash_state["processed_count"],
        "is_paused": not duplicate_hash_pause_event.is_set(),
        "last_synced": duplicate_hash_state["last_synced"]
    }

@app.post("/api/backup/duplicates/hash-pause")
async def toggle_duplicate_hash_pause():
    if duplicate_hash_pause_event.is_set():
        duplicate_hash_pause_event.clear()
        duplicate_hash_state["status"] = "paused"
        duplicate_hash_state["is_paused"] = True
        return {"success": True, "is_paused": True, "message": "Duplicate hash verification paused"}
    else:
        duplicate_hash_pause_event.set()
        duplicate_hash_state["status"] = "hashing"
        duplicate_hash_state["is_paused"] = False
        return {"success": True, "is_paused": False, "message": "Duplicate hash verification resumed"}

@app.post("/api/backup/duplicates/hash-start")
async def start_duplicate_hash():
    duplicate_hash_pause_event.set()
    duplicate_hash_state["status"] = "hashing"
    duplicate_hash_state["is_paused"] = False
    return {"success": True, "is_paused": False, "message": "Duplicate hash verification active"}

# ==============================================================================
# Visual Duplicate Detection & Clustering
# ==============================================================================
visual_duplicates_cache = {
    "timestamp": 0,
    "total_groups": 0,
    "total_reclaimable": 0,
    "groups": [],
    "db_hash_count": 0,
    "ignored_count": 0,
}

def find_visual_duplicates(limit: int = 50, offset: int = 0, max_distance: int = 4) -> Dict[str, Any]:
    global visual_duplicates_cache

    base_dir = os.path.realpath(state.get("destination_dir") or DEFAULT_DESTINATION_DIR)
    conn = get_db()
    try:
        cur_hash_count_row = conn.execute("SELECT COUNT(*) FROM files WHERE thumb_hash IS NOT NULL AND thumb_hash != 'no_thumb'").fetchone()
        cur_hash_count = cur_hash_count_row[0] if cur_hash_count_row else 0
        cur_ignored_count_row = conn.execute("SELECT COUNT(*) FROM ignored_duplicates").fetchone()
        cur_ignored_count = cur_ignored_count_row[0] if cur_ignored_count_row else 0

        now = time.time()
        if (now - visual_duplicates_cache["timestamp"] < 30.0 and
            visual_duplicates_cache["db_hash_count"] == cur_hash_count and
            visual_duplicates_cache["ignored_count"] == cur_ignored_count):
            all_groups = visual_duplicates_cache["groups"]
            return {
                "success": True,
                "mode": "visual",
                "total_groups": visual_duplicates_cache["total_groups"],
                "reclaimable_bytes": visual_duplicates_cache["total_reclaimable"],
                "reclaimable_formatted": format_size(visual_duplicates_cache["total_reclaimable"]),
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
            WHERE thumb_hash IS NOT NULL AND thumb_hash != 'no_thumb' AND length(thumb_hash) = 16
        """).fetchall()

        ignored_rows = conn.execute("SELECT path1, path2 FROM ignored_duplicates").fetchall()
        ignored_pairs = {(r["path1"], r["path2"]) for r in ignored_rows}
    finally:
        conn.close()

    DEGENERATE_HASHES = {
        "0000000000000000",
        "ffffffffffffffff",
        "f0f0f0f0f0f0f0f0",
        "f0f0f0f0f0f8f0f0",
        "0f0f0f0f0f0f0f0f",
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
        (0x1FFF, 0),
        (0x1FFF, 13),
        (0x1FFF, 26),
        (0x1FFF, 39),
        (0x0FFF, 52),
    ]
    buckets = [defaultdict(list) for _ in range(5)]
    for idx, (_, h_val) in enumerate(items):
        for b, (mask, shift) in enumerate(masks_shifts):
            buckets[b][(h_val >> shift) & mask].append(idx)

    parent = list(range(n))
    def find_p(i):
        root = i
        while parent[root] != root:
            root = parent[root]
        curr = i
        while curr != root:
            nxt = parent[curr]
            parent[curr] = root
            curr = nxt
        return root

    def union_p(i, j):
        ri, rj = find_p(i), find_p(j)
        if ri != rj:
            parent[ri] = rj

    checked_pairs = set()
    pair_distances = {}

    for b in range(5):
        for chunk_val, idxs in buckets[b].items():
            if len(idxs) > 1:
                for i in range(len(idxs)):
                    idx_i = idxs[i]
                    for j in range(i + 1, len(idxs)):
                        idx_j = idxs[j]
                        u, v = (idx_i, idx_j) if idx_i < idx_j else (idx_j, idx_i)
                        if (u, v) in checked_pairs:
                            continue
                        checked_pairs.add((u, v))

                        p1, p2 = normalize_pair(items[u][0]["path"], items[v][0]["path"])
                        if (p1, p2) in ignored_pairs:
                            continue

                        diff = (items[u][1] ^ items[v][1]).bit_count()
                        if diff <= max_distance:
                            union_p(u, v)
                            pair_distances[(u, v)] = diff

    components = defaultdict(list)
    for i in range(n):
        root = find_p(i)
        components[root].append(i)

    all_groups = []
    total_reclaimable = 0

    for root_idx, member_idxs in components.items():
        if len(member_idxs) < 2:
            continue

        file_list = []
        for idx in member_idxs:
            fr = items[idx][0]
            fpath = fr["path"]
            is_vid = (fr["type"] == "video")
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

            file_list.append({
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
                "score": score_duplicate_file(fr["name"], fr["folder_path"], fr["mtime"] or 0, fr["size"] or 0),
            })

        best_idx = max(range(len(file_list)), key=lambda i: file_list[i]["score"])
        for i, f in enumerate(file_list):
            f["is_recommended"] = (i == best_idx)

        group_diffs = [
            pair_distances[(u, v)]
            for u in member_idxs
            for v in member_idxs
            if (u, v) in pair_distances
        ]
        min_dist = min(group_diffs) if group_diffs else 0
        similarity = round((1.0 - (min_dist / 64.0)) * 100.0, 1)

        reclaim_bytes = sum(f["size"] for i, f in enumerate(file_list) if i != best_idx)
        total_reclaimable += reclaim_bytes

        all_groups.append({
            "group_id": f"visual_{root_idx}",
            "name": file_list[best_idx]["name"],
            "size_formatted": file_list[best_idx]["size_formatted"],
            "match_type": "visual",
            "similarity": similarity,
            "min_distance": min_dist,
            "files_count": len(file_list),
            "reclaimable_bytes": reclaim_bytes,
            "reclaimable_formatted": format_size(reclaim_bytes),
            "suggested_keep_index": best_idx,
            "is_verified_match": False,
            "files": file_list,
        })

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

@app.get("/api/backup/duplicates")
def get_backup_duplicates(
    mode: str = Query("content"),
    limit: int = Query(50),
    offset: int = Query(0)
):
    """Returns grouped duplicate files from SQLite backup index (by size/content, exact name, or visual thumbnail match)."""
    if mode == "visual":
        return find_visual_duplicates(limit=limit, offset=offset)

    conn = get_db()
    base_dir = os.path.realpath(state.get("destination_dir") or DEFAULT_DESTINATION_DIR)
    groups = []
    ignored_pairs = get_ignored_pairs_set()

    if mode == "content":
        total_stats = conn.execute("""
            SELECT COUNT(*), SUM(waste) FROM (
                SELECT size, (COUNT(*) - 1) * size as waste
                FROM files
                WHERE size > 0
                GROUP BY size
                HAVING COUNT(*) > 1
            )
        """).fetchone()
        total_groups = total_stats[0] if total_stats and total_stats[0] else 0
        total_reclaimable = total_stats[1] if total_stats and total_stats[1] else 0

        size_rows = conn.execute("""
            SELECT size, COUNT(*) as cnt
            FROM files
            WHERE size > 0
            GROUP BY size
            HAVING cnt > 1
            ORDER BY size DESC
            LIMIT ? OFFSET ?
        """, (limit, offset)).fetchall()

        for s_row in size_rows:
            target_size = s_row["size"]
            file_rows = conn.execute("""
                SELECT path, name, folder_path, type, ext, size, size_formatted, mtime, has_thumb, quick_hash
                FROM files
                WHERE size = ?
                ORDER BY mtime DESC
            """, (target_size,)).fetchall()

            file_list = []
            for fr in file_rows:
                fpath = fr["path"]
                is_vid = (fr["type"] == "video")
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

                file_list.append({
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
                    "score": score_duplicate_file(fr["name"], fr["folder_path"], fr["mtime"] or 0, fr["size"] or 0),
                })

            if file_list:
                # If all pairs in this set are ignored, skip this group
                if len(file_list) >= 2 and ignored_pairs:
                    all_ignored = True
                    for i in range(len(file_list)):
                        for j in range(i + 1, len(file_list)):
                            pair = normalize_pair(file_list[i]["path"], file_list[j]["path"])
                            if pair not in ignored_pairs:
                                all_ignored = False
                                break
                        if not all_ignored:
                            break
                    if all_ignored:
                        continue

                best_idx = max(range(len(file_list)), key=lambda i: file_list[i]["score"])
                for i, f in enumerate(file_list):
                    f["is_recommended"] = (i == best_idx)

                known_hashes = [f["quick_hash"] for f in file_list if f["quick_hash"] and f["quick_hash"] != "unhashable"]
                is_verified_match = (len(known_hashes) == len(file_list) and len(set(known_hashes)) == 1)

                groups.append({
                    "group_id": f"size_{target_size}",
                    "size": target_size,
                    "size_formatted": format_size(target_size),
                    "match_type": "content",
                    "files_count": len(file_list),
                    "reclaimable_bytes": (len(file_list) - 1) * target_size,
                    "reclaimable_formatted": format_size((len(file_list) - 1) * target_size),
                    "suggested_keep_index": best_idx,
                    "is_verified_match": is_verified_match,
                    "files": file_list,
                })

        # Overall verified duplicate statistics across all candidate files
        cand_rows = conn.execute("""
            SELECT size, quick_hash FROM files 
            WHERE size > 0 AND size IN (SELECT size FROM files WHERE size > 0 GROUP BY size HAVING COUNT(*) > 1)
        """).fetchall()
        size_cand_map = defaultdict(list)
        for c_sz, c_qh in cand_rows:
            size_cand_map[c_sz].append(c_qh)
        
        verified_match_groups = 0
        verified_reclaimable_bytes = 0
        for c_sz, qh_list in size_cand_map.items():
            if len(qh_list) > 1 and len(set(qh_list)) == 1 and qh_list[0] and qh_list[0] != "unhashable":
                verified_match_groups += 1
                verified_reclaimable_bytes += c_sz * (len(qh_list) - 1)

        conn.close()
        return {
            "success": True,
            "mode": "content",
            "total_groups": total_groups,
            "reclaimable_bytes": total_reclaimable,
            "reclaimable_formatted": format_size(total_reclaimable),
            "verified_groups_count": verified_match_groups,
            "verified_reclaimable_bytes": verified_reclaimable_bytes,
            "verified_reclaimable_formatted": format_size(verified_reclaimable_bytes),
            "groups": groups,
            "limit": limit,
            "offset": offset,
        }

    else:
        # Exact Name Mode
        total_stats = conn.execute("""
            SELECT COUNT(*) FROM (
                SELECT name
                FROM files
                GROUP BY name
                HAVING COUNT(*) > 1
            )
        """).fetchone()
        total_groups = total_stats[0] if total_stats and total_stats[0] else 0

        name_rows = conn.execute("""
            SELECT name, COUNT(*) as cnt
            FROM files
            GROUP BY name
            HAVING cnt > 1
            ORDER BY cnt DESC, name ASC
            LIMIT ? OFFSET ?
        """, (limit, offset)).fetchall()

        total_reclaimable = 0
        for n_row in name_rows:
            target_name = n_row["name"]
            file_rows = conn.execute("""
                SELECT path, name, folder_path, type, ext, size, size_formatted, mtime, has_thumb, quick_hash
                FROM files
                WHERE name = ?
                ORDER BY size DESC, mtime DESC
            """, (target_name,)).fetchall()

            file_list = []
            for fr in file_rows:
                fpath = fr["path"]
                is_vid = (fr["type"] == "video")
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

                file_list.append({
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
                    "score": score_duplicate_file(fr["name"], fr["folder_path"], fr["mtime"] or 0, fr["size"] or 0),
                })

            if file_list:
                # If all pairs in this set are ignored, skip this group
                if len(file_list) >= 2 and ignored_pairs:
                    all_ignored = True
                    for i in range(len(file_list)):
                        for j in range(i + 1, len(file_list)):
                            pair = normalize_pair(file_list[i]["path"], file_list[j]["path"])
                            if pair not in ignored_pairs:
                                all_ignored = False
                                break
                        if not all_ignored:
                            break
                    if all_ignored:
                        continue

                best_idx = max(range(len(file_list)), key=lambda i: file_list[i]["score"])
                for i, f in enumerate(file_list):
                    f["is_recommended"] = (i == best_idx)

                reclaim_bytes = sum(f["size"] for f in file_list[1:])
                total_reclaimable += reclaim_bytes
                groups.append({
                    "group_id": f"name_{hashlib.md5(target_name.encode('utf-8')).hexdigest()[:12]}",
                    "name": target_name,
                    "size_formatted": file_list[0]["size_formatted"],
                    "match_type": "name",
                    "files_count": len(file_list),
                    "reclaimable_bytes": reclaim_bytes,
                    "reclaimable_formatted": format_size(reclaim_bytes),
                    "suggested_keep_index": best_idx,
                    "files": file_list,
                })

        conn.close()
        return {
            "success": True,
            "mode": "name",
            "total_groups": total_groups,
            "reclaimable_bytes": total_reclaimable,
            "reclaimable_formatted": format_size(total_reclaimable),
            "groups": groups,
            "limit": limit,
            "offset": offset,
        }

@app.get("/api/backup/duplicates/thumb-hash-status")
async def get_thumb_hash_status():
    return {
        "status": thumb_hash_state["status"],
        "total_thumbnails": thumb_hash_state["total_thumbnails"],
        "hashed_count": thumb_hash_state["hashed_count"],
        "pending_count": thumb_hash_state["pending_count"],
        "percent": thumb_hash_state["percent"],
        "current_file": thumb_hash_state["current_file"],
        "processed_count": thumb_hash_state["processed_count"]
    }

@app.post("/api/backup/duplicates/ignore")
def ignore_duplicate_match(req: DuplicateIgnoreRequest):
    if not req.paths or len(req.paths) < 2:
        raise HTTPException(status_code=400, detail="At least 2 file paths are required to ignore a duplicate match.")
    
    conn = get_db()
    pairs = []
    now = time.time()
    for i in range(len(req.paths)):
        for j in range(i + 1, len(req.paths)):
            p1, p2 = normalize_pair(req.paths[i], req.paths[j])
            pairs.append((p1, p2, req.match_type, now))
    
    with conn:
        conn.executemany("INSERT OR IGNORE INTO ignored_duplicates (path1, path2, match_type, created_at) VALUES (?, ?, ?, ?)", pairs)
    conn.close()

    # Invalidate visual cache
    visual_duplicates_cache["timestamp"] = 0
    return {"success": True, "message": f"Ignored {len(pairs)} pair(s) successfully."}

@app.get("/api/backup/duplicates/ignored")
def get_ignored_duplicates(limit: int = 100, offset: int = 0):
    conn = get_db()
    try:
        tot_row = conn.execute("SELECT COUNT(*) FROM ignored_duplicates").fetchone()
        total = tot_row[0] if tot_row else 0
        rows = conn.execute("""
            SELECT id, path1, path2, match_type, created_at
            FROM ignored_duplicates
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
        """, (limit, offset)).fetchall()

        items = []
        for r in rows:
            f1 = conn.execute("SELECT name, size_formatted FROM files WHERE path = ?", (r["path1"],)).fetchone()
            f2 = conn.execute("SELECT name, size_formatted FROM files WHERE path = ?", (r["path2"],)).fetchone()
            items.append({
                "id": r["id"],
                "path1": r["path1"],
                "name1": f1["name"] if f1 else Path(r["path1"]).name,
                "size1": f1["size_formatted"] if f1 else "",
                "thumb1": f"/api/backup/thumbnail?path={urllib.parse.quote(r['path1'])}",
                "path2": r["path2"],
                "name2": f2["name"] if f2 else Path(r["path2"]).name,
                "size2": f2["size_formatted"] if f2 else "",
                "thumb2": f"/api/backup/thumbnail?path={urllib.parse.quote(r['path2'])}",
                "match_type": r["match_type"],
                "created_at": r["created_at"],
            })
        return {
            "success": True,
            "total": total,
            "items": items,
            "limit": limit,
            "offset": offset,
        }
    finally:
        conn.close()

@app.post("/api/backup/duplicates/unignore")
def unignore_duplicate_match(req: DuplicateUnignoreRequest):
    conn = get_db()
    with conn:
        if req.id is not None:
            conn.execute("DELETE FROM ignored_duplicates WHERE id = ?", (req.id,))
        elif req.paths and len(req.paths) >= 2:
            p1, p2 = normalize_pair(req.paths[0], req.paths[1])
            conn.execute("DELETE FROM ignored_duplicates WHERE path1 = ? AND path2 = ?", (p1, p2))
        else:
            raise HTTPException(status_code=400, detail="Must provide either 'id' or 'paths' with 2 paths.")
    conn.close()

    # Invalidate visual cache
    visual_duplicates_cache["timestamp"] = 0
    return {"success": True, "message": "Duplicate match un-ignored."}

@app.post("/api/backup/duplicates/verify-group")
def verify_duplicate_group(req: DuplicateVerifyRequest):
    """Verifies fast chunk hashes for files in a duplicate group and caches hashes in SQLite."""
    hashes = {}
    missing_paths = []

    conn = get_db()
    for p in req.paths:
        try:
            real_p = validate_backup_path(p)
            row = conn.execute("SELECT quick_hash FROM files WHERE path = ?", (real_p,)).fetchone()
            if row and row["quick_hash"]:
                hashes[p] = row["quick_hash"]
            else:
                missing_paths.append((p, real_p))
        except Exception as e:
            print(f"Error checking hash for {p}: {e}")
    conn.close()

    if missing_paths:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=min(4, len(missing_paths))) as executor:
            future_to_path = {executor.submit(compute_quick_hash, real_p): (p, real_p) for p, real_p in missing_paths}
            to_update = []
            for fut in future_to_path:
                p, real_p = future_to_path[fut]
                try:
                    q_hash = fut.result()
                    if q_hash:
                        hashes[p] = q_hash
                        to_update.append((q_hash, real_p))
                except Exception as e:
                    print(f"Error hashing {real_p}: {e}")

            if to_update:
                conn = get_db()
                with conn:
                    conn.executemany("UPDATE files SET quick_hash = ? WHERE path = ?", to_update)
                conn.close()

    unique_hashes = set(hashes.values())
    all_match = (len(unique_hashes) == 1 and len(hashes) == len(req.paths))
    return {
        "success": True,
        "all_match": all_match,
        "hashes": hashes
    }

@app.post("/api/backup/duplicates/resolve")
def resolve_duplicate_group(req: DuplicateResolveRequest):
    """Keeps the specified file and safely removes selected duplicate files."""
    keep_real = validate_backup_path(req.keep_path)
    if not os.path.exists(keep_real):
        raise HTTPException(status_code=404, detail="The file to keep was not found.")

    deleted = []
    failed = []
    for dp in req.delete_paths:
        try:
            d_real = validate_backup_path(dp)
            if d_real == keep_real:
                continue # Safety: Never delete the file selected to keep
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

    return {
        "success": True,
        "kept": req.keep_path,
        "deleted": deleted,
        "failed": failed
    }

@app.post("/api/backup/duplicates/autodelete-verified")
def autodelete_verified_duplicates():
    """
    Finds all duplicate sets in Backup v2 where all candidate files have 100% matching chunk hashes.
    For each verified set, safely keeps the single best recommended file and deletes
    all other redundant duplicate copies. Preserves all hash mismatch sets untouched.
    """
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT path, name, folder_path, size, size_formatted, mtime, quick_hash
            FROM files
            WHERE size > 0 AND size IN (SELECT size FROM files WHERE size > 0 GROUP BY size HAVING COUNT(*) > 1)
            ORDER BY size DESC
        """).fetchall()
    finally:
        conn.close()

    size_groups = defaultdict(list)
    for r in rows:
        size_groups[r["size"]].append(dict(r))

    resolved_groups = 0
    deleted_count = 0
    reclaimed_bytes = 0
    deleted_paths = []
    failed_deletions = []

    for sz, files in size_groups.items():
        if len(files) < 2:
            continue

        # Check if all files in this group have a calculated quick_hash and are 100% identical
        hashes = [f.get("quick_hash") for f in files]
        if any(not h or h == "unhashable" for h in hashes):
            continue  # Skip pending or unhashable candidate sets

        if len(set(hashes)) != 1:
            continue  # Hash mismatch! Different videos with same size; preserve for manual review!

        # Score files to determine which copy to keep
        scored = []
        for f in files:
            score = score_duplicate_file(f["name"], f["folder_path"], f["mtime"] or 0)
            scored.append((score, f))
        scored.sort(key=lambda x: x[0], reverse=True)

        keep_file = scored[0][1]
        keep_real = validate_backup_path(keep_file["path"])

        # Check that the file to keep exists before deleting other copies
        if not os.path.exists(keep_real):
            continue

        # Delete all other copies in this group
        group_deleted = 0
        for _, file_to_delete in scored[1:]:
            dp = file_to_delete["path"]
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
        "failed": failed_deletions
    }

@app.get("/api/sorter/check-duplicate")
def check_sorter_duplicate(file_id: Optional[str] = Query(None), path: Optional[str] = Query(None)):
    """Checks whether an incoming video in the sorter already exists in Backup v2."""
    target_path = None
    if path:
        target_path = os.path.abspath(path.strip())
    elif file_id:
        target_path = resolve_video_path(file_id)

    if not target_path or not os.path.exists(target_path) or not os.path.isfile(target_path):
        return {"is_duplicate": False, "matches": []}

    try:
        incoming_size = os.path.getsize(target_path)
        incoming_name = os.path.basename(target_path)
        if incoming_size == 0:
            return {"is_duplicate": False, "matches": []}

        conn = get_db()
        base_dir = os.path.realpath(state.get("destination_dir") or DEFAULT_DESTINATION_DIR)

        # 1. Content / Size match check
        size_matches = conn.execute("""
            SELECT path, name, folder_path, type, ext, size, size_formatted, mtime, has_thumb, quick_hash
            FROM files
            WHERE size = ?
        """, (incoming_size,)).fetchall()

        matched_content = []
        if size_matches:
            incoming_hash = compute_quick_hash(target_path)
            if incoming_hash:
                for sm in size_matches:
                    cand_path = sm["path"]
                    cand_hash = sm["quick_hash"]
                    if not cand_hash:
                        cand_hash = compute_quick_hash(cand_path)
                        if cand_hash:
                            with conn:
                                conn.execute("UPDATE files SET quick_hash = ? WHERE path = ?", (cand_hash, cand_path))
                    if cand_hash and cand_hash == incoming_hash:
                        try:
                            rel_folder = os.path.relpath(sm["folder_path"], base_dir)
                        except Exception:
                            rel_folder = sm["folder_path"]
                        if rel_folder == ".":
                            rel_folder = "Root"
                        matched_content.append({
                            "path": cand_path,
                            "name": sm["name"],
                            "folder_path": sm["folder_path"],
                            "folder_display": rel_folder,
                            "size": sm["size"],
                            "size_formatted": sm["size_formatted"],
                            "match_reason": f"Exact content match ({sm['size_formatted']})",
                            "media_url": f"/api/backup/media?path={urllib.parse.quote(cand_path)}",
                            "thumb_url": f"/api/backup/thumbnail?path={urllib.parse.quote(cand_path)}",
                        })

        # 2. Filename match check
        name_matches = conn.execute("""
            SELECT path, name, folder_path, type, ext, size, size_formatted, mtime, has_thumb
            FROM files
            WHERE name = ?
        """, (incoming_name,)).fetchall()

        matched_names = []
        existing_paths = {m["path"] for m in matched_content}
        for nm in name_matches:
            if nm["path"] not in existing_paths:
                try:
                    rel_folder = os.path.relpath(nm["folder_path"], base_dir)
                except Exception:
                    rel_folder = nm["folder_path"]
                if rel_folder == ".":
                    rel_folder = "Root"
                matched_names.append({
                    "path": nm["path"],
                    "name": nm["name"],
                    "folder_path": nm["folder_path"],
                    "folder_display": rel_folder,
                    "size": nm["size"],
                    "size_formatted": nm["size_formatted"],
                    "match_reason": f"Exact filename match (in {rel_folder})",
                    "media_url": f"/api/backup/media?path={urllib.parse.quote(nm['path'])}",
                    "thumb_url": f"/api/backup/thumbnail?path={urllib.parse.quote(nm['path'])}",
                })

        conn.close()

        if matched_content:
            return {
                "is_duplicate": True,
                "match_type": "content",
                "incoming_file": {
                    "filename": incoming_name,
                    "size": incoming_size,
                    "size_formatted": format_size(incoming_size),
                },
                "matches": matched_content
            }
        elif matched_names:
            return {
                "is_duplicate": True,
                "match_type": "name",
                "incoming_file": {
                    "filename": incoming_name,
                    "size": incoming_size,
                    "size_formatted": format_size(incoming_size),
                },
                "matches": matched_names
            }
        else:
            return {
                "is_duplicate": False,
                "matches": []
            }
    except Exception as e:
        print(f"Error checking duplicate for {target_path}: {e}")
        return {"is_duplicate": False, "matches": [], "error": str(e)}


if __name__ == "__main__":
    import uvicorn
    import argparse

    parser = argparse.ArgumentParser(description="Web-based Media Library & Video Sorter")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8888)), help="Port to listen on (default: 8888)")
    parser.add_argument("--host", type=str, default=os.environ.get("HOST", "0.0.0.0"), help="Host interface (default: 0.0.0.0)")
    parser.add_argument("--source", type=str, default=os.environ.get("INCOMING_DIR", ""), help="Initial source directory")
    parser.add_argument("--dest", type=str, default=DEFAULT_DESTINATION_DIR, help="Destination directory (default: configured LIBRARY_DIR)")
    args = parser.parse_args()

    if args.source:
        state["source_dir"] = os.path.abspath(args.source)
    if args.dest:
        state["destination_dir"] = os.path.abspath(args.dest)

    lan_ip = get_lan_ip()
    print("\n" + "=" * 65)
    print("  📁 Media Library & Video Sorter Starting")
    print(f"  Media Library:   http://{lan_ip}:{args.port}")
    print(f"  Incoming Sorter: http://{lan_ip}:{args.port}/sorter")
    print(f"  Local Machine:   http://localhost:{args.port}")
    print(f"  Destination Dir: {state['destination_dir']}")
    if state["source_dir"]:
        print(f"  Source Dir:      {state['source_dir']}")
    print(f"  Data Dir:        {DATA_DIR}")
    print(f"  Database:        {DB_PATH}")
    print("=" * 65 + "\n")

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
