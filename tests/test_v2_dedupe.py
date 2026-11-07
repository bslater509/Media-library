"""Dedupe tests for v2: hashing, grouping, verification, resolve, ignore."""

from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from media_library import cache, dedupe
from media_library import config as v2_config
from media_library.app import create_app


def _insert_file(
    conn,
    path: Path,
    kind: str,
    ext: str,
    size: int,
    mtime: float,
    quick: str | None,
    thumb_hash: str | None,
    has_thumb: int = 1,
) -> None:
    conn.execute(
        "INSERT INTO files (path, name, folder_path, type, ext, size, "
        "size_formatted, mtime, needs_transcode, has_thumb, width, height, "
        "thumb_hash, quick_hash) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            str(path),
            path.name,
            str(path.parent),
            kind,
            ext,
            size,
            f"{size} B",
            mtime,
            0,
            has_thumb,
            640,
            480,
            thumb_hash,
            quick,
        ),
    )


@pytest.fixture
def dedupe_env(tmp_path, monkeypatch):
    library = tmp_path / "library"
    (library / "A").mkdir(parents=True)
    (library / "B").mkdir()
    thumbs = tmp_path / "thumbs"
    (thumbs / "animated").mkdir(parents=True)
    monkeypatch.setenv("LIBRARY_DIR", str(library))
    monkeypatch.setenv("LIBRARY_DIR", str(library))
    monkeypatch.setenv("THUMBNAIL_DIR", str(thumbs))
    monkeypatch.setenv("DB_READ_ONLY", "false")
    monkeypatch.setenv("WRITES", "true")
    v2_config.reload_settings()

    db_path = Path(os.environ["DB_PATH"])
    with sqlite3.connect(db_path) as conn:
        for folder in (library, library / "A", library / "B"):
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

        same = b"identical-bytes" * 70
        a = library / "A" / "identical-a.mp4"
        b = library / "B" / "identical-b.mp4"
        a.write_bytes(same)
        b.write_bytes(same)

        head = b"H" * (128 * 1024)
        ha = library / "A" / "headmatch-a.mp4"
        hb = library / "B" / "headmatch-b.mp4"
        ha.write_bytes(head + b"left")
        hb.write_bytes(head + b"rght")

        for path in (a, b, ha, hb):
            stat = path.stat()
            _insert_file(
                conn,
                path,
                "video",
                ".mp4",
                stat.st_size,
                stat.st_mtime,
                dedupe.quick_hash(str(path)),
                None,
                has_thumb=0,
            )

        # A ZIP member must never be grouped/deleted by the content scanner.
        _insert_file(
            conn,
            library / "archive.zip" / "inside.mp4",
            "video",
            ".mp4",
            a.stat().st_size,
            a.stat().st_mtime,
            dedupe.quick_hash(str(a)),
            None,
            has_thumb=0,
        )

        unhashed = library / "A" / "unhashed.mp4"
        unhashed.write_bytes(same)
        _insert_file(
            conn,
            unhashed,
            "video",
            ".mp4",
            unhashed.stat().st_size,
            unhashed.stat().st_mtime,
            None,
            None,
            has_thumb=0,
        )

        va = library / "A" / "visual-a.jpg"
        vb = library / "B" / "visual-b.jpg"
        vc = library / "B" / "visual-c.jpg"
        for path, value, payload in (
            (va, "0000000000000000", b"image-a"),
            (vb, "0000000000000003", b"image-bb"),
            (vc, "ffffffffffffffff", b"image-ccc"),
        ):
            path.write_bytes(payload)
            _insert_file(
                conn,
                path,
                "image",
                ".jpg",
                path.stat().st_size,
                path.stat().st_mtime,
                None,
                value,
            )

        hashme = library / "A" / "zz-hashme.jpg"
        hashme.write_bytes(b"image-dddd")
        _insert_file(
            conn,
            hashme,
            "image",
            ".jpg",
            hashme.stat().st_size,
            hashme.stat().st_mtime,
            None,
            None,
        )

        # A real checkerboard poster so the pHash is not blank.
        from PIL import Image

        thumb = cache.thumbnail_path(
            str(hashme), hashme.stat().st_mtime, hashme.stat().st_size
        )
        image = Image.new("RGB", (96, 96), (20, 20, 20))
        for x in range(0, 96, 12):
            for y in range(0, 96, 12):
                if (x // 12 + y // 12) % 2 == 0:
                    for dx in range(12):
                        for dy in range(12):
                            image.putpixel((x + dx, y + dy), (230, 210, 190))
        image.save(thumb, "JPEG")

    try:
        yield {
            "library": library,
            "db_path": db_path,
            "a": a,
            "b": b,
            "ha": ha,
            "hb": hb,
            "unhashed": unhashed,
            "va": va,
            "vb": vb,
            "vc": vc,
            "hashme": hashme,
            "thumbs": thumbs,
        }
    finally:
        v2_config.reload_settings()


def _db(env):
    return sqlite3.connect(env["db_path"])


def test_quick_hash_matches_the_v1_format(dedupe_env):
    a = dedupe_env["a"]
    b = dedupe_env["b"]
    assert dedupe.quick_hash(str(a)) == dedupe.quick_hash(str(b))
    assert len(dedupe.quick_hash(str(a)) or "") == 64
    assert dedupe.quick_hash(str(a)) != dedupe.quick_hash(str(dedupe_env["ha"]))


def test_content_groups_skip_zip_members_and_ignored_pairs(dedupe_env):
    result = dedupe.content_groups()
    assert result["total_groups"] == 2  # identical pair + head-match pair
    groups = {group["size"]: group for group in result["groups"]}
    identical = groups[dedupe_env["a"].stat().st_size]
    paths = {file["path"] for file in identical["files"]}
    assert paths == {str(dedupe_env["a"]), str(dedupe_env["b"])}
    assert all("archive.zip" not in path for path in paths)
    assert identical["reclaimable"] == dedupe_env["a"].stat().st_size

    dedupe.ignore_pair(str(dedupe_env["a"]), str(dedupe_env["b"]), "content")
    after = dedupe.content_groups()
    assert all(
        {file["path"] for file in group["files"]}
        != {str(dedupe_env["a"]), str(dedupe_env["b"])}
        for group in after["groups"]
    )


def test_hash_content_fills_missing_hashes_and_joins_the_group(dedupe_env):
    before = dedupe.content_groups()
    assert before["unhashed"] == 1

    result = dedupe.hash_content(limit=10)
    assert result["hashed"] == 1
    assert result["remaining"] == 0

    after = dedupe.content_groups()
    matching = [
        group
        for group in after["groups"]
        if str(dedupe_env["unhashed"]) in {f["path"] for f in group["files"]}
    ]
    assert len(matching) == 1
    assert len(matching[0]["files"]) == 3


def test_full_verification_rejects_a_head_only_match(dedupe_env):
    assert dedupe.files_identical(str(dedupe_env["a"]), str(dedupe_env["b"]))
    assert not dedupe.files_identical(str(dedupe_env["ha"]), str(dedupe_env["hb"]))

    result = dedupe.resolve(
        str(dedupe_env["ha"]), [str(dedupe_env["hb"])], mode="content"
    )
    assert result["deleted"] == []
    assert result["mismatched"] == [str(dedupe_env["hb"])]
    assert dedupe_env["hb"].is_file()


def test_resolve_deletes_a_verified_duplicate(dedupe_env):
    with _db(dedupe_env) as conn:
        conn.execute(
            "INSERT INTO favorites (path, created_at) VALUES (?, ?)",
            (str(dedupe_env["b"]), 1.0),
        )

    result = dedupe.resolve(
        str(dedupe_env["a"]), [str(dedupe_env["b"])], mode="content"
    )
    assert result["deleted"] == [str(dedupe_env["b"])]
    assert not dedupe_env["b"].exists()
    assert dedupe_env["a"].is_file()

    with _db(dedupe_env) as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM files WHERE path = ?", (str(dedupe_env["b"]),)
            ).fetchone()[0]
            == 0
        )
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM favorites WHERE path = ?",
                (str(dedupe_env["b"]),),
            ).fetchone()[0]
            == 0
        )


