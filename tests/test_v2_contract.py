"""Contract tests: v1 and v2 must describe the same library.

Both apps read one SQLite index. These tests compare the parts users see —
child folder names, file names in a folder, and search results — so the
rewrite cannot silently drift from the deployed behaviour.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from media_library_v2 import config as v2_config
from media_library_v2.mount import build_v2_app


@pytest.fixture
def shared_library(tmp_path, monkeypatch):
    library = tmp_path / "library"
    (library / "Iceland 2023").mkdir(parents=True)
    (library / "Alinity").mkdir(parents=True)

    files = [
        library / "Iceland 2023" / "aurora.mp4",
        library / "Iceland 2023" / "harbour.jpg",
        library / "Alinity" / "portrait.webp",
    ]
    for path in files:
        path.write_bytes(b"x" * 256)

    monkeypatch.setenv("LIBRARY_DIR", str(library))
    monkeypatch.setenv("V2_LIBRARY_DIR", str(library))
    monkeypatch.setenv("THUMBNAIL_DIR", str(tmp_path / "thumbs"))
    v2_config.reload_settings()

    db_path = Path(os.environ["DB_PATH"])
    with sqlite3.connect(db_path) as conn:
        for index, folder in enumerate(
            [library, library / "Iceland 2023", library / "Alinity"]
        ):
            conn.execute(
                "INSERT OR REPLACE INTO folders (path, name, parent_path, file_count, "
                "subfolder_count, last_scanned, is_zip, total_size) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (
                    str(folder),
                    folder.name,
                    str(folder.parent) if folder != library else None,
                    2
                    if folder.name == "Iceland 2023"
                    else (1 if folder != library else 0),
                    2 if folder == library else 0,
                    1.0,
                    0,
                    0,
                ),
            )
        for path in files:
            conn.execute(
                "INSERT INTO files (path, name, folder_path, type, ext, size, "
                "size_formatted, mtime, needs_transcode, has_thumb) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    str(path),
                    path.name,
                    str(path.parent),
                    "video" if path.suffix == ".mp4" else "image",
                    path.suffix,
                    path.stat().st_size,
                    "256 B",
                    path.stat().st_mtime,
                    0,
                    0,
                ),
            )

    from media_library.app_factory import create_app
    from media_library.core.state import state

    state.set_destination_dir(str(library))
    try:
        yield {
            "library": library,
            "v1": TestClient(create_app(run_startup_workers=False)),
            "v2": TestClient(build_v2_app()),
        }
    finally:
        v2_config.reload_settings()


def test_browse_agrees_on_folders_and_files(shared_library):
    library = shared_library["library"]
    v1 = shared_library["v1"].get("/api/backup/browse").json()
    v2 = shared_library["v2"].get("/api/v2/library/browse").json()

    assert {f["name"] for f in v1["subfolders"]} == {f["name"] for f in v2["folders"]}
    assert [c["name"] for c in v1["breadcrumbs"]] == [
        c["name"] for c in v2["breadcrumbs"]
    ]

    target = str(library / "Iceland 2023")
    v1_folder = (
        shared_library["v1"].get("/api/backup/browse", params={"path": target}).json()
    )
    v2_folder = (
        shared_library["v2"]
        .get("/api/v2/library/browse", params={"path": target})
        .json()
    )

    assert {f["path"] for f in v1_folder["files"]} == {
        f["path"] for f in v2_folder["files"]
    }
    assert v1_folder["total_files"] == v2_folder["total"]


def test_search_agrees_on_result_paths(shared_library):
    v1 = shared_library["v1"].get("/api/backup/search", params={"q": "iceland"}).json()
    v2 = (
        shared_library["v2"]
        .get("/api/v2/library/search", params={"q": "iceland"})
        .json()
    )

    assert {r["path"] for r in v1["results"]} == {r["path"] for r in v2["results"]}
    assert v1["truncated"] == v2["truncated"]
