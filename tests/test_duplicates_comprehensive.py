"""Comprehensive integration and unit tests for duplicates router, service, and workers."""

import asyncio
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from media_library.core.state import state
from media_library.db.connection import db_conn
from media_library.services.deduplication_service import (
    autodelete_verified_duplicates,
    compute_quick_hash,
    filter_and_prune_groups,
    get_backup_duplicates,
    invalidate_visual_cache,
    score_duplicate_file,
)
from media_library.workers.duplicate_hash_worker import (
    DuplicateHashWorker,
    ThumbHashWorker,
)


def test_score_duplicate_file_heuristics():
    # Test scoring with date, space, copy indicator, and depth
    s1 = score_duplicate_file(
        "My Vacation 2026-05-01.mp4", "/media/folder/sub", 1000.0, 50 * 1024 * 1024
    )
    s2 = score_duplicate_file(
        "My Vacation (1).mp4", "/media/folder", 1000.0, 10 * 1024 * 1024
    )
    assert s1 > s2

    # Source pattern penalty
    s_source = score_duplicate_file(
        "abcdef1234567890_source.mp4", "/media", 1000.0, 1000
    )
    s_normal = score_duplicate_file("normal.mp4", "/media", 1000.0, 1000)
    assert s_normal > s_source


def test_get_backup_duplicates_content_mode(sandbox_env):
    media = sandbox_env["media_dir"]
    state["destination_dir"] = str(media)

    f1 = media / "clip_a.mp4"
    f2 = media / "clip_b.mp4"
    data = b"\x00\x00\x00\x20ftypmp42" + b"X" * 2000
    f1.write_bytes(data)
    f2.write_bytes(data)

    h1 = compute_quick_hash(str(f1))
    sz = len(data)

    with db_conn() as conn, conn:
        conn.execute(
            """INSERT INTO files (path, name, folder_path, type, ext, size, size_formatted, mtime, quick_hash, has_thumb)
               VALUES (?, ?, ?, 'video', '.mp4', ?, '2 KB', 100.0, ?, 0)""",
            (str(f1), "clip_a.mp4", str(media), sz, h1),
        )
        conn.execute(
            """INSERT INTO files (path, name, folder_path, type, ext, size, size_formatted, mtime, quick_hash, has_thumb)
               VALUES (?, ?, ?, 'video', '.mp4', ?, '2 KB', 200.0, ?, 0)""",
            (str(f2), "clip_b.mp4", str(media), sz, h1),
        )

    res = get_backup_duplicates(mode="content", limit=10, offset=0)
    assert res["success"] is True
    assert res["mode"] == "content"
    assert res["total_groups"] >= 1
    assert res["reclaimable_bytes"] == sz
    assert len(res["groups"]) >= 1

    group = res["groups"][0]
    assert group["files_count"] == 2
    assert "suggested_keep_index" in group
    assert group["files"][group["suggested_keep_index"]]["is_recommended"] is True


def test_get_backup_duplicates_visual_mode(sandbox_env):
    media = sandbox_env["media_dir"]
    state["destination_dir"] = str(media)
    invalidate_visual_cache()

    f1 = media / "v1.mp4"
    f2 = media / "v2.mp4"
    f1.write_bytes(b"V1" * 500)
    f2.write_bytes(b"V2" * 500)

    # 16-hex char hash with hamming distance of 1 (0000000000000010 vs 0000000000000011)
    hash1 = "0000000000000010"
    hash2 = "0000000000000011"

    with db_conn() as conn, conn:
        conn.execute(
            """INSERT INTO files (path, name, folder_path, type, ext, size, size_formatted, mtime, thumb_hash, has_thumb)
               VALUES (?, ?, ?, 'video', '.mp4', 1000, '1 KB', 100.0, ?, 1)""",
            (str(f1), "v1.mp4", str(media), hash1),
        )
        conn.execute(
            """INSERT INTO files (path, name, folder_path, type, ext, size, size_formatted, mtime, thumb_hash, has_thumb)
               VALUES (?, ?, ?, 'video', '.mp4', 1000, '1 KB', 200.0, ?, 1)""",
            (str(f2), "v2.mp4", str(media), hash2),
        )

    res = get_backup_duplicates(mode="visual", limit=10, offset=0)
    assert res["success"] is True
    assert res["mode"] == "visual"
    assert res["total_groups"] == 1
    group = res["groups"][0]
    assert group["similarity"] >= 95.0
    assert group["min_distance"] == 1
    assert group["max_distance"] == 1
    assert group["files_count"] == 2

    # Cached hit within 30s
    res_cached = get_backup_duplicates(mode="visual", limit=10, offset=0)
    assert res_cached["total_groups"] == 1


