"""Pytest fixtures for isolated media library tests."""

import asyncio
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from media_library.db.schema import init_backup_db


@pytest.fixture(autouse=True)
def isolate_index_database(tmp_path, monkeypatch):
    """Point ``DB_PATH`` at a throwaway database for *every* test.

    ``get_current_db_path()`` falls back to the configured (production) index
    database when ``DB_PATH`` is unset, and only the tests that request
    ``sandbox_env``/``api_client`` set it. Tests that use ``tmp_path`` directly
    (e.g. the transcode worker tests) therefore used to write real folder and
    file rows into the live ``backup_index.db``. This pins the env var for the
    whole session so no test can reach the developer's real index.
    """
    db_path = tmp_path / "isolated_index.db"
    monkeypatch.setenv("DB_PATH", str(db_path))
    init_backup_db(db_path)
    yield


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
    zip_mod = importlib.import_module("media_library.services.zip")
    ffmpeg_mod = importlib.import_module("media_library.services.ffmpeg_service")
    backup_mod = importlib.import_module("media_library.services.backup_service")
    transcode_mod = importlib.import_module("media_library.workers.transcode_worker")
    h265_mod = importlib.import_module("media_library.workers.h265_worker")

    transcode_dir = data_dir / "transcodes"
    webp_staging = data_dir / "webp_staging"
    backup_staging = data_dir / "backup_staging"
    zip_staging = data_dir / "zip_staging"
    zip_cache = data_dir / "cache" / "zips"
    upload_staging = data_dir / "upload_staging"
    face_models = data_dir / "models"
    face_crops = thumb_dir / "faces"
    for d in (
        transcode_dir,
        webp_staging,
        backup_staging,
        zip_staging,
        zip_cache,
        upload_staging,
        face_models,
        face_crops,
    ):
        d.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("TRANSCODE_DIR", str(transcode_dir))
    monkeypatch.setenv("WEBP_STAGING_DIR", str(webp_staging))
    monkeypatch.setenv("BACKUP_STAGING_DIR", str(backup_staging))
    monkeypatch.setenv("ZIP_STAGING_DIR", str(zip_staging))
    monkeypatch.setenv("ZIP_CACHE_DIR", str(zip_cache))
    monkeypatch.setenv("UPLOAD_STAGING_DIR", str(upload_staging))
    monkeypatch.setenv("FACE_MODEL_DIR", str(face_models))
    monkeypatch.setenv("FACE_CROP_DIR", str(face_crops))

    monkeypatch.setattr(config_mod, "TRANSCODE_TEMP_DIR", transcode_dir)
    monkeypatch.setattr(config_mod, "THUMBNAIL_CACHE_DIR", thumb_dir)
    monkeypatch.setattr(config_mod, "WEBP_STAGING_DIR", webp_staging)
    monkeypatch.setattr(config_mod, "BACKUP_STAGING_DIR", backup_staging)
    monkeypatch.setattr(config_mod, "ZIP_STAGING_DIR", zip_staging)
    monkeypatch.setattr(config_mod, "ZIP_CACHE_DIR", zip_cache)
    monkeypatch.setattr(config_mod, "UPLOAD_STAGING_DIR", upload_staging)
    # The face feature resolves its directories at call time from core.config
    # (like anim_cache), so pinning the module attributes is enough — and it
    # keeps a test that triggers a model download inside the sandbox.
    monkeypatch.setattr(config_mod, "FACE_MODEL_DIR", str(face_models))
    monkeypatch.setattr(config_mod, "FACE_CROP_DIR", str(face_crops))

    upload_service_mod = importlib.import_module(
        "media_library.services.upload_service"
    )
    monkeypatch.setattr(upload_service_mod, "UPLOAD_STAGING_DIR", upload_staging)

    url_dl_mod = importlib.import_module("media_library.workers.url_download_worker")
    monkeypatch.setattr(url_dl_mod, "UPLOAD_STAGING_DIR", upload_staging)

    monkeypatch.setattr(state_mod, "THUMBNAIL_CACHE_DIR", thumb_dir)
    # The indexer is split into submodules that each bind the config values at
    # import time, so patch every module that holds a copy.
    for _mod_name in (
        "indexer",
        "indexer_scan",
        "indexer_files",
        "indexer_prune",
        "indexer_crawl",
    ):
        _mod = importlib.import_module(f"media_library.db.{_mod_name}")
        if hasattr(_mod, "THUMBNAIL_CACHE_DIR"):
            monkeypatch.setattr(_mod, "THUMBNAIL_CACHE_DIR", thumb_dir)
    monkeypatch.setattr(zip_mod.core, "ZIP_CACHE_DIR", zip_cache)
    monkeypatch.setattr(zip_mod.core, "ZIP_STAGING_DIR", zip_staging)
    monkeypatch.setattr(zip_mod.core, "THUMBNAIL_CACHE_DIR", thumb_dir)
    monkeypatch.setattr(ffmpeg_mod, "WEBP_STAGING_DIR", webp_staging)
    monkeypatch.setattr(backup_mod, "THUMBNAIL_CACHE_DIR", thumb_dir)
    monkeypatch.setattr(backup_mod, "BACKUP_STAGING_DIR", backup_staging)
    monkeypatch.setattr(transcode_mod, "TRANSCODE_TEMP_DIR", transcode_dir)
    monkeypatch.setattr(h265_mod, "TRANSCODE_TEMP_DIR", transcode_dir)


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


