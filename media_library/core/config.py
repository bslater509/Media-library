"""Configuration, environment loading, and path resolutions."""

import json
import logging
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from media_library.core.constants import CANDIDATE_DESTINATIONS

logger = logging.getLogger(__name__)

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

TRANSCODE_TEMP_DIR = Path(
    os.environ.get("TRANSCODE_DIR", DATA_DIR / "transcodes")
).resolve()
TRANSCODE_TEMP_DIR.mkdir(parents=True, exist_ok=True)

THUMBNAIL_CACHE_DIR = Path(
    os.environ.get("THUMBNAIL_DIR", DATA_DIR / "thumbnails")
).resolve()
THUMBNAIL_CACHE_DIR.mkdir(parents=True, exist_ok=True)

WEBP_STAGING_DIR = Path(
    os.environ.get("WEBP_STAGING_DIR", DATA_DIR / "webp_staging")
).resolve()
WEBP_STAGING_DIR.mkdir(parents=True, exist_ok=True)

UPLOAD_STAGING_DIR = Path(
    os.environ.get("UPLOAD_STAGING_DIR", DATA_DIR / "upload_staging")
).resolve()
UPLOAD_STAGING_DIR.mkdir(parents=True, exist_ok=True)
MAX_UPLOAD_FILE_SIZE_GB = float(os.environ.get("MAX_UPLOAD_FILE_SIZE_GB", "20.0"))

WEBP_WORKER_CONCURRENCY = max(1, int(os.environ.get("WEBP_WORKER_CONCURRENCY", "1")))
WEBP_QUALITY = max(1, min(100, int(os.environ.get("WEBP_QUALITY", "85"))))
WEBP_SKIP_LARGER = os.environ.get("WEBP_SKIP_LARGER", "false").lower() in (
    "true",
    "1",
    "yes",
)
WEBP_AUTO_OPTIMIZE = os.environ.get("WEBP_AUTO_OPTIMIZE", "true").lower() in (
    "true",
    "1",
    "yes",
)

# Search configuration (maximum files returned in a single global search query)
SEARCH_LIMIT = max(1, int(os.environ.get("SEARCH_LIMIT", "1000")))

# Library folder backup configuration
LIBRARY_BACKUP_SUBDIR = (
    os.environ.get("LIBRARY_BACKUP_SUBDIR", ".media_library_backup").strip()
    or ".media_library_backup"
)
LIBRARY_BACKUP_DIR = os.environ.get("LIBRARY_BACKUP_DIR", "").strip()
BACKUP_STAGING_DIR = Path(
    os.environ.get("BACKUP_STAGING_DIR", DATA_DIR / "backup_staging")
).resolve()
BACKUP_STAGING_DIR.mkdir(parents=True, exist_ok=True)
LIBRARY_BACKUP_AUTO_ENABLED = os.environ.get(
    "LIBRARY_BACKUP_AUTO_ENABLED", "true"
).lower() in ("true", "1", "yes")
LIBRARY_BACKUP_AUTO_INTERVAL_HOURS = float(
    os.environ.get("LIBRARY_BACKUP_AUTO_INTERVAL_HOURS", "6.0")
)

# Optional single shared password gating the whole web UI and API.
# Empty or unset disables authentication entirely.
APP_PASSWORD = os.environ.get("APP_PASSWORD", "")

# ZIP staging and extraction cache configuration
ZIP_STAGING_DIR = Path(
    os.environ.get("ZIP_STAGING_DIR", DATA_DIR / "zip_staging")
).resolve()
ZIP_STAGING_DIR.mkdir(parents=True, exist_ok=True)
ZIP_CACHE_DIR = Path(
    os.environ.get("ZIP_CACHE_DIR", DATA_DIR / "cache" / "zips")
).resolve()
ZIP_CACHE_DIR.mkdir(parents=True, exist_ok=True)
ZIP_CACHE_MAX_SIZE_GB = float(os.environ.get("ZIP_CACHE_MAX_SIZE_GB", "5.0"))


def detect_default_destination() -> str:
    env_dest = os.environ.get("LIBRARY_DIR", "").strip()
    if env_dest:
        dest_path = Path(env_dest).resolve()
        try:
            dest_path.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.warning(
                f"Warning: could not create or stat destination directory {dest_path}: {e}"
            )
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


def load_config_data() -> dict[str, Any]:
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
            logger.error(f"Error loading config.json: {e}")
    return config


def save_config_data(current_state: dict[str, Any]):
    try:
        data = {
            "recent_destinations": current_state.get("recent_destinations", [])[:15],
            "source_dir": current_state.get("source_dir", ""),
        }
        tmp_file = CONFIG_FILE.with_name(f".tmp_{CONFIG_FILE.name}_{os.getpid()}")
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_file, CONFIG_FILE)
    except Exception as e:
        logger.error(f"Error saving config.json: {e}")


# URL playlist downloading (yt-dlp)
DOWNLOAD_WORKER_CONCURRENCY = max(1, int(os.environ.get("DOWNLOAD_WORKER_CONCURRENCY", "2")))
DOWNLOAD_COOKIES_FILE = os.environ.get("DOWNLOAD_COOKIES_FILE", "").strip()
