"""Runtime configuration.

Every setting is read from the process environment first and from ``.env`` as a
fallback. The settings are deliberately a plain frozen dataclass rebuilt by
:func:`reload_settings`, so a test can point ``DATA_DIR``/``DB_PATH`` at a
temporary directory and re-read without restarting the process.

Variable names lost their ``V2_`` prefix when the rewrite became the only app.
The old names are still read as a second choice, so an existing ``.env`` keeps
working across the upgrade; new deployments should use the shorter name.

``load_dotenv`` is never called here: the composition root (``main`` in
:mod:`media_library.app`) owns real dotenv loading. Reading ``.env`` through
``dotenv_values`` only for keys missing from the environment keeps an import of
this module side-effect free.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values

BASE_DIR = Path(__file__).resolve().parent.parent

# Read .env without mutating os.environ; the composition root owns the real
# environment, and loading values here would re-inject a variable a test
# deliberately removed.
_DOTENV = dotenv_values(BASE_DIR / ".env")

_TRUE = {"1", "true", "yes", "on"}


def _raw(*names: str) -> str:
    """First non-empty value among ``names`` in the environment, then ``.env``."""
    for name in names:
        value = os.environ.get(name)
        if value is None or not value.strip():
            value = _DOTENV.get(name, "")
        if value and value.strip():
            return value.strip()
    return ""


def _env_str(*names: str, default: str = "") -> str:
    return _raw(*names) or default


def _env_path(*names: str, default: Path) -> Path:
    raw = _raw(*names)
    return Path(raw).resolve() if raw else default.resolve()


def _env_bool(*names: str, default: bool) -> bool:
    raw = _raw(*names)
    if not raw:
        return default
    return raw.lower() in _TRUE


def _env_int(*names: str, default: int, minimum: int = 1) -> int:
    try:
        return max(minimum, int(_raw(*names) or default))
    except ValueError:
        return default


def _env_float(*names: str, default: float) -> float:
    try:
        return float(_raw(*names) or default)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    """Resolved runtime configuration."""

    base_dir: Path
    data_dir: Path
    library_dir: Path
    source_dir: Path
    db_path: Path
    thumbnail_dir: Path
    upload_staging_dir: Path
    upload_staging_ttl_hours: float
    sorter_state_path: Path
    transcode_dir: Path
    own_workers: bool
    crawl_interval_minutes: int
    backup_interval_hours: float
    x265_preset: str
    x265_crf: int
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
    db_read_only: bool
    writes_enabled: bool
    browse_limit: int
    search_limit: int
    native_video_exts: frozenset[str]
    video_exts: frozenset[str]
    image_exts: frozenset[str]


def _resolve_db_path(data_dir: Path) -> Path:
    explicit = _env_str("DB_PATH")
    if explicit:
        return Path(explicit).resolve()
    root_db = BASE_DIR / "backup_index.db"
    if root_db.exists():
        return root_db.resolve()
    return (data_dir / "library.db").resolve()


def _resolve_library_dir(data_dir: Path) -> Path:
    explicit = _env_str("LIBRARY_DIR", "V2_LIBRARY_DIR")
    if explicit:
        return Path(explicit).resolve()
    return (data_dir / "media").resolve()


def load_settings() -> Settings:
    """Build settings from the environment and ``.env`` (no caching)."""
    data_dir = _env_path("DATA_DIR", default=BASE_DIR / "data")
    library_dir = _resolve_library_dir(data_dir)
    return Settings(
        base_dir=BASE_DIR,
        data_dir=data_dir,
        library_dir=library_dir,
        source_dir=_env_path(
            "SOURCE_DIR",
            "V2_SOURCE_DIR",
            "INCOMING_DIR",
            default=data_dir / "incoming",
        ),
        db_path=_resolve_db_path(data_dir),
        transcode_dir=_env_path("TRANSCODE_DIR", default=data_dir / "transcodes"),
        own_workers=_env_bool("OWN_WORKERS", "V2_OWN_WORKERS", default=False),
        crawl_interval_minutes=_env_int(
            "CRAWL_INTERVAL_MINUTES", "V2_CRAWL_INTERVAL_MINUTES", default=30
        ),
        backup_interval_hours=_env_float(
            "BACKUP_INTERVAL_HOURS", "V2_BACKUP_INTERVAL_HOURS", default=6.0
        ),
        x265_preset=_env_str("X265_PRESET", default="medium"),
        x265_crf=max(0, min(51, _env_int("X265_CRF", default=18, minimum=0))),
        backup_dir=_env_path(
            "LIBRARY_BACKUP_DIR", default=library_dir / ".media_library_backup"
        ),
        backup_staging_dir=_env_path(
            "BACKUP_STAGING_DIR",
            "V2_BACKUP_STAGING_DIR",
            default=data_dir / "backup_staging",
        ),
        allow_live_restore=_env_bool(
            "ALLOW_LIVE_RESTORE", "V2_ALLOW_LIVE_RESTORE", default=False
        ),
        thumbnail_dir=_env_path("THUMBNAIL_DIR", default=data_dir / "thumbnails"),
        upload_staging_dir=_env_path(
            "UPLOAD_STAGING_DIR",
            "V2_UPLOAD_STAGING_DIR",
            default=data_dir / "upload_staging",
        ),
        upload_staging_ttl_hours=_env_float("UPLOAD_STAGING_TTL_HOURS", default=24.0),
        sorter_state_path=_env_path(
            "SORTER_STATE", "V2_SORTER_STATE", default=data_dir / "sorter_state.json"
        ),
        download_staging_dir=_env_path(
            "DOWNLOAD_STAGING_DIR",
            "V2_DOWNLOAD_STAGING_DIR",
            default=data_dir / "download_staging",
        ),
        download_concurrency=_env_int("DOWNLOAD_WORKER_CONCURRENCY", default=2),
        download_cookies_file=_env_str("DOWNLOAD_COOKIES_FILE"),
        zip_cache_dir=_env_path("ZIP_CACHE_DIR", default=data_dir / "cache" / "zips"),
        zip_cache_max_size_gb=_env_float("ZIP_CACHE_MAX_SIZE_GB", default=5.0),
        zip_staging_dir=_env_path(
            "ZIP_STAGING_DIR", "V2_ZIP_STAGING_DIR", default=data_dir / "zip_staging"
        ),
        webp_quality=min(100, max(1, _env_int("WEBP_QUALITY", default=85))),
        webp_skip_larger=_env_bool("WEBP_SKIP_LARGER", default=False),
        static_dir=(BASE_DIR / "static").resolve(),
        db_read_only=_env_bool("DB_READ_ONLY", "V2_DB_READ_ONLY", default=False),
        writes_enabled=_env_bool("WRITES", "V2_WRITES", default=True),
        browse_limit=_env_int("BROWSE_LIMIT", "V2_BROWSE_LIMIT", default=120),
        search_limit=_env_int("SEARCH_LIMIT", "V2_SEARCH_LIMIT", default=1000),
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
    """Re-read the environment (used by tests and by the entrypoint)."""
    global _settings
    _settings = load_settings()
    return _settings
