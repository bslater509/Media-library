"""Library snapshot backup and restore manager.

Saves a clean snapshot of SQLite DB + uncompressed thumbnails.tar + config.json
directly into <LIBRARY_DIR>/.media_library_backup/ to eliminate redundant work on restarts.
"""

import os
import json
import time
import uuid
import shutil
import tarfile
import sqlite3
import hashlib
from typing import Optional, Dict, Any
from pathlib import Path

from media_library.core.config import (
    CONFIG_FILE,
    DB_PATH,
    DEFAULT_DESTINATION_DIR,
    THUMBNAIL_CACHE_DIR,
    BACKUP_STAGING_DIR,
    LIBRARY_BACKUP_DIR,
    LIBRARY_BACKUP_SUBDIR,
    load_config_data,
)
from media_library.core.state import state, format_size
from media_library.db.connection import get_db, get_current_db_path
from media_library.db.schema import init_backup_db

library_backup_state: Dict[str, Any] = {
    "status": "idle",  # "idle" | "backing_up" | "restoring" | "error"
    "progress_percent": 100.0,
    "current_step": "Idle",
    "last_backup_time": None,
    "last_backup_str": "",
    "backup_exists": False,
    "file_count": 0,
    "folder_count": 0,
    "thumb_count": 0,
    "total_size_bytes": 0,
    "total_size_formatted": "0 B",
    "backup_path": "",
    "error_message": None,
}

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

def get_library_backup_dir() -> Path:
    if LIBRARY_BACKUP_DIR:
        return Path(LIBRARY_BACKUP_DIR).resolve()
    base = state.get("destination_dir") or DEFAULT_DESTINATION_DIR
    return Path(base).resolve() / LIBRARY_BACKUP_SUBDIR

