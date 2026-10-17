"""Comprehensive integration and unit tests for backup watcher, backup router, library admin, and thumbnail worker."""

import asyncio
import zipfile
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from media_library.core.state import state
from media_library.db.connection import db_conn
from media_library.services.backup_service import library_backup_state
from media_library.services.zip import index_zip_archive
from media_library.workers.backup_watcher import (
    run_library_backup_async,
    run_library_restore_async,
)
from media_library.workers.thumbnail_worker import (
    ThumbnailWorker,
)


def test_backup_router_and_watcher_tasks(api_client, tmp_path):
    # 1. Status endpoint
    res_st = api_client.get("/api/backup/library-backup/status")
    assert res_st.status_code == 200
    st_data = res_st.json()
    assert "status" in st_data
    assert "progress_percent" in st_data

    # 2. Save endpoint
    res_save = api_client.post("/api/backup/library-backup/save")
    assert res_save.status_code == 200
    assert res_save.json()["success"] is True

    # 409 conflict when status is already backing_up
    library_backup_state["status"] = "backing_up"
    res_save_conflict = api_client.post("/api/backup/library-backup/save")
    assert res_save_conflict.status_code == 409

    # 3. Restore endpoint
    res_restore_conflict = api_client.post("/api/backup/library-backup/restore")
    assert res_restore_conflict.status_code == 409

    library_backup_state["status"] = "idle"
    res_restore = api_client.post("/api/backup/library-backup/restore")
    assert res_restore.status_code == 200
    assert res_restore.json()["success"] is True


def test_run_library_backup_and_restore_async():
    async def _test():
        # Test backup async success
        with patch(
            "media_library.workers.backup_watcher._create_library_backup_sync",
            return_value={"success": True},
        ):
            await run_library_backup_async()
            assert library_backup_state["status"] == "idle"

        # Test backup async error
        with patch(
            "media_library.workers.backup_watcher._create_library_backup_sync",
            return_value={"success": False, "error": "Disk full"},
        ):
            await run_library_backup_async()
            assert library_backup_state["status"] == "error"
            assert library_backup_state["error_message"] == "Disk full"

        # Test restore async success
        with patch(
            "media_library.workers.backup_watcher._restore_library_backup_sync",
            return_value={"success": True},
        ):
            await run_library_restore_async()
            assert library_backup_state["status"] == "idle"

        # Test restore async error
        with patch(
            "media_library.workers.backup_watcher._restore_library_backup_sync",
            return_value={"success": False, "error": "Corrupted tar"},
        ):
            await run_library_restore_async()
            assert library_backup_state["status"] == "error"
            assert library_backup_state["error_message"] == "Corrupted tar"

    asyncio.run(_test())


def test_library_admin_rename_endpoints(api_client, tmp_path):
    dest = Path(state["destination_dir"])
    doc = dest / "test_doc.mp4"
    doc.write_bytes(b"\x00" * 128)

    with db_conn() as conn, conn:
        conn.execute(
            """INSERT INTO files (path, name, folder_path, type, ext, size, size_formatted, mtime)
               VALUES (?, ?, ?, 'video', '.mp4', 128, '128 B', 100.0)""",
            (str(doc), "test_doc.mp4", str(dest)),
        )

    # 1. Invalid names -> 400
    res_bad1 = api_client.post(
        "/api/backup/rename", json={"path": str(doc), "new_name": ".hidden"}
    )
    assert res_bad1.status_code == 400

    res_bad2 = api_client.post(
        "/api/backup/rename", json={"path": str(doc), "new_name": "sub/name.mp4"}
    )
    assert res_bad2.status_code == 400

    # 2. Missing file -> 404
    res_404 = api_client.post(
        "/api/backup/rename",
        json={"path": str(dest / "ghost.mp4"), "new_name": "new.mp4"},
    )
    assert res_404.status_code == 404

    # 3. Same name -> 200
    res_same = api_client.post(
        "/api/backup/rename", json={"path": str(doc), "new_name": "test_doc.mp4"}
    )
    assert res_same.status_code == 200

    # 4. Target already exists -> 400
    existing = dest / "target_exists.mp4"
    existing.write_bytes(b"\x00" * 64)
    res_col = api_client.post(
        "/api/backup/rename", json={"path": str(doc), "new_name": "target_exists.mp4"}
    )
    assert res_col.status_code == 400

    # 5. Successful rename
    res_ok = api_client.post(
        "/api/backup/rename", json={"path": str(doc), "new_name": "renamed_doc.mp4"}
    )
    assert res_ok.status_code == 200
    assert (dest / "renamed_doc.mp4").exists()
    assert not doc.exists()