# ---------------------------------------------------------------------------
# Face recognition ("People") test doubles.
#
# The real pipeline needs OpenCV plus two ONNX models (~37 MB) and would be
# non-deterministic to assert on, so the engine is a stand-in: detection is
# driven by the scan image's mean colour (red = person A, blue = person B, black
# = no face), and embeddings are one-hot vectors. Everything else — clustering,
# crops, the cache, the API — is the production code path.
# ---------------------------------------------------------------------------


class FakeFaceEngine:
    """Deterministic stand-in for the YuNet + SFace engine."""

    def __init__(self):
        self.calls = 0

    def detect(self, image, max_faces=None):
        self.calls += 1
        arr = np.asarray(image)
        if arr.ndim != 3:
            return []
        mean = arr.reshape(-1, 3).mean(axis=0)
        b, g, r = float(mean[0]), float(mean[1]), float(mean[2])
        if r > 150 and max(r, g, b) > 150:
            seed = 1
        elif b > 150:
            seed = 2
        else:
            return []
        embedding = np.zeros(128, dtype="float32")
        embedding[seed % 128] = 1.0
        return [
            {
                "x": 10,
                "y": 10,
                "w": 60,
                "h": 60,
                "score": 0.9,
                "embedding": embedding,
            }
        ]


@pytest.fixture
def fake_face_engine():
    """Install the deterministic face engine and reset the clustering caches."""
    from media_library.services import face_engine, face_service

    engine = FakeFaceEngine()
    face_engine.set_engine(engine)
    face_service.invalidate_centroids()
    yield engine
    face_engine.reset_engine()
    face_service.invalidate_centroids()


@pytest.fixture
def faces_enabled(monkeypatch):
    """Turn face recognition on with test-friendly thresholds."""
    import importlib

    config_mod = importlib.import_module("media_library.core.config")
    face_service_mod = importlib.import_module("media_library.services.face_service")
    monkeypatch.setattr(config_mod, "FACE_RECOGNITION_ENABLED", True)
    monkeypatch.setattr(config_mod, "FACE_MATCH_THRESHOLD", 0.36)
    monkeypatch.setattr(config_mod, "FACE_MIN_FACE_PX", 8)
    monkeypatch.setattr(config_mod, "FACE_CROP_MAX_SIZE_GB", 2.0)
    face_service_mod._THRESHOLD_CACHE.update(value=None, at=0.0)
    yield
    face_service_mod._THRESHOLD_CACHE.update(value=None, at=0.0)


@pytest.fixture
def face_image_factory(sandbox_env):
    """``factory(name, color)`` -> an indexed image with a cached thumbnail.

    The thumbnail is what the worker actually reads, so the fixture writes the
    image's own bytes there (no FFmpeg, no remote reads).
    """
    from media_library.core.state import get_thumbnail_cache_path
    from media_library.db.connection import db_conn
    from media_library.db.indexer_files import add_file_to_index

    media_dir = sandbox_env["media_dir"]

    def _make(name: str, color: tuple[int, int, int]):
        target = media_dir / name
        target.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (64, 64), color).save(target, "JPEG")
        add_file_to_index(str(target))
        stat = target.stat()
        thumb = get_thumbnail_cache_path(str(target), stat.st_mtime, stat.st_size)
        Path(thumb).write_bytes(target.read_bytes())
        with db_conn() as conn, conn:
            conn.execute(
                "UPDATE files SET type='image', has_thumb=1 WHERE path = ?",
                (str(target),),
            )
        return target

    return _make


@pytest.fixture
def scan_face_batch():
    """``scan(worker)`` — drive the worker's internals once over a batch."""

    def _scan(worker) -> int:
        from media_library.services import face_engine, face_service

        engine = face_engine.get_engine()
        batch = asyncio.run(worker._next_batch())
        centroids = face_service.get_centroids()
        processed = 0
        for row in batch:
            if row["type"] == "video":
                faces = asyncio.run(worker._scan_video(engine, row))
            else:
                faces = asyncio.run(worker._scan_image(engine, row))
            if faces is None:
                continue
            face_service.record_scan(
                row["path"], row["mtime"], row["size"], faces, centroids
            )
            processed += 1
        return processed

    return _scan


@pytest.fixture
def people_client(sandbox_env, fake_face_engine, faces_enabled):
    """A TestClient over the sandbox library, with faces enabled and faked."""
    from starlette.testclient import TestClient

    from media_library.app_factory import create_app
    from media_library.core.state import state

    state.reset_runtime()
    state["destination_dir"] = str(sandbox_env["media_dir"])
    state["source_dir"] = str(sandbox_env["incoming_dir"])

    app = create_app(
        source_dir=str(sandbox_env["incoming_dir"]),
        destination_dir=str(sandbox_env["media_dir"]),
        run_startup_workers=False,
    )
    with TestClient(app) as client:
        yield client


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
