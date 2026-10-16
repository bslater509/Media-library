"""The /analytics overview must be an index-only, recursive, well-shaped rollup.

Guards the contract the dashboard depends on:
- the recursive folder rollup sums whole subtrees and reports a *partition*
  (no folder is listed together with its own descendant, so the ranking's
  percentages cannot double-count),
- every heavy section is computed from SQLite alone — the request must not walk
  or scan the filesystem (AGENTS.md Rule 2),
- the payload is memoized but ``refresh=1`` bypasses it.
"""

import os

import pytest
from starlette.testclient import TestClient

from media_library.app_factory import create_app
from media_library.core.state import state
from media_library.db.connection import get_db
from media_library.db.schema import init_backup_db
from media_library.services.analytics_service import invalidate_analytics_cache
from tests.conftest import patch_runtime_directories


@pytest.fixture
def analytics_client(tmp_path, monkeypatch):
    """FastAPI app on an isolated library, with the analytics memo cleared."""
    db_file = tmp_path / "test_analytics.db"
    dest_dir = tmp_path / "media_dest"
    source_dir = tmp_path / "media_src"
    thumb_dir = tmp_path / "thumbnails"
    dest_dir.mkdir()
    source_dir.mkdir()
    thumb_dir.mkdir()

    monkeypatch.setenv("DB_PATH", str(db_file))
    monkeypatch.setenv("LIBRARY_DIR", str(dest_dir))
    monkeypatch.setenv("INCOMING_DIR", str(source_dir))
    monkeypatch.setenv("THUMBNAIL_DIR", str(thumb_dir))

    patch_runtime_directories(monkeypatch, tmp_path, thumb_dir)

    state["source_dir"] = str(source_dir)
    state["destination_dir"] = str(dest_dir)

    init_backup_db(db_file)
    # The memo is process-global and revision-keyed, so a payload built by an
    # earlier test (or module import) must never leak into this one.
    invalidate_analytics_cache()

    app = create_app(
        source_dir=str(source_dir),
        destination_dir=str(dest_dir),
        run_startup_workers=False,
    )

    with TestClient(app) as client:
        invalidate_analytics_cache()
        yield client, dest_dir


def _index(dest_dir, *folders):
    from media_library.db.indexer import index_folder

    for folder in folders:
        index_folder(str(folder), str(dest_dir))


def _set_video_probe(db_file, name, **columns):
    """Fill probed columns (dimensions/fps/thumb flags) on one indexed file."""
    with get_db(db_file) as conn, conn:
        assignments = ", ".join(f"{k} = ?" for k in columns)
        conn.execute(
            f"UPDATE files SET {assignments} WHERE name = ?",
            [*columns.values(), name],
        )


def _seed_library(dest_dir, db_file):
    """Three media folders, one nested, with probed dimensions."""
    movies = dest_dir / "Movies"
    series = movies / "Series"
    images = dest_dir / "Images"
    for folder in (movies, series, images):
        folder.mkdir(parents=True, exist_ok=True)

    (movies / "big.mp4").write_bytes(b"\x00" * 5000)
    (movies / "mid.mkv").write_bytes(b"\x00" * 2000)
    (series / "episode1.mp4").write_bytes(b"\x00" * 3000)
    (images / "shot.jpg").write_bytes(b"\x00" * 500)
    (images / "shot2.webp").write_bytes(b"\x00" * 250)

    _index(dest_dir, dest_dir, movies, series, images)

    # big.mp4 is 4K/60fps, mid.mkv is 1080p/24; episode1.mp4 stays unprobed so
    # the "unknown" bucket and the unprobed counts are covered.
    _set_video_probe(
        db_file,
        "big.mp4",
        width=3840,
        height=2160,
        duration=120.0,
        fps=60.0,
        has_thumb=1,
        thumb_hash="abcdef0123456789",
    )
    _set_video_probe(
        db_file,
        "mid.mkv",
        width=1920,
        height=1080,
        duration=300.0,
        fps=24.0,
        has_thumb=1,
    )