def test_visual_groups_cluster_within_two_bits(dedupe_env):
    result = dedupe.visual_groups()
    groups = result["groups"]
    pair = [
        group
        for group in groups
        if {str(dedupe_env["va"]), str(dedupe_env["vb"])}
        <= {file["path"] for file in group["files"]}
    ]
    assert pair, "distance-2 hashes should cluster"
    assert all(
        str(dedupe_env["vc"]) not in {file["path"] for file in group["files"]}
        for group in groups
    )

    dedupe.ignore_pair(str(dedupe_env["va"]), str(dedupe_env["vb"]), "visual")
    assert all(
        {str(dedupe_env["va"]), str(dedupe_env["vb"])}
        != {file["path"] for file in group["files"]}
        for group in dedupe.visual_groups()["groups"]
    )


def test_all_groups_merge_content_and_visual(tmp_path, dedupe_env):
    # Make the identical pair also visually identical so the visual cluster is
    # fully covered by the content group and must not be listed twice.
    with _db(dedupe_env) as conn:
        conn.execute(
            "UPDATE files SET thumb_hash = 'aaaaaaaaaaaaaaaa', has_thumb = 1 "
            "WHERE path IN (?, ?)",
            (str(dedupe_env["a"]), str(dedupe_env["b"])),
        )

    result = dedupe.all_groups(limit=100)
    assert result["mode"] == "all"
    kinds = {group["match_type"] for group in result["groups"]}
    assert kinds == {"content", "visual"}
    paths = [{file["path"] for file in group["files"]} for group in result["groups"]]
    assert {str(dedupe_env["a"]), str(dedupe_env["b"])} in paths
    assert {str(dedupe_env["va"]), str(dedupe_env["vb"])} in paths
    # The visual cluster over a/b is fully represented by the content group.
    assert not any(
        {str(dedupe_env["a"]), str(dedupe_env["b"])} <= group
        and group != {str(dedupe_env["a"]), str(dedupe_env["b"])}
        for group in paths
    )


