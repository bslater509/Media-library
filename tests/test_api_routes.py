"""Integration tests for all major API routes and endpoints."""

import os
from media_library.core.state import state


def test_html_views(api_client):
    res_root = api_client.get("/")
    assert res_root.status_code == 200
    assert "text/html" in res_root.headers.get("content-type", "")
    assert "tasksTabBtnActive" in res_root.text
    assert "tasksTabBtnWorkers" in res_root.text
    assert "tasksTabBtnTools" in res_root.text
    assert "activeQueuesEmptyState" in res_root.text
    assert "mediaInfoModal" in res_root.text
    assert 'data-cm="info"' in res_root.text
    assert 'data-cm="gen-animated"' in res_root.text
    assert "toggleAnimatedPreviewsBtn" in res_root.text
    assert "generateFolderAnimatedBtn" in res_root.text
    assert "generateAllAnimatedBtn" in res_root.text
    import re
    assert re.search(r"library\.js\?v=\d+", res_root.text)
    assert "styles.css?v=42" in res_root.text
    assert 'id="mobileHeader"' in res_root.text
    assert 'id="mobileControlsModal"' in res_root.text
    assert 'id="mobileControlsBtn"' in res_root.text
    assert 'id="mobileControlsSheet"' in res_root.text
    assert 'id="mobileBreadcrumbsModal"' in res_root.text
    assert 'id="mainStage"' in res_root.text
    assert 'id="libraryStickyHeader"' in res_root.text
    assert 'id="galleryContentArea"' in res_root.text
    assert 'pb-28 sm:pb-32 md:pb-6' in res_root.text
    assert '/static/dist/styles.css?v=42' in res_root.text
    assert 'id="scrollToTopBtn"' in res_root.text
    assert 'id="sidebarDrawer"' in res_root.text
    assert 'id="sidebarBackdrop"' in res_root.text
    assert 'id="closeSidebarBtn"' in res_root.text
    assert "thumbnailChooserModal" in res_root.text
    assert "sticky top-0 z-50" in res_root.text
    assert "h-[100dvh] overflow-hidden" in res_root.text
    assert "themeCustomizerModal" in res_root.text
    assert "themeModalBtn" in res_root.text

    res_sorter = api_client.get("/sorter")
    assert res_sorter.status_code == 200
    assert "text/html" in res_sorter.headers.get("content-type", "")
    assert '/static/dist/styles.css?v=42' in res_sorter.text
    assert 'tasksTabBtnActive' in res_sorter.text
    assert "tasksTabBtnWorkers" in res_sorter.text
    assert "tasksTabBtnTools" in res_sorter.text
    assert "activeQueuesEmptyState" in res_sorter.text
    assert "app.js?v=20" in res_sorter.text
    assert 'data-tasks-modal-close' in res_sorter.text
    assert 'role="dialog"' in res_sorter.text
    assert 'role="tabpanel"' in res_sorter.text
    assert "styles.css?v=42" in res_sorter.text
    assert "sticky top-0 z-50" in res_sorter.text
    assert "themeCustomizerModal" in res_sorter.text
    assert "themeModalBtn" in res_sorter.text

    res_dl = api_client.get("/downloads")
    assert res_dl.status_code == 200
    assert "text/html" in res_dl.headers.get("content-type", "")
    assert "downloads.js?v=3" in res_dl.text
    assert 'id="dlUrlsInput"' in res_dl.text
    assert 'id="dlPickerModal"' in res_dl.text
    assert 'id="dlBrowseBtn"' in res_dl.text
    assert 'id="dlClearBtn"' in res_dl.text
    assert "themeCustomizerModal" in res_dl.text
    assert 'href="/downloads"' in res_dl.text
    # Both header triggers must carry the shared attribute (no duplicate ids),
    # and a mobile spacer keeps content clear of the fixed top bar.
    assert res_dl.text.count("data-theme-modal-btn") == 2
    assert "themeModalBtnMobile" in res_dl.text
    assert "safe-area-inset-top, 0px))" in res_dl.text

    res_lib = api_client.get("/library", follow_redirects=True)
    assert res_lib.status_code == 200
    assert "tasksTabBtnActive" in res_lib.text
    assert "tasksTabBtnWorkers" in res_lib.text
    assert "tasksTabBtnTools" in res_lib.text
    assert "activeQueuesEmptyState" in res_lib.text
    assert "duplicatesModal" in res_lib.text
    assert 'id="duplicatesModal" class="ml-sheet fixed inset-0 bg-slate-950/85 backdrop-blur-md z-[80] hidden flex flex-col items-center justify-center p-2 sm:p-4 md:p-6 h-[100dvh] max-h-[100dvh]' in res_lib.text
    assert 'id="duplicateCardsContainer" class="flex-1 min-h-0 overflow-y-auto' in res_lib.text
    assert "ignoredDuplicatesModal" in res_lib.text
    assert 'id="ignoredDuplicatesModal" class="ml-sheet fixed inset-0 bg-slate-950/85 backdrop-blur-md z-[80] hidden flex flex-col items-center justify-center p-2 sm:p-4 md:p-6 h-[100dvh] max-h-[100dvh]' in res_lib.text
    assert "styles.css?v=42" in res_lib.text
    assert 'id="libraryStickyHeader"' in res_lib.text
    assert 'id="scrollToTopBtn"' in res_lib.text