def test_autodelete_verified_duplicates(sandbox_env):
    media = sandbox_env["media_dir"]
    state["destination_dir"] = str(media)

    f1 = media / "clean_copy.mp4"
    f2 = media / "clean_copy (1).mp4"
    f3 = media / "different_content.mp4"
    content = b"ORIGINAL BYTES" * 200
    f1.write_bytes(content)
    f2.write_bytes(content)
    f3.write_bytes(b"DIFFERENT CONTENT" * 200)

    h = compute_quick_hash(str(f1))
    h_fake = h  # Same quick hash mock for testing identical content guard

    with db_conn() as conn, conn:
        conn.execute(
            """INSERT INTO files (path, name, folder_path, type, ext, size, size_formatted, mtime, quick_hash, has_thumb)
               VALUES (?, ?, ?, 'video', '.mp4', ?, '2 KB', 100.0, ?, 0)""",
            (str(f1), "clean_copy.mp4", str(media), len(content), h),
        )
        conn.execute(
            """INSERT INTO files (path, name, folder_path, type, ext, size, size_formatted, mtime, quick_hash, has_thumb)
               VALUES (?, ?, ?, 'video', '.mp4', ?, '2 KB', 90.0, ?, 0)""",
            (str(f2), "clean_copy (1).mp4", str(media), len(content), h),
        )
        conn.execute(
            """INSERT INTO files (path, name, folder_path, type, ext, size, size_formatted, mtime, quick_hash, has_thumb)
               VALUES (?, ?, ?, 'video', '.mp4', ?, '2 KB', 80.0, ?, 0)""",
            (str(f3), "different_content.mp4", str(media), len(content), h_fake),
        )

    res = autodelete_verified_duplicates()
    assert res["success"] is True
    assert res["resolved_groups"] == 1
    assert res["deleted_count"] == 1
    assert str(f2) in res["deleted_paths"]
    assert f1.exists()
    assert not f2.exists()
    # f3 must not be deleted because full content comparison failed
    assert f3.exists()


def test_filter_and_prune_groups(sandbox_env):
    media = sandbox_env["media_dir"]
    exists1 = media / "e1.mp4"
    exists2 = media / "e2.mp4"
    exists1.write_bytes(b"A" * 10)
    exists2.write_bytes(b"B" * 10)

    groups = [
        {
            "size": 10,
            "files": [
                {"path": str(exists1), "score": 10},
                {"path": str(exists2), "score": 20},
                {"path": str(media / "ghost.mp4"), "score": 5},
            ],
        },
        {
            "size": 10,
            "files": [
                {"path": str(media / "ghost1.mp4"), "score": 10},
                {"path": str(media / "ghost2.mp4"), "score": 5},
            ],
        },
    ]

    pruned = filter_and_prune_groups(groups)
    assert len(pruned) == 1
    assert pruned[0]["files_count"] == 2
    assert pruned[0]["suggested_keep_index"] == 1