def test_library_admin_delete_endpoints(api_client, tmp_path):
    dest = Path(state["destination_dir"])
    del_file = dest / "to_delete.mp4"
    del_file.write_bytes(b"\x00" * 256)

    with db_conn() as conn, conn:
        conn.execute(
            """INSERT INTO files (path, name, folder_path, type, ext, size, size_formatted, mtime)
               VALUES (?, ?, ?, 'video', '.mp4', 256, '256 B', 100.0)""",
            (str(del_file), "to_delete.mp4", str(dest)),
        )

    # 1. Missing file -> 404
    res_404 = api_client.post(
        "/api/backup/delete", json={"path": str(dest / "ghost.mp4")}
    )
    assert res_404.status_code == 404

    # 2. Directory passed -> 400
    sub_dir = dest / "subfolder_dir"
    sub_dir.mkdir(exist_ok=True)
    res_dir = api_client.post("/api/backup/delete", json={"path": str(sub_dir)})
    assert res_dir.status_code == 400

    # 3. Successful file deletion
    res_del = api_client.post("/api/backup/delete", json={"path": str(del_file)})
    assert res_del.status_code == 200
    assert not del_file.exists()

    # 4. Entire ZIP archive deletion
    zip_path = dest / "archive_to_del.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("a.txt", "content")
    index_zip_archive(str(zip_path))

    res_del_zip = api_client.post("/api/backup/delete", json={"path": str(zip_path)})
    assert res_del_zip.status_code == 200
    assert not zip_path.exists()


def test_library_admin_thumbnail_controls_and_set_poster(api_client, tmp_path):
    dest = Path(state["destination_dir"])

    # 1. Thumbnail status
    res_st = api_client.get("/api/backup/thumbnails/status")
    assert res_st.status_code == 200
    assert "status" in res_st.json()

    # 2. Thumbnail pause & resume toggle
    res_pause = api_client.post("/api/backup/thumbnails/pause")
    assert res_pause.status_code == 200
    assert res_pause.json()["is_paused"] is True

    res_resume = api_client.post("/api/backup/thumbnails/pause")
    assert res_resume.status_code == 200
    assert res_resume.json()["is_paused"] is False

    # 3. Trigger thumbnail generation
    # With folder_path
    res_gen_folder = api_client.post(
        "/api/backup/thumbnails/generate", json={"folder_path": str(dest)}
    )
    assert res_gen_folder.status_code == 200

    # Without folder_path
    res_gen_all = api_client.post("/api/backup/thumbnails/generate", json={})
    assert res_gen_all.status_code == 200

    # 4. Set poster frame
    # Non-video file -> 400
    txt_file = dest / "doc.txt"
    txt_file.write_text("not video")
    res_txt = api_client.post(
        "/api/backup/thumbnails/set-poster",
        json={"path": str(txt_file), "timestamp": 1.0},
    )
    assert res_txt.status_code == 400

    # Missing video -> 404
    res_miss_vid = api_client.post(
        "/api/backup/thumbnails/set-poster",
        json={"path": str(dest / "ghost.mp4"), "timestamp": 1.0},
    )
    assert res_miss_vid.status_code == 404

    # Valid video poster frame setting
    vid_file = dest / "poster_test.mp4"
    vid_file.write_bytes(b"\x00\x00\x00 ftypmp42" + b"X" * 1024)

    with db_conn() as conn, conn:
        conn.execute(
            """INSERT INTO files (path, name, folder_path, type, ext, size, size_formatted, mtime, has_thumb)
               VALUES (?, ?, ?, 'video', '.mp4', 1024, '1 KB', 100.0, 0)""",
            (str(vid_file), "poster_test.mp4", str(dest)),
        )

    poster_img = tmp_path / "poster_frame.jpg"
    Image.new("RGB", (100, 100), color="green").save(poster_img, format="JPEG")

    with patch(
        "media_library.routers.library_admin.extract_frame_poster",
        return_value=poster_img,
    ):
        res_poster = api_client.post(
            "/api/backup/thumbnails/set-poster",
            json={"path": str(vid_file), "timestamp": 5.0},
        )
        assert res_poster.status_code == 200
        p_data = res_poster.json()
        assert p_data["success"] is True
        assert "v=" in p_data["thumb_url"]


def test_thumbnail_worker_operations(sandbox_env):
    media = sandbox_env["media_dir"]
    sub = media / "thumb_worker_test"
    sub.mkdir()
    f1 = sub / "video.mp4"
    f1.write_bytes(b"\x00" * 100)

    with db_conn() as conn, conn:
        conn.execute(
            """INSERT INTO files (path, name, folder_path, type, ext, size, size_formatted, mtime, has_thumb)
               VALUES (?, ?, ?, 'video', '.mp4', 100, '100 B', 100.0, 0)""",
            (str(f1), "video.mp4", str(sub)),
        )

    worker = ThumbnailWorker()
    worker.enqueue_folder(str(sub))
    assert worker.priority_queue.qsize() == 1

    worker._refresh_stats()
    st = worker.get_status()
    assert st["total_files"] >= 1
    assert st["pending_count"] >= 1