def test_verify_visual_group_needs_five_frame_agreement(dedupe_env, monkeypatch):
    with _db(dedupe_env) as conn:
        conn.execute(
            "UPDATE files SET duration = 100 WHERE path IN (?, ?)",
            (str(dedupe_env["a"]), str(dedupe_env["b"])),
        )

    calls: list[tuple[str, float]] = []

    def matching(path: str, timestamp: float) -> str:
        calls.append((path, timestamp))
        return (
            "0000000000000003" if path == str(dedupe_env["b"]) else "0000000000000000"
        )

    monkeypatch.setattr(dedupe, "_frame_phash", matching)
    result = dedupe.verify_visual_group([str(dedupe_env["a"]), str(dedupe_env["b"])])
    assert result["available"] is True
    assert result["confirmed"] is True
    assert result["checked_pairs"] == 1
    assert len(result["pairs"][0]["distances"]) == 5
    assert len(calls) == 10

    # A second call reuses the stored signatures instead of re-sampling.
    dedupe.verify_visual_group([str(dedupe_env["a"]), str(dedupe_env["b"])])
    assert len(calls) == 10

    def different(path: str, _timestamp: float) -> str:
        return (
            "ffffffffffffffff" if path == str(dedupe_env["b"]) else "0000000000000000"
        )

    dedupe.reset_duplicates()
    monkeypatch.setattr(dedupe, "_frame_phash", different)
    rejected = dedupe.verify_visual_group([str(dedupe_env["a"]), str(dedupe_env["b"])])
    assert rejected["confirmed"] is False
    assert rejected["pairs"][0]["matches"] == 0


