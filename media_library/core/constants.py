"""Constants and file extensions for media processing."""

from pathlib import Path

# Supported video extensions
VIDEO_EXTENSIONS = {
    ".mp4",
    ".m4v",
    ".mkv",
    ".webm",
    ".avi",
    ".mov",
    ".wmv",
    ".flv",
    ".ts",
    ".m2ts",
    ".mpg",
    ".mpeg",
    ".vob",
    ".3gp",
    ".asf",
}

try:
    import pillow_heif

    pillow_heif.register_heif_opener()
except Exception:
    pass

# Supported image extensions
IMAGE_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".gif",
    ".bmp",
    ".avif",
    ".svg",
    ".heic",
    ".heif",
    ".dng",
}

# Browser-native streaming extensions
NATIVE_EXTENSIONS = {".mp4", ".m4v", ".webm", ".mov"}

# Browse-time background indexing scheduler tuning
INDEX_SCHEDULE_DEBOUNCE_S = (
    5.0  # Skip re-scheduling an index for the same folder within this window
)
INDEX_INFLIGHT_CAP = 5000  # Max entries in the inflight-folder map before pruning
INDEX_INFLIGHT_PRUNE_S = 120.0  # Age after which an inflight-folder entry is pruned
INDEX_COUNT_CACHE_TTL_S = 30.0  # TTL for the cached folder file-count lookup
QUEUE_REFRESH_INTERVAL_S = (
    8.0  # Minimum seconds between automatic incoming-folder re-scans
)

# HTTP byte-range streaming chunk/limit sizes
STREAM_FULL_CHUNK = 1024 * 1024  # Full-file sender read chunk (1 MB)
STREAM_RANGE_CHUNK = 128 * 1024  # Range sender read chunk (128 KB)
STREAM_MAX_RANGE_BYTES = (
    2 * 1024 * 1024
)  # Upper clamp for a single ranged response (2 MB)

# Cache-Control max-age values (seconds)
THUMB_CACHE_MAX_AGE_IMMUTABLE = 2592000  # 30 days
MEDIA_CACHE_MAX_AGE = 86400  # 1 day
SQL_IN_CHUNK = 400  # Max bound variables per SQL IN (...) query batch

# Potential default destination directories (fallback detection)
CANDIDATE_DESTINATIONS = [
    str(Path.home() / "Videos"),
    str(Path.home() / "Movies"),
    "/media/library",
]