def test_config_and_destinations(api_client, tmp_path):
    res = api_client.get("/api/config")
    assert res.status_code == 200
    data = res.json()
    assert "source_dir" in data
    assert "destination_dir" in data

    res_dest = api_client.get("/api/destinations")
    assert res_dest.status_code == 200
    data_dest = res_dest.json()
    assert "folders" in data_dest
    assert "recent" in data_dest

    res_create = api_client.post(
        "/api/destinations/create",
        json={"folder_name": "NewSubfolder"},
    )
    assert res_create.status_code == 200
    assert res_create.json()["success"] is True


def test_scan_and_queue(api_client):
    res_scan = api_client.post(
        "/api/source/scan", json={"source_dir": state["source_dir"]}
    )
    assert res_scan.status_code == 200
    scan_data = res_scan.json()
    assert scan_data["success"] is True
    assert scan_data["count"] >= 1

    res_queue = api_client.get("/api/queue")
    assert res_queue.status_code == 200
    queue_data = res_queue.json()
    assert "videos" in queue_data
    assert len(queue_data["videos"]) >= 1


def test_backup_index_and_search(api_client):
    res_status = api_client.get("/api/backup/index/status")
    assert res_status.status_code == 200
    data = res_status.json()
    assert "status" in data
    assert "total_files" in data

    res_search = api_client.get("/api/backup/search?q=clip")
    assert res_search.status_code == 200
    search_data = res_search.json()
    assert "results" in search_data


def test_browse_root_returns_indexing_flag(api_client):
    """Browsing the root library must return a valid payload (the `indexing`
    flag is defined even when `is_root` is true — regression guard)."""
    res = api_client.get("/api/backup/browse")
    assert res.status_code == 200
    data = res.json()
    assert data["is_root"] is True
    assert data["indexing"] is False
    assert "subfolders" in data
    assert "files" in data


def test_browse_unindexed_folder_is_non_blocking(api_client):
    """Browsing a folder that is not in the index must not scan the filesystem
    synchronously in the request; the server schedules background indexing."""
    import time
    from pathlib import Path

    dest = Path(state["destination_dir"])
    sub = dest / "UnindexedFolder"
    sub.mkdir()
    (sub / "video.mp4").write_bytes(b"\x00" * 128)

    res = api_client.get("/api/backup/browse", params={"path": str(sub)})
    assert res.status_code == 200
    data = res.json()
    assert data["indexing"] is True
    assert data["files"] == []

    # Background indexing should populate the folder shortly.
    last_total = 0
    for _ in range(60):
        time.sleep(0.05)
        res2 = api_client.get("/api/backup/browse", params={"path": str(sub)})
        last_total = res2.json()["total_files"]
        if last_total == 1:
            break
    assert last_total == 1


def test_folder_children_endpoint(api_client):
    """The sidebar tree's children endpoint lists a folder's immediate child
    folders (index-only) with file/subfolder counts and the zip flag."""
    from pathlib import Path

    from media_library.db.indexer import index_folder

    dest = Path(state["destination_dir"])
    cat_a = dest / "catA"
    sub_b = cat_a / "subB"
    sub_b.mkdir(parents=True)
    (cat_a / "video.mp4").write_bytes(b"\x00" * 64)

    index_folder(str(dest), str(dest))
    index_folder(str(cat_a), str(dest))
    index_folder(str(sub_b), str(dest))

    res = api_client.get("/api/backup/folders", params={"path": str(cat_a)})
    assert res.status_code == 200
    data = res.json()
    assert data["indexing"] is False
    assert len(data["subfolders"]) == 1
    child = data["subfolders"][0]
    assert child["name"] == "subB"
    assert child["file_count"] == 0
    assert "subfolder_count" in child
    assert "is_zip" in child

    # Parent folder should expose catA's counts to the tree roots.
    res_root = api_client.get("/api/backup/folders", params={"path": str(dest)})
    assert res_root.status_code == 200
    root_children = res_root.json()["subfolders"]
    names = [f["name"] for f in root_children]
    assert "catA" in names
    cat_a_entry = next(f for f in root_children if f["name"] == "catA")
    assert cat_a_entry["file_count"] == 1
    assert cat_a_entry["subfolder_count"] == 1


