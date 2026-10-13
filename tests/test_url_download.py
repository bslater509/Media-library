"""Tests for the URL/playlist downloader (yt-dlp backed).

The yt-dlp subprocess is mocked (no network): execution tests replace
``subprocess.Popen`` with a fake process that emits progress-template JSON
lines, and pre-seed the per-job staging directory with downloaded files whose
finalization into the library is then asserted.
"""

import json
from pathlib import Path

import pytest

from media_library.workers import url_download_worker
from media_library.workers.url_download_worker import (
    _apply_gallerydl_line,
    _apply_progress,
    _build_gallerydl_command,
    _finalize_downloaded_file,
    _run_ytdlp_job,
    url_download_worker_instance,
)


@pytest.fixture(autouse=True)
def clean_worker_state():
    url_download_worker_instance.jobs_map.clear()
    url_download_worker_instance.active_jobs.clear()
    url_download_worker_instance.history.clear()
    yield
    url_download_worker_instance.jobs_map.clear()
    url_download_worker_instance.active_jobs.clear()
    url_download_worker_instance.history.clear()


WORKER = "media_library.workers.url_download_worker"


@pytest.fixture(autouse=True)
def default_downloaders(monkeypatch):
    """Pin downloader availability.

    The tests replace ``subprocess.Popen`` with a fake, but availability is
    probed via ``subprocess.run`` (which uses ``Popen`` internally), so a real
    probe would spuriously report the downloaders missing. Individual tests
    override these as needed.
    """
    monkeypatch.setattr(f"{WORKER}.is_ytdlp_available", lambda: True)
    monkeypatch.setattr(f"{WORKER}.is_gallery_dl_available", lambda: True)


class FakeProc:
    """Stand-in for subprocess.Popen: iterable stdout + returncode."""

    def __init__(self, lines, returncode=0):
        self.lines = list(lines)
        self.stdout = iter(self.lines)
        self.returncode = returncode
        self.killed = False

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.killed = True
        self.returncode = 1


def _staging():
    """Runtime lookup so conftest's monkeypatched UPLOAD_STAGING_DIR wins."""
    return url_download_worker.UPLOAD_STAGING_DIR


def _progress_line(**overrides):
    data = {
        "type": "progress",
        "title": "Episode One",
        "filename": "Episode One [abc123].mp4",
        "percent": "75.0",
        "downloaded_bytes": "750",
        "total_bytes": "1000",
        "speed": "1.50MiB/s",
        "eta": "00:00:02",
        "playlist_index": "1",
        "playlist_count": "2",
    }
    data.update(overrides)
    return json.dumps(data)


def test_apply_progress_parses_strings_and_na():
    item = {"percent": 0.0, "downloaded_bytes": 0, "total_bytes": 0, "speed": "", "eta": ""}
    _apply_progress(item, json.loads(_progress_line()))
    assert item["percent"] == 75.0
    assert item["downloaded_bytes"] == 750
    assert item["speed"] == "1.50MiB/s"
    assert item["playlist_index"] == 1
    assert item["playlist_count"] == 2
    # "NA" markers must not break parsing
    _apply_progress(item, json.loads(_progress_line(percent="NA", eta="NA")))
    assert item["percent"] == 75.0  # unchanged when NA


def test_finalize_downloaded_file_filters_sidecars(sandbox_env):
    target = sandbox_env["media_dir"] / "dest"
    target.mkdir()
    vid = _staging() / "src_video.mp4"
    vid.write_bytes(b"\x00\x00\x00 ftypmp42" + b"X" * 256)
    dest = _finalize_downloaded_file(vid, target, "dl_test")
    assert dest and (target / "src_video.mp4").exists()

    sidecar = _staging() / "subs.vtt"
    sidecar.write_text("WEBVTT")
    assert _finalize_downloaded_file(sidecar, target, "dl_test") is None
    assert not (target / "subs.vtt").exists()

    meta = _staging() / "meta.info.json"
    meta.write_text("{}")
    # .json matches SKIPPED_EXTENSIONS via suffix lookup below; assert drop
    assert _finalize_downloaded_file(meta, target, "dl_test") is None


