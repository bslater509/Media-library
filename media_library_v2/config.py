"""Configuration for the v2 application.

Deliberately independent of ``media_library`` (the v1 package): it reads the
same environment variable names so one configuration drives both apps, but it
imports none of v1's modules.

It does **not** call ``load_dotenv``. The composition root that imports this
package (today v1's ``create_app``, which has already loaded ``.env``) owns
dotenv loading; a future standalone v2 entrypoint must load it before import.
Loading it here made a late import re-inject ``APP_PASSWORD`` from ``.env``
after a test had deliberately removed it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values

BASE_DIR = Path(__file__).resolve().parent.parent

# Read .env without mutating os.environ: the composition root (v1's
# ``create_app``) owns the real environment, and loading values into
# ``os.environ`` here would re-inject a variable a test deliberately removed.
_DOTENV = dotenv_values(BASE_DIR / ".env")

_TRUE = {"1", "true", "yes", "on"}


def _env_str(name: str, default: str = "") -> str:
    value = os.environ.get(name)
    if value is None or not value.strip():
        value = _DOTENV.get(name, "")
    return (value or default).strip()


def _env_path(name: str, default: Path) -> Path:
    raw = _env_str(name)
    return Path(raw).resolve() if raw else default.resolve()


def _env_bool(name: str, default: bool) -> bool:
    raw = _env_str(name)
    if not raw:
        return default
    return raw.lower() in _TRUE


def _env_int(name: str, default: int, *, minimum: int = 1) -> int:
    try:
        return max(minimum, int(_env_str(name) or default))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    """Resolved runtime configuration for v2."""

    base_dir: Path
    data_dir: Path
    library_dir: Path
    source_dir: Path
    db_path: Path
    thumbnail_dir: Path
    upload_staging_dir: Path
    sorter_state_path: Path
    backup_dir: Path
    backup_staging_dir: Path
    allow_live_restore: bool
    download_staging_dir: Path
    download_concurrency: int
    download_cookies_file: str
    zip_cache_dir: Path
    zip_cache_max_size_gb: float
    zip_staging_dir: Path
    webp_quality: int
    webp_skip_larger: bool
    static_dir: Path
    enabled: bool
    db_read_only: bool
    writes_enabled: bool
    browse_limit: int
    search_limit: int
    native_video_exts: frozenset[str]
    video_exts: frozenset[str]
    image_exts: frozenset[str]

    @property
    def api_prefix(self) -> str:
        return "/api/v2"

    @property
    def app_prefix(self) -> str:
        return "/v2"


def _resolve_db_path(data_dir: Path) -> Path:
    explicit = _env_str("DB_PATH")
    if explicit:
        return Path(explicit).resolve()
    root_db = BASE_DIR / "backup_index.db"
    if root_db.exists():
        return root_db.resolve()
    return (data_dir / "library.db").resolve()


def _resolve_library_dir(data_dir: Path) -> Path:
    explicit = _env_str("V2_LIBRARY_DIR") or _env_str("LIBRARY_DIR")
    if explicit:
        return Path(explicit).resolve()
    return (data_dir / "media").resolve()


def load_settings() -> Settings:
    """Build settings from the environment and ``.env`` (no caching)."""
    data_dir = _env_path("DATA_DIR", BASE_DIR / "data")
    return Settings(
        base_dir=BASE_DIR,
        data_dir=data_dir,
        library_dir=_resolve_library_dir(data_dir),
        source_dir=_env_path(
            "V2_SOURCE_DIR", Path(_env_str("INCOMING_DIR") or data_dir / "incoming")
        ),
        db_path=_resolve_db_path(data_dir),
        backup_dir=_env_path(
            "LIBRARY_BACKUP_DIR",
            _resolve_library_dir(data_dir) / ".media_library_backup",
        ),
        backup_staging_dir=_env_path(
            "V2_BACKUP_STAGING_DIR", data_dir / "backup_staging_v2"
        ),
        allow_live_restore=_env_bool("V2_ALLOW_LIVE_RESTORE", False),
        thumbnail_dir=_env_path("THUMBNAIL_DIR", data_dir / "thumbnails"),
        upload_staging_dir=_env_path(
            "V2_UPLOAD_STAGING_DIR", data_dir / "upload_staging_v2"
        ),
        sorter_state_path=_env_path(
            "V2_SORTER_STATE", data_dir / "v2_sorter_state.json"
        ),
        download_staging_dir=_env_path(
            "V2_DOWNLOAD_STAGING_DIR", data_dir / "download_staging_v2"
        ),
        download_concurrency=_env_int("DOWNLOAD_WORKER_CONCURRENCY", 2),
        download_cookies_file=_env_str("DOWNLOAD_COOKIES_FILE"),
        zip_cache_dir=_env_path("ZIP_CACHE_DIR", data_dir / "cache" / "zips"),
        zip_cache_max_size_gb=float(_env_str("ZIP_CACHE_MAX_SIZE_GB") or 5.0),
        zip_staging_dir=_env_path(
            "V2_ZIP_STAGING_DIR",
            Path(_env_str("ZIP_STAGING_DIR") or data_dir / "zip_staging_v2"),
        ),
        webp_quality=min(100, max(1, _env_int("WEBP_QUALITY", 85))),
        webp_skip_larger=_env_bool("WEBP_SKIP_LARGER", False),
        static_dir=(BASE_DIR / "static").resolve(),
        enabled=_env_bool("V2_ENABLED", True),
        db_read_only=_env_bool("V2_DB_READ_ONLY", False),
        writes_enabled=_env_bool("V2_WRITES", True),
        browse_limit=_env_int("V2_BROWSE_LIMIT", 120),
        search_limit=_env_int("V2_SEARCH_LIMIT", 1000),
        native_video_exts=frozenset({".mp4", ".m4v", ".webm", ".mov", ".ogv", ".ogg"}),
        video_exts=frozenset(
            {
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
        ),
        image_exts=frozenset(
            {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".avif", ".svg"}
        ),
    )


_settings = load_settings()


def get_settings() -> Settings:
    """Return the process-wide settings object."""
    return _settings


def reload_settings() -> Settings:
    """Re-read the environment (used by tests and by the mount helper)."""
    global _settings
    _settings = load_settings()
    return _settings
