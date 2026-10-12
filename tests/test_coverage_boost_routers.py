"""Extra coverage for Library management router branches and core config.

Exercises the error and edge branches of ``routers/library_manage`` (empty
paths, self-moves, undo failures, bulk errors) plus the destination/DB path
resolution and config load/save helpers in ``core/config``.
"""

import asyncio
import json
import os
import runpy
from pathlib import Path
from unittest.mock import patch

import pytest

from media_library.core.state import state
from media_library.workers.library_ops_worker import library_ops_worker_instance


@pytest.fixture
def client(api_client, tmp_path):
    """Yields (client, media_dir) for the isolated app from ``api_client``."""
    return api_client, tmp_path / "media_dest"


def _mkfile(path: Path, data: bytes = b"payload"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return str(path)


def _mkzip(path: Path) -> str:
    import zipfile

    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("inner.txt", b"zipped")
    return str(path)


# ==============================================================================
# /api/backup/move/check and /api/backup/move
# ==============================================================================


def test_move_check_rejects_empty_paths(client):
    c, media = client
    res = c.post(
        "/api/backup/move/check", json={"paths": [], "destination_folder": str(media)}
    )
    assert res.status_code == 400

    res = c.post("/api/backup/move", json={"paths": [], "destination_folder": str(media)})
    assert res.status_code == 400


def test_move_check_reports_collisions(client):
    c, media = client
    src = _mkfile(media / "src" / "dup.txt")
    _mkfile(media / "dest" / "dup.txt")

    res = c.post(
        "/api/backup/move/check",
        json={"paths": [src], "destination_folder": str(media / "dest")},
    )
    body = res.json()
    assert res.status_code == 200
    assert body["collision_count"] == 1
    assert body["collisions"][0]["name"] == "dup.txt"


def test_move_to_library_root_is_allowed(client):
    """The library root is a valid destination even without an isdir() re-check."""
    c, media = client
    src = _mkfile(media / "sub" / "loose.txt")

    res = c.post(
        "/api/backup/move",
        json={"paths": [src], "destination_folder": str(media)},
    )
    assert res.status_code == 200
    assert res.json()["queued"] == 1


def test_move_missing_destination_folder_returns_400(client):
    c, media = client
    src = _mkfile(media / "a.txt")
    res = c.post(
        "/api/backup/move",
        json={"paths": [src], "destination_folder": str(media / "ghost")},
    )
    assert res.status_code == 400


def test_move_skip_policy_with_all_collisions_queues_nothing(client):
    c, media = client
    src = _mkfile(media / "src" / "dup.txt")
    _mkfile(media / "dest" / "dup.txt")

    res = c.post(
        "/api/backup/move",
        json={
            "paths": [src],
            "destination_folder": str(media / "dest"),
            "conflict_policy": "skip",
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["queued"] == 0
    assert body["tasks"] == []
    assert Path(src).exists()


def test_move_invalid_policy_falls_back_to_auto_rename(client):
    c, media = client
    src = _mkfile(media / "src" / "dup.txt")
    _mkfile(media / "dest" / "dup.txt")

    res = c.post(
        "/api/backup/move",
        json={
            "paths": [src],
            "destination_folder": str(media / "dest"),
            "conflict_policy": "nonsense",
        },
    )
    assert res.status_code == 200
    task = res.json()["tasks"][0]
    assert task["conflict"] is True
    assert Path(task["target_path"]).name == "dup (1).txt"


def test_move_makedirs_failure_returns_500(client):
    c, media = client
    src = _mkfile(media / "a.txt")
    # Destination must already exist to pass validation; the mkdir still runs
    # for the batch (exist_ok=True) and its failure is reported as a 500.
    (media / "dest").mkdir()

    with patch(
        "media_library.routers.library_manage.os.makedirs",
        side_effect=OSError("read-only mount"),
    ):
        res = c.post(
            "/api/backup/move",
            json={"paths": [src], "destination_folder": str(media / "dest")},
        )
    assert res.status_code == 500


def test_move_folder_into_itself_rejected(client):
    c, media = client
    folder = media / "outer" / "inner"
    folder.mkdir(parents=True)

    res = c.post(
        "/api/backup/move",
        json={
            "paths": [str(media / "outer")],
            "destination_folder": str(folder),
        },
    )
    assert res.status_code == 400
    assert "into itself" in res.json()["detail"]


def test_move_commonpath_value_error_is_tolerated(client):
    """Odd path arguments make the self-move check meaningless, not fatal."""
    c, media = client
    folder = media / "movable"
    folder.mkdir()
    (media / "dest").mkdir()

    class _PathProxy:
        def __getattr__(self, name):
            return getattr(os.path, name)

        def commonpath(self, paths):
            raise ValueError("mixed drives")

    class _OsProxy:
        """Delegates to the real os module except for path.commonpath."""

        def __init__(self, real):
            self._real = real

        def __getattr__(self, name):
            if name == "path":
                return _PathProxy()
            return getattr(self._real, name)

    # Only the router's own self-move guard sees the raising commonpath; the
    # path validator in the service keeps the real one.
    with patch("media_library.routers.library_manage.os", _OsProxy(os)):
        res = c.post(
            "/api/backup/move",
            json={
                "paths": [str(folder)],
                "destination_folder": str(media / "dest"),
            },
        )
    assert res.status_code == 200
    assert res.json()["queued"] == 1


# ==============================================================================
# /api/backup/undo
# ==============================================================================


def _push_undo(source: str, target: str, kind: str = "file"):
    record = {
        "id": "undo_test",
        "op": "move",
        "source_path": source,
        "target_path": target,
        "kind": kind,
    }
    library_ops_worker_instance.push_undo(record)
    return record


def test_undo_restores_to_restored_name_when_source_exists(client):
    c, media = client
    source = _mkfile(media / "name.txt")
    target = _mkfile(media / "dest" / "name.txt")
    _push_undo(source, target, "file")

    try:
        res = c.post("/api/backup/undo")
        assert res.status_code == 200
        body = res.json()
        assert body["restored_path"].endswith("name_restored_1.txt")
        assert Path(body["restored_path"]).exists()
        assert not Path(target).exists()
    finally:
        library_ops_worker_instance.undo_stack.clear()


def test_undo_missing_target_returns_404(client):
    c, media = client
    _push_undo(str(media / "ghost.txt"), str(media / "dest" / "ghost.txt"), "file")

    try:
        res = c.post("/api/backup/undo")
        assert res.status_code == 404
        # The record was consumed and not restored onto the stack.
        assert library_ops_worker_instance.pop_undo() is None
    finally:
        library_ops_worker_instance.undo_stack.clear()


def test_undo_failure_pushes_record_back(client):
    c, media = client
    source = _mkfile(media / "boom.txt")
    target = _mkfile(media / "dest" / "boom.txt")
    _push_undo(source, target, "file")

    try:
        with patch(
            "media_library.routers.library_manage.move_library_path",
            side_effect=RuntimeError("move exploded"),
        ):
            res = c.post("/api/backup/undo")
        assert res.status_code == 500
        # The record goes back on the stack so the user can retry.
        assert library_ops_worker_instance.pop_undo()["target_path"] == target
        assert Path(target).exists()
    finally:
        library_ops_worker_instance.undo_stack.clear()


def test_undo_with_nothing_returns_400(client):
    c, _media = client
    library_ops_worker_instance.undo_stack.clear()
    res = c.post("/api/backup/undo")
    assert res.status_code == 400


# ==============================================================================
# folder create / rename / delete
# ==============================================================================


def test_create_folder_duplicate_name_returns_400(client):
    c, media = client
    (media / "exists").mkdir()
    res = c.post("/api/backup/folders", json={"parent_path": str(media), "name": "exists"})
    assert res.status_code == 400


def test_create_folder_unexpected_error_returns_500(client):
    c, media = client
    with patch(
        "media_library.routers.library_manage.create_library_folder",
        side_effect=RuntimeError("disk exploded"),
    ):
        res = c.post("/api/backup/folders", json={"parent_path": str(media), "name": "new"})
    assert res.status_code == 500


def test_rename_folder_error_branches(client):
    c, media = client
    folder = media / "orig"
    folder.mkdir()

    # Invalid new name -> ValueError -> 400
    res = c.post(
        "/api/backup/folders/rename", json={"path": str(folder), "new_name": ".."}
    )
    assert res.status_code == 400

    # Duplicate target name -> FileExistsError -> 400
    (media / "taken").mkdir()
    res = c.post(
        "/api/backup/folders/rename", json={"path": str(folder), "new_name": "taken"}
    )
    assert res.status_code == 400

    # Unexpected failure -> 500
    with patch(
        "media_library.routers.library_manage.rename_library_path",
        side_effect=RuntimeError("rename exploded"),
    ):
        res = c.post(
            "/api/backup/folders/rename", json={"path": str(folder), "new_name": "ok"}
        )
    assert res.status_code == 500


def test_delete_folder_queues_zip_and_rejects_file(client):
    c, media = client

    # A plain file cannot be deleted through this endpoint.
    file_path = _mkfile(media / "plain.txt")
    res = c.post("/api/backup/folders/delete", json={"path": file_path})
    assert res.status_code == 400

    # A folder is queued for the ops worker.
    (media / "doomed").mkdir()
    res = c.post("/api/backup/folders/delete", json={"path": str(media / "doomed")})
    assert res.status_code == 200
    body = res.json()
    assert body["kind"] == "folder"
    assert body["queued"] is True

    # A ZIP archive is queued with kind=zip.
    zip_path = _mkzip(media / "archive.zip")
    res = c.post("/api/backup/folders/delete", json={"path": zip_path})
    assert res.status_code == 200
    assert res.json()["kind"] == "zip"


# ==============================================================================
# bulk rename / delete / webp
# ==============================================================================


def test_bulk_rename_errors(client):
    c, media = client
    existing = _mkfile(media / "a.txt")

    # Empty paths are rejected by both endpoints.
    assert (
        c.post("/api/backup/bulk/rename/preview", json={"paths": [], "mode": "prefix"}).status_code
        == 400
    )
    assert (
        c.post("/api/backup/bulk/rename", json={"paths": [], "mode": "prefix"}).status_code
        == 400
    )

    # Unknown mode -> ValueError -> 400 (preview and apply).
    res = c.post(
        "/api/backup/bulk/rename/preview",
        json={"paths": [existing], "mode": "bogus"},
    )
    assert res.status_code == 400
    res = c.post(
        "/api/backup/bulk/rename", json={"paths": [existing], "mode": "bogus"}
    )
    assert res.status_code == 400

    # A rename that would collide is refused with 409.
    other = _mkfile(media / "b.txt")
    res = c.post(
        "/api/backup/bulk/rename",
        json={
            "paths": [existing, other],
            "mode": "find_replace",
            "params": {"find": "a", "replace": "b", "case_sensitive": False},
        },
    )
    assert res.status_code == 409

    # A no-op rename queues nothing.
    res = c.post(
        "/api/backup/bulk/rename",
        json={
            "paths": [existing],
            "mode": "find_replace",
            "params": {"find": "zzz", "replace": "yyy"},
        },
    )
    assert res.status_code == 200
    assert res.json()["queued"] == 0


def test_bulk_delete_rejects_empty_paths(client):
    c, _media = client
    res = c.post("/api/backup/bulk/delete", json={"paths": []})
    assert res.status_code == 400


def test_bulk_webp_optimize_kinds_and_errors(client):
    from PIL import Image

    c, media = client

    assert (
        c.post("/api/backup/bulk/webp-optimize", json={"paths": []}).status_code == 400
    )

    img = media / "photo.jpg"
    Image.new("RGB", (12, 12), color="red").save(img, "JPEG")
    txt = _mkfile(media / "notes.txt")
    folder = media / "album"
    folder.mkdir()
    zip_path = _mkzip(media / "bundle.zip")

    from media_library.workers.webp_worker import webp_worker_instance

    with patch.object(
        webp_worker_instance,
        "enqueue_folder",
        side_effect=RuntimeError("queue exploded"),
    ):
        res = c.post(
            "/api/backup/bulk/webp-optimize",
            json={"paths": [str(img), txt, str(folder), zip_path]},
        )

    assert res.status_code == 200
    body = res.json()
    assert body["images_queued"] == 1
    assert body["folders_queued"] == 2
    assert body["skipped"] == 1


# ==============================================================================
# empty folder cleanup
# ==============================================================================


def test_delete_empty_folders_without_body(client):
    c, media = client
    (media / "empty1").mkdir()
    (media / "empty2").mkdir()

    # Empty-folder discovery is index-driven, so register the folders first.
    from media_library.db.connection import db_conn

    with db_conn() as conn, conn:
        for name in ("empty1", "empty2"):
            conn.execute(
                """INSERT INTO folders (path, name, parent_path, file_count,
                       subfolder_count, last_scanned)
                   VALUES (?, ?, ?, 0, 0, 0.0)""",
                (str(media / name), name, str(media)),
            )

    # A body-less POST resolves req to None and scans the whole library.
    res = c.post("/api/backup/folders/empty/delete")
    assert res.status_code == 200
    body = res.json()
    assert body["deleted_count"] == 2
    assert not (media / "empty1").exists()


# ==============================================================================
# core/config helpers
# ==============================================================================


def test_detect_default_destination_oserror(monkeypatch, tmp_path):
    from media_library.core import config as config_mod

    blocker = tmp_path / "blocker"
    blocker.write_bytes(b"I am a file")
    monkeypatch.setenv("LIBRARY_DIR", str(blocker / "sub"))

    result = config_mod.detect_default_destination()
    assert result == str((blocker / "sub").resolve())


def test_detect_default_destination_candidate_and_fallback(monkeypatch, tmp_path):
    from media_library.core import config as config_mod

    monkeypatch.delenv("LIBRARY_DIR", raising=False)

    # First existing candidate wins.
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    monkeypatch.setattr(config_mod, "CANDIDATE_DESTINATIONS", [str(candidate)])
    assert config_mod.detect_default_destination() == str(candidate)

    # No candidates: fall back to DATA_DIR/media (created on demand).
    monkeypatch.setattr(config_mod, "CANDIDATE_DESTINATIONS", [str(tmp_path / "ghost")])
    data_dir = tmp_path / "data"
    monkeypatch.setattr(config_mod, "DATA_DIR", data_dir)
    result = config_mod.detect_default_destination()
    assert result == str((data_dir / "media").resolve())
    assert (data_dir / "media").is_dir()


def test_db_path_resolution(monkeypatch, tmp_path):
    """The DB_PATH fallback chain: env > root snapshot > DATA_DIR default."""
    from media_library.core import config as config_mod

    # Run a copy of config.py from a throwaway tree so the module's BASE_DIR
    # (derived from __file__) points at a directory we control: this exercises
    # the root-level backup_index.db branch without touching the real repo.
    base = tmp_path / "root"
    shim = base / "a" / "b" / "c" / "config.py"
    shim.parent.mkdir(parents=True)
    shim.write_text(Path(config_mod.__file__).read_text(encoding="utf-8"), encoding="utf-8")

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("LIBRARY_DIR", str(tmp_path / "media"))

    with patch("dotenv.load_dotenv"):
        # 1. An explicit DB_PATH env always wins.
        explicit = tmp_path / "explicit.db"
        monkeypatch.setenv("DB_PATH", str(explicit))
        ns = runpy.run_path(str(shim))
        assert ns["DB_PATH"] == explicit.resolve()

        # 2. No env, no root-level snapshot: DATA_DIR/library.db.
        monkeypatch.delenv("DB_PATH")
        ns = runpy.run_path(str(shim))
        assert ns["DB_PATH"] == (data_dir / "library.db").resolve()

        # 3. A root-level backup_index.db beats the DATA_DIR default.
        (base / "a" / "backup_index.db").write_bytes(b"")
        ns = runpy.run_path(str(shim))
        assert ns["DB_PATH"] == base / "a" / "backup_index.db"


def test_load_config_data_variants(monkeypatch, tmp_path):
    from media_library.core import config as config_mod

    cfg = tmp_path / "config.json"
    monkeypatch.setattr(config_mod, "CONFIG_FILE", cfg)
    monkeypatch.delenv("INCOMING_DIR", raising=False)

    # Missing file: env-derived defaults.
    assert config_mod.load_config_data() == {"recent_destinations": [], "source_dir": ""}

    # Valid file supplies recent destinations and the source dir.
    cfg.write_text(
        json.dumps({"recent_destinations": ["/a", "/b"], "source_dir": "/incoming"}),
        encoding="utf-8",
    )
    loaded = config_mod.load_config_data()
    assert loaded["recent_destinations"] == ["/a", "/b"]
    assert loaded["source_dir"] == "/incoming"

    # A real INCOMING_DIR env takes precedence over the stored source_dir.
    monkeypatch.setenv("INCOMING_DIR", str(tmp_path / "env_incoming"))
    loaded = config_mod.load_config_data()
    assert loaded["source_dir"] == str(tmp_path / "env_incoming")

    # Corrupt JSON is reported and ignored.
    cfg.write_text("{not json", encoding="utf-8")
    loaded = config_mod.load_config_data()
    assert loaded["recent_destinations"] == []


def test_save_config_data_writes_atomically_and_tolerates_errors(monkeypatch, tmp_path):
    from media_library.core import config as config_mod

    cfg = tmp_path / "config.json"
    monkeypatch.setattr(config_mod, "CONFIG_FILE", cfg)

    config_mod.save_config_data(
        {"recent_destinations": [f"/p{i}" for i in range(20)], "source_dir": "/src"}
    )
    saved = json.loads(cfg.read_text(encoding="utf-8"))
    assert saved["source_dir"] == "/src"
    assert len(saved["recent_destinations"]) == 15
    # No staging leftovers.
    assert list(tmp_path.glob(".tmp_*")) == []

    # A serialization failure is swallowed, leaving the previous file intact.
    with patch("json.dump", side_effect=OSError("disk full")):
        config_mod.save_config_data({"recent_destinations": ["/x"]})
    assert json.loads(cfg.read_text(encoding="utf-8"))["source_dir"] == "/src"