def test_verify_visual_group_reports_file_and_frame_progress(dedupe_env, monkeypatch):
    with _db(dedupe_env) as conn:
        conn.execute(
            "UPDATE files SET duration = 100 WHERE path IN (?, ?)",
            (str(dedupe_env["a"]), str(dedupe_env["b"])),
        )
    monkeypatch.setattr(
        dedupe, "_frame_phash", lambda _path, _timestamp: "0000000000000001"
    )

    updates: list[dict] = []
    dedupe.verify_visual_group(
        [str(dedupe_env["a"]), str(dedupe_env["b"])], progress=updates.append
    )

    assert updates[0]["active_files"] == 2
    assert updates[0]["active_index"] == 1
    frames = {}
    for update in updates:
        frames.setdefault(update["active_index"], set()).add(update["active_frame"])
    assert frames[1] == {0, 1, 2, 3, 4, 5}
    assert frames[2] == {0, 1, 2, 3, 4, 5}
    assert updates[-1]["stage"] == "comparing"


def test_visual_check_batch_reports_progress_and_survives_the_request(
    dedupe_env, monkeypatch
):
    calls: list[list[str]] = []

    def fake(paths: list[str], progress=None) -> dict:
        calls.append(paths)
        if progress is not None:
            progress(
                {
                    "stage": "sampling",
                    "active_files": len(paths),
                    "active_index": 1,
                    "active_file": paths[0],
                    "active_frame": 1,
                    "active_frames": 5,
                }
            )
        return {
            "available": True,
            "confirmed": len(calls) == 1,
            "checked_pairs": 1,
            "confirmed_pairs": 1 if len(calls) == 1 else 0,
            "frames": 5,
            "pairs": [],
        }

    monkeypatch.setattr(dedupe, "verify_visual_group", fake)
    first = dedupe.start_visual_checks(
        [
            [str(dedupe_env["a"]), str(dedupe_env["b"])],
            [str(dedupe_env["b"]), str(dedupe_env["a"])],  # duplicate group
            [str(dedupe_env["va"]), str(dedupe_env["vb"])],
        ]
    )
    assert first["total_groups"] == 2
    assert first["running"] is True

    deadline = time.time() + 5
    status = dedupe.visual_check_status()
    while status["running"] and time.time() < deadline:
        time.sleep(0.01)
        status = dedupe.visual_check_status()

    assert status["status"] == "done"
    assert status["completed_groups"] == 2
    assert status["confirmed"] == 1
    assert status["rejected"] == 1
    assert len(calls) == 2


def test_reset_duplicates_clears_ignored_pairs_and_frame_checks(dedupe_env):
    dedupe.ignore_pair(str(dedupe_env["a"]), str(dedupe_env["b"]), "content")
    with _db(dedupe_env) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO visual_frames "
            "(path, mtime, size, duration, hashes, captured_at) "
            "VALUES (?, 1, 2, 10, '[]', 3)",
            (str(dedupe_env["a"]),),
        )

    result = dedupe.reset_duplicates()
    assert result == {"ignored_cleared": 1, "signatures_cleared": 1}
    assert dedupe.ignored_page()["total"] == 0
    with _db(dedupe_env) as conn:
        assert conn.execute("SELECT COUNT(*) FROM visual_frames").fetchone()[0] == 0


def test_visual_groups_do_not_chain(dedupe_env):
    base = dedupe_env["library"] / "B"
    chain = {
        "chain-a.jpg": "0000000000000010",
        "chain-b.jpg": "0000000000000011",
        "chain-c.jpg": "0000000000000013",
    }
    with _db(dedupe_env) as conn:
        for name, value in chain.items():
            path = base / name
            path.write_bytes(name.encode())
            _insert_file(
                conn,
                path,
                "image",
                ".jpg",
                path.stat().st_size,
                path.stat().st_mtime,
                None,
                value,
            )

    groups = dedupe.visual_groups(limit=100)["groups"]
    chain_paths = {str(base / name) for name in chain}
    assert not any(
        chain_paths <= {file["path"] for file in group["files"]} for group in groups
    )
    assert max(len(group["files"]) for group in groups) <= 12