def test_analytics_overview_shape(analytics_client):
    client, dest_dir = analytics_client
    db_file = None
    from media_library.db.connection import get_current_db_path

    db_file = get_current_db_path()
    _seed_library(dest_dir, db_file)

    res = client.get("/api/analytics/overview")
    assert res.status_code == 200
    data = res.json()

    assert data["library_root"] == str(dest_dir)
    assert data["limits"] == {"folders": 15, "files": 25}
    assert data["generated_at"] > 0

    totals = data["totals"]
    assert totals["files"] == 5
    assert totals["videos"] == 3
    assert totals["images"] == 2
    assert totals["folders"] == 4  # library root + Movies + Movies/Series + Images
    assert totals["bytes"] == 5000 + 2000 + 3000 + 500 + 250
    assert totals["bytes_formatted"].endswith("KB")

    types = {t["type"]: t for t in data["by_type"]}
    assert types["video"]["count"] == 3
    assert types["image"]["count"] == 2
    assert types["video"]["bytes"] == 10000
    assert types["image"]["bytes"] == 750
    assert round(types["video"]["percent_bytes"] + types["image"]["percent_bytes"]) == 100

    exts = {e["ext"]: e for e in data["by_extension"]["items"]}
    assert exts[".mp4"]["count"] == 2
    assert exts[".mp4"]["bytes"] == 8000
    assert data["by_extension"]["count"] == 5
    assert data["by_extension"]["other_count"] == 0

    # Recursive rollup: Movies/Series (3000) is counted inside Movies.
    largest = {f["name"]: f for f in data["folders"]["largest"]}
    assert largest["Movies"]["bytes"] == 10000
    assert largest["Movies"]["files"] == 3
    assert "Series" not in largest
    assert largest["Images"]["bytes"] == 750

    top = {f["name"]: f for f in data["folders"]["top"]}
    assert top["Movies"]["bytes"] == 10000
    assert [c["name"] for c in top["Movies"]["children"]] == ["Series"]
    assert top["Movies"]["children"][0]["bytes"] == 3000

    assert data["folders"]["root_bytes"] == totals["bytes"]
    assert data["folders"]["root_files"] == 5

    # Largest files: descending by size, shaped like a browse entry.
    files = data["largest_files"]
    assert [f["name"] for f in files[:3]] == ["big.mp4", "episode1.mp4", "mid.mkv"]
    assert files[0]["size"] == 5000
    assert files[0]["thumb_url"].startswith("/api/backup/thumbnail?path=")
    assert files[0]["media_url"].startswith("/api/backup/media?path=")

    quality = data["quality"]
    buckets = {b["key"]: b for b in quality["buckets"]}
    assert buckets["4k"]["count"] == 1  # the 3840x2160 mp4
    assert buckets["1080p"]["count"] == 1  # the 1920x1080 mkv
    assert buckets["unknown"]["count"] == 1  # the unprobed second mp4
    assert quality["videos"] == 3
    assert quality["probed_videos"] == 2
    assert quality["unprobed_videos"] == 1
    assert quality["high_fps_count"] == 1
    assert quality["total_duration"] == pytest.approx(120.0 + 300.0)
    assert quality["total_duration_formatted"] == "7m"

    reclaim = data["reclaimable"]
    assert reclaim["duplicate_groups"] == 0
    assert reclaim["empty_folders"] == 0
    assert "webp_saved_formatted" in reclaim
    assert "zip_eligible_folders" in reclaim

    health = data["health"]
    # The probe covers images too, so the two unprobed images count here.
    assert health["unprobed"] == 3
    assert health["missing_thumbs"] == 3  # 2 images + the untouched episode mp4
    assert health["is_crawling"] is False
    assert health["is_complete"] is True


def test_analytics_ranks_a_partition_not_ancestors(analytics_client):
    """A folder whose ancestor is already ranked must not appear again."""
    client, dest_dir = analytics_client
    from media_library.db.connection import get_current_db_path

    _seed_library(dest_dir, get_current_db_path())

    # Ask for a folder ranking deep enough that both Movies and Movies/Series
    # would qualify on size alone.
    res = client.get("/api/analytics/overview", params={"folder_limit": 100})
    assert res.status_code == 200
    names = [f["name"] for f in res.json()["folders"]["largest"]]

    assert "Movies" in names
    assert "Series" not in names
    assert len(names) == len(set(names))

    shares = [f["percent_bytes"] for f in res.json()["folders"]["largest"]]
    assert sum(shares) <= 100.0 + 0.5


def test_analytics_respects_limits_and_refresh_bypasses_cache(analytics_client):
    client, dest_dir = analytics_client
    from media_library.db.connection import get_current_db_path

    db_path = get_current_db_path()
    _seed_library(dest_dir, db_path)

    limited = client.get(
        "/api/analytics/overview", params={"file_limit": 2, "folder_limit": 1}
    ).json()
    assert limited["limits"] == {"folders": 1, "files": 2}
    assert len(limited["largest_files"]) == 2
    assert len(limited["folders"]["largest"]) == 1

    assert client.get("/api/analytics/overview", params={"file_limit": 0}).status_code == 422
    assert client.get("/api/analytics/overview", params={"file_limit": 500}).status_code == 422

    # Warm the default-limits memo, then add a row. Indexing one folder does not
    # bump indexer_state["revision"], so the memo legitimately still serves the
    # pre-insert snapshot until an explicit refresh drops it.
    warmed = client.get("/api/analytics/overview").json()
    assert warmed["totals"]["files"] == 5

    (dest_dir / "Images" / "late.webp").write_bytes(b"\x00" * 1000)
    _index(dest_dir, dest_dir / "Images")

    stale = client.get("/api/analytics/overview").json()
    assert stale["totals"]["files"] == 5

    fresh = client.get("/api/analytics/overview", params={"refresh": 1}).json()
    assert fresh["totals"]["files"] == 6
    assert fresh["totals"]["bytes"] == 5000 + 2000 + 3000 + 500 + 250 + 1000


def test_analytics_never_walks_or_scans_the_filesystem(analytics_client, monkeypatch):
    """Every heavy section is index-only: no directory walk, no scandir."""
    client, dest_dir = analytics_client
    from media_library.db.connection import get_current_db_path

    _seed_library(dest_dir, get_current_db_path())
    invalidate_analytics_cache()

    def _forbidden(*args, **kwargs):
        raise AssertionError("the analytics overview must not touch the filesystem")

    monkeypatch.setattr(os, "walk", _forbidden)
    monkeypatch.setattr(os, "scandir", _forbidden)

    res = client.get("/api/analytics/overview", params={"refresh": 1})
    assert res.status_code == 200
    assert res.json()["totals"]["files"] == 5
