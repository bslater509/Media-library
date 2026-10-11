"""Tests for ZIP archive compression, extraction cache, virtual folder browsing, and API routes."""

import os
import time
import zipfile
from pathlib import Path

from starlette.testclient import TestClient

from media_library.app_factory import create_app
from media_library.db.connection import get_db
from media_library.db.indexer import index_folder
from media_library.services.zip import (
    cancel_batch_unzip,
    cancel_batch_zip,
    check_folder_compressibility,
    compress_folder_to_zip,
    extract_zip_to_cache,
    find_batch_unzip_candidate_zips,
    find_batch_zip_candidate_folders,
    get_batch_unzip_status,
    get_batch_zip_status,
    get_zip_cache_dir,
    get_zip_metadata,
    is_zip_extracted,
    pause_batch_unzip,
    pause_batch_zip,
    resolve_zip_file_path,
    resume_batch_unzip,
    resume_batch_zip,
    split_zip_path,
    start_batch_unzip,
    start_batch_zip,
    unpack_zip_to_folder,
    zip_tasks_state,
)


def test_check_folder_compressibility(sandbox_env):
    media_dir = sandbox_env["media_dir"]

    # 1. Folder with videos and images -> not eligible
    mixed_dir = media_dir / "mixed_folder"
    mixed_dir.mkdir()
    (mixed_dir / "photo1.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"A" * 100)
    (mixed_dir / "video1.mp4").write_bytes(b"\x00\x00\x00 ftyp" + b"B" * 200)

    res_mixed = check_folder_compressibility(str(mixed_dir))
    assert res_mixed["eligible"] is False
    assert "video" in res_mixed["reason"].lower()

    # 2. Folder with only images -> eligible
    img_dir = media_dir / "vacation_photos"
    img_dir.mkdir()
    (img_dir / "beach.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"J" * 500)
    (img_dir / "sunset.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"P" * 600)
    (img_dir / "notes.txt").write_bytes(b"trip notes")

    res_img = check_folder_compressibility(str(img_dir))
    assert res_img["eligible"] is True
    assert res_img["image_count"] == 2
    assert res_img["total_files"] == 3


def test_compress_and_unpack_folder(sandbox_env):
    media_dir = sandbox_env["media_dir"]
    album_dir = media_dir / "album_2024"
    album_dir.mkdir()
    (album_dir / "pic1.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"P1" * 100)
    (album_dir / "pic2.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"P2" * 150)

    # Initial index
    index_folder(str(album_dir))
    conn = get_db()
    try:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM folders WHERE path = ?", (str(album_dir),)
            ).fetchone()[0]
            == 1
        )
    finally:
        conn.close()

    # 1. Compress folder
    comp_res = compress_folder_to_zip(str(album_dir))
    assert comp_res["success"] is True
    zip_path = comp_res["zip_path"]
    assert os.path.exists(zip_path)
    assert not os.path.exists(str(album_dir))  # Original folder was safely removed

    # Verify original folder is removed from SQLite and zip is indexed as virtual folder
    conn = get_db()
    try:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM folders WHERE path = ?", (str(album_dir),)
            ).fetchone()[0]
            == 0
        )
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM files WHERE folder_path = ?", (str(album_dir),)
            ).fetchone()[0]
            == 0
        )
        zip_row = conn.execute(
            "SELECT is_zip, file_count FROM folders WHERE path = ?", (zip_path,)
        ).fetchone()
        assert zip_row is not None
        assert zip_row[0] == 1
        assert zip_row[1] == 2
    finally:
        conn.close()

    # Verify archive integrity
    with zipfile.ZipFile(zip_path, "r") as zf:
        assert zf.testzip() is None
        names = zf.namelist()
        assert "pic1.jpg" in names
        assert "pic2.png" in names

    # 2. Inspect metadata directly from ZIP (<50ms, central directory)
    meta = get_zip_metadata(zip_path)
    assert meta["clean_name"] == "album_2024"
    assert meta["total_files"] == 2
    assert meta["image_count"] == 2
    assert meta["video_count"] == 0

    # 3. Unpack archive back to folder
    unpack_res = unpack_zip_to_folder(zip_path)
    assert unpack_res["success"] is True
    restored_folder = unpack_res["folder_path"]
    assert os.path.isdir(restored_folder)
    assert not os.path.exists(zip_path)  # Zip archive was removed
    assert (Path(restored_folder) / "pic1.jpg").exists()
    assert (Path(restored_folder) / "pic2.png").exists()

    # Verify zip was removed from SQLite and restored folder is present
    conn = get_db()
    try:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM folders WHERE path = ?", (zip_path,)
            ).fetchone()[0]
            == 0
        )
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM folders WHERE path = ?", (restored_folder,)
            ).fetchone()[0]
            == 1
        )
    finally:
        conn.close()


