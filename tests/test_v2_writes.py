"""Write-path tests for v2: favorites, rename, move, delete, folders, uploads."""

from __future__ import annotations

import hashlib
import os
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from media_library_v2 import config as v2_config
from media_library_v2.mount import build_v2_app


def _thumb_path(thumbs: Path, path: Path, mtime: float, size: int) -> Path:
    key = hashlib.sha256(f"{path}_{mtime}_{size}".encode()).hexdigest()[:24]
    return thumbs / f"thumb_{key}.jpg"


@pytest.fixture
def write_env(tmp_path, monkeypatch):
    library = tmp_path / "library"
    (library / "Trip").mkdir(parents=True)
    (library / "Inbox").mkdir(parents=True)
    thumbs = tmp_path / "thumbs"
    (thumbs / "animated").mkdir(parents=True)
    staging = tmp_path / "upload_staging"
    staging.mkdir()

    monkeypatch.setenv("LIBRARY_DIR", str(library))
    monkeypatch.setenv("V2_LIBRARY_DIR", str(library))
    monkeypatch.setenv("THUMBNAIL_DIR", str(thumbs))
    monkeypatch.setenv("V2_UPLOAD_STAGING_DIR", str(staging))
    monkeypatch.setenv("V2_DB_READ_ONLY", "false")
    monkeypatch.setenv("V2_WRITES", "true")
    v2_config.reload_settings()

    db_path = Path(os.environ["DB_PATH"])
    clip = library / "Trip" / "sunset.mp4"
    clip.write_bytes(b"video-bytes" * 100)
    image = library / "Trip" / "harbour.jpg"
    image.write_bytes(b"image-bytes" * 50)

    with sqlite3.connect(db_path) as conn:
        for folder in (library, library / "Trip", library / "Inbox"):
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
        for media, kind in ((clip, "video"), (image, "image")):
            stat = media.stat()
            conn.execute(
                "INSERT INTO files (path, name, folder_path, type, ext, size, "
                "size_formatted, mtime, needs_transcode, has_thumb, width, height, "
                "video_codec) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    str(media),
                    media.name,
                    str(media.parent),
                    kind,
                    media.suffix,
                    stat.st_size,
                    "1 KB",
                    stat.st_mtime,
                    0,
                    1,
                    1920,
                    1080,
                    "h264" if kind == "video" else None,
                ),
            )
        conn.execute(
            "INSERT INTO favorites (path, created_at) VALUES (?, ?)",
            (str(clip), 5.0),
        )
        conn.execute(
            "UPDATE folders SET file_count = 2, total_size = ? WHERE path = ?",
            (clip.stat().st_size + image.stat().st_size, str(library / "Trip")),
        )

    client = TestClient(build_v2_app())
    clip_stat = clip.stat()
    thumb = _thumb_path(thumbs, clip, clip_stat.st_mtime, clip_stat.st_size)
    thumb.write_bytes(b"poster")

    try:
        yield {
            "client": client,
            "library": library,
            "thumbs": thumbs,
            "staging": staging,
            "db_path": db_path,
            "clip": clip,
            "image": image,
            "thumb": thumb,
        }
    finally:
        v2_config.reload_settings()


def _db(write_env):
    return sqlite3.connect(write_env["db_path"])


def test_toggle_favorite_round_trip(write_env):
    client = write_env["client"]
    path = str(write_env["image"])

    on = client.post("/api/v2/favorites/toggle", json={"path": path}).json()
    assert on["is_favorite"] is True
    assert on["counts"] == {"all": 2, "videos": 1, "images": 1}

    off = client.post("/api/v2/favorites/toggle", json={"path": path}).json()
    assert off["is_favorite"] is False
    assert off["counts"] == {"all": 1, "videos": 1, "images": 0}