def test_duplicates_api_routes(api_client, tmp_path):
    dest = Path(state["destination_dir"])
    f1 = dest / "dup1.mp4"
    f2 = dest / "dup2.mp4"
    data = b"DUP DATA 123456" * 50
    f1.write_bytes(data)
    f2.write_bytes(data)

    qhash = compute_quick_hash(str(f1))
    with db_conn() as conn, conn:
        conn.execute(
            """INSERT INTO files (path, name, folder_path, type, ext, size, size_formatted, mtime, quick_hash, has_thumb)
               VALUES (?, ?, ?, 'video', '.mp4', ?, '1 KB', 100.0, ?, 0)""",
            (str(f1), "dup1.mp4", str(dest), len(data), qhash),
        )
        conn.execute(
            """INSERT INTO files (path, name, folder_path, type, ext, size, size_formatted, mtime, quick_hash, has_thumb)
               VALUES (?, ?, ?, 'video', '.mp4', ?, '1 KB', 90.0, ?, 0)""",
            (str(f2), "dup2.mp4", str(dest), len(data), qhash),
        )

    # 1. GET /api/backup/duplicates
    res = api_client.get("/api/backup/duplicates?mode=content")
    assert res.status_code == 200
    assert res.json()["total_groups"] >= 1

    # 2. Worker controls
    res_st = api_client.get("/api/backup/duplicates/hash-status")
    assert res_st.status_code == 200
    assert "status" in res_st.json()

    res_pause = api_client.post("/api/backup/duplicates/hash-pause")
    assert res_pause.status_code == 200
    assert res_pause.json()["success"] is True

    res_start = api_client.post("/api/backup/duplicates/hash-start")
    assert res_start.status_code == 200
    assert res_start.json()["is_paused"] is False

    res_th = api_client.get("/api/backup/duplicates/thumb-hash-status")
    assert res_th.status_code == 200

    # 3. Ignore duplicate pair
    res_bad_ignore = api_client.post(
        "/api/backup/duplicates/ignore", json={"paths": [str(f1)]}
    )
    assert res_bad_ignore.status_code == 400

    res_ignore = api_client.post(
        "/api/backup/duplicates/ignore",
        json={"paths": [str(f1), str(f2)], "match_type": "content"},
    )
    assert res_ignore.status_code == 200
    assert res_ignore.json()["success"] is True

    # 4. Ignored duplicates list
    res_ignored = api_client.get("/api/backup/duplicates/ignored")
    assert res_ignored.status_code == 200
    ign_data = res_ignored.json()
    assert ign_data["total"] >= 1
    assert len(ign_data["items"]) >= 1
    item_id = ign_data["items"][0]["id"]

    # limit <= 0 returns empty items
    res_zero = api_client.get("/api/backup/duplicates/ignored?limit=0")
    assert res_zero.status_code == 200
    assert res_zero.json()["items"] == []

    # 5. Unignore
    res_unignore_bad = api_client.post("/api/backup/duplicates/unignore", json={})
    assert res_unignore_bad.status_code == 400

    res_unignore_id = api_client.post(
        "/api/backup/duplicates/unignore", json={"id": item_id}
    )
    assert res_unignore_id.status_code == 200
    assert res_unignore_id.json()["success"] is True

    # Unignore by paths
    api_client.post(
        "/api/backup/duplicates/ignore",
        json={"paths": [str(f1), str(f2)], "match_type": "content"},
    )
    res_unignore_paths = api_client.post(
        "/api/backup/duplicates/unignore", json={"paths": [str(f1), str(f2)]}
    )
    assert res_unignore_paths.status_code == 200

    # 6. Verify duplicate group
    res_verify = api_client.post(
        "/api/backup/duplicates/verify-group",
        json={"paths": [str(f1), str(f2)]},
    )
    assert res_verify.status_code == 200
    v_data = res_verify.json()
    assert v_data["all_match"] is True

    # 7. Resolve duplicate group
    res_resolve = api_client.post(
        "/api/backup/duplicates/resolve",
        json={"keep_path": str(f1), "delete_paths": [str(f2)]},
    )
    assert res_resolve.status_code == 200
    assert res_resolve.json()["success"] is True
    assert f1.exists()
    assert not f2.exists()

    # Resolve with nonexistent keep_path raises 404
    res_resolve_404 = api_client.post(
        "/api/backup/duplicates/resolve",
        json={"keep_path": str(dest / "ghost.mp4"), "delete_paths": []},
    )
    assert res_resolve_404.status_code == 404

    # 8. Autodelete verified
    res_autodelete = api_client.post("/api/backup/duplicates/autodelete-verified")
    assert res_autodelete.status_code == 200


