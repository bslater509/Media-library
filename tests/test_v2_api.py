"""Tests for the v2 API slice: browse, search, streaming, boundaries."""

from __future__ import annotations

import ast
import hashlib
import os
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from media_library_v2 import config as v2_config
from media_library_v2.mount import build_v2_app


@pytest.fixture
def v2_env(tmp_path, monkeypatch):
    """Isolated library, thumbnails and index for one v2 test."""
    library = tmp_path / "library"
    thumbs = tmp_path / "thumbs"
    (thumbs / "animated").mkdir(parents=True)
    (library / "Iceland 2023").mkdir(parents=True)
    (library / "Alinity").mkdir(parents=True)
    monkeypatch.setenv("LIBRARY_DIR", str(library))
    monkeypatch.setenv("V2_LIBRARY_DIR", str(library))
    monkeypatch.setenv("THUMBNAIL_DIR", str(thumbs))
    monkeypatch.setenv("V2_DB_READ_ONLY", "true")
    monkeypatch.setenv("V2_ENABLED", "true")
    v2_config.reload_settings()

    db_path = Path(os.environ["DB_PATH"])
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO folders (path, name, parent_path, file_count, "
            "subfolder_count, total_size, is_zip) VALUES (?,?,?,?,?,?,?)",
            (str(library), "library", str(library.parent), 1, 2, 0, 0),
        )
        conn.execute(
            "INSERT INTO folders (path, name, parent_path, file_count, "
            "subfolder_count, total_size, is_zip) VALUES (?,?,?,?,?,?,?)",
            (str(library / "Iceland 2023"), "Iceland 2023", str(library), 1, 0, 0, 0),
        )
        conn.execute(
            "INSERT INTO folders (path, name, parent_path, file_count, "
            "subfolder_count, total_size, is_zip) VALUES (?,?,?,?,?,?,?)",
            (str(library / "Alinity"), "Alinity", str(library), 0, 0, 0, 0),
        )

    video = library / "Iceland 2023" / "aurora.mp4"
    video.write_bytes(b"0123456789" * 100)
    image = library / "Iceland 2023" / "harbour.jpg"
    image.write_bytes(b"\xff\xd8\xff" + b"x" * 500)

    st = video.stat()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO files (path, name, folder_path, type, ext, size, "
            "size_formatted, mtime, needs_transcode, has_thumb, width, height, "
            "duration, video_codec, fps) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                str(video),
                video.name,
                str(video.parent),
                "video",
                ".mp4",
                st.st_size,
                "1.0 KB",
                st.st_mtime,
                0,
                1,
                1920,
                1080,
                12.5,
                "h264",
                30.0,
            ),
        )
        conn.execute(
            "INSERT INTO files (path, name, folder_path, type, ext, size, "
            "size_formatted, mtime, needs_transcode, has_thumb, width, height) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                str(image),
                image.name,
                str(image.parent),
                "image",
                ".jpg",
                image.stat().st_size,
                "503 B",
                image.stat().st_mtime,
                0,
                0,
                800,
                600,
            ),
        )
        conn.execute(
            "INSERT INTO favorites (path, created_at) VALUES (?, ?)",
            (str(image), 1.0),
        )

    # A cached poster for the video, at the key v1 uses.
    key = hashlib.sha256(f"{video}_{st.st_mtime}_{st.st_size}".encode()).hexdigest()[
        :24
    ]
    (thumbs / f"thumb_{key}.jpg").write_bytes(b"fakejpeg")

    client = TestClient(build_v2_app())
    try:
        yield {"client": client, "library": library, "video": video, "image": image}
    finally:
        v2_config.reload_settings()


def test_browse_returns_folders_files_and_crumbs(v2_env):
    client = v2_env["client"]
    root = client.get("/api/v2/library/browse").json()
    assert root["path"] == str(v2_env["library"])
    assert [f["name"] for f in root["folders"]] == ["Alinity", "Iceland 2023"]
    assert root["indexed"] is True

    folder = client.get(
        "/api/v2/library/browse",
        params={"path": str(v2_env["library"] / "Iceland 2023")},
    ).json()
    assert folder["total"] == 2
    assert [f["name"] for f in folder["files"]] == ["aurora.mp4", "harbour.jpg"]
    entry = folder["files"][0]
    assert entry["type"] == "video"
    assert entry["width"] == 1920
    assert entry["playable"] is True
    assert entry["thumb_url"].startswith("/api/v2/thumbnails?path=")
    assert len(entry["id"]) == 16
    assert [c["name"] for c in folder["breadcrumbs"]] == ["library", "Iceland 2023"]


def test_search_matches_name_and_relative_folder(v2_env):
    client = v2_env["client"]
    by_name = client.get("/api/v2/library/search", params={"q": "aurora"}).json()
    assert by_name["total"] == 1
    assert by_name["results"][0]["rel_folder"] == "Iceland 2023"

    by_folder = client.get("/api/v2/library/search", params={"q": "iceland"}).json()
    assert {r["name"] for r in by_folder["results"]} == {"aurora.mp4", "harbour.jpg"}

    filtered = client.get(
        "/api/v2/library/search", params={"q": "iceland", "type": "image"}
    ).json()
    assert [r["name"] for r in filtered["results"]] == ["harbour.jpg"]