def test_rename_updates_index_favorites_and_poster(write_env):
    client = write_env["client"]
    old = write_env["clip"]
    assert write_env["thumb"].is_file()

    response = client.post(
        "/api/v2/manage/rename",
        json={"path": str(old), "name": "sunset-final.mp4"},
    )
    assert response.status_code == 200, response.text
    payload = response.json()

    new = Path(payload["path"])
    assert new.name == "sunset-final.mp4"
    assert new.is_file() and not old.exists()
    assert payload["file"]["width"] == 1920
    assert payload["file"]["is_favorite"] is True

    with _db(write_env) as conn:
        row = conn.execute(
            "SELECT path, name, folder_path FROM files WHERE path = ?", (str(new),)
        ).fetchone()
        favorite = conn.execute(
            "SELECT path FROM favorites WHERE path = ?", (str(new),)
        ).fetchone()
    assert row == (str(new), "sunset-final.mp4", str(old.parent))
    assert favorite == (str(new),)

    stat = new.stat()
    new_thumb = _thumb_path(write_env["thumbs"], new, stat.st_mtime, stat.st_size)
    assert new_thumb.is_file()
    assert not write_env["thumb"].exists()


def test_rename_rejects_bad_names_and_collisions(write_env):
    client = write_env["client"]
    path = str(write_env["clip"])
    for bad in ("../escape.mp4", "a/b.mp4", ".hidden.mp4", " "):
        response = client.post(
            "/api/v2/manage/rename", json={"path": path, "name": bad}
        )
        assert response.status_code == 400, bad

    collision = client.post(
        "/api/v2/manage/rename", json={"path": path, "name": "harbour.jpg"}
    )
    assert collision.status_code == 409


def test_delete_removes_file_row_favorite_and_poster(write_env):
    client = write_env["client"]
    clip = write_env["clip"]

    response = client.post("/api/v2/manage/delete", json={"paths": [str(clip)]})
    assert response.status_code == 200, response.text
    assert response.json()["deleted"] == [str(clip)]
    assert not clip.exists()
    assert not write_env["thumb"].exists()

    with _db(write_env) as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM files WHERE path = ?", (str(clip),)
            ).fetchone()[0]
            == 0
        )
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM favorites WHERE path = ?", (str(clip),)
            ).fetchone()[0]
            == 0
        )
        stats = conn.execute(
            "SELECT file_count, total_size FROM folders WHERE path = ?",
            (str(clip.parent),),
        ).fetchone()
    assert stats[0] == 1


def test_delete_reports_missing_files_per_item(write_env):
    client = write_env["client"]
    missing = str(write_env["library"] / "Trip" / "nope.mp4")
    response = client.post("/api/v2/manage/delete", json={"paths": [missing]})
    assert response.status_code == 200
    assert response.json()["deleted"] == []
    assert response.json()["failed"][0]["path"] == missing


def test_rename_recomputes_type_from_the_new_extension(write_env):
    client = write_env["client"]
    clip = write_env["clip"]

    response = client.post(
        "/api/v2/manage/rename",
        json={"path": str(clip), "name": "sunset.mkv"},
    )
    assert response.status_code == 200, response.text
    entry = response.json()["file"]
    assert entry["type"] == "video"
    assert entry["needs_transcode"] is True

    renamed = Path(response.json()["path"])
    back = client.post(
        "/api/v2/manage/rename",
        json={"path": str(renamed), "name": "sunset.mp4"},
    )
    assert back.status_code == 200
    assert back.json()["file"]["needs_transcode"] is False


def test_delete_a_folder_recursively(write_env):
    client = write_env["client"]
    folder = write_env["library"] / "Trip"
    assert folder.is_dir()

    response = client.post("/api/v2/manage/delete", json={"paths": [str(folder)]})
    assert response.status_code == 200, response.text
    assert response.json()["deleted"] == [str(folder)]
    assert not folder.exists()

    with _db(write_env) as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM folders WHERE path = ? OR path LIKE ?",
                (str(folder), str(folder) + "/%"),
            ).fetchone()[0]
            == 0
        )
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM files WHERE folder_path = ?", (str(folder),)
            ).fetchone()[0]
            == 0
        )
        parent = conn.execute(
            "SELECT subfolder_count FROM folders WHERE path = ?",
            (str(write_env["library"]),),
        ).fetchone()
    assert parent[0] == 1  # only Inbox remains


