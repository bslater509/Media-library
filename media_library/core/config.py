"""Configuration, environment loading, and path resolutions."""

import os
import json
from pathlib import Path
from typing import Dict, Any, Optional
from dotenv import load_dotenv

from media_library.core.constants import CANDIDATE_DESTINATIONS

# Base repository root directory
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Load environment configuration (.env) if present
load_dotenv(BASE_DIR / ".env")

STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"
CONFIG_FILE = BASE_DIR / "config.json"

PORT = int(os.environ.get("PORT", "8888"))
HOST = os.environ.get("HOST", "0.0.0.0")

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

# Library folder backup configuration
LIBRARY_BACKUP_SUBDIR = os.environ.get("LIBRARY_BACKUP_SUBDIR", ".media_library_backup").strip() or ".media_library_backup"
LIBRARY_BACKUP_DIR = os.environ.get("LIBRARY_BACKUP_DIR", "").strip()
BACKUP_STAGING_DIR = Path(os.environ.get("BACKUP_STAGING_DIR", DATA_DIR / "backup_staging")).resolve()
BACKUP_STAGING_DIR.mkdir(parents=True, exist_ok=True)

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

# SQLite Database Path
env_db = os.environ.get("DB_PATH", "").strip()
if env_db:
    DB_PATH = Path(env_db).resolve()
elif (BASE_DIR / "backup_index.db").exists():
    DB_PATH = BASE_DIR / "backup_index.db"
else:
    DB_PATH = DATA_DIR / "library.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

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

def save_config_data(current_state: Dict[str, Any]):
    try:
        data = {
            "recent_destinations": current_state.get("recent_destinations", [])[:15],
            "source_dir": current_state.get("source_dir", ""),
        }
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"Error saving config.json: {e}")
