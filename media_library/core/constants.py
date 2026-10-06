"""Constants and file extensions for media processing."""

from pathlib import Path

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

# Potential default destination directories (fallback detection)
CANDIDATE_DESTINATIONS = [
    str(Path.home() / "Videos"),
    str(Path.home() / "Movies"),
    "/media/library",
]
