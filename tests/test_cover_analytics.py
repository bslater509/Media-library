"""Directed coverage for analytics, verify, runtime and build-info edge paths.

The route-level suites cover the happy paths; these tests drive the private
helpers with awkward index shapes (folders outside the configured root, empty
path keys, ZIP members) and workers' failure branches directly.
"""

from __future__ import annotations

import contextlib
import os
import sqlite3
import threading
import time
from pathlib import Path

import pytest
from PIL import Image

from media_library import analytics, backups, build_info, runtime, verify
from media_library import config as v2_config
from media_library.db import reader
from media_library.writes import WriteError

_SHA = "0123456789abcdef0123456789abcdef01234567"


def _wait_for(predicate, timeout: float = 2.0) -> bool:
    """Poll *predicate* briefly; never a long busy wait."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def _no_build_env(monkeypatch) -> None:
    monkeypatch.delenv("GIT_COMMIT", raising=False)
    monkeypatch.delenv("BUILD_COMMIT", raising=False)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@contextlib.contextmanager
def _db(db_path):
    """A SQLite connection that is actually closed on exit (no GC warnings)."""
    conn = sqlite3.connect(db_path)
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def _insert_folder(conn, path: str, name: str, parent: str | None, is_zip=0):
    conn.execute(
        "INSERT INTO folders (path, name, parent_path, is_zip) VALUES (?,?,?,?)",
        (path, name, parent, is_zip),
    )


def _insert_file(
    conn,
    path: str,
    kind: str,
    ext: str,
    folder: str,
    size: int,
    width=None,
    height=None,
    has_thumb=1,
    duration=None,
    fps=None,
):
    conn.execute(
        "INSERT INTO files (path, name, folder_path, type, ext, size, "
        "size_formatted, mtime, needs_transcode, has_thumb, width, height, "
        "fps, duration) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            path,
            os.path.basename(path),
            folder,
            kind,
            ext,
            size,
            f"{size} B",
            1.0,
            0,
            has_thumb,
            width,
            height,
            fps,
            duration,
        ),
    )


# --- analytics -------------------------------------------------------------


@pytest.fixture
def cover_env(tmp_path, monkeypatch):
    """A library root with a sibling directory outside of it."""
    library = tmp_path / "library"
    library.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.setenv("LIBRARY_DIR", str(library))
    monkeypatch.setenv("THUMBNAIL_DIR", str(tmp_path / "thumbs"))
    v2_config.reload_settings()
    analytics.invalidate()
    try:
        yield {
            "library": library,
            "outside": outside,
            "tmp": tmp_path,
            "db_path": Path(os.environ["DB_PATH"]),
        }
    finally:
        analytics.invalidate()
        v2_config.reload_settings()


def test_format_duration_renders_hours_and_minutes():
    assert analytics._format_duration(3660) == "1h 1m"
    assert analytics._format_duration(125) == "2m 5s"
    assert analytics._format_duration(45) == "45s"
    assert analytics._format_duration(0) == "0s"


def test_derive_root_falls_back_to_the_shallowest_folder(tmp_path):
    assert analytics._derive_root({}) == str(v2_config.get_settings().library_dir)
    meta = {
        str(tmp_path / "a" / "b"): {"name": "b"},
        str(tmp_path / "abc"): {"name": "abc"},
        str(tmp_path / "a"): {"name": "a"},
    }
    assert analytics._derive_root(meta) == str(tmp_path / "a")


def _seed_rollup(cover_env):
    library = str(cover_env["library"])
    outside = str(cover_env["outside"])
    library_out = str(cover_env["library"].parent)
    with _db(cover_env["db_path"]) as conn:
        _insert_folder(conn, library, "library", None)
        _insert_folder(conn, "", "", None)  # an empty path row: skipped
        _insert_folder(conn, f"{library}/A", "A", "nowhere")  # unindexed parent
        _insert_folder(conn, f"{library}/B", "B", library)
        _insert_folder(conn, f"{library}/A/sub", "sub", f"{library}/A")
        _insert_folder(conn, outside, "outside", library_out)  # outside the root
        _insert_file(conn, f"{library}/root.mp4", "video", ".mp4", library, 1500)
        _insert_file(conn, f"{library}/B/b.mp4", "video", ".mp4", f"{library}/B", 800)
        _insert_file(conn, f"{library}/no-folder.png", "image", ".png", "", 5)
        _insert_file(
            conn,
            f"{library}/A/sub/deep.mp4",
            "video",
            ".mp4",
            f"{library}/A/sub",
            300,
        )
        # A file living in an unindexed in-library folder walks up to the root.
        _insert_file(
            conn,
            f"{library}/untracked/loose.mp4",
            "video",
            ".mp4",
            f"{library}/untracked",
            20,
        )
        _insert_file(conn, f"{outside}/far.jpg", "image", ".jpg", outside, 40)


def test_rollup_survives_empty_and_untied_rows(cover_env):
    _seed_rollup(cover_env)
    library = str(cover_env["library"])

    seen = analytics.overview(refresh=True)

    assert seen["folders"]["root"] == library
    assert seen["totals"]["files"] == 6
    assert seen["totals"]["folders"] == 6
    # The untracked subfolder rolled up into A; the outside folder is ignored.
    largest = {entry["path"]: entry for entry in seen["folders"]["largest"]}
    test_library = str(cover_env["library"])
    test_outside = str(cover_env["outside"])
    assert largest[f"{test_library}/A"]["bytes"] == 300
    assert largest[f"{test_library}/B"]["bytes"] == 800
    # The empty-folder file is dropped by design (empty folder key).
    assert seen["folders"]["root_bytes"] == 1500 + 20 + 300 + 800 + 40
    assert f"{test_library}/A/sub" not in largest  # covered by chosen ancestor
    assert test_outside not in largest

    # A cache hit inside the TTL returns the memoised payload.
    assert analytics.overview() is seen


def test_folder_limit_breaks_and_folds_the_remainder(cover_env):
    _seed_rollup(cover_env)
    folders = analytics.overview(folder_limit=1, refresh=True)["folders"]
    assert folders["top"][0]["name"] == "B"
    group = folders["top"][1]
    assert group["is_group"] is True
    assert group["name"] == "Smaller folders (1)"
    assert group["bytes"] == 300


def test_quality_buckets_cover_every_band(cover_env):
    library = str(cover_env["library"])
    with _db(cover_env["db_path"]) as conn:
        _insert_folder(conn, library, "library", None)
        for name, width, height in (
            ("4k.mp4", 3840, 2160),
            ("qhd.mp4", 2560, 1440),
            ("fhd.mp4", 1920, 1080),
            ("hd.mp4", 1280, 720),
            ("sd.mp4", 640, 480),
        ):
            _insert_file(
                conn,
                f"{library}/{name}",
                "video",
                ".mp4",
                library,
                100,
                width=width,
                height=height,
                fps=60 if name == "4k.mp4" else None,
                duration=10.0,
            )
        _insert_file(conn, f"{library}/dark.mp4", "video", ".mp4", library, 100)

    counts = {
        bucket["label"]: bucket["count"]
        for bucket in analytics.overview(refresh=True)["quality"]["buckets"]
    }
    assert counts == {
        "4K": 1,
        "1440p": 1,
        "1080p": 1,
        "720p": 1,
        "SD": 1,
        "unprobed": 1,
    }


# --- verify ----------------------------------------------------------------


@pytest.fixture
def verify_seed(tmp_path, monkeypatch):
    """A tiny index: damaged files, a clean one, and three ZIP members."""
    library = tmp_path / "library"
    library.mkdir()
    monkeypatch.setenv("LIBRARY_DIR", str(library))
    v2_config.reload_settings()
    db_path = Path(os.environ["DB_PATH"])
    Image.new("RGB", (4, 3), (9, 9, 9)).save(library / "corrupt.jpg", "PNG")

    with _db(db_path) as conn:
        _insert_folder(conn, str(library), "library", str(library.parent))
        _insert_folder(
            conn, str(library / "bundle.zip"), "bundle.zip", str(library), is_zip=1
        )
        _insert_folder(
            conn, str(library / "real.zip"), "real.zip", str(library), is_zip=0
        )
        # zero-byte ghost poster failed; corrupt.jpg probes as 0 x 0.
        _insert_file(
            conn,
            str(library / "ghost.mp4"),
            "video",
            ".mp4",
            str(library),
            0,
            has_thumb=-1,
        )
        _insert_file(
            conn,
            str(library / "corrupt.jpg"),
            "image",
            ".jpg",
            str(library),
            10,
            width=0,
            height=0,
        )
        _insert_file(conn, str(library / "ok.png"), "image", ".png", str(library), 5)
        # A virtual archive member (has an is_zip = 1 row): never flagged.
        _insert_file(
            conn,
            f"{library}/bundle.zip/inside.mp4",
            "video",
            ".mp4",
            str(library / "bundle.zip"),
            0,
            has_thumb=-1,
        )
        # A member without a folders row at all: still a virtual member.
        _insert_file(
            conn,
            f"{library}/loose.zip/member.mp4",
            "video",
            ".mp4",
            str(library / "loose.zip"),
            0,
            has_thumb=-1,
        )
        # A member of a real directory that happens to end in .zip: flagged.
        _insert_file(
            conn,
            f"{library}/real.zip/member.mp4",
            "video",
            ".mp4",
            str(library / "real.zip"),
            0,
            has_thumb=-1,
        )
    return {"library": library, "db_path": db_path, "corrupt": library / "corrupt.jpg"}


def _issues(db_path) -> dict[tuple[str, str], int]:
    with _db(db_path) as conn:
        return {
            (row[0], row[1]): row[2]
            for row in conn.execute(
                "SELECT path, issue, SUM(dismissed_at IS NULL) FROM file_issues "
                "GROUP BY path, issue"
            )
        }


def test_derive_issues_skips_non_media_rows():
    assert verify.derive_issues({"type": "document", "size": 0}) == set()


def test_existing_issues_with_no_paths_is_empty():
    with reader() as conn:
        assert verify._existing_issues(conn, []) == {}


def test_pass_flags_every_issue_and_skips_zip_members(verify_seed):
    library = str(verify_seed["library"])
    result = verify.VerifyWorker(batch_pause=0.0).run_pass()

    assert result["scanned"] == 6
    assert set(_issues(verify_seed["db_path"])) == {
        (f"{library}/ghost.mp4", "zero_size"),
        (f"{library}/ghost.mp4", "thumb_failed"),
        (f"{library}/corrupt.jpg", "probe_failed"),
        (f"{library}/real.zip/member.mp4", "zero_size"),
        (f"{library}/real.zip/member.mp4", "thumb_failed"),
    }
    counts = {row["issue"]: row["open"] for row in verify.issue_counts()}
    assert counts == {"zero_size": 2, "probe_failed": 1, "thumb_failed": 2}
    assert verify.totals() == {"open": 5, "dismissed": 0}

    listed = verify.list_issues()
    assert listed["total"] == 5
    assert listed["limit"] == 100
    probes = verify.list_issues(issue="probe_failed")
    assert [row["path"] for row in probes["issues"]] == [f"{library}/corrupt.jpg"]

    # Dismiss one issue, then every issue on the ghost, and list both ways.
    assert verify.dismiss_issues(f"{library}/corrupt.jpg", issue="probe_failed") == {
        "path": f"{library}/corrupt.jpg",
        "dismissed": 1,
        "issues": ["probe_failed"],
    }
    again = verify.dismiss_issues(f"{library}/ghost.mp4")
    assert again["dismissed"] == 2
    assert again["issues"] == ["thumb_failed", "zero_size"]
    assert verify.list_issues()["total"] == 2
    assert verify.list_issues(include_dismissed=True)["total"] == 5
    assert verify.totals() == {"open": 2, "dismissed": 3}


def test_a_later_pass_clears_repaired_rows(verify_seed):
    """Fix the index rows; the second pass deletes the stale issue rows."""
    library = str(verify_seed["library"])
    verify.VerifyWorker(batch_pause=0.0).run_pass()
    with _db(verify_seed["db_path"]) as conn:
        conn.execute(
            "UPDATE files SET size = 10 WHERE path = ?",
            (f"{library}/ghost.mp4",),
        )
        conn.execute(
            "UPDATE files SET width = NULL WHERE path = ?",
            (f"{library}/corrupt.jpg",),
        )

    second = verify.VerifyWorker(batch_pause=0.0).run_pass()

    assert second["pruned"] == 2
    assert _issues(verify_seed["db_path"]) == {
        (f"{library}/ghost.mp4", "thumb_failed"): 1,
        (f"{library}/real.zip/member.mp4", "zero_size"): 1,
        (f"{library}/real.zip/member.mp4", "thumb_failed"): 1,
    }


def test_rescan_signals_and_worker_helpers(verify_seed):
    worker = verify.worker()
    worker.run_pass()
    verify.request_rescan()
    assert verify.rescan_pending() is True
    assert worker.rescan_pending() is True

    state = verify.worker_state()
    assert state["passes"] == 1
    assert state["running"] is False
    assert state["rescan_pending"] is True


def test_worker_start_is_idempotent(monkeypatch):
    monkeypatch.setattr(verify, "_worker", None)
    worker = verify.VerifyWorker(idle_wait=60.0)
    worker.start()
    try:
        assert _wait_for(
            lambda: worker._thread is not None and worker._thread.is_alive()
        )
        worker.start()  # already alive: must return without a second thread
        assert _wait_for(lambda: worker.state()["passes"] >= 1)
    finally:
        worker.stop()
    assert worker.state()["running"] is False


def test_worker_loop_survives_a_failing_pass(monkeypatch):
    worker = verify.VerifyWorker(idle_wait=60.0)
    calls: list[int] = []

    def boom():
        calls.append(1)
        raise RuntimeError("bad pass")

    monkeypatch.setattr(worker, "run_pass", boom)
    worker.start()
    try:
        assert _wait_for(lambda: bool(calls))
    finally:
        worker.stop()
    state = worker.state()
    assert state["pass_running"] is False
    assert state["passes"] == 0  # nothing to report from a failed pass


def test_a_pass_stops_between_batches(verify_seed):
    """A stop arriving while a pass parks between batches ends the loop."""
    worker = verify.VerifyWorker(batch=1, batch_pause=0.5, idle_wait=60.0)

    def stopper():
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            state = worker.state()
            if state["pass_running"] and state["scanned"] >= 2:
                worker.stop()
                return
            time.sleep(0.01)

    watcher = threading.Thread(target=stopper)
    watcher.start()
    worker.start()
    watcher.join()
    worker.stop()  # safety net if the watcher never triggered


def test_module_stop_worker_stops_the_shared_worker(monkeypatch):
    monkeypatch.setattr(verify, "_worker", None)
    verify.worker()
    verify.stop_worker()
    thread = verify.start_worker()
    verify.request_rescan()
    verify.stop_worker()
    assert thread.state()["running"] is False


def test_list_issues_rejects_an_unknown_issue(verify_seed):
    with pytest.raises(WriteError) as error:
        verify.list_issues(issue="nope")
    assert error.value.status == 400


def test_dismiss_rejects_an_unknown_issue(verify_seed):
    with pytest.raises(WriteError) as error:
        verify.dismiss_issues(str(verify_seed["library"] / "ghost.mp4"), issue="nope")
    assert error.value.status == 400


def test_recheck_of_a_missing_file_falls_back_to_the_index(verify_seed):
    body = verify.recheck_file(str(verify_seed["library"] / "ghost.mp4"))
    assert body["issues"] == ["thumb_failed", "zero_size"]
    assert body["cleared"] == []
    assert body["probe_status"] == "skipped"


def test_recheck_reports_a_failing_probe(verify_seed, monkeypatch):
    from media_library import probe

    monkeypatch.setattr(probe, "probe_media", lambda path: None)
    body = verify.recheck_file(str(verify_seed["corrupt"]))
    assert body["issues"] == ["probe_failed"]
    assert body["probe_status"] == "failed"


def test_recheck_keeps_the_issue_when_the_probe_is_transient(verify_seed, monkeypatch):
    from media_library import probe

    monkeypatch.setattr(probe, "probe_media", lambda path: probe.TRANSIENT)
    body = verify.recheck_file(str(verify_seed["corrupt"]))
    assert body["issues"] == ["probe_failed"]
    assert body["probe_status"] == "transient"


def test_recheck_clears_probe_failed_after_a_real_probe(verify_seed):
    """The row said 0 x 0, but the PNG on disk probes fine."""
    library = str(verify_seed["library"])
    verify.VerifyWorker(batch_pause=0.0).run_pass()

    body = verify.recheck_file(f"{library}/corrupt.jpg")

    assert body["issues"] == []
    assert body["cleared"] == ["probe_failed"]
    assert body["probe_status"] == "ok"


def test_recheck_rejects_unindexed_paths(verify_seed):
    with pytest.raises(WriteError) as error:
        verify.recheck_file(str(verify_seed["library"] / "absent.mp4"))
    assert error.value.status == 404


# --- runtime ---------------------------------------------------------------


def test_start_runtime_stays_off_without_owned_workers():
    assert runtime.start_runtime() is False
    status = runtime.runtime_status()
    assert status["owned"] is False
    assert status["started"] is False
    runtime.stop_runtime()


def test_batch_sweep_wont_start_twice_and_reports_work():
    calls: list[int] = []

    def step():
        calls.append(1)

    sweep = runtime._BatchSweep("cover-sweep", step, idle_wait=120.0)
    sweep.start()
    try:
        assert _wait_for(lambda: sweep._thread is not None and sweep._thread.is_alive())
        sweep.start()  # already alive: must return without a second thread
    finally:
        sweep.stop()

    assert sweep._worked(None) is False
    assert sweep._worked({"converted": 0, "hashed": None}) is False
    assert sweep._worked({"converted": 1}) is True
    assert sweep._worked({"hashed": 3}) is True
    assert len(calls) <= 1  # the second start must not re-run the step


def test_batch_sweep_survives_a_failing_step():
    calls: list[int] = []

    def boom():
        calls.append(1)
        raise RuntimeError("bad batch")

    sweep = runtime._BatchSweep("cover-broken-sweep", boom, idle_wait=0.2)
    sweep.start()
    try:
        assert _wait_for(lambda: len(calls) >= 1)
    finally:
        sweep.stop()


def test_backup_watcher_tolerates_a_failing_snapshot(monkeypatch):
    calls: list[int] = []

    def failing_snapshot():
        calls.append(1)
        raise RuntimeError("disk full")

    monkeypatch.setattr(backups, "create_snapshot", failing_snapshot)
    watcher = runtime._BackupWatcher(0.001, first_delay=0.05)
    assert watcher.interval == 360.0  # clamped to the six-minute minimum
    watcher.start()
    try:
        assert _wait_for(lambda: len(calls) >= 1)
    finally:
        watcher.stop()


def test_start_runtime_is_idempotent(tmp_path, monkeypatch):
    library = tmp_path / "library"
    library.mkdir()
    monkeypatch.setenv("LIBRARY_DIR", str(library))
    monkeypatch.setenv("OWN_WORKERS", "true")
    monkeypatch.setattr(verify, "_worker", None)
    v2_config.reload_settings()
    try:
        assert runtime.start_runtime() is True
        assert runtime.start_runtime() is True  # the _started guard
        status = runtime.runtime_status()
        assert status["started"] is True
        assert status["verify"] is True
        assert status["crawler"] is True
    finally:
        runtime.stop_runtime()
        v2_config.reload_settings()
    assert runtime.runtime_status()["started"] is False


def test_stop_runtime_tolerates_a_failing_worker(monkeypatch):
    class _StubAlarm:
        @staticmethod
        def stop():
            raise RuntimeError("stop failed")

    monkeypatch.setattr(runtime, "_webp", _StubAlarm)
    runtime.stop_runtime()
    assert runtime.runtime_status()["started"] is False


# --- build_info ------------------------------------------------------------


def test_build_info_resolves_a_real_git_directory(tmp_path, monkeypatch):
    _no_build_env(monkeypatch)
    _write(tmp_path / ".git" / "HEAD", _SHA + "\n")
    assert build_info.resolve_commit(tmp_path) == _SHA[:12]


def test_build_info_follows_a_relative_gitdir_pointer(tmp_path, monkeypatch):
    _no_build_env(monkeypatch)
    repo = tmp_path / "repo"
    real = tmp_path / "real.git"
    real.mkdir()
    _write(repo / ".git", "gitdir: ../real.git\n")
    _write(real / "HEAD", _SHA + "\n")
    assert build_info.resolve_commit(repo) == _SHA[:12]


def test_build_info_falls_back_through_a_worktree_commondir(tmp_path, monkeypatch):
    _no_build_env(monkeypatch)
    worktree = tmp_path / "checkout"
    worktree.mkdir()
    main_git = tmp_path / "main" / ".git"
    git_dir = main_git / "worktrees" / "checkout"
    _write(worktree / ".git", f"gitdir: {git_dir}\n")
    _write(git_dir / "commondir", "../..\n")
    _write(git_dir / "HEAD", "ref: refs/heads/feature\n")
    _write(main_git / "refs" / "heads" / "feature", _SHA + "\n")
    assert build_info.resolve_commit(worktree) == _SHA[:12]


def test_build_info_returns_none_without_a_git_directory(tmp_path, monkeypatch):
    _no_build_env(monkeypatch)
    tmp_path.joinpath("media_library").mkdir()
    assert build_info.resolve_commit(tmp_path) is None


def test_build_info_ignores_a_gitdir_target_that_is_missing(tmp_path, monkeypatch):
    _no_build_env(monkeypatch)
    _write(tmp_path / ".git", "gitdir: ../never.git\n")
    assert build_info.resolve_commit(tmp_path) is None


def test_build_info_ignores_a_gitfile_without_a_pointer(tmp_path, monkeypatch):
    _no_build_env(monkeypatch)
    _write(tmp_path / ".git", "something else\n")
    assert build_info.resolve_commit(tmp_path) is None


def test_build_info_rejects_a_non_object_id_head(tmp_path, monkeypatch):
    _no_build_env(monkeypatch)
    _write(tmp_path / ".git" / "HEAD", "not a sha\n")
    assert build_info.resolve_commit(tmp_path) is None


def test_build_info_resolves_a_ref_from_packed_refs(tmp_path, monkeypatch):
    _no_build_env(monkeypatch)
    _write(tmp_path / ".git" / "HEAD", "ref: refs/heads/main\n")
    _write(tmp_path / ".git" / "packed-refs", f"{_SHA} refs/heads/main\n")
    assert build_info.resolve_commit(tmp_path) == _SHA[:12]


def test_build_info_environment_overrides_the_checkout(tmp_path, monkeypatch):
    _no_build_env(monkeypatch)
    monkeypatch.setenv("GIT_COMMIT", _SHA + "deadbeef")
    assert build_info.resolve_commit(tmp_path) == _SHA[:12]


def test_get_commit_caches_the_resolution(monkeypatch):
    monkeypatch.setenv("GIT_COMMIT", _SHA)
    build_info.get_commit.cache_clear()
    try:
        assert build_info.get_commit() == _SHA[:12]
        # A second call must hit the cache, not resolve again.
        monkeypatch.delenv("GIT_COMMIT")
        assert build_info.get_commit() == _SHA[:12]
    finally:
        build_info.get_commit.cache_clear()


def test_packed_ref_lookup_finds_and_misses(tmp_path):
    git_dir = tmp_path / ".git"
    empty = tmp_path / "empty.git"
    empty.mkdir()
    _write(
        git_dir / "packed-refs",
        f"# comment line\n{_SHA} refs/heads/main\n",
    )
    assert build_info._read_packed_ref([empty, git_dir], "refs/heads/main") == _SHA
    assert build_info._read_packed_ref([git_dir], "refs/heads/gone") is None


def test_resolve_head_without_a_head_file(tmp_path):
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    assert build_info._resolve_head(git_dir) is None