def test_duplicate_and_thumb_hash_workers():
    d_worker = DuplicateHashWorker()
    assert d_worker.get_status()["status"] == "idle"
    d_worker.pause()
    assert d_worker.is_paused()
    assert d_worker.get_status()["status"] == "paused"
    d_worker.resume()
    assert not d_worker.is_paused()
    assert d_worker.get_status()["status"] == "hashing"

    t_worker = ThumbHashWorker()
    st = t_worker.get_status()
    assert st["status"] == "idle"
    assert "total_thumbnails" in st


def test_duplicate_hash_worker_run_loop(sandbox_env):
    media = sandbox_env["media_dir"]
    f1 = media / "cand1.mp4"
    f2 = media / "cand2.mp4"
    f1.write_bytes(b"DATA" * 50)
    f2.write_bytes(b"DATA" * 50)

    with db_conn() as conn, conn:
        conn.execute(
            "INSERT INTO files (path, name, folder_path, type, ext, size, size_formatted, mtime) VALUES (?, ?, ?, 'video', '.mp4', 200, '200 B', 100.0)",
            (str(f1), "cand1.mp4", str(media)),
        )
        conn.execute(
            "INSERT INTO files (path, name, folder_path, type, ext, size, size_formatted, mtime) VALUES (?, ?, ?, 'video', '.mp4', 200, '200 B', 100.0)",
            (str(f2), "cand2.mp4", str(media)),
        )

    worker = DuplicateHashWorker()
    orig_sleep = asyncio.sleep

    async def fast_sleep(sec):
        if sec > 0.05:
            await orig_sleep(0.01)
        else:
            await orig_sleep(sec)

    async def _run():
        with patch("asyncio.sleep", side_effect=fast_sleep):
            task = asyncio.create_task(worker._run_loop())
            for _ in range(50):
                await orig_sleep(0.05)
                if worker.state["hashed_count"] >= 2:
                    break
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    asyncio.run(_run())
    assert worker.state["hashed_count"] >= 2


def test_thumb_hash_worker_run_loop(sandbox_env):
    from media_library.core.state import get_thumbnail_cache_path

    media = sandbox_env["media_dir"]
    f1 = media / "th_v1.mp4"
    f1.write_bytes(b"DATA" * 50)

    thumb_p = get_thumbnail_cache_path(str(f1), mtime=100.0, size=200)
    thumb_p.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (32, 32), color="purple").save(thumb_p, format="JPEG")

    with db_conn() as conn, conn:
        conn.execute(
            "INSERT INTO files (path, name, folder_path, type, ext, size, size_formatted, mtime, has_thumb) VALUES (?, ?, ?, 'video', '.mp4', 200, '200 B', 100.0, 1)",
            (str(f1), "th_v1.mp4", str(media)),
        )

    worker = ThumbHashWorker()
    orig_sleep = asyncio.sleep

    async def fast_sleep(sec):
        if sec > 0.05:
            await orig_sleep(0.01)
        else:
            await orig_sleep(sec)

    async def _run():
        with patch("asyncio.sleep", side_effect=fast_sleep):
            task = asyncio.create_task(worker._run_loop())
            for _ in range(50):
                await orig_sleep(0.05)
                if worker.state["hashed_count"] >= 1:
                    break
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    asyncio.run(_run())
    assert worker.state["hashed_count"] >= 1
