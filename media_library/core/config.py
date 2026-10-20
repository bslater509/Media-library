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

# Extracted animated WebP previews are large (up to 24 frames at 480px) but fully
# regenerable, so their cache directory is bounded by an LRU ceiling like the
# extracted-ZIP cache is, instead of growing without limit as videos come and go.
ANIMATED_CACHE_MAX_SIZE_GB = float(os.environ.get("ANIMATED_CACHE_MAX_SIZE_GB", "5.0"))

UPLOAD_STAGING_DIR = Path(
    os.environ.get("UPLOAD_STAGING_DIR", DATA_DIR / "upload_staging")
).resolve()
UPLOAD_STAGING_DIR.mkdir(parents=True, exist_ok=True)
MAX_UPLOAD_FILE_SIZE_GB = float(os.environ.get("MAX_UPLOAD_FILE_SIZE_GB", "20.0"))

# Browser uploads stage chunks as `<upload_id>.part` and can be resumed after a
# dropped connection or a closed tab. Staging files untouched for longer than
# this are garbage-collected at startup so abandoned uploads cannot fill the disk.
UPLOAD_STAGING_TTL_HOURS = float(os.environ.get("UPLOAD_STAGING_TTL_HOURS", "24.0"))

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

# Candidate frames (480px stills the thumbnail chooser, the animated preview
# compiler and the face worker pull out of a video) are regenerable, so their
# cache gets an LRU ceiling too; the oldest frames are evicted past it.
CANDIDATE_CACHE_MAX_SIZE_GB = float(
    os.environ.get("CANDIDATE_CACHE_MAX_SIZE_GB", "5.0")
)

# Face recognition ("People"). Off by default: it is the only feature that loads
# an inference runtime (opencv-contrib, lazily) and a pair of ONNX models, so an
# untouched install pays nothing. Empty *_DIR values mean "derive from DATA_DIR /
# THUMBNAIL_CACHE_DIR at call time", so a sandboxed DATA_DIR/THUMBNAIL_DIR is
# honoured (the face modules read these at call time, never at import).
FACE_RECOGNITION_ENABLED = os.environ.get(
    "FACE_RECOGNITION_ENABLED", "false"
).lower() in ("true", "1", "yes")
FACE_MODEL_DIR = os.environ.get("FACE_MODEL_DIR", "").strip()
FACE_CROP_DIR = os.environ.get("FACE_CROP_DIR", "").strip()
# YuNet detection score floor (0.1-0.99).
FACE_DETECTION_CONFIDENCE = min(
    0.99, max(0.1, float(os.environ.get("FACE_DETECTION_CONFIDENCE", "0.7")))
)
# Cosine *distance* ceiling for joining an existing person (SFace embeddings).
# Lower is stricter; 0.36 is "balanced" for SFace.
FACE_MATCH_THRESHOLD = min(
    1.2, max(0.05, float(os.environ.get("FACE_MATCH_THRESHOLD", "0.36")))
)
# Faces smaller than this (in pixels, on the FACE_SCAN_WIDTH-wide scan image)
# are ignored: in a crowd or a wide shot they are too small to cluster reliably.
# The default is the same ~10% of the frame the 480px scan used, now measured on
# a 960px image — the floor is physical, the pixels are sharper.
FACE_MIN_FACE_PX = max(8, int(os.environ.get("FACE_MIN_FACE_PX", "96")))
FACE_MAX_PER_FRAME = max(1, int(os.environ.get("FACE_MAX_PER_FRAME", "20")))
# Width the video sweep decodes each sampled frame at (never upscaling a smaller
# source). The recogniser aligns every crop to 112x112, so a face that is 48px on
# a 480px frame reaches it as a 2.3x upscale — which is why one person's faces
# landed up to 0.46 apart and split into separate clusters.
FACE_SCAN_WIDTH = max(160, int(os.environ.get("FACE_SCAN_WIDTH", "960")))
# Video sampling: one frame every N seconds, capped at FACE_VIDEO_MAX_FRAMES.
# The sweep decodes each video once and samples on that grid (a seek per frame
# over a FUSE mount is far more expensive than one sequential pass), so the cap
# is a frame budget: past it the spacing widens evenly (a 12 h clip at 2 s is
# 21,600 frames, so the default covers every realistic length at full density).
FACE_VIDEO_SAMPLE_INTERVAL = max(
    0.5, float(os.environ.get("FACE_VIDEO_SAMPLE_INTERVAL", "2"))
)
FACE_VIDEO_MAX_FRAMES = max(1, int(os.environ.get("FACE_VIDEO_MAX_FRAMES", "20000")))
# A frame pass reads a whole clip over the (possibly FUSE) mount, and a mount
# hiccup can stall that read for as long as the mount stays wedged. Nothing else
# on the box is harmed by one pending file, but a pass that never returns would
# pin a decode slot (and the sweep with it) indefinitely, so a pass that goes this
# long without producing a frame is abandoned and the file stays pending for the
# next sweep. It also bounds how long the ordered pipeline waits for a detection
# that never comes back.
FACE_FRAME_STALL_TIMEOUT = max(
    10.0, float(os.environ.get("FACE_FRAME_STALL_TIMEOUT", "60"))
)
# One record per person per appearance: a 2 s grid detects the same face dozens
# of times in a static shot, so detections are folded into a track while they
# stay within this cosine distance of the track's running mean and within this
# many seconds of its last hit. The track keeps its sharpest crop and its mean
# embedding (a cleaner cluster vote than any single frame).
FACE_TRACK_GAP_S = max(0.0, float(os.environ.get("FACE_TRACK_GAP_S", "15")))
FACE_TRACK_DISTANCE = min(
    1.2, max(0.02, float(os.environ.get("FACE_TRACK_DISTANCE", "0.25")))
)
# Face crops are regenerable, so their cache gets the same LRU ceiling the
# animated-preview and extracted-ZIP caches have.
FACE_CROP_MAX_SIZE_GB = float(os.environ.get("FACE_CROP_MAX_SIZE_GB", "2.0"))


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
DOWNLOAD_WORKER_CONCURRENCY = max(
    1, int(os.environ.get("DOWNLOAD_WORKER_CONCURRENCY", "2"))
)
DOWNLOAD_COOKIES_FILE = os.environ.get("DOWNLOAD_COOKIES_FILE", "").strip()
