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

BASE_DIR = Path(__file__).resolve().parent.parent

_TRUE = {"1", "true", "yes", "on"}


def _env_str(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_path(name: str, default: Path) -> Path:
    raw = _env_str(name)
    return Path(raw).resolve() if raw else default.resolve()


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in _TRUE


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
    db_path: Path
    thumbnail_dir: Path
    upload_staging_dir: Path
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
        db_path=_resolve_db_path(data_dir),
        thumbnail_dir=_env_path("THUMBNAIL_DIR", data_dir / "thumbnails"),
        upload_staging_dir=_env_path(
            "V2_UPLOAD_STAGING_DIR", data_dir / "upload_staging_v2"
        ),
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
