"""Pytest fixtures for isolated media library tests."""

from pathlib import Path

import pytest

from media_library.db.schema import init_backup_db


def patch_runtime_directories(monkeypatch, data_dir: Path, thumb_dir: Path):
    """Point import-time-resolved directory constants at sandbox dirs.

    Many config constants (ZIP_CACHE_DIR, ZIP_STAGING_DIR, THUMBNAIL_CACHE_DIR,
    WEBP_STAGING_DIR, BACKUP_STAGING_DIR, TRANSCODE_TEMP_DIR) are resolved when
    ``media_library.core.config`` is first imported. Setting env vars alone is
    therefore not enough to isolate tests from the real ``data/`` directory, so
    we also patch the module-level constants in every module that imported them.
    """
    import importlib

    config_mod = importlib.import_module("media_library.core.config")
    state_mod = importlib.import_module("media_library.core.state")
    indexer_mod = importlib.import_module("media_library.db.indexer")
    zip_mod = importlib.import_module("media_library.services.zip")
    ffmpeg_mod = importlib.import_module("media_library.services.ffmpeg_service")
    backup_mod = importlib.import_module("media_library.services.backup_service")
    transcode_mod = importlib.import_module("media_library.workers.transcode_worker")

    transcode_dir = data_dir / "transcodes"
    webp_staging = data_dir / "webp_staging"
    backup_staging = data_dir / "backup_staging"
    zip_staging = data_dir / "zip_staging"
    zip_cache = data_dir / "cache" / "zips"
    upload_staging = data_dir / "upload_staging"
    for d in (transcode_dir, webp_staging, backup_staging, zip_staging, zip_cache, upload_staging):
        d.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("TRANSCODE_DIR", str(transcode_dir))
    monkeypatch.setenv("WEBP_STAGING_DIR", str(webp_staging))
    monkeypatch.setenv("BACKUP_STAGING_DIR", str(backup_staging))
    monkeypatch.setenv("ZIP_STAGING_DIR", str(zip_staging))
    monkeypatch.setenv("ZIP_CACHE_DIR", str(zip_cache))
    monkeypatch.setenv("UPLOAD_STAGING_DIR", str(upload_staging))

    monkeypatch.setattr(config_mod, "TRANSCODE_TEMP_DIR", transcode_dir)
    monkeypatch.setattr(config_mod, "THUMBNAIL_CACHE_DIR", thumb_dir)
    monkeypatch.setattr(config_mod, "WEBP_STAGING_DIR", webp_staging)
    monkeypatch.setattr(config_mod, "BACKUP_STAGING_DIR", backup_staging)
    monkeypatch.setattr(config_mod, "ZIP_STAGING_DIR", zip_staging)
    monkeypatch.setattr(config_mod, "ZIP_CACHE_DIR", zip_cache)
    monkeypatch.setattr(config_mod, "UPLOAD_STAGING_DIR", upload_staging)

    upload_service_mod = importlib.import_module("media_library.services.upload_service")
    monkeypatch.setattr(upload_service_mod, "UPLOAD_STAGING_DIR", upload_staging)

    url_dl_mod = importlib.import_module("media_library.workers.url_download_worker")
    monkeypatch.setattr(url_dl_mod, "UPLOAD_STAGING_DIR", upload_staging)

    monkeypatch.setattr(state_mod, "THUMBNAIL_CACHE_DIR", thumb_dir)
    monkeypatch.setattr(indexer_mod, "THUMBNAIL_CACHE_DIR", thumb_dir)
    monkeypatch.setattr(zip_mod.core, "ZIP_CACHE_DIR", zip_cache)
    monkeypatch.setattr(zip_mod.core, "ZIP_STAGING_DIR", zip_staging)
    monkeypatch.setattr(zip_mod.core, "THUMBNAIL_CACHE_DIR", thumb_dir)
    monkeypatch.setattr(ffmpeg_mod, "WEBP_STAGING_DIR", webp_staging)
    monkeypatch.setattr(backup_mod, "THUMBNAIL_CACHE_DIR", thumb_dir)
    monkeypatch.setattr(backup_mod, "BACKUP_STAGING_DIR", backup_staging)
    monkeypatch.setattr(transcode_mod, "TRANSCODE_TEMP_DIR", transcode_dir)