def test_refuses_to_delete_the_library_root(write_env):
    client = write_env["client"]
    response = client.post(
        "/api/v2/manage/delete", json={"paths": [str(write_env["library"])]}
    )
    assert response.status_code == 200
    assert response.json()["deleted"] == []
    assert "library root" in response.json()["failed"][0]["error"]
    assert write_env["library"].is_dir()


def test_create_folder_indexes_it(write_env):
    client = write_env["client"]
    parent = write_env["library"] / "Trip"

    response = client.post(
        "/api/v2/manage/folders", json={"parent": str(parent), "name": "Day 1"}
    )
    assert response.status_code == 200, response.text
    created = Path(response.json()["path"])
    assert created.is_dir()

    with _db(write_env) as conn:
        row = conn.execute(
            "SELECT name, parent_path FROM folders WHERE path = ?", (str(created),)
        ).fetchone()
        parent_row = conn.execute(
            "SELECT subfolder_count FROM folders WHERE path = ?", (str(parent),)
        ).fetchone()
    assert row == ("Day 1", str(parent))
    assert parent_row[0] == 1

    again = client.post(
        "/api/v2/manage/folders", json={"parent": str(parent), "name": "Day 1"}
    )
    assert again.status_code == 409


def test_move_auto_renames_collisions_and_updates_index(write_env):
    client = write_env["client"]
    clip = write_env["clip"]
    inbox = write_env["library"] / "Inbox"
    (inbox / "sunset.mp4").write_bytes(b"existing")

    response = client.post(
        "/api/v2/manage/move",
        json={
            "paths": [str(clip)],
            "destination": str(inbox),
            "conflict": "auto_rename",
        },
    )
    assert response.status_code == 200, response.text
    moved = response.json()["moved"][0]
    assert Path(moved["target"]).name == "sunset (1).mp4"
    assert Path(moved["target"]).is_file()
    assert not clip.exists()

    with _db(write_env) as conn:
        row = conn.execute(
            "SELECT folder_path, path FROM files WHERE path = ?",
            (moved["target"],),
        ).fetchone()
    assert row == (str(inbox), moved["target"])


def test_move_skip_and_error_policies(write_env):
    client = write_env["client"]
    clip = write_env["clip"]
    inbox = write_env["library"] / "Inbox"
    (inbox / "sunset.mp4").write_bytes(b"existing")

    skipped = client.post(
        "/api/v2/manage/move",
        json={
            "paths": [str(clip)],
            "destination": str(inbox),
            "conflict": "skip",
        },
    )
    assert skipped.status_code == 200
    assert skipped.json()["moved"] == []
    assert skipped.json()["skipped"][0]["path"] == str(clip)
    assert clip.is_file()

    refused = client.post(
        "/api/v2/manage/move",
        json={
            "paths": [str(clip)],
            "destination": str(inbox),
            "conflict": "error",
        },
    )
    assert refused.status_code == 409
    assert clip.is_file()


def test_mutations_reject_paths_outside_the_library(write_env):
    client = write_env["client"]
    assert (
        client.post(
            "/api/v2/favorites/toggle", json={"path": "/etc/passwd"}
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/v2/manage/move",
            json={
                "paths": [str(write_env["clip"])],
                "destination": "/tmp",
            },
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/v2/manage/folders",
            json={"parent": str(write_env["library"]), "name": "../out"},
        ).status_code
        == 400
    )


def test_read_only_mode_refuses_mutations(write_env, monkeypatch):
    monkeypatch.setenv("V2_DB_READ_ONLY", "true")
    v2_config.reload_settings()
    client = write_env["client"]

    response = client.post(
        "/api/v2/favorites/toggle", json={"path": str(write_env["image"])}
    )
    assert response.status_code == 503
    assert "Writes are disabled" in response.json()["detail"]