def test_finalize_downloaded_file_auto_renames(sandbox_env):
    target = sandbox_env["media_dir"] / "dest"
    target.mkdir()
    (target / "clip.mp4").write_bytes(b"existing")
    src = _staging() / "clip.mp4"
    src.write_bytes(b"\x00\x00\x00 ftypmp42" + b"Y" * 128)
    dest = _finalize_downloaded_file(src, target, "dl_test")
    assert dest is not None and str(dest).endswith("clip (1).mp4")
    assert (target / "clip (1).mp4").exists()


def test_run_ytdlp_job_completes_and_indexes(sandbox_env, monkeypatch):
    media_dir = sandbox_env["media_dir"]
    dest = media_dir / "downloads"
    dest.mkdir()

    job_id = url_download_worker_instance.enqueue(
        "https://example.com/playlist?list=abc", str(dest)
    )
    item = url_download_worker_instance.jobs_map[job_id]

    # Pre-seed the staging dir with what yt-dlp would have downloaded.
    job_dir = _staging() / f"dl_{job_id}"
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "Episode One [abc123].mp4").write_bytes(
        b"\x00\x00\x00 ftypmp42" + b"Z" * 4096
    )
    (job_dir / "Episode One [abc123].en.vtt").write_text("WEBVTT")

    monkeypatch.setattr(
        "subprocess.Popen",
        lambda *a, **k: FakeProc(
            [
                _progress_line(),
                _progress_line(percent="100.0", downloaded_bytes="1000"),
            ],
            returncode=0,
        ),
    )

    _run_ytdlp_job(item)

    assert item["status"] == "completed"
    assert item["imported_count"] == 1
    assert item["percent"] == 100.0
    assert (dest / "Episode One [abc123].mp4").exists()
    assert not (dest / "Episode One [abc123].en.vtt").exists()
    assert not job_dir.exists()  # staging cleaned up

    # Indexed into SQLite
    from media_library.db.connection import db_conn

    with db_conn() as conn:
        row = conn.execute(
            "SELECT path FROM files WHERE folder_path = ?", (str(dest),)
        ).fetchone()
    assert row and row[0].endswith("Episode One [abc123].mp4")


def test_run_ytdlp_job_handles_full_playlist_skip(sandbox_env, monkeypatch):
    """A large playlist completes and counts each imported file once."""
    media_dir = sandbox_env["media_dir"]
    dest = media_dir / "playlist_dest"
    dest.mkdir()
    job_id = url_download_worker_instance.enqueue(
        "https://example.com/playlist?list=xyz", str(dest)
    )
    item = url_download_worker_instance.jobs_map[job_id]
    job_dir = _staging() / f"dl_{job_id}"
    job_dir.mkdir(parents=True, exist_ok=True)
    for i in range(3):
        (job_dir / f"ep{i}.mp4").write_bytes(b"\x00\x00\x00 ftypmp42" + bytes([i]) * 512)

    monkeypatch.setattr(
        "subprocess.Popen",
        lambda *a, **k: FakeProc([_progress_line()], returncode=0),
    )
    _run_ytdlp_job(item)
    assert item["status"] == "completed"
    assert item["imported_count"] == 3
    assert len(list(dest.iterdir())) == 3


def test_run_ytdlp_job_fails_on_nonzero_exit(sandbox_env, monkeypatch):
    dest = sandbox_env["media_dir"] / "dest"
    dest.mkdir()
    job_id = url_download_worker_instance.enqueue("https://example.com/v", str(dest))
    item = url_download_worker_instance.jobs_map[job_id]
    monkeypatch.setattr(
        "subprocess.Popen",
        lambda *a, **k: FakeProc([], returncode=1),
    )
    _run_ytdlp_job(item)
    assert item["status"] == "failed"
    assert "code 1" in item["error"]


def test_run_ytdlp_job_cancelled(sandbox_env, monkeypatch):
    dest = sandbox_env["media_dir"] / "dest"
    dest.mkdir()
    job_id = url_download_worker_instance.enqueue("https://example.com/v", str(dest))
    item = url_download_worker_instance.jobs_map[job_id]

    item["abort_event"].set()  # cancel before processing begins

    monkeypatch.setattr(
        "subprocess.Popen",
        lambda *a, **k: FakeProc([_progress_line()], returncode=0),
    )
    _run_ytdlp_job(item)
    assert item["status"] == "cancelled"