def test_browse_zip_auto_enqueues_webp(api_client):
    """Browsing a ZIP auto-enqueues that archive for WebP optimization (on demand)."""
    import io
    import zipfile
    from pathlib import Path

    from PIL import Image

    from media_library.services.zip import index_zip_archive
    from media_library.workers.webp_worker import webp_worker_instance

    dest = Path(state["destination_dir"])
    zip_path = dest / "browse_album.zip"

    img = io.BytesIO()
    Image.new("RGB", (8, 8), color="red").save(img, format="JPEG")
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("one.jpg", img.getvalue())
    index_zip_archive(str(zip_path), parent_path=str(dest))

    # Clear stale queue entries / throttle state for this path from other tests.
    worker = webp_worker_instance
    while not worker.priority_queue.empty():
        try:
            worker.priority_queue.get_nowait()
        except Exception:
            break
    worker._zip_enqueue_recent.pop(str(zip_path), None)

    res = api_client.get("/api/backup/browse", params={"path": str(zip_path)})
    assert res.status_code == 200
    assert res.json()["is_zip"] is True

    items = []
    while not worker.priority_queue.empty():
        items.append(worker.priority_queue.get_nowait())
    zip_items = [
        i for i in items if i.get("is_zip") == 1 and i["path"] == str(zip_path)
    ]
    assert len(zip_items) == 1


