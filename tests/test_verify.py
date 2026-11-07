"""Verification queue coverage: detection, worker pass, and the API.

``file_issues`` holds one row per (path, issue) the index shows: a zero-byte
media file, a definitive probe failure stored as ``0 x 0``, or a failed poster
(``has_thumb = -1``). A dismissal keeps the row, a recheck (or the next pass)
clears a row whose issue no longer derives, and a pass prunes rows whose
``files`` row is gone.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from media_library import app as api_app
from media_library import config as api_config
from media_library import verify
from media_library.app import create_app
from media_library.schema import init_schema
from media_library.verify import VerifyWorker


@pytest.fixture(autouse=True)
def fresh_verify_worker(monkeypatch):
    """A predictable, unstarted module-level worker for every test.

    A rescan request that lands on the module singleton (through the API)
    must never leak counters or pending flags into the next test.
    """
    existing = verify._worker
    if existing is not None and existing._thread and existing._thread.is_alive():
        existing.stop()
    monkeypatch.setattr(verify, "_worker", None)


@pytest.fixture
def seeded(tmp_path, monkeypatch):
    """A tiny library and index: one clean file, three broken ones."""
    library = tmp_path / "library"
    library.mkdir()
    monkeypatch.setenv("LIBRARY_DIR", str(library))
    api_config.reload_settings()
    db = Path(api_config.get_settings().db_path)

    zero_bytes = library / "broken.mp4"
    zero_bytes.write_bytes(b"")
    corrupt = library / "corrupt.jpg"
    corrupt.write_bytes(b"not really a jpeg")
    clean = library / "ok.png"
    Image.new("RGB", (8, 6), (10, 20, 30)).save(clean, "PNG")

    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO folders (path, name, parent_path, is_zip) VALUES (?,?,?,0)",
            (str(library), "library", str(library.parent)),
        )
        corrupt_size = corrupt.stat().st_size
        for path, kind, ext, size, has_thumb, width, height in (
            (zero_bytes, "video", ".mp4", 0, 0, None, None),
            (corrupt, "image", ".jpg", corrupt_size, 0, 0, 0),
            (clean, "image", ".png", 1, 0, None, None),
            (library / "ghost.jpg", "image", ".jpg", 50, -1, None, None),
        ):
            conn.execute(
                "INSERT INTO files (path, name, folder_path, type, ext, size, "
                "size_formatted, mtime, has_thumb, width, height) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    str(path),
                    path.name,
                    str(path.parent),
                    kind,
                    ext,
                    size,
                    f"{size} B",
                    1.0,
                    has_thumb,
                    width,
                    height,
                ),
            )

    client = TestClient(create_app())
    return client, db, library


def _issues(db: Path) -> set[tuple[str, str]]:
    with sqlite3.connect(db) as conn:
        return {
            (row[0], row[1])
            for row in conn.execute("SELECT path, issue FROM file_issues")
        }


def _issue_rows(db: Path) -> dict[tuple[str, str], dict]:
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        return {
            (row["path"], row["issue"]): dict(row)
            for row in conn.execute("SELECT * FROM file_issues")
        }


def _dismissed(db: Path) -> set[tuple[str, str]]:
    with sqlite3.connect(db) as conn:
        return {
            (row[0], row[1])
            for row in conn.execute(
                "SELECT path, issue FROM file_issues WHERE dismissed_at IS NOT NULL"
            )
        }


def test_pass_records_issues_and_skips_clean_files(seeded):
    _client, db, library = seeded
    broken = str(library / "broken.mp4")
    corrupt = str(library / "corrupt.jpg")
    clean = str(library / "ok.png")
    ghost = str(library / "ghost.jpg")  # indexed, no poster, size 50

    result = VerifyWorker(batch_pause=0.0).run_pass()
    assert result["scanned"] == 4

    assert _issues(db) == {
        (broken, "zero_size"),
        (corrupt, "probe_failed"),
        (ghost, "thumb_failed"),
    }
    # The zero-byte mp4 was never probed (``width IS NULL``): no probe_failed.
    # ``width IS NULL`` is "not probed yet" and must never be flagged, and
    # the healthy image produces no issue row at all.
    assert clean not in {path for path, _issue in _issues(db)}


def test_pass_flags_zero_byte_and_probe_failed_together(seeded):
    """The definition the sweep derives: size 0 plus a hard ``0 x 0`` flag."""
    _client, db, library = seeded
    with sqlite3.connect(db) as conn:
        conn.execute(
            "UPDATE files SET width = 0, height = 0 WHERE path = ?",
            (str(library / "broken.mp4"),),
        )
    VerifyWorker(batch_pause=0.0).run_pass()
    path = str(library / "broken.mp4")
    assert _issues(db) == {
        (path, "zero_size"),
        (path, "probe_failed"),
        (str(library / "corrupt.jpg"), "probe_failed"),
        (str(library / "ghost.jpg"), "thumb_failed"),
    }


def test_pass_keeps_first_detection_and_dismissal(seeded):
    client, db, library = seeded
    corrupted = str(library / "corrupt.jpg")
    VerifyWorker(batch_pause=0.0).run_pass()

    # Dismiss the poster issue; then rerun the pass.
    assert (
        client.post(
            "/api/verify/dismiss",
            json={"path": corrupted, "issue": "probe_failed"},
        ).status_code
        == 200
    )
    rows = _issue_rows(db)
    first_detected = rows[(corrupted, "probe_failed")]["detected_at"]
    assert rows[(corrupted, "probe_failed")]["dismissed_at"] is not None

    VerifyWorker(batch_pause=0.0).run_pass()
    rows = _issue_rows(db)
    # The row is still there, still dismissed, still first-detected.
    assert rows[(corrupted, "probe_failed")]["dismissed_at"] is not None
    assert rows[(corrupted, "probe_failed")]["detected_at"] == first_detected


def test_pass_prunes_rows_whose_file_row_is_gone(seeded):
    _client, db, library = seeded
    ghost = str(library / "ghost.jpg")
    VerifyWorker(batch_pause=0.0).run_pass()
    assert (ghost, "thumb_failed") in _issues(db)

    # Delete the file row the way a move or delete would: the issue row
    # must not follow immediately but be swept by the next pass.
    with sqlite3.connect(db) as conn:
        conn.execute("DELETE FROM files WHERE path = ?", (ghost,))
    result = VerifyWorker(batch_pause=0.0).run_pass()
    assert result["pruned"] == 1
    assert (ghost, "thumb_failed") not in _issues(db)


def test_zip_members_are_never_flagged(seeded, tmp_path):
    _client, db, library = seeded
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO folders (path, name, parent_path, is_zip) VALUES (?,?,?,1)",
            (str(library / "bundle.zip"), "bundle.zip", str(library)),
        )
        conn.execute(
            "INSERT INTO files (path, name, folder_path, type, ext, size, "
            "size_formatted, mtime, has_thumb, width, height) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                str(library / "bundle.zip" / "inside.jpg"),
                "inside.jpg",
                str(library / "bundle.zip"),
                "image",
                ".jpg",
                5,
                "5 B",
                1.0,
                -1,
                0,
                0,
            ),
        )
    VerifyWorker(batch_pause=0.0).run_pass()
    # A member's ``0 x 0`` / ``-1`` are the probe and poster workers' own
    # markers, not damage.
    assert not [(path, issue) for path, issue in _issues(db) if "bundle.zip" in path]


def test_status_route_reports_counts_and_worker_state(seeded):
    client, _db, _library = seeded
    # The route reads the module singleton, so drive a pass through it.
    verify.worker().run_pass()
    body = client.get("/api/verify/status").json()

    assert body["owned"] is False
    assert body["running"] is False
    assert body["pass_running"] is False
    assert body["passes"] == 1
    assert body["scanned"] == 4
    counts = {count["issue"]: count for count in body["counts"]}
    assert set(counts) == {"zero_size", "probe_failed", "thumb_failed"}
    assert counts["zero_size"]["open"] == 1
    assert counts["probe_failed"]["open"] == 1
    assert counts["thumb_failed"]["open"] == 1
    assert counts["probe_failed"]["total"] == 1
    assert body["last_pass"] is not None
    assert body["open"] == 3
    assert body["dismissed"] == 0


def test_issues_route_filters_paginates_and_hides_dismissed(seeded):
    client, _db, library = seeded
    VerifyWorker(batch_pause=0.0).run_pass()

    body = client.get("/api/verify/issues").json()
    assert body["total"] == 3
    assert body["limit"] == 100
    assert all(row["dismissed_at"] is None for row in body["issues"])
    # Joined with the files row.
    named = {row["path"]: row for row in body["issues"]}
    assert named[str(library / "corrupt.jpg")]["name"] == "corrupt.jpg"
    assert named[str(library / "broken.mp4")]["size"] == 0

    page = client.get("/api/verify/issues", params={"limit": 1, "offset": 1}).json()
    assert page["total"] == 3
    assert len(page["issues"]) == 1

    only_thumbs = client.get(
        "/api/verify/issues", params={"issue": "thumb_failed"}
    ).json()
    assert [row["path"] for row in only_thumbs["issues"]] == [
        str(library / "ghost.jpg")
    ]

    unknown = client.get("/api/verify/issues", params={"issue": "nope"})
    assert unknown.status_code == 422  # the router's Literal rejects it

    # Dismiss one issue: hidden by default, visible on request.
    assert (
        client.post(
            "/api/verify/dismiss",
            json={"path": str(library / "corrupt.jpg"), "issue": "probe_failed"},
        ).json()["dismissed"]
        == 1
    )
    after = client.get("/api/verify/issues").json()
    assert after["total"] == 2
    with_dismissed = client.get(
        "/api/verify/issues", params={"include_dismissed": "true"}
    ).json()
    assert with_dismissed["total"] == 3
    kept = next(
        row
        for row in with_dismissed["issues"]
        if row["path"] == str(library / "corrupt.jpg")
    )
    assert kept["dismissed_at"] is not None

    # Dismissing the whole path when nothing is left to hide: zero updates.
    again = client.post(
        "/api/verify/dismiss", json={"path": str(library / "corrupt.jpg")}
    )
    assert again.json()["dismissed"] == 0


def test_recheck_clears_probe_failed_after_successful_probe(seeded):
    client, db, library = seeded
    target = library / "corrupt.jpg"
    # The body is a real PNG the row never found out about: the index says
    # ``0 x 0``, the file itself is perfectly readable.
    Image.new("RGB", (8, 6), (10, 20, 30)).save(target, "PNG")
    VerifyWorker(batch_pause=0.0).run_pass()
    assert (str(target), "probe_failed") in _issues(db)

    response = client.post("/api/verify/recheck", json={"path": str(target)})
    assert response.status_code == 200
    body = response.json()
    assert body["probe_status"] == "ok"
    assert body["issues"] == []
    assert body["cleared"] == ["probe_failed"]

    # The issue row is gone; the files row stays at 0 x 0 — fixing that is
    # the owned MetadataWorker's job, not the recheck's.
    assert (str(target), "probe_failed") not in _issue_rows(db)
    with sqlite3.connect(db) as conn:
        width = conn.execute(
            "SELECT width FROM files WHERE path = ?", (str(target),)
        ).fetchone()[0]
    assert width == 0

    # A later pass re-derives from the index alone, and this row still says
    # 0 x 0. The real repair path is owned: when the file changes, the
    # crawler resets ``width`` to NULL (indexer.py) and MetadataWorker
    # re-probes; the recommend flow is recheck after re-probing.
    VerifyWorker(batch_pause=0.0).run_pass()
    assert (str(target), "probe_failed") in _issue_rows(db)


def test_recheck_rejects_unindexed_and_outside_paths(seeded):
    client, _db, library = seeded
    missing = client.post(
        "/api/verify/recheck",
        json={"path": str(library / "not-indexed.jpg")},
    )
    assert missing.status_code == 404
    outside = client.post("/api/verify/recheck", json={"path": "../outside.mp4"})
    assert outside.status_code == 403
    invalid = client.post("/api/verify/recheck", json={"path": ""})
    assert invalid.status_code == 422  # the model requires a path


def test_rescan_route_needs_owned_workers(seeded, monkeypatch):
    client, _db, _library = seeded
    monkeypatch.delenv("OWN_WORKERS", raising=False)
    monkeypatch.setenv("OWN_WORKERS", "false")
    api_config.reload_settings()
    assert client.post("/api/verify/rescan").status_code == 409


def test_rescan_route_schedules_a_pass(seeded, monkeypatch):
    monkeypatch.setenv("OWN_WORKERS", "true")
    api_config.reload_settings()
    # Keep the owned threads out of the unit test (the route only sets a
    # signal); the runtime is started for real by the app's lifespan.
    monkeypatch.setattr(api_app, "start_runtime", lambda: False)
    monkeypatch.setattr(api_app, "stop_runtime", lambda: None)
    with TestClient(create_app()) as client:
        response = client.post("/api/verify/rescan")
        assert response.status_code == 200
        assert response.json() == {"started": True, "rescan_pending": True}


def test_read_only_index_dismiss_answers_503(seeded, monkeypatch):
    client, _db, library = seeded
    VerifyWorker(batch_pause=0.0).run_pass()
    monkeypatch.setenv("DB_READ_ONLY", "true")
    api_config.reload_settings()
    response = client.post(
        "/api/verify/dismiss",
        json={"path": str(library / "ghost.jpg")},
    )
    assert response.status_code == 503


def test_schema_migration_is_idempotent(seeded):
    _client, db, _library = seeded
    init_schema(db)
    init_schema(db)
    with sqlite3.connect(db) as conn:
        tables = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name = 'file_issues'"
            )
        ]
    assert tables == ["file_issues"]