def test_resumable_upload_commits_and_indexes(write_env):
    client = write_env["client"]
    upload_id = "abc123def456"
    first = b"a" * 1000
    second = b"b" * 500

    chunk = client.post(
        "/api/v2/uploads/chunk",
        data={"upload_id": upload_id, "offset": "0"},
        files={"file": ("blob", first, "application/octet-stream")},
    )
    assert chunk.status_code == 200, chunk.text
    assert chunk.json()["received_bytes"] == 1000

    duplicate = client.post(
        "/api/v2/uploads/chunk",
        data={"upload_id": upload_id, "offset": "0"},
        files={"file": ("blob", first, "application/octet-stream")},
    )
    assert duplicate.json() == {"received_bytes": 1000, "duplicate": True}

    gap = client.post(
        "/api/v2/uploads/chunk",
        data={"upload_id": upload_id, "offset": "5000"},
        files={"file": ("blob", second, "application/octet-stream")},
    )
    assert gap.status_code == 409

    rest = client.post(
        "/api/v2/uploads/chunk",
        data={"upload_id": upload_id, "offset": "1000"},
        files={"file": ("blob", second, "application/octet-stream")},
    )
    assert rest.json()["received_bytes"] == 1500

    status = client.get(
        "/api/v2/uploads/status", params={"upload_id": upload_id}
    ).json()
    assert status == {
        "upload_id": upload_id,
        "exists": True,
        "received_bytes": 1500,
    }

    incomplete = client.post(
        "/api/v2/uploads/finalize",
        json={
            "upload_id": upload_id,
            "name": "new clip.mp4",
            "folder": str(write_env["library"] / "Inbox"),
            "total_size": 9999,
        },
    )
    assert incomplete.status_code == 400

    done = client.post(
        "/api/v2/uploads/finalize",
        json={
            "upload_id": upload_id,
            "name": "new clip.mp4",
            "folder": str(write_env["library"] / "Inbox"),
            "total_size": 1500,
        },
    )
    assert done.status_code == 200, done.text
    payload = done.json()
    assert payload["skipped"] is False
    target = Path(payload["path"])
    assert target.read_bytes() == first + second
    assert payload["file"]["type"] == "video"
    assert payload["file"]["needs_transcode"] == 0

    with _db(write_env) as conn:
        row = conn.execute(
            "SELECT name, size FROM files WHERE path = ?", (str(target),)
        ).fetchone()
    assert row == ("new clip.mp4", 1500)
    assert not list(write_env["staging"].glob("*.part"))


def test_upload_finalize_skip_keeps_existing_file(write_env):
    client = write_env["client"]
    upload_id = "skipupload01"
    client.post(
        "/api/v2/uploads/chunk",
        data={"upload_id": upload_id, "offset": "0"},
        files={"file": ("blob", b"payload", "application/octet-stream")},
    )
    target = write_env["library"] / "Inbox" / "existing.mp4"
    target.write_bytes(b"keep me")

    response = client.post(
        "/api/v2/uploads/finalize",
        json={
            "upload_id": upload_id,
            "name": "existing.mp4",
            "folder": str(write_env["library"] / "Inbox"),
            "total_size": 7,
            "conflict": "skip",
        },
    )
    assert response.status_code == 200
    assert response.json()["skipped"] is True
    assert target.read_bytes() == b"keep me"


def test_upload_cancel_removes_staging(write_env):
    client = write_env["client"]
    upload_id = "cancelupload1"
    client.post(
        "/api/v2/uploads/chunk",
        data={"upload_id": upload_id, "offset": "0"},
        files={"file": ("blob", b"partial", "application/octet-stream")},
    )
    response = client.post("/api/v2/uploads/cancel", json={"upload_id": upload_id})
    assert response.json() == {"cancelled": True}
    assert not list(write_env["staging"].glob("*.part"))