def test_hash_visual_uses_the_cached_poster(dedupe_env):
    result = dedupe.hash_visual(limit=50)
    assert result["hashed"] >= 1

    with _db(dedupe_env) as conn:
        value = conn.execute(
            "SELECT thumb_hash FROM files WHERE path = ?",
            (str(dedupe_env["hashme"]),),
        ).fetchone()[0]
    assert value is not None and len(value) == 16
    int(value, 16)


def test_ignore_round_trip_and_page(dedupe_env):
    dedupe.ignore_pair(str(dedupe_env["a"]), str(dedupe_env["b"]), "content")
    dedupe.ignore_pair(str(dedupe_env["b"]), str(dedupe_env["a"]), "content")

    page = dedupe.ignored_page()
    assert page["total"] == 1
    assert page["items"][0]["path1"] == str(dedupe_env["a"])

    removed = dedupe.unignore_pair(str(dedupe_env["a"]), str(dedupe_env["b"]))
    assert removed["unignored"] is True
    assert dedupe.ignored_page()["total"] == 0


def test_router_wires_dedupe_and_validates_input(dedupe_env, monkeypatch):
    client = TestClient(create_app())

    status = client.get("/api/dedupe/status").json()
    assert status["content"]["candidate_files"] >= 4

    groups = client.get("/api/dedupe/groups", params={"mode": "content"}).json()
    assert groups["total_groups"] == 2

    assert client.get("/api/dedupe/groups", params={"mode": "nope"}).status_code == 400

    verify = client.post(
        "/api/dedupe/verify",
        json={"keep": str(dedupe_env["a"]), "path": str(dedupe_env["b"])},
    ).json()
    assert verify["identical"] is True

    outside = client.post(
        "/api/dedupe/verify",
        json={"keep": str(dedupe_env["a"]), "path": "/etc/passwd"},
    )
    assert outside.status_code == 403

    ignored = client.post(
        "/api/dedupe/ignore",
        json={
            "path1": str(dedupe_env["va"]),
            "path2": str(dedupe_env["vb"]),
            "match_type": "visual",
        },
    ).json()
    assert ignored["ignored"] is True
    page = client.get("/api/dedupe/ignored").json()
    assert page["total"] == 1

    combined = client.get("/api/dedupe/groups").json()
    assert combined["mode"] == "all"
    assert combined["total_groups"] >= 2

    assert (
        client.post(
            "/api/dedupe/verify-visual",
            json={"paths": [str(dedupe_env["va"])]},
        ).status_code
        == 422
    )
    frame_check = client.post(
        "/api/dedupe/verify-visual",
        json={"paths": [str(dedupe_env["va"]), str(dedupe_env["vb"])]},
    )
    assert frame_check.status_code == 200
    assert frame_check.json()["available"] is False

    monkeypatch.setattr(
        dedupe,
        "verify_visual_group",
        lambda paths, progress=None: {
            "available": True,
            "confirmed": True,
            "checked_pairs": 1,
            "confirmed_pairs": 1,
            "frames": 5,
            "pairs": [],
        },
    )
    batch = client.post(
        "/api/dedupe/verify-visual/batch",
        json={"groups": [[str(dedupe_env["va"]), str(dedupe_env["vb"])]]},
    )
    assert batch.status_code == 200
    assert batch.json()["total_groups"] == 1
    deadline = time.time() + 5
    state = client.get("/api/dedupe/verify-visual/status").json()
    while state["running"] and time.time() < deadline:
        time.sleep(0.01)
        state = client.get("/api/dedupe/verify-visual/status").json()
    assert state["status"] == "done"
    assert state["completed_groups"] == 1

    reset = client.post("/api/dedupe/reset").json()
    assert reset["ignored_cleared"] == 1
    assert client.get("/api/dedupe/ignored").json()["total"] == 0