def test_cancel_kills_running_process(sandbox_env, monkeypatch):
    dest = sandbox_env["media_dir"] / "dest"
    dest.mkdir()
    job_id = url_download_worker_instance.enqueue("https://example.com/v", str(dest))
    item = url_download_worker_instance.jobs_map[job_id]

    fake = FakeProc([_progress_line()], returncode=0)
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: fake)

    # Simulate the job thread having started: attach the proc, then cancel.
    item["proc"] = fake
    assert url_download_worker_instance.cancel(job_id) is True
    assert fake.killed is True
    assert item["status"] == "cancelled"

    _run_ytdlp_job(item)
    assert item["status"] == "cancelled"


# ------------------------------------------------------- gallery-dl fallback


def test_build_gallerydl_command_flattens_output():
    cmd = _build_gallerydl_command("https://bunkr.cr/a/x", Path("/tmp/out"))
    assert "-m" in cmd and "gallery_dl" in cmd
    assert cmd[cmd.index("--dest") + 1] == "/tmp/out"
    assert cmd[cmd.index("-o") + 1] == "directory=[]"
    assert cmd[-1] == "https://bunkr.cr/a/x"


def test_apply_gallerydl_line_tracks_completed_files(tmp_path):
    item = {"playlist_index": 0, "current_title": ""}
    _apply_gallerydl_line(item, str(tmp_path / "clip.mp4"), tmp_path)
    assert item["current_title"] == "clip.mp4"
    assert item["playlist_index"] == 1
    # nested path still counts; non-media / unrelated paths are ignored
    nested = tmp_path / "sub" / "deep.webp"
    _apply_gallerydl_line(item, str(nested), tmp_path)
    assert item["playlist_index"] == 2
    _apply_gallerydl_line(item, str(tmp_path / "subs.vtt"), tmp_path)
    _apply_gallerydl_line(item, "/elsewhere/other.mp4", tmp_path)
    assert item["playlist_index"] == 2