@pytest.fixture(autouse=True)
def disable_auth_by_default(monkeypatch):
    """Keep auth off for every test regardless of the developer's real .env.

    ``get_app_password`` reads the live environment first, and ``core.config``
    loads the repo ``.env`` at import, so without this an ``APP_PASSWORD`` in
    ``.env`` would gate every TestClient in the suite. Tests that exercise auth
    set the env var themselves, overriding this.
    """
    import importlib

    monkeypatch.delenv("APP_PASSWORD", raising=False)
    monkeypatch.setattr(
        importlib.import_module("media_library.core.config"), "APP_PASSWORD", ""
    )


@pytest.fixture
def sandbox_env(tmp_path, monkeypatch):
    """Sets up a sandboxed environment with mock directories and SQLite database."""
    base_dir = tmp_path / "sandbox"
    base_dir.mkdir()

    media_dir = base_dir / "media"
    media_dir.mkdir()

    incoming_dir = base_dir / "incoming"
    incoming_dir.mkdir()

    data_dir = base_dir / "data"
    data_dir.mkdir()

    thumb_dir = data_dir / "thumbnails"
    thumb_dir.mkdir()

    db_path = data_dir / "test_library.db"

    # Set environment variables for isolation
    monkeypatch.setenv("LIBRARY_DIR", str(media_dir))
    monkeypatch.setenv("INCOMING_DIR", str(incoming_dir))
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("THUMBNAIL_DIR", str(thumb_dir))
    monkeypatch.setenv("DB_PATH", str(db_path))

    # Isolate import-time-resolved cache/staging directories from the real data/
    patch_runtime_directories(monkeypatch, data_dir, thumb_dir)

    # Initialize schema
    init_backup_db(db_path)

    return {
        "base_dir": base_dir,
        "media_dir": media_dir,
        "incoming_dir": incoming_dir,
        "data_dir": data_dir,
        "thumb_dir": thumb_dir,
        "db_path": db_path,
    }


@pytest.fixture
def sample_media(sandbox_env):
    """Creates sample categories, video files, and images in the sandboxed media dir."""
    media_dir = sandbox_env["media_dir"]

    # Category 1: action
    action_cat = media_dir / "action"
    action_cat.mkdir()
    vid1 = action_cat / "clip1.mp4"
    vid1.write_bytes(b"\x00\x00\x00 ftypmp42" + b"A" * 1024)

    vid2 = action_cat / "clip2.mkv"
    vid2.write_bytes(b"\x1a\x45\xdf\xa3" + b"B" * 2048)

    img1 = action_cat / "poster1.jpg"
    img1.write_bytes(b"\xff\xd8\xff\xe0" + b"C" * 512)

    # Category 2: comedy
    comedy_cat = media_dir / "comedy"
    comedy_cat.mkdir()
    vid3 = comedy_cat / "funny.mp4"
    vid3.write_bytes(b"\x00\x00\x00 ftypmp42" + b"D" * 4096)

    return {
        "action_cat": action_cat,
        "comedy_cat": comedy_cat,
        "vid1": vid1,
        "vid2": vid2,
        "img1": img1,
        "vid3": vid3,
    }


@pytest.fixture
def api_client(tmp_path, monkeypatch):
    """Spin up the FastAPI app against an isolated library (no startup workers)."""
    from starlette.testclient import TestClient

    from media_library.app_factory import create_app
    from media_library.core.state import state

    db_file = tmp_path / "test_api.db"
    dest_dir = tmp_path / "media_dest"
    source_dir = tmp_path / "media_src"
    thumb_dir = tmp_path / "thumbnails"
    dest_dir.mkdir()
    source_dir.mkdir()
    thumb_dir.mkdir()

    (source_dir / "test_clip.mp4").write_bytes(b"\x00" * 1024)

    monkeypatch.setenv("DB_PATH", str(db_file))
    monkeypatch.setenv("LIBRARY_DIR", str(dest_dir))
    monkeypatch.setenv("INCOMING_DIR", str(source_dir))
    monkeypatch.setenv("THUMBNAIL_DIR", str(thumb_dir))

    patch_runtime_directories(monkeypatch, tmp_path, thumb_dir)

    state.reset_runtime()
    state["source_dir"] = str(source_dir)
    state["destination_dir"] = str(dest_dir)

    init_backup_db()

    app = create_app(
        source_dir=str(source_dir),
        destination_dir=str(dest_dir),
        run_startup_workers=False,
    )

    with TestClient(app) as client:
        yield client
