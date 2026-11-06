"""Analytics tests for v2: rollups, partitions, quality, reclaimable, cache."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from media_library_v2 import analytics
from media_library_v2 import config as v2_config
from media_library_v2.mount import build_v2_app


@pytest.fixture
def analytics_env(tmp_path, monkeypatch):
    library = tmp_path / "library"
    (library / "A" / "sub").mkdir(parents=True)
    (library / "B").mkdir()
    (library / "C").mkdir()
    (library / "Empty").mkdir()

    monkeypatch.setenv("LIBRARY_DIR", str(library))
    monkeypatch.setenv("V2_LIBRARY_DIR", str(library))
    monkeypatch.setenv("THUMBNAIL_DIR", str(tmp_path / "thumbs"))
    v2_config.reload_settings()
    analytics.invalidate()

    db_path = Path(os.environ["DB_PATH"])
    with sqlite3.connect(db_path) as conn:
        folders = [
            library,
            library / "A",
            library / "A" / "sub",
            library / "B",
            library / "C",
            library / "Empty",
        ]
        for folder in folders:
            conn.execute(
                "INSERT INTO folders (path, name, parent_path, file_count, "
                "subfolder_count, last_scanned, is_zip, total_size) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (
                    str(folder),
                    folder.name,
                    str(folder.parent) if folder != library else None,
                    0,
                    0,
                    1.0,
                    0,
                    0,
                ),
            )
        rows = [
            (library / "A" / "one.mp4", "video", ".mp4", 1000, 1920, 1080, 60.0, 10),
            (library / "A" / "two.jpg", "image", ".jpg", 500, 800, 600, None, None),
            (
                library / "A" / "sub" / "deep.mp4",
                "video",
                ".mp4",
                2000,
                3840,
                2160,
                30.0,
                20,
            ),
            (library / "B" / "b.jpg", "image", ".jpg", 300, 1200, 800, None, None),
            (library / "B" / "dup.mp4", "video", ".mp4", 1000, None, None, None, None),
            (library / "C" / "c1.jpg", "image", ".jpg", 200, 100, 100, None, None),
            (library / "C" / "c2.jpg", "image", ".jpg", 200, 100, 100, None, None),
        ]
        for index, (path, kind, ext, size, width, height, fps, duration) in enumerate(
            rows
        ):
            conn.execute(
                "INSERT INTO files (path, name, folder_path, type, ext, size, "
                "size_formatted, mtime, needs_transcode, has_thumb, width, height, "
                "fps, duration, thumb_hash) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    str(path),
                    path.name,
                    str(path.parent),
                    kind,
                    ext,
                    size,
                    f"{size} B",
                    1.0,
                    0,
                    1 if index % 2 == 0 else 0,
                    width,
                    height,
                    fps,
                    duration,
                    "abcdef0123456789" if index % 3 else None,
                ),
            )
        conn.execute(
            "UPDATE webp_stats SET saved_bytes = 300, converted_count = 2, "
            "skipped_count = 0 WHERE id = 1"
        )
        conn.execute(
            "INSERT INTO ignored_duplicates (path1, path2, match_type, created_at) "
            "VALUES (?,?,?,?)",
            (
                str(library / "A" / "one.mp4"),
                str(library / "B" / "dup.mp4"),
                "content",
                1.0,
            ),
        )

    try:
        yield {"library": library, "db_path": db_path}
    finally:
        analytics.invalidate()
        v2_config.reload_settings()


def test_totals_and_type_shares(analytics_env):
    payload = analytics.overview(refresh=True)
    totals = payload["totals"]
    assert totals["files"] == 7
    assert totals["videos"] == 3
    assert totals["images"] == 4
    assert totals["bytes"] == 5200
    assert totals["folders"] == 6
    assert totals["runtime"] == 30.0

    types = {item["type"]: item for item in payload["by_type"]}
    assert set(types) == {"video", "image"}
    assert types["video"]["bytes"] == 4000
    assert types["image"]["bytes"] == 1200
    assert types["video"]["share"] == pytest.approx(76.92, abs=0.1)


def test_folder_rollup_is_recursive_and_pruned(analytics_env):
    folders = analytics.overview(refresh=True)["folders"]
    library = str(analytics_env["library"])

    assert folders["root"] == library
    # A = 1000 + 500 + 2000 (the deep clip rolls up)
    largest = {entry["path"]: entry for entry in folders["largest"]}
    assert largest[str(Path(library) / "A")]["bytes"] == 3500
    # Nested nodes never appear alongside their selected ancestor.
    assert str(Path(library) / "A" / "sub") not in largest
    assert all(entry["path"] for entry in folders["largest"])

    top = {entry["name"]: entry for entry in folders["top"]}
    assert top["A"]["bytes"] == 3500
    assert top["B"]["bytes"] == 1300
    assert any(child["name"] == "sub" for child in top["A"]["children"])


def test_folder_limit_folds_the_rest_into_a_group(analytics_env):
    folders = analytics.overview(folder_limit=1, refresh=True)["folders"]
    assert len(folders["top"]) == 2
    assert folders["top"][0]["name"] == "A"
    assert folders["top"][1]["is_group"] is True
    assert folders["top"][1]["bytes"] == 1300 + 400


def test_quality_buckets(analytics_env):
    quality = analytics.overview(refresh=True)["quality"]
    buckets = {item["label"]: item["count"] for item in quality["buckets"]}
    assert buckets["4K"] == 1
    assert buckets["1080p"] == 1
    assert buckets["unprobed"] == 1
    assert quality["probed"] == 2
    assert quality["high_fps"] == 1


def test_reclaimable_and_health(analytics_env):
    payload = analytics.overview(refresh=True)
    reclaimable = payload["reclaimable"]

    assert reclaimable["duplicates"]["groups"] == 2  # size 1000 and size 200
    assert reclaimable["duplicates"]["files"] == 4
    assert reclaimable["duplicates"]["bytes"] == 1000 + 200
    assert reclaimable["webp"]["saved_bytes"] == 300
    assert reclaimable["webp"]["pending"] == 4
    assert reclaimable["zip"]["folders"] == 1  # only C is leaf + images only
    assert reclaimable["empty_folders"] == 1

    health = payload["health"]
    assert health["unprobed"] == 1
    assert health["ignored_pairs"] == 1
    assert health["needs_transcode"] == 0


def test_overview_never_touches_the_filesystem(analytics_env, monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("filesystem access in the analytics request")

    monkeypatch.setattr(os, "walk", boom)
    monkeypatch.setattr(os, "scandir", boom)
    monkeypatch.setattr(os, "stat", boom)
    monkeypatch.setattr(os.path, "getsize", boom)

    payload = analytics.overview(refresh=True)
    assert payload["totals"]["files"] == 7


def test_overview_is_memoised_until_refreshed(analytics_env):
    first = analytics.overview(refresh=True)
    assert first["totals"]["files"] == 7

    with sqlite3.connect(analytics_env["db_path"]) as conn:
        conn.execute(
            "INSERT INTO files (path, name, folder_path, type, ext, size, "
            "size_formatted, mtime, needs_transcode, has_thumb) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                str(analytics_env["library"] / "B" / "new.mp4"),
                "new.mp4",
                str(analytics_env["library"] / "B"),
                "video",
                ".mp4",
                10,
                "10 B",
                1.0,
                0,
                0,
            ),
        )

    cached = analytics.overview()
    assert cached["totals"]["files"] == 7
    refreshed = analytics.overview(refresh=True)
    assert refreshed["totals"]["files"] == 8


def test_router_serves_the_overview_and_validates_limits(analytics_env, monkeypatch):
    client = TestClient(build_v2_app())
    response = client.get("/api/v2/analytics/overview", params={"folder_limit": 2})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["totals"]["files"] == 7
    assert payload["largest_files"][0]["media_url"].startswith("/api/v2/media?path=")

    assert (
        client.get("/api/v2/analytics/overview", params={"folder_limit": 0}).status_code
        == 422
    )
    assert (
        client.get("/api/v2/analytics/overview", params={"file_limit": 101}).status_code
        == 422
    )