def test_run_job_falls_back_to_gallery_dl(sandbox_env, monkeypatch):
    dest = sandbox_env["media_dir"] / "dest"
    dest.mkdir()
    job_id = url_download_worker_instance.enqueue("https://bunkr.cr/a/DaMdNcS9", str(dest))
    item = url_download_worker_instance.jobs_map[job_id]
    job_dir = _staging() / f"dl_{job_id}"

    monkeypatch.setattr(f"{WORKER}.is_ytdlp_available", lambda: True)
    monkeypatch.setattr(f"{WORKER}.is_gallery_dl_available", lambda: True)

    calls = {"n": 0}

    def fake_popen(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            return FakeProc(
                ["ERROR: Unsupported URL: https://bunkr.cr/a/DaMdNcS9"], returncode=1
            )
        job_dir.mkdir(parents=True, exist_ok=True)
        f = job_dir / "clip.mp4"
        f.write_bytes(b"\x00\x00\x00 ftypmp42" + b"Q" * 256)
        return FakeProc([str(f)], returncode=0)

    monkeypatch.setattr("subprocess.Popen", fake_popen)
    _run_ytdlp_job(item)

    assert calls["n"] == 2  # yt-dlp attempt + gallery-dl fallback
    assert item["downloader"] == "gallery-dl"
    assert item["status"] == "completed"
    assert item["imported_count"] == 1
    assert (dest / "clip.mp4").exists()


def test_run_job_uses_gallery_dl_when_ytdlp_missing(sandbox_env, monkeypatch):
    dest = sandbox_env["media_dir"] / "dest"
    dest.mkdir()
    job_id = url_download_worker_instance.enqueue("https://bunkr.cr/a/abc", str(dest))
    item = url_download_worker_instance.jobs_map[job_id]
    job_dir = _staging() / f"dl_{job_id}"

    monkeypatch.setattr(f"{WORKER}.is_ytdlp_available", lambda: False)
    monkeypatch.setattr(f"{WORKER}.is_gallery_dl_available", lambda: True)

    def fake_popen(*a, **k):
        job_dir.mkdir(parents=True, exist_ok=True)
        f = job_dir / "only.webm"
        f.write_bytes(b"\x1a\x45\xdf\xa3" + b"W" * 128)
        return FakeProc([str(f)], returncode=0)

    monkeypatch.setattr("subprocess.Popen", fake_popen)
    _run_ytdlp_job(item)

    assert item["downloader"] == "gallery-dl"
    assert item["status"] == "completed"
    assert item["imported_count"] == 1


def test_run_job_no_fallback_without_gallery_dl(sandbox_env, monkeypatch):
    dest = sandbox_env["media_dir"] / "dest"
    dest.mkdir()
    job_id = url_download_worker_instance.enqueue("https://example.com/v", str(dest))
    item = url_download_worker_instance.jobs_map[job_id]

    monkeypatch.setattr(f"{WORKER}.is_ytdlp_available", lambda: True)
    monkeypatch.setattr(f"{WORKER}.is_gallery_dl_available", lambda: False)
    monkeypatch.setattr(
        "subprocess.Popen",
        lambda *a, **k: FakeProc(["ERROR: Unsupported URL"], returncode=1),
    )
    _run_ytdlp_job(item)

    assert item["status"] == "failed"
    assert "code 1" in item["error"]
    assert item["downloader"] == "yt-dlp"


def test_status_entry_exposes_downloader(sandbox_env):
    dest = sandbox_env["media_dir"] / "dest"
    dest.mkdir()
    job_id = url_download_worker_instance.enqueue("https://example.com/v", str(dest))
    item = url_download_worker_instance.jobs_map[job_id]
    item["downloader"] = "gallery-dl"
    entry = url_download_worker_instance._entry(item)
    assert entry["downloader"] == "gallery-dl"


# ---------------------------------------------------------------- router tests

ROUTER = "media_library.routers.url_download"


def test_enqueue_rejects_bad_inputs(api_client, monkeypatch):
    monkeypatch.setattr(f"{ROUTER}.is_ytdlp_available", lambda: True)
    dest = api_client._transport.app.state  # noqa - app reachable via environment
    # Invalid URL schemes
    for bad in ["ftp://example.com/v.mp4", "notaurl", "javascript:alert(1)"]:
        r = api_client.post("/api/backup/upload/url", json={"url": bad, "destination_dir": "/"})
        assert r.status_code == 400, (bad, r.text)
    # Missing destination
    r = api_client.post(
        "/api/backup/upload/url", json={"url": "https://example.com/v", "destination_dir": ""}
    )
    assert r.status_code == 400


def test_enqueue_rejects_zip_and_missing_dest(api_client, monkeypatch, tmp_path):
    monkeypatch.setattr(f"{ROUTER}.is_ytdlp_available", lambda: True)
    from media_library.core.state import state

    dest_dir = state.get("destination_dir")
    archive_zip = Path(dest_dir) / "archive.zip"
    archive_zip.write_bytes(b"PK\x05\x06" + b"\x00" * 18)

    r = api_client.post(
        "/api/backup/upload/url",
        json={"url": "https://example.com/v", "destination_dir": str(archive_zip)},
    )
    assert r.status_code == 400
    assert "ZIP" in r.json()["detail"]

    r = api_client.post(
        "/api/backup/upload/url",
        json={"url": "https://example.com/v", "destination_dir": str(Path(dest_dir) / "nope")},
    )
    assert r.status_code == 400

    assert dest_dir and Path(dest_dir).is_dir()


def test_enqueue_status_cancel_flow(api_client, monkeypatch):
    monkeypatch.setattr(f"{ROUTER}.is_ytdlp_available", lambda: True)
    from media_library.core.state import state

    r = api_client.post(
        "/api/backup/upload/url",
        json={"url": "https://example.com/playlist?list=1", "destination_dir": state.get("destination_dir")},
    )
    assert r.status_code == 200
    job_id = r.json()["job_id"]
    assert job_id.startswith("dl_")

    st = api_client.get("/api/backup/upload/url/status").json()
    assert st["queued_count"] == 1
    assert st["queued"][0]["id"] == job_id
    assert st["queued"][0]["url"].startswith("https://")

    c = api_client.post("/api/backup/upload/url/cancel", json={"job_id": job_id})
    assert c.status_code == 200
    assert c.json()["status"] == "cancelled"

    c2 = api_client.post("/api/backup/upload/url/cancel", json={"job_id": "dl_unknown"})
    assert c2.status_code == 404

    bad = api_client.post("/api/backup/upload/url/cancel", json={"job_id": "evil"})
    assert bad.status_code == 400


def test_enqueue_503_when_ytdlp_missing(api_client, monkeypatch):
    monkeypatch.setattr(f"{ROUTER}.is_ytdlp_available", lambda: False)
    monkeypatch.setattr(f"{ROUTER}.is_gallery_dl_available", lambda: False)
    from media_library.core.state import state

    r = api_client.post(
        "/api/backup/upload/url",
        json={"url": "https://example.com/v", "destination_dir": state.get("destination_dir")},
    )
    assert r.status_code == 503
    assert "yt-dlp" in r.json()["detail"]


def test_enqueue_allows_gallery_dl_only(api_client, monkeypatch):
    """With yt-dlp absent but gallery-dl present the job is still accepted."""
    monkeypatch.setattr(f"{ROUTER}.is_ytdlp_available", lambda: False)
    monkeypatch.setattr(f"{ROUTER}.is_gallery_dl_available", lambda: True)
    from media_library.core.state import state

    r = api_client.post(
        "/api/backup/upload/url",
        json={"url": "https://bunkr.cr/a/abc123", "destination_dir": state.get("destination_dir")},
    )
    assert r.status_code == 200
    assert r.json()["job_id"].startswith("dl_")


def test_system_status_includes_url_downloads(api_client):
    data = api_client.get("/api/system/status").json()
    assert "url_downloads" in data
    assert data["url_downloads"]["queued_count"] == 0
    assert data["url_downloads"]["active_count"] == 0


# ------------------------------------------------- retry / history / tools


def _finish(worker, job_id, status):
    """Move a queued job into history with a terminal status."""
    item = worker.jobs_map[job_id]
    item["status"] = status
    worker.history.append(item)
    return item


def test_status_payload_exposes_downloader_tools(monkeypatch):
    monkeypatch.setattr(f"{WORKER}.is_ytdlp_available", lambda: True)
    monkeypatch.setattr(f"{WORKER}.is_gallery_dl_available", lambda: False)
    tools = url_download_worker_instance.get_status()["tools"]
    assert tools == {"ytdlp": True, "gallery_dl": False}


def test_retry_clones_failed_and_cancelled_jobs(sandbox_env):
    dest = sandbox_env["media_dir"] / "dest"
    dest.mkdir()
    worker = url_download_worker_instance
    failed_id = worker.enqueue("https://example.com/a", str(dest))
    _finish(worker, failed_id, "failed")

    new_id = worker.retry(failed_id)
    assert new_id and new_id != failed_id and new_id.startswith("dl_")
    clone = worker.jobs_map[new_id]
    assert clone["url"] == "https://example.com/a"
    assert clone["destination_dir"] == str(dest)
    assert clone["status"] == "queued"

    cancelled_id = worker.enqueue("https://example.com/b", str(dest))
    _finish(worker, cancelled_id, "cancelled")
    assert worker.retry(cancelled_id) is not None


def test_retry_ignores_completed_and_unknown_jobs(sandbox_env):
    dest = sandbox_env["media_dir"] / "dest"
    dest.mkdir()
    worker = url_download_worker_instance
    completed_id = worker.enqueue("https://example.com/done", str(dest))
    _finish(worker, completed_id, "completed")

    assert worker.retry(completed_id) is None
    assert worker.retry("dl_missing") is None


def test_clear_history_removes_only_finished_jobs(sandbox_env):
    dest = sandbox_env["media_dir"] / "dest"
    dest.mkdir()
    worker = url_download_worker_instance
    a = worker.enqueue("https://example.com/a", str(dest))
    b = worker.enqueue("https://example.com/b", str(dest))
    c = worker.enqueue("https://example.com/c", str(dest))
    _finish(worker, a, "completed")
    _finish(worker, b, "failed")
    # c stays queued in jobs_map and must survive the clear.
    worker.cancel(c)
    _finish(worker, c, "cancelled")

    cleared = worker.clear_history()
    assert cleared == 3
    assert worker.history == []
    # Queued/active bookkeeping is untouched by the clear.
    assert c in worker.jobs_map


def test_get_history_paginates_newest_first(sandbox_env):
    dest = sandbox_env["media_dir"] / "dest"
    dest.mkdir()
    worker = url_download_worker_instance
    ids = []
    for i in range(5):
        jid = worker.enqueue(f"https://example.com/{i}", str(dest))
        _finish(worker, jid, "completed")
        ids.append(jid)

    page1 = worker.get_history(limit=2, offset=0)
    assert page1["total"] == 5
    assert [e["id"] for e in page1["history"]] == [ids[4], ids[3]]

    page2 = worker.get_history(limit=2, offset=2)
    assert [e["id"] for e in page2["history"]] == [ids[2], ids[1]]

    page3 = worker.get_history(limit=2, offset=4)
    assert [e["id"] for e in page3["history"]] == [ids[0]]

    # limit is clamped into a sane range rather than trusted blindly.
    assert worker.get_history(limit=0, offset=0)["limit"] == 1
    assert worker.get_history(limit=9999, offset=0)["limit"] == 500


def test_retry_endpoint_clones_failed_job(api_client, monkeypatch):
    monkeypatch.setattr(f"{ROUTER}.is_ytdlp_available", lambda: True)
    from media_library.core.state import state

    dest = state.get("destination_dir")
    r = api_client.post(
        "/api/backup/upload/url",
        json={"url": "https://example.com/v", "destination_dir": dest},
    )
    job_id = r.json()["job_id"]
    _finish(url_download_worker_instance, job_id, "failed")

    retry = api_client.post("/api/backup/upload/url/retry", json={"job_id": job_id})
    assert retry.status_code == 200
    body = retry.json()
    assert body["status"] == "queued"
    assert body["retried_from"] == job_id
    assert body["job_id"].startswith("dl_")

    # Bad id -> 400; unknown -> 404; completed -> 409.
    assert api_client.post(
        "/api/backup/upload/url/retry", json={"job_id": "nope"}
    ).status_code == 400
    assert api_client.post(
        "/api/backup/upload/url/retry", json={"job_id": "dl_missing"}
    ).status_code == 404

    done_id = api_client.post(
        "/api/backup/upload/url",
        json={"url": "https://example.com/done", "destination_dir": dest},
    ).json()["job_id"]
    _finish(url_download_worker_instance, done_id, "completed")
    assert api_client.post(
        "/api/backup/upload/url/retry", json={"job_id": done_id}
    ).status_code == 409


def test_history_and_clear_endpoints(api_client, monkeypatch):
    monkeypatch.setattr(f"{ROUTER}.is_ytdlp_available", lambda: True)
    from media_library.core.state import state

    dest = state.get("destination_dir")
    ids = []
    for i in range(3):
        ids.append(api_client.post(
            "/api/backup/upload/url",
            json={"url": f"https://example.com/{i}", "destination_dir": dest},
        ).json()["job_id"])
    for jid in ids:
        _finish(url_download_worker_instance, jid, "completed")

    hist = api_client.get("/api/backup/upload/url/history?limit=2&offset=0")
    assert hist.status_code == 200
    body = hist.json()
    assert body["total"] == 3
    assert [e["id"] for e in body["history"]] == [ids[2], ids[1]]

    assert api_client.get(
        "/api/backup/upload/url/history?limit=0"
    ).status_code == 422  # FastAPI range validation

    cleared = api_client.post("/api/backup/upload/url/clear-history")
    assert cleared.status_code == 200
    assert cleared.json()["cleared"] == 3
    assert api_client.get(
        "/api/backup/upload/url/history"
    ).json()["total"] == 0