def get_existing_backup_meta() -> Optional[Dict[str, Any]]:
    try:
        meta_file = get_library_backup_dir() / "backup_meta.json"
        if meta_file.exists() and meta_file.stat().st_size > 0:
            with open(meta_file, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        print(f"[Library Backup] Error reading backup metadata: {e}")
    return None

def _create_library_backup_sync(progress_cb=None, is_shutdown: bool = False) -> Dict[str, Any]:
    bck_dir = get_library_backup_dir()
    try:
        if not bck_dir.parent.exists():
            return {
                "success": False,
                "error": f"Destination parent directory {bck_dir.parent} does not exist or is unmounted."
            }
        bck_dir.mkdir(parents=True, exist_ok=True)
        BACKUP_STAGING_DIR.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        return {"success": False, "error": f"Failed to access library backup directory: {e}"}

    stage_db = BACKUP_STAGING_DIR / f"temp_db_{uuid.uuid4().hex[:8]}.db"
    stage_tar = BACKUP_STAGING_DIR / f"temp_thumbs_{uuid.uuid4().hex[:8]}.tar"

    try:
        if progress_cb:
            progress_cb("Creating SQLite snapshot...", 10.0)

        if stage_db.exists():
            stage_db.unlink()

        conn = get_db()
        try:
            clean_stage_db_path = str(stage_db).replace("'", "''")
            conn.execute(f"VACUUM INTO '{clean_stage_db_path}'")
        except Exception:
            if stage_db.exists():
                try:
                    stage_db.unlink()
                except OSError:
                    pass
            dst_conn = sqlite3.connect(str(stage_db))
            with dst_conn:
                conn.backup(dst_conn)
            dst_conn.close()
        finally:
            conn.close()

        chk_conn = sqlite3.connect(str(stage_db))
        try:
            db_file_count = chk_conn.execute("SELECT count(*) FROM files").fetchone()[0]
            db_folder_count = chk_conn.execute("SELECT count(*) FROM folders").fetchone()[0]
        except Exception:
            db_file_count = 0
            db_folder_count = 0
        finally:
            chk_conn.close()

        db_size = stage_db.stat().st_size

        if progress_cb:
            progress_cb("Packaging thumbnails into archive...", 25.0)

        if stage_tar.exists():
            stage_tar.unlink()

        thumb_files = list(THUMBNAIL_CACHE_DIR.glob("thumb_*.jpg")) if THUMBNAIL_CACHE_DIR.exists() else []
        thumb_count = len(thumb_files)

        with tarfile.open(str(stage_tar), "w") as tar:
            for idx, tf in enumerate(thumb_files):
                tar.add(tf, arcname=tf.name)
                if progress_cb and idx % 2500 == 0 and thumb_count > 0:
                    pct = 25.0 + (idx / thumb_count) * 45.0
                    progress_cb(f"Archiving thumbnail {idx:,}/{thumb_count:,}...", pct)

        tar_size = stage_tar.stat().st_size

        if progress_cb:
            progress_cb("Writing database snapshot to library...", 75.0)

        target_db = bck_dir / "backup_index.db"
        target_db_tmp = bck_dir / f"backup_index.{uuid.uuid4().hex[:6]}.tmp"
        shutil.copyfile(str(stage_db), str(target_db_tmp))
        os.replace(str(target_db_tmp), str(target_db))

        if progress_cb:
            progress_cb("Writing thumbnail archive to library...", 85.0)

        target_tar = bck_dir / "thumbnails.tar"
        target_tar_tmp = bck_dir / f"thumbnails.{uuid.uuid4().hex[:6]}.tmp"
        shutil.copyfile(str(stage_tar), str(target_tar_tmp))
        os.replace(str(target_tar_tmp), str(target_tar))

        target_cfg = bck_dir / "config.json"
        if CONFIG_FILE.exists():
            target_cfg_tmp = bck_dir / f"config.{uuid.uuid4().hex[:6]}.tmp"
            shutil.copyfile(str(CONFIG_FILE), str(target_cfg_tmp))
            os.replace(str(target_cfg_tmp), str(target_cfg))

        if progress_cb:
            progress_cb("Saving backup metadata...", 95.0)

        total_size = db_size + tar_size
        meta = {
            "timestamp": time.time(),
            "datetime": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "file_count": db_file_count,
            "folder_count": db_folder_count,
            "thumb_count": thumb_count,
            "db_size_bytes": db_size,
            "thumbnails_tar_size_bytes": tar_size,
            "total_size_bytes": total_size,
            "total_size_formatted": format_size(total_size),
            "app_version": "2.0.0",
        }
        meta_tmp = bck_dir / f"backup_meta.{uuid.uuid4().hex[:6]}.tmp"
        with open(meta_tmp, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2)
        os.replace(str(meta_tmp), str(bck_dir / "backup_meta.json"))

        library_backup_state["last_backup_time"] = meta["timestamp"]
        library_backup_state["last_backup_str"] = time.strftime("%b %d, %H:%M:%S", time.localtime(meta["timestamp"]))
        library_backup_state["backup_exists"] = True
        library_backup_state["file_count"] = db_file_count
        library_backup_state["folder_count"] = db_folder_count
        library_backup_state["thumb_count"] = thumb_count
        library_backup_state["total_size_bytes"] = total_size
        library_backup_state["total_size_formatted"] = meta["total_size_formatted"]
        library_backup_state["backup_path"] = str(bck_dir)
        library_backup_state["error_message"] = None

        if progress_cb:
            progress_cb("Backup complete", 100.0)

        return {"success": True, "meta": meta}

    except Exception as e:
        print(f"[Library Backup] Exception during backup: {e}")
        return {"success": False, "error": str(e)}
    finally:
        if stage_db.exists():
            try:
                stage_db.unlink()
            except OSError:
                pass
        if stage_tar.exists():
            try:
                stage_tar.unlink()
            except OSError:
                pass

def _restore_library_backup_sync(progress_cb=None) -> Dict[str, Any]:
    bck_dir = get_library_backup_dir()
    source_db = bck_dir / "backup_index.db"
    source_tar = bck_dir / "thumbnails.tar"
    source_cfg = bck_dir / "config.json"

    if not bck_dir.exists() or not source_db.exists() or source_db.stat().st_size == 0:
        return {"success": False, "error": f"No valid database backup found in {bck_dir}"}

    try:
        if progress_cb:
            progress_cb("Restoring SQLite index database...", 15.0)

        target_db_path = get_current_db_path()
        for ext in ["-wal", "-shm", "-journal"]:
            wal_p = Path(str(target_db_path) + ext)
            if wal_p.exists():
                try:
                    wal_p.unlink()
                except OSError:
                    pass

        target_db_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_target_db = target_db_path.with_suffix(f".{uuid.uuid4().hex[:6]}.restore.tmp")
        shutil.copyfile(str(source_db), str(tmp_target_db))
        os.replace(str(tmp_target_db), str(target_db_path))

        init_backup_db()

        if source_tar.exists() and source_tar.stat().st_size > 0:
            if progress_cb:
                progress_cb("Extracting thumbnails archive...", 40.0)
            THUMBNAIL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
            with tarfile.open(str(source_tar), "r") as tar:
                if hasattr(tarfile, 'data_filter'):
                    tar.extractall(str(THUMBNAIL_CACHE_DIR), filter='data')
                else:
                    tar.extractall(str(THUMBNAIL_CACHE_DIR))

        if source_cfg.exists() and source_cfg.stat().st_size > 0:
            if progress_cb:
                progress_cb("Restoring configuration...", 85.0)
            try:
                shutil.copyfile(str(source_cfg), str(CONFIG_FILE))
                loaded = load_config_data()
                state["recent_destinations"] = loaded.get("recent_destinations", [])
                if loaded.get("source_dir"):
                    state["source_dir"] = loaded.get("source_dir")
            except Exception as e:
                print(f"[Library Restore] Warning restoring config: {e}")

        if progress_cb:
            progress_cb("Synchronizing thumbnail cache...", 90.0)

        sync_thumbnail_db_cache()

        conn = get_db()
        try:
            cur_files = conn.execute("SELECT count(*) FROM files").fetchone()[0]
            cur_folders = conn.execute("SELECT count(*) FROM folders").fetchone()[0]
            cur_thumbs = conn.execute("SELECT count(*) FROM files WHERE has_thumb = 1").fetchone()[0]
        finally:
            conn.close()

        meta = get_existing_backup_meta() or {}
        library_backup_state["last_backup_time"] = meta.get("timestamp")
        library_backup_state["last_backup_str"] = time.strftime("%b %d, %H:%M:%S", time.localtime(meta["timestamp"])) if meta.get("timestamp") else ""
        library_backup_state["backup_exists"] = True
        library_backup_state["file_count"] = meta.get("file_count", cur_files)
        library_backup_state["folder_count"] = meta.get("folder_count", cur_folders)
        library_backup_state["thumb_count"] = meta.get("thumb_count", cur_thumbs)
        library_backup_state["total_size_bytes"] = meta.get("total_size_bytes", 0)
        library_backup_state["total_size_formatted"] = meta.get("total_size_formatted", "0 B")
        library_backup_state["backup_path"] = str(bck_dir)
        library_backup_state["error_message"] = None

        if progress_cb:
            progress_cb("Restore complete", 100.0)

        return {"success": True, "restored_files": cur_files, "restored_thumbs": cur_thumbs}

    except Exception as e:
        print(f"[Library Restore] Exception during restore: {e}")
        return {"success": False, "error": str(e)}

def check_and_auto_restore_library_backup() -> bool:
    bck_dir = get_library_backup_dir()
    bck_db = bck_dir / "backup_index.db"
    bck_tar = bck_dir / "thumbnails.tar"

    if not bck_dir.exists() or not bck_db.exists() or bck_db.stat().st_size == 0:
        return False

    need_db_restore = False
    curr_db = get_current_db_path()
    if not curr_db.exists() or curr_db.stat().st_size == 0:
        need_db_restore = True
    else:
        try:
            conn = sqlite3.connect(str(curr_db), timeout=5.0)
            cnt = conn.execute("SELECT count(*) FROM files").fetchone()[0]
            conn.close()
            if cnt == 0:
                need_db_restore = True
        except Exception:
            need_db_restore = True

    need_thumb_restore = False
    if bck_tar.exists() and bck_tar.stat().st_size > 0:
        if not THUMBNAIL_CACHE_DIR.exists():
            need_thumb_restore = True
        else:
            try:
                local_thumb_count = len(list(THUMBNAIL_CACHE_DIR.glob("thumb_*.jpg")))
                if local_thumb_count < 10:
                    need_thumb_restore = True
            except Exception:
                pass

    if need_db_restore or need_thumb_restore:
        print(f"[Auto-Restore] Missing/empty local data (db={need_db_restore}, thumbs={need_thumb_restore}). Auto-restoring from {bck_dir}...")
        res = _restore_library_backup_sync()
        if res.get("success"):
            print(f"[Auto-Restore] Successfully restored {res.get('restored_files', 0)} files and {res.get('restored_thumbs', 0)} thumbnails from library backup!")
            return True
        else:
            print(f"[Auto-Restore] Failed to auto-restore: {res.get('error')}")
            return False

    meta = get_existing_backup_meta()
    if meta:
        library_backup_state["last_backup_time"] = meta.get("timestamp")
        library_backup_state["last_backup_str"] = time.strftime("%b %d, %H:%M:%S", time.localtime(meta["timestamp"])) if meta.get("timestamp") else ""
        library_backup_state["backup_exists"] = True
        library_backup_state["file_count"] = meta.get("file_count", 0)
        library_backup_state["folder_count"] = meta.get("folder_count", 0)
        library_backup_state["thumb_count"] = meta.get("thumb_count", 0)

    return False