def test_zip_cache_extraction_and_resolution(sandbox_env):
    media_dir = sandbox_env["media_dir"]
    sample_dir = media_dir / "gallery"
    sample_dir.mkdir()
    (sample_dir / "item1.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"X" * 300)

    comp = compress_folder_to_zip(str(sample_dir))
    zip_path = comp["zip_path"]

    # Extract to cache
    cache_dir = extract_zip_to_cache(zip_path)
    assert cache_dir.is_dir()
    assert (cache_dir / ".extracted_done").exists()
    assert is_zip_extracted(zip_path) is True

    # Test path resolution
    virtual_file_path = os.path.join(zip_path, "item1.jpg")
    resolved = resolve_zip_file_path(virtual_file_path)
    assert resolved is not None
    assert os.path.isfile(resolved)
    assert (cache_dir / "item1.jpg").resolve() == Path(resolved).resolve()


def test_split_zip_path():
    zip_p, inner_p = split_zip_path("/media/library/photos.zip")
    assert zip_p == "/media/library/photos.zip"
    assert inner_p == ""

    zip_p2, inner_p2 = split_zip_path("/media/library/photos.zip/sub/pic.jpg")
    assert zip_p2 == "/media/library/photos.zip"
    assert inner_p2 == "sub/pic.jpg"

    zip_none, _inner_none = split_zip_path("/media/library/regular_folder/pic.jpg")
    assert zip_none is None


def test_api_browse_and_zip_operations(sandbox_env):
    media_dir = sandbox_env["media_dir"]
    album = media_dir / "holiday"
    album.mkdir()
    (album / "h1.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"H1" * 200)
    (album / "h2.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"H2" * 300)

    # Initialize app client
    app = create_app(
        source_dir=str(sandbox_env["incoming_dir"]),
        destination_dir=str(media_dir),
        run_startup_workers=False,
    )
    client = TestClient(app)

    # 1. Check eligibility endpoint
    res_chk = client.get(f"/api/backup/zip/check?folder_path={album}")
    assert res_chk.status_code == 200
    assert res_chk.json()["eligible"] is True
    assert res_chk.json()["image_count"] == 2

    # 2. Compress endpoint
    comp_res = client.post("/api/backup/zip/compress", json={"folder_path": str(album)})
    assert comp_res.status_code == 200
    data = comp_res.json()
    assert data["success"] is True

    # Allow background task to finish
    time.sleep(1.0)

    zip_path = str(album) + ".zip"
    assert os.path.exists(zip_path)

    # 3. Browse into the zip file as a virtual folder
    browse_res = client.get(f"/api/backup/browse?path={zip_path}")
    assert browse_res.status_code == 200
    b_data = browse_res.json()
    assert b_data["is_zip"] is True
    assert b_data["total_files"] == 2
    filenames = [f["name"] for f in b_data["files"]]
    assert "h1.jpg" in filenames
    assert "h2.png" in filenames

    # 4. Stream media from inside the zip
    img_vpath = os.path.join(zip_path, "h1.jpg")
    media_res = client.get(f"/api/backup/media?path={img_vpath}")
    assert media_res.status_code == 200

    # 5. Rename file inside the zip
    rename_res = client.post(
        "/api/backup/rename", json={"path": img_vpath, "new_name": "renamed_h1.jpg"}
    )
    assert rename_res.status_code == 200
    assert rename_res.json()["success"] is True

    # 6. Delete file inside the zip
    renamed_vpath = rename_res.json()["new_path"]
    del_res = client.post("/api/backup/delete", json={"path": renamed_vpath})
    assert del_res.status_code == 200
    assert del_res.json()["success"] is True

    # 7. Unpack endpoint
    unpack_res = client.post("/api/backup/zip/unpack", json={"zip_path": zip_path})
    assert unpack_res.status_code == 200
    time.sleep(1.0)
    assert os.path.isdir(str(album))
    assert not os.path.exists(zip_path)


def test_find_batch_zip_candidate_folders(sandbox_env):
    media_dir = sandbox_env["media_dir"]

    # 1. Eligible leaf folder with 3 images
    c1 = media_dir / "leaf_images_a"
    c1.mkdir()
    (c1 / "img1.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"A" * 100)
    (c1 / "img2.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"B" * 100)
    (c1 / "img3.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"C" * 100)
    index_folder(str(c1))

    # 2. Eligible leaf folder with 2 images
    c2 = media_dir / "leaf_images_b"
    c2.mkdir()
    (c2 / "photo1.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"D" * 150)
    (c2 / "photo2.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"E" * 150)
    index_folder(str(c2))

    # 3. Disqualified: contains video
    with_vid = media_dir / "folder_with_video"
    with_vid.mkdir()
    (with_vid / "p1.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"F" * 100)
    (with_vid / "p2.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"G" * 100)
    (with_vid / "clip.mp4").write_bytes(b"\x00\x00\x00 ftyp" + b"H" * 100)
    index_folder(str(with_vid))

    # 4. Disqualified: only 1 image (needs >= 2)
    single_img = media_dir / "single_image_folder"
    single_img.mkdir()
    (single_img / "lone.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"I" * 100)
    index_folder(str(single_img))

    # 5. Disqualified: has child directory (subfolder_count > 0)
    parent_dir = media_dir / "parent_with_child"
    parent_dir.mkdir()
    (parent_dir / "sub").mkdir()
    (parent_dir / "pic1.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"J" * 100)
    (parent_dir / "pic2.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"K" * 100)
    index_folder(str(parent_dir))

    # Query candidate folders
    res = find_batch_zip_candidate_folders()
    candidate_paths = [c["path"] for c in res["candidates"]]

    assert str(c1) in candidate_paths
    assert str(c2) in candidate_paths
    assert str(with_vid) not in candidate_paths
    assert str(single_img) not in candidate_paths
    assert str(parent_dir) not in candidate_paths
    assert res["total_candidates"] == 2
    assert res["total_images"] == 5


def test_batch_zip_execution_and_resilience(sandbox_env):
    media_dir = sandbox_env["media_dir"]

    fold_ok = media_dir / "batch_target_ok"
    fold_ok.mkdir()
    (fold_ok / "a.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"A" * 100)
    (fold_ok / "b.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"B" * 100)
    index_folder(str(fold_ok))

    fake_fold = str(media_dir / "nonexistent_folder_xyz")

    # Start batch zip with 1 good folder and 1 invalid folder
    res = start_batch_zip([str(fold_ok), fake_fold])
    assert res["success"] is True
    assert res["total_folders"] == 2

    # Wait for completion
    for _ in range(50):
        st = get_batch_zip_status()
        if st["status"] in ("completed", "canceled"):
            break
        time.sleep(0.1)

    st = get_batch_zip_status()
    assert st["status"] == "completed"
    assert st["succeeded"] == 1
    assert st["failed"] == 1
    assert len(st["errors"]) == 1
    assert "nonexistent_folder_xyz" in st["errors"][0]["folder"]

    # Verify good folder was converted to zip and directory removed
    expected_zip = str(fold_ok) + ".zip"
    assert os.path.exists(expected_zip)
    assert not os.path.exists(str(fold_ok))


def test_batch_zip_pause_resume_cancel(sandbox_env):
    media_dir = sandbox_env["media_dir"]

    f1 = media_dir / "pause_test_1"
    f1.mkdir()
    (f1 / "p1.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"P1" * 100)
    (f1 / "p2.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"P2" * 100)
    index_folder(str(f1))

    f2 = media_dir / "pause_test_2"
    f2.mkdir()
    (f2 / "p3.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"P3" * 100)
    (f2 / "p4.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"P4" * 100)
    index_folder(str(f2))

    # Pause before start or check controls
    start_res = start_batch_zip([str(f1), str(f2)])
    assert start_res["success"] is True

    # Test pause
    pause_res = pause_batch_zip()
    assert pause_res["success"] is True

    # Test resume
    resume_res = resume_batch_zip()
    assert resume_res["success"] is True

    # Test cancel
    cancel_res = cancel_batch_zip()
    assert cancel_res["success"] is True
    st = get_batch_zip_status()
    assert st["status"] == "canceled"


def test_batch_zip_api_endpoints(sandbox_env):
    media_dir = sandbox_env["media_dir"]
    app = create_app(
        source_dir=str(sandbox_env["incoming_dir"]),
        destination_dir=str(media_dir),
        run_startup_workers=False,
    )
    client = TestClient(app)

    f = media_dir / "api_batch_folder"
    f.mkdir()
    (f / "img1.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"1" * 100)
    (f / "img2.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"2" * 100)
    index_folder(str(f))

    # Candidates endpoint
    cand_res = client.get("/api/backup/zip/batch/candidates")
    assert cand_res.status_code == 200
    c_data = cand_res.json()
    assert c_data["total_candidates"] >= 1

    # Start batch
    start_res = client.post(
        "/api/backup/zip/batch/start", json={"folder_paths": [str(f)]}
    )
    assert start_res.status_code == 200
    assert start_res.json()["success"] is True

    # Status endpoint
    status_res = client.get("/api/backup/zip/batch/status")
    assert status_res.status_code == 200
    assert "status" in status_res.json()

    # Wait for batch to complete to avoid leaking thread past test teardown
    for _ in range(50):
        st = client.get("/api/backup/zip/batch/status").json()
        if st.get("status") in ("completed", "canceled", "failed"):
            break
        time.sleep(0.1)


def _make_zip_archive(media_dir, folder_name):
    """Creates an image-only folder, indexes it, and compresses it to a ZIP archive."""
    folder = media_dir / folder_name
    folder.mkdir()
    (folder / "img1.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"Z" * 100)
    (folder / "img2.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"Y" * 150)
    index_folder(str(folder))
    return compress_folder_to_zip(str(folder))


def test_find_batch_unzip_candidate_zips(sandbox_env):
    media_dir = sandbox_env["media_dir"]

    # Two ZIP archives indexed as virtual folders
    zip_a = _make_zip_archive(media_dir, "archive_alpha")
    zip_b = _make_zip_archive(media_dir, "archive_beta")
    assert zip_a["success"] is True and zip_b["success"] is True

    # A plain folder must NOT be a candidate
    plain = media_dir / "plain_folder"
    plain.mkdir()
    (plain / "p1.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"A" * 100)
    (plain / "p2.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"B" * 100)
    index_folder(str(plain))

    res = find_batch_unzip_candidate_zips()
    candidate_paths = [c["path"] for c in res["candidates"]]

    assert zip_a["zip_path"] in candidate_paths
    assert zip_b["zip_path"] in candidate_paths
    assert str(plain) not in candidate_paths
    assert res["total_candidates"] == 2
    assert res["total_files"] == 4  # 2 files per archive
    for c in res["candidates"]:
        assert c["total_files"] == 2
        assert c["total_size"] > 0
        assert c["total_size_formatted"]


def test_batch_unpack_execution_and_resilience(sandbox_env):
    media_dir = sandbox_env["media_dir"]

    zip_ok = _make_zip_archive(media_dir, "unpack_target_ok")
    zip_path = zip_ok["zip_path"]
    assert os.path.exists(zip_path)

    fake_zip = str(media_dir / "nonexistent_archive_xyz.zip")

    # Start batch unpack with 1 good archive and 1 missing archive
    res = start_batch_unzip([zip_path, fake_zip])
    assert res["success"] is True
    assert res["total_zips"] == 2

    # Wait for completion
    for _ in range(200):
        st = get_batch_unzip_status()
        if st["status"] in ("completed", "canceled"):
            break
        time.sleep(0.1)

    st = get_batch_unzip_status()
    assert st["status"] == "completed"
    assert st["succeeded"] == 1
    assert st["failed"] == 1
    assert len(st["errors"]) == 1
    assert "nonexistent_archive_xyz" in st["errors"][0]["zip"]

    # Verify good archive was extracted to folder and zip removed
    assert not os.path.exists(zip_path)
    restored_folder = media_dir / "unpack_target_ok"
    assert os.path.isdir(restored_folder)
    assert (restored_folder / "img1.jpg").exists()
    assert (restored_folder / "img2.png").exists()


def test_batch_unpack_cancel(sandbox_env):
    media_dir = sandbox_env["media_dir"]

    zip_a = _make_zip_archive(media_dir, "unpack_cancel_1")
    zip_b = _make_zip_archive(media_dir, "unpack_cancel_2")

    start_res = start_batch_unzip([zip_a["zip_path"], zip_b["zip_path"]])
    assert start_res["success"] is True

    # Pause / resume toggling
    pause_res = pause_batch_unzip()
    assert pause_res["success"] is True
    st = get_batch_unzip_status()
    assert st["status"] == "paused"

    resume_res = resume_batch_unzip()
    assert resume_res["success"] is True
    st = get_batch_unzip_status()
    assert st["status"] == "running"

    # Cancel the batch
    cancel_res = cancel_batch_unzip()
    assert cancel_res["success"] is True
    st = get_batch_unzip_status()
    assert st["status"] == "canceled"


def test_batch_unpack_api_endpoints(sandbox_env):
    media_dir = sandbox_env["media_dir"]
    app = create_app(
        source_dir=str(sandbox_env["incoming_dir"]),
        destination_dir=str(media_dir),
        run_startup_workers=False,
    )
    client = TestClient(app)

    zip_info = _make_zip_archive(media_dir, "api_batch_unpack_archive")
    zip_path = zip_info["zip_path"]
    assert os.path.exists(zip_path)

    # Candidates endpoint
    cand_res = client.get("/api/backup/zip/batch-unpack/candidates")
    assert cand_res.status_code == 200
    c_data = cand_res.json()
    assert c_data["total_candidates"] >= 1
    assert zip_path in [c["path"] for c in c_data["candidates"]]

    # Start batch
    start_res = client.post(
        "/api/backup/zip/batch-unpack/start", json={"zip_paths": [zip_path]}
    )
    assert start_res.status_code == 200
    assert start_res.json()["success"] is True

    # Status endpoint
    status_res = client.get("/api/backup/zip/batch-unpack/status")
    assert status_res.status_code == 200
    assert "status" in status_res.json()

    # Wait for batch to complete to avoid leaking thread past test teardown
    for _ in range(200):
        st = client.get("/api/backup/zip/batch-unpack/status").json()
        if st.get("status") in ("completed", "canceled", "failed"):
            break
        time.sleep(0.1)

    st = client.get("/api/backup/zip/batch-unpack/status").json()
    assert st.get("status") == "completed"

    # Archive should now be a folder
    assert not os.path.exists(zip_path)
    assert os.path.isdir(media_dir / "api_batch_unpack_archive")


def test_optimize_zip_images_to_webp(sandbox_env):
    import io

    from PIL import Image

    from media_library.services.zip import (
        index_zip_archive,
        optimize_zip_images_to_webp,
    )

    media_dir = sandbox_env["media_dir"]
    test_zip_path = media_dir / "photos_archive.zip"

    # Create real 10x10 JPEG and PNG images
    img1_io = io.BytesIO()
    Image.new("RGB", (10, 10), color="red").save(img1_io, format="JPEG")
    img1_bytes = img1_io.getvalue()

    img2_io = io.BytesIO()
    Image.new("RGB", (10, 10), color="blue").save(img2_io, format="PNG")
    img2_bytes = img2_io.getvalue()

    with zipfile.ZipFile(test_zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("photo1.jpg", img1_bytes)
        zf.writestr("photo2.png", img2_bytes)
        zf.writestr("notes.txt", b"my trip")

    # Index zip initially
    index_zip_archive(str(test_zip_path))

    # Verify non-webp in index
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT name, ext, webp_optimized FROM files WHERE folder_path = ?",
            (str(test_zip_path),),
        ).fetchall()
        assert len(rows) == 3
        assert any(r["ext"] == ".jpg" and r["webp_optimized"] == 0 for r in rows)
    finally:
        conn.close()

    # Optimize zip images to WebP
    res = optimize_zip_images_to_webp(str(test_zip_path))
    assert res["status"] == "converted"
    assert res["converted_count"] == 2

    # Verify zip members are now .webp
    with zipfile.ZipFile(test_zip_path, "r") as zf:
        assert zf.testzip() is None
        names = zf.namelist()
        assert "photo1.webp" in names
        assert "photo2.webp" in names
        assert "photo1.jpg" not in names
        assert "photo2.png" not in names
        assert "notes.txt" in names

    # Verify SQLite index has .webp
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT name, ext, webp_optimized FROM files WHERE folder_path = ?",
            (str(test_zip_path),),
        ).fetchall()
        assert len(rows) == 3
        webp_rows = [r for r in rows if r["ext"] == ".webp"]
        assert len(webp_rows) == 2
        assert all(r["webp_optimized"] == 1 for r in webp_rows)
    finally:
        conn.close()


def test_optimize_zip_images_to_webp_serializes_concurrent_calls(sandbox_env):
    """Concurrent WebP optimization of the same ZIP must serialize via the per-archive lock."""
    import concurrent.futures
    import io

    from PIL import Image

    from media_library.services.zip import (
        index_zip_archive,
        optimize_zip_images_to_webp,
    )

    media_dir = sandbox_env["media_dir"]
    zip_path = media_dir / "concurrent_webp.zip"

    img1 = io.BytesIO()
    Image.new("RGB", (10, 10), color="green").save(img1, format="JPEG")
    img2 = io.BytesIO()
    Image.new("RGB", (10, 10), color="blue").save(img2, format="JPEG")

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("a.jpg", img1.getvalue())
        zf.writestr("b.jpg", img2.getvalue())

    index_zip_archive(str(zip_path))

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        futures = [
            ex.submit(optimize_zip_images_to_webp, str(zip_path)) for _ in range(2)
        ]
        for f in concurrent.futures.as_completed(futures):
            results.append(f.result())

    statuses = sorted(r["status"] for r in results)
    # One call performs the conversion; the serialized second call observes already_webp.
    assert statuses == ["already_webp", "converted"]
    assert sum(r.get("converted_count", 0) for r in results) == 2

    with zipfile.ZipFile(zip_path, "r") as zf:
        assert zf.testzip() is None
        names = zf.namelist()
        assert "a.webp" in names and "b.webp" in names
        assert "a.jpg" not in names and "b.jpg" not in names


def test_optimize_zip_images_to_webp_uses_cache(sandbox_env):
    """When a ZIP is already extracted to the local cache, optimization should read members from it."""
    import io

    from PIL import Image

    from media_library.services.zip import (
        extract_zip_to_cache,
        index_zip_archive,
        is_zip_extracted,
        optimize_zip_images_to_webp,
    )

    media_dir = sandbox_env["media_dir"]
    zip_path = media_dir / "cached_album.zip"

    img = io.BytesIO()
    Image.new("RGB", (10, 10), color="red").save(img, format="JPEG")
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("p.jpg", img.getvalue())

    index_zip_archive(str(zip_path))
    extract_zip_to_cache(str(zip_path))
    assert is_zip_extracted(str(zip_path))

    res = optimize_zip_images_to_webp(str(zip_path))
    assert res["status"] == "converted"
    assert res["converted_count"] == 1

    with zipfile.ZipFile(zip_path, "r") as zf:
        assert "p.webp" in zf.namelist()


def test_optimize_zip_images_to_webp_skip_larger_marks_member(sandbox_env, monkeypatch):
    """WEBP_SKIP_LARGER should keep the original member and mark it skipped (-1) to avoid retries."""
    import media_library.services.ffmpeg_service as ffmpeg_mod
    import media_library.services.zip as zip_service_mod
    from media_library.services.zip import (
        index_zip_archive,
        optimize_zip_images_to_webp,
    )

    media_dir = sandbox_env["media_dir"]
    zip_path = media_dir / "skip_album.zip"

    original = b"\xff\xd8\xff\xe0" + b"A" * 64
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("photo.jpg", original)

    index_zip_archive(str(zip_path))

    monkeypatch.setattr("media_library.services.zip.optimize.WEBP_SKIP_LARGER", True)
    # Force conversion to produce bytes larger than the original.
    monkeypatch.setattr(
        ffmpeg_mod, "convert_image_to_webp_bytes", lambda src, quality=None: b"W" * 512
    )

    res = optimize_zip_images_to_webp(str(zip_path))
    assert res["status"] == "converted"
    assert res["converted_count"] == 0
    assert res["skipped_count"] == 1

    with zipfile.ZipFile(zip_path, "r") as zf:
        names = zf.namelist()
        assert "photo.jpg" in names
        assert "photo.webp" not in names

    conn = get_db()
    try:
        row = conn.execute(
            "SELECT webp_optimized FROM files WHERE folder_path = ?", (str(zip_path),)
        ).fetchone()
        assert row is not None
        assert row[0] == -1
    finally:
        conn.close()


def test_zip_staging_directory_usage_and_cleanup(sandbox_env):
    """Verifies that ZIP compression stages archive in ZIP_STAGING_DIR and cleans up temp files."""
    from media_library.services.zip import ZIP_STAGING_DIR

    media_dir = sandbox_env["media_dir"]
    staging_test_dir = media_dir / "staging_album"
    staging_test_dir.mkdir()
    (staging_test_dir / "photo1.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"X" * 200)
    (staging_test_dir / "photo2.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"Y" * 300)

    # Ensure staging dir exists
    ZIP_STAGING_DIR.mkdir(parents=True, exist_ok=True)

    res = compress_folder_to_zip(str(staging_test_dir))
    assert res["success"] is True
    zip_path = res["zip_path"]
    assert os.path.exists(zip_path)

    # Check that after completion, any temp staging file created for this album was cleaned up
    remaining_staged = list(ZIP_STAGING_DIR.glob(".tmp_staging_album_*"))
    assert len(remaining_staged) == 0

    # Verify no temp transfer files remain in parent dir
    parent_temp_files = list(
        Path(zip_path).parent.glob(".tmp_transfer_staging_album_*")
    )
    assert len(parent_temp_files) == 0

    # Verify final archive integrity
    with zipfile.ZipFile(zip_path, "r") as zf:
        assert zf.testzip() is None
        assert len(zf.namelist()) == 2


def test_concurrent_zip_extractions(sandbox_env):
    """Verifies that concurrent extraction calls on the same ZIP archive do not race or fail."""
    import concurrent.futures

    media_dir = sandbox_env["media_dir"]
    test_zip = media_dir / "concurrent_album.zip"

    with zipfile.ZipFile(test_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for idx in range(15):
            zf.writestr(f"img_{idx:03d}.jpg", b"\xff\xd8\xff\xe0" + bytes([idx] * 200))

    # Run 10 threads trying to extract at the exact same time
    results = []
    errors = []

    def _worker():
        try:
            return extract_zip_to_cache(str(test_zip))
        except Exception as e:
            return e

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(_worker) for _ in range(10)]
        for f in concurrent.futures.as_completed(futures):
            res = f.result()
            if isinstance(res, Exception):
                errors.append(res)
            else:
                results.append(res)

    assert len(errors) == 0, f"Concurrent extractions encountered errors: {errors}"
    assert len(results) == 10
    first_path = results[0]
    for r in results:
        assert r == first_path
    assert (first_path / ".extracted_done").exists()
    assert (first_path / "img_000.jpg").exists()


def test_zip_with_root_prefix_extraction_and_resolution(sandbox_env):
    """Verifies archives with top-level root prefixes extract and resolve without nesting."""
    media_dir = sandbox_env["media_dir"]
    prefixed_zip = media_dir / "sunset_trip.zip"

    with zipfile.ZipFile(prefixed_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("sunset_trip/p1.jpg", b"\xff\xd8\xff\xe0" + b"P1" * 100)
        zf.writestr("sunset_trip/p2.jpg", b"\xff\xd8\xff\xe0" + b"P2" * 100)
        zf.writestr("sunset_trip/__MACOSX/._p1.jpg", b"meta")

    # 1. Extraction strips common root prefix
    cache_dir = extract_zip_to_cache(str(prefixed_zip))
    assert (cache_dir / ".extracted_done").exists()
    assert (cache_dir / "p1.jpg").exists()
    assert (cache_dir / "p2.jpg").exists()

    # 2. Path resolution
    res_p1 = resolve_zip_file_path(f"{prefixed_zip}/p1.jpg")
    assert res_p1 is not None
    assert os.path.isfile(res_p1)
    assert Path(res_p1).name == "p1.jpg"

    # 3. Unpack does not create double-nested sunset_trip/sunset_trip/p1.jpg
    unpack_info = unpack_zip_to_folder(str(prefixed_zip))
    assert unpack_info["success"] is True
    dest_folder = Path(unpack_info["folder_path"])
    assert (dest_folder / "p1.jpg").exists()
    assert not (dest_folder / "sunset_trip" / "p1.jpg").exists()


def test_resolve_zip_file_path_in_existing_subfolder_cache(sandbox_env):
    """Verifies that resolve_zip_file_path finds files in existing caches with preserved subfolders."""
    media_dir = sandbox_env["media_dir"]
    legacy_zip = media_dir / "legacy_archive.zip"

    with zipfile.ZipFile(legacy_zip, "w") as zf:
        zf.writestr("legacy_archive/photoA.jpg", b"\xff\xd8\xff\xe0" + b"A" * 50)

    # Simulate an existing cache folder created prior with the subfolder preserved
    cache_dir = get_zip_cache_dir(str(legacy_zip))
    sub_dir = cache_dir / "legacy_archive"
    sub_dir.mkdir(parents=True, exist_ok=True)
    (sub_dir / "photoA.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"A" * 50)
    (cache_dir / ".extracted_done").touch()

    # resolve_zip_file_path should resolve via subfolder fallback
    resolved = resolve_zip_file_path(f"{legacy_zip}/photoA.jpg")
    assert resolved is not None
    assert os.path.exists(resolved)
    assert Path(resolved).name == "photoA.jpg"


def test_extract_after_unpack_is_quiet_noop(sandbox_env):
    """A stale background extraction that runs after the archive was unpacked
    must not record a bogus 'Extraction failed' task entry.

    Regression test for the reported symptom: browsing a ZIP auto-starts a cache
    extraction; unpacking it concurrently deletes the archive; the extract step then
    overwrote the successful unpack record with an ENOENT failure.
    """
    media_dir = sandbox_env["media_dir"]
    album = media_dir / "race_album"
    album.mkdir()
    (album / "pic1.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"P1" * 100)
    (album / "pic2.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"P2" * 100)
    index_folder(str(album))

    zip_path = compress_folder_to_zip(str(album))["zip_path"]

    unpack_res = unpack_zip_to_folder(zip_path)
    assert unpack_res["success"] is True
    assert not os.path.exists(zip_path)

    # The queued extraction now runs against an archive that no longer exists.
    result = extract_zip_to_cache(zip_path)
    assert result is None

    task = zip_tasks_state["active_tasks"].get(zip_path)
    assert task is None or task.get("status") != "failed", (
        f"Spurious failure recorded: {task}"
    )


def test_concurrent_extract_and_unpack_no_spurious_failure(sandbox_env):
    """Race a cache extraction against an unpack of the same archive; the unpack must
    win cleanly and no spurious failed task may be recorded."""
    import threading

    media_dir = sandbox_env["media_dir"]
    album = media_dir / "race_album_concurrent"
    album.mkdir()
    for idx in range(15):
        (album / f"img_{idx:03d}.jpg").write_bytes(
            b"\xff\xd8\xff\xe0" + bytes([idx] * 200)
        )
    index_folder(str(album))

    zip_path = compress_folder_to_zip(str(album))["zip_path"]

    barrier = threading.Barrier(2)
    outcome = {}

    def _extract():
        barrier.wait()
        try:
            outcome["extract"] = extract_zip_to_cache(zip_path)
        except Exception as e:  # pragma: no cover - surfaced via assertion
            outcome["extract"] = e

    def _unpack():
        barrier.wait()
        try:
            outcome["unpack"] = unpack_zip_to_folder(zip_path)
        except Exception as e:  # pragma: no cover - surfaced via assertion
            outcome["unpack"] = e

    t1 = threading.Thread(target=_extract)
    t2 = threading.Thread(target=_unpack)
    t1.start()
    t2.start()
    t1.join(timeout=30)
    t2.join(timeout=30)

    assert not isinstance(outcome.get("unpack"), Exception), (
        f"Unpack failed: {outcome.get('unpack')}"
    )
    assert outcome["unpack"]["success"] is True
    assert os.path.isdir(outcome["unpack"]["folder_path"])
    assert not os.path.exists(zip_path)

    task = zip_tasks_state["active_tasks"].get(zip_path)
    assert task is None or task.get("status") != "failed", (
        f"Spurious failure recorded: {task}"
    )


def test_manage_lru_cache_evicts_oldest_when_over_limit(sandbox_env):
    """Verifies that manage_lru_cache evicts the oldest cache entry first when
    the total extracted-ZIP cache size exceeds ZIP_CACHE_MAX_SIZE_GB."""
    import zipfile
    from unittest.mock import patch

    from media_library.services.zip import (
        get_zip_cache_dir,
        manage_lru_cache,
    )

    media_dir = sandbox_env["media_dir"]

    # Create two fake ZIP archives and extract them into the cache manually.
    zip_a = media_dir / "lru_old.zip"
    zip_b = media_dir / "lru_new.zip"

    for zp in (zip_a, zip_b):
        with zipfile.ZipFile(zp, "w") as zf:
            # Write ~200 KB of data into each archive so both together exceed
            # any tiny test limit we set (e.g. 300 KB).
            zf.writestr("data.bin", b"X" * 200_000)

    # Simulate that both ZIPs have already been extracted to the local cache.
    cache_a = get_zip_cache_dir(str(zip_a))
    cache_b = get_zip_cache_dir(str(zip_b))

    for cache_dir, zip_path in ((cache_a, zip_a), (cache_b, zip_b)):
        cache_dir.mkdir(parents=True, exist_ok=True)
        (cache_dir / "data.bin").write_bytes(b"X" * 200_000)
        marker = cache_dir / ".extracted_done"
        marker.touch()

    # Make cache_a appear older by backdating its marker mtime.
    old_ts = time.time() - 3600  # 1 hour ago
    os.utime(cache_a / ".extracted_done", (old_ts, old_ts))

    # Both caches together are ~400 KB; patch the limit to 300 KB so one eviction is needed.
    limit_bytes_as_gb = 300_000 / (1024**3)
    with patch(
        "media_library.services.zip.core.ZIP_CACHE_MAX_SIZE_GB", limit_bytes_as_gb
    ):
        manage_lru_cache()

    # The OLDEST entry (cache_a) must have been evicted.
    assert not cache_a.exists(), "Oldest cache entry should have been evicted"
    # The NEWEST entry (cache_b) must still be present.
    assert cache_b.exists(), "Newest cache entry should have been retained"
    assert (cache_b / "data.bin").exists()


def test_manage_lru_cache_no_eviction_under_limit(sandbox_env):
    """Verifies that manage_lru_cache leaves caches untouched when total size is under the limit."""
    import zipfile

    from media_library.services.zip import get_zip_cache_dir, manage_lru_cache

    media_dir = sandbox_env["media_dir"]
    zip_c = media_dir / "lru_keep.zip"
    with zipfile.ZipFile(zip_c, "w") as zf:
        zf.writestr("file.bin", b"Y" * 1_000)

    cache_c = get_zip_cache_dir(str(zip_c))
    cache_c.mkdir(parents=True, exist_ok=True)
    (cache_c / "file.bin").write_bytes(b"Y" * 1_000)
    (cache_c / ".extracted_done").touch()

    # Default ZIP_CACHE_MAX_SIZE_GB is 5.0 GB; 1 KB is well under the limit.
    manage_lru_cache()

    assert cache_c.exists(), "Cache entry under size limit must not be evicted"
    assert (cache_c / "file.bin").exists()