def test_search_never_returns_the_library_root_match(v2_env):
    client = v2_env["client"]
    # The library directory is named "library"; that segment is the root and
    # is stripped from the path comparison, so it must not match every file.
    result = client.get("/api/v2/library/search", params={"q": "library"}).json()
    assert result["total"] == 0


def test_paths_outside_the_library_are_denied(v2_env):
    client = v2_env["client"]
    assert (
        client.get("/api/v2/library/browse", params={"path": "/etc"}).status_code == 403
    )
    assert (
        client.get("/api/v2/media", params={"path": "/etc/hostname"}).status_code == 403
    )


def test_media_supports_range_and_head(v2_env):
    client = v2_env["client"]
    path = str(v2_env["video"])
    full = client.get("/api/v2/media", params={"path": path})
    assert full.status_code == 200
    assert full.headers["accept-ranges"] == "bytes"
    assert len(full.content) == 1000

    partial = client.get(
        "/api/v2/media", params={"path": path}, headers={"Range": "bytes=10-19"}
    )
    assert partial.status_code == 206
    assert partial.content == b"0123456789"
    assert partial.headers["content-range"] == "bytes 10-19/1000"

    head = client.head("/api/v2/media", params={"path": path})
    assert head.status_code == 200
    assert head.headers["content-length"] == "1000"

    bad = client.get(
        "/api/v2/media", params={"path": path}, headers={"Range": "bytes=5000-"}
    )
    assert bad.status_code == 416


def test_thumbnail_serves_the_v1_cache_key(v2_env):
    client = v2_env["client"]
    ok = client.get("/api/v2/thumbnails", params={"path": str(v2_env["video"])})
    assert ok.status_code == 200
    assert ok.content == b"fakejpeg"
    assert "immutable" in ok.headers["cache-control"]

    missing = client.get("/api/v2/thumbnails", params={"path": str(v2_env["image"])})
    assert missing.status_code == 404


def test_folder_search_finds_folders_by_name(v2_env):
    client = v2_env["client"]
    found = client.get("/api/v2/library/folders/search", params={"q": "ice"}).json()
    assert [folder["name"] for folder in found["results"]] == ["Iceland 2023"]

    missed = client.get("/api/v2/library/folders/search", params={"q": "zzz"}).json()
    assert missed["results"] == []


def test_browse_paginates_with_next_offset(v2_env):
    client = v2_env["client"]
    target = str(v2_env["library"] / "Iceland 2023")
    page = client.get(
        "/api/v2/library/browse", params={"path": target, "limit": 1}
    ).json()
    assert len(page["files"]) == 1
    assert page["next_offset"] == 1
    assert page["total"] == 2

    second = client.get(
        "/api/v2/library/browse",
        params={"path": target, "limit": 1, "offset": page["next_offset"]},
    ).json()
    assert second["next_offset"] is None
    assert second["files"][0]["path"] != page["files"][0]["path"]


def test_browse_filters_by_type_favorites_and_sort(v2_env):
    client = v2_env["client"]
    target = str(v2_env["library"] / "Iceland 2023")

    images = client.get(
        "/api/v2/library/browse", params={"path": target, "type": "image"}
    ).json()
    assert [f["name"] for f in images["files"]] == ["harbour.jpg"]
    assert images["total"] == 1

    favorites = client.get(
        "/api/v2/library/browse", params={"path": target, "favorite": "true"}
    ).json()
    assert [f["name"] for f in favorites["files"]] == ["harbour.jpg"]

    none = client.get(
        "/api/v2/library/browse",
        params={"path": target, "favorite": "true", "type": "video"},
    ).json()
    assert none["files"] == []
    assert none["total"] == 0

    largest = client.get(
        "/api/v2/library/browse", params={"path": target, "sort": "size_desc"}
    ).json()
    assert [f["name"] for f in largest["files"]] == ["aurora.mp4", "harbour.jpg"]

    by_name_desc = client.get(
        "/api/v2/library/browse", params={"path": target, "sort": "name_desc"}
    ).json()
    assert [f["name"] for f in by_name_desc["files"]] == ["harbour.jpg", "aurora.mp4"]


def test_system_info_reports_totals(v2_env):
    client = v2_env["client"]
    info = client.get("/api/v2/system/info").json()
    assert info["version"] == "3.0.0"
    assert info["counts"] == {
        "files": 2,
        "folders": 3,
        "videos": 1,
        "images": 1,
        "favorites": 1,
    }
    assert info["features"]["browse"] is True
    assert client.get("/api/v2/health").json()["status"] == "ok"


def _imported_modules(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if isinstance(node, ast.ImportFrom) and node.module:
        return [node.module]
    return []


def test_v2_never_imports_v1():
    """The rewrite must stay a rewrite: v2 never reaches into media_library."""
    package = Path(__file__).resolve().parent.parent / "media_library_v2"
    offenders: list[str] = []
    for path in package.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            for module in _imported_modules(node):
                if module == "media_library" or module.startswith("media_library."):
                    offenders.append(f"{path}: {module}")
    assert not offenders, offenders