def test_browse_exposes_folder_cover_thumbnails(api_client):
    """Subfolder cards expose a cover thumbnail: the alphabetically-first
    direct-child image. Folders with no image fall back to the first video's
    poster; folders with no media get ``thumb_url = None``."""
    from pathlib import Path

    from media_library.db.indexer import index_folder

    dest = Path(state["destination_dir"])

    cover = dest / "coverA"
    cover.mkdir()
    (cover / "b.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"B" * 64)
    (cover / "a.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"A" * 64)

    video_only = dest / "coverVideo"
    video_only.mkdir()
    (video_only / "clip.mp4").write_bytes(b"\x00" * 64)

    empty = dest / "coverEmpty"
    empty.mkdir()
    (empty / "readme.txt").write_bytes(b"not media")

    index_folder(str(dest), str(dest))
    index_folder(str(cover), str(dest))
    index_folder(str(video_only), str(dest))
    index_folder(str(empty), str(dest))

    res = api_client.get("/api/backup/browse", params={"path": str(dest)})
    assert res.status_code == 200
    subs = {s["name"]: s for s in res.json()["subfolders"]}

    assert "a.jpg" in subs["coverA"]["thumb_url"]
    assert subs["coverA"]["thumb_url"].startswith("/api/backup/thumbnail?path=")
    assert subs["coverA"]["has_thumb"] is False

    # No image in the folder -> the video's poster is used as the cover.
    assert "clip.mp4" in subs["coverVideo"]["thumb_url"]
    assert subs["coverVideo"]["has_thumb"] is False

    assert subs["coverEmpty"]["thumb_url"] is None
    assert subs["coverEmpty"]["has_thumb"] is False


def test_browse_zip_folder_cover_is_first_image(api_client):
    """A ZIP virtual folder's cover is its alphabetically-first image member."""
    import io
    import zipfile
    from pathlib import Path

    from PIL import Image

    from media_library.services.zip import index_zip_archive

    dest = Path(state["destination_dir"])
    zip_path = dest / "cover_album.zip"

    img = io.BytesIO()
    Image.new("RGB", (8, 8), color="blue").save(img, format="JPEG")
    data = img.getvalue()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("zebra.jpg", data)
        zf.writestr("apple.jpg", data)
    index_zip_archive(str(zip_path), parent_path=str(dest))

    res = api_client.get("/api/backup/browse", params={"path": str(dest)})
    assert res.status_code == 200
    subs = {s["name"]: s for s in res.json()["subfolders"]}
    entry = subs["cover_album"]
    assert entry["is_zip"] is True
    assert "apple.jpg" in entry["thumb_url"]
    assert "zebra.jpg" not in entry["thumb_url"]


def test_thumbnail_and_webp_status(api_client):
    res_thumb = api_client.get("/api/backup/thumbnails/status")
    assert res_thumb.status_code == 200
    assert "status" in res_thumb.json()

    res_webp = api_client.get("/api/backup/webp/status")
    assert res_webp.status_code == 200
    assert "status" in res_webp.json()

    res_pause = api_client.post("/api/backup/webp/pause")
    assert res_pause.status_code == 200
    assert "is_paused" in res_pause.json()

    res_resume = api_client.post("/api/backup/webp/resume")
    assert res_resume.status_code == 200
    assert res_resume.json()["is_paused"] is False


def test_duplicates_and_library_backup(api_client):
    res_dup_status = api_client.get("/api/backup/duplicates/hash-status")
    assert res_dup_status.status_code == 200
    assert "status" in res_dup_status.json()

    res_dups = api_client.get("/api/backup/duplicates")
    assert res_dups.status_code == 200
    assert "groups" in res_dups.json()

    res_backup_status = api_client.get("/api/backup/library-backup/status")
    assert res_backup_status.status_code == 200
    assert "status" in res_backup_status.json()
    assert "progress_percent" in res_backup_status.json()


def test_transcode_status(api_client):
    res = api_client.get("/api/transcode/status")
    assert res.status_code == 200
    data = res.json()
    assert "paused" in data
    assert "queue_length" in data


def test_background_tasks_modal_button_always_visible(api_client):
    res_root = api_client.get("/")
    assert res_root.status_code == 200
    root_html = res_root.text
    # In Library view (/), button must be present, visible (not hidden by default), and contain task icon
    assert 'id="transferBadgeBtn"' in root_html
    assert (
        '<button id="transferBadgeBtn" title="View background activity" class="px-2'
        in root_html
    )
    assert 'id="transferIcon"' in root_html
    assert 'id="transferDropdown"' in root_html
    assert 'id="closeTransferDropdownBtn"' in root_html
    # Mobile header has a one-tap tasks button; the Controls sheet no longer
    # carries the redundant Background Tasks "Open" card.
    assert 'id="mobileTasksBtn"' in root_html
    assert 'id="mobileTasksCountBadge"' in root_html
    assert 'id="mobileOpenTasksBtn"' not in root_html

    res_sorter = api_client.get("/sorter")
    assert res_sorter.status_code == 200
    sorter_html = res_sorter.text
    # In Sorter view (/sorter), button must be present, visible (not hidden by default), and contain task icon
    assert 'id="transferBadgeBtn"' in sorter_html
    assert (
        '<button id="transferBadgeBtn" title="View background activity" class="px-2'
        in sorter_html
    )
    assert 'id="transferIcon"' in sorter_html
    assert 'id="transferDropdown"' in sorter_html
    assert 'id="closeTransferDropdownBtn"' in sorter_html
    # Sorter header adopts the unified icon + count-badge tasks button design.
    assert 'id="transferCountText"' in sorter_html
    assert 'rounded-full' in sorter_html


def test_range_byte_streaming_media_and_sorter(api_client):
    """Shared 206 range-streaming helper: full, HEAD, ranged, 416, and clamp."""
    import os

    from media_library.core.state import state

    video = os.path.join(state["destination_dir"], "clipy.mp4")
    payload = b"\x00\x00\x00 ftypmp42" + b"A" * 5000
    with open(video, "wb") as f:
        f.write(payload)

    # Library media: full stream + Accept-Ranges
    r = api_client.get("/api/backup/media", params={"path": video})
    assert r.status_code == 200
    assert r.headers["Accept-Ranges"] == "bytes"
    assert int(r.headers["Content-Length"]) == len(payload)
    assert r.content == payload

    # HEAD
    r = api_client.head("/api/backup/media", params={"path": video})
    assert r.status_code == 200
    assert int(r.headers["Content-Length"]) == len(payload)

    # Ranged
    r = api_client.get(
        "/api/backup/media", params={"path": video}, headers={"Range": "bytes=100-199"}
    )
    assert r.status_code == 206
    assert r.headers["Content-Range"] == f"bytes 100-199/{len(payload)}"
    assert r.content == payload[100:200]

    # Open-ended range clamps to EOF
    r = api_client.get(
        "/api/backup/media", params={"path": video}, headers={"Range": "bytes=0-"}
    )
    assert r.status_code == 206
    assert r.headers["Content-Range"] == f"bytes 0-{len(payload) - 1}/{len(payload)}"
    assert r.content == payload

    # Out-of-range start -> 416
    r = api_client.get(
        "/api/backup/media",
        params={"path": video},
        headers={"Range": f"bytes={len(payload) + 10}-"},
    )
    assert r.status_code == 416
    assert r.headers["Content-Range"] == f"bytes */{len(payload)}"

    # Oversized contiguous range clamped to the 2 MB limit
    big = os.path.join(state["destination_dir"], "big.mp4")
    big_payload = b"\x00" * (3 * 1024 * 1024)
    with open(big, "wb") as f:
        f.write(big_payload)
    r = api_client.get(
        "/api/backup/media", params={"path": big}, headers={"Range": "bytes=0-"}
    )
    assert r.status_code == 206
    assert int(r.headers["Content-Length"]) == 2 * 1024 * 1024
    assert (
        r.headers["Content-Range"]
        == f"bytes 0-{2 * 1024 * 1024 - 1}/{len(big_payload)}"
    )

    # Sorter incoming-stream endpoint uses the same helper
    r = api_client.get("/api/stream/test_clip.mp4")
    assert r.status_code == 200
    assert r.content == b"\x00" * 1024
    r = api_client.head("/api/stream/test_clip.mp4")
    assert r.status_code == 200
    assert r.headers["Accept-Ranges"] == "bytes"
    r = api_client.get("/api/stream/test_clip.mp4", headers={"Range": "bytes=0-100"})
    assert r.status_code == 206
    assert r.headers["Content-Range"] == "bytes 0-100/1024"
    assert r.content == b"\x00" * 101

    # Suffix range requests final N bytes
    r = api_client.get("/api/stream/test_clip.mp4", headers={"Range": "bytes=-100"})
    assert r.status_code == 206
    assert r.headers["Content-Range"] == "bytes 924-1023/1024"
    assert len(r.content) == 100


def test_set_poster_frame(api_client):
    from media_library.core.state import state

    dest = state["destination_dir"]
    video_path = os.path.join(dest, "sample_clip.mp4")
    with open(video_path, "wb") as f:
        f.write(b"\x00" * 1024)

    # 1. Reject non-video
    img_path = os.path.join(dest, "sample_photo.jpg")
    with open(img_path, "wb") as f:
        f.write(b"\x00" * 1024)
    res_bad = api_client.post(
        "/api/backup/thumbnails/set-poster",
        json={"path": img_path, "timestamp": 5.0},
    )
    assert res_bad.status_code == 400
    assert "Only video files" in res_bad.json()["detail"]

    # 2. Reject non-existent video
    res_404 = api_client.post(
        "/api/backup/thumbnails/set-poster",
        json={"path": os.path.join(dest, "missing.mp4"), "timestamp": 5.0},
    )
    assert res_404.status_code == 404

    # 3. Successful poster frame set using base64 image payload
    # Generate a small 10x10 JPEG in base64
    import io
    import base64
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (10, 10), color="red").save(buf, format="JPEG")
    b64_str = base64.b64encode(buf.getvalue()).decode("ascii")

    res_ok = api_client.post(
        "/api/backup/thumbnails/set-poster",
        json={"path": video_path, "timestamp": 12.5, "image_base64": b64_str},
    )
    assert res_ok.status_code == 200
    data = res_ok.json()
    assert data["success"] is True
    assert data["timestamp"] == 12.5
    assert "thumb_url" in data
    assert "&v=" in data["thumb_url"]

    # Check thumbnail can be retrieved
    res_thumb = api_client.get(data["thumb_url"])
    assert res_thumb.status_code == 200
    assert res_thumb.headers["content-type"] == "image/jpeg"


def test_service_worker_route(api_client, tmp_path, monkeypatch):
    res = api_client.get("/sw.js")
    assert res.status_code == 200
    assert "application/javascript" in res.headers.get("content-type", "")
    assert res.headers.get("service-worker-allowed") == "/"
    assert "media-library-anim-v1" in res.text

    # HEAD request
    res_head = api_client.head("/sw.js")
    assert res_head.status_code == 200
    assert "application/javascript" in res_head.headers.get("content-type", "")

    # 404 when file does not exist
    with monkeypatch.context() as m:
        m.setattr("media_library.routers.views.STATIC_DIR", tmp_path / "empty_dir")
        res_404 = api_client.get("/sw.js")
        assert res_404.status_code == 404

