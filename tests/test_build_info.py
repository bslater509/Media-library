"""Tests for the best-effort build identity used by ``/api/health``."""

from pathlib import Path

from media_library.core import build_info

_SHA = "0123456789abcdef0123456789abcdef01234567"


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _no_env(monkeypatch) -> None:
    monkeypatch.delenv("GIT_COMMIT", raising=False)
    monkeypatch.delenv("BUILD_COMMIT", raising=False)


def test_resolves_an_unborn_ref_directory(tmp_path, monkeypatch):
    _no_env(monkeypatch)
    _write(tmp_path / ".git" / "HEAD", "ref: refs/heads/main\n")
    _write(tmp_path / ".git" / "refs" / "heads" / "main", _SHA + "\n")
    assert build_info.resolve_commit(tmp_path) == _SHA[:12]


def test_resolves_a_detached_head(tmp_path, monkeypatch):
    _no_env(monkeypatch)
    _write(tmp_path / ".git" / "HEAD", _SHA + "\n")
    assert build_info.resolve_commit(tmp_path) == _SHA[:12]


def test_falls_back_to_packed_refs(tmp_path, monkeypatch):
    _no_env(monkeypatch)
    _write(tmp_path / ".git" / "HEAD", "ref: refs/heads/main\n")
    _write(
        tmp_path / ".git" / "packed-refs",
        "# pack-refs with: peeled fully-peeled sorted\n"
        f"{_SHA} refs/heads/main\n"
        "^1111111111111111111111111111111111111111\n",
    )
    assert build_info.resolve_commit(tmp_path) == _SHA[:12]


def test_follows_a_linked_worktree_gitfile(tmp_path, monkeypatch):
    """A ``.git`` file points at the worktree dir; its ref lives in commondir."""
    _no_env(monkeypatch)
    worktree = tmp_path / "checkout"
    worktree.mkdir()
    git_dir = tmp_path / "main" / ".git" / "worktrees" / "checkout"
    _write(worktree / ".git", f"gitdir: {git_dir}\n")
    _write(git_dir / "commondir", "../..\n")
    _write(git_dir / "HEAD", "ref: refs/heads/feature\n")
    _write(
        tmp_path / "main" / ".git" / "packed-refs",
        f"{_SHA} refs/heads/feature\n",
    )
    assert build_info.resolve_commit(worktree) == _SHA[:12]


def test_returns_none_without_a_git_directory(tmp_path, monkeypatch):
    _no_env(monkeypatch)
    (tmp_path / "media_library").mkdir()
    assert build_info.resolve_commit(tmp_path) is None


def test_returns_none_for_a_non_object_id_head(tmp_path, monkeypatch):
    _no_env(monkeypatch)
    _write(tmp_path / ".git" / "HEAD", "not a sha\n")
    assert build_info.resolve_commit(tmp_path) is None


def test_environment_overrides_the_checkout(tmp_path, monkeypatch):
    _no_env(monkeypatch)
    monkeypatch.setenv("GIT_COMMIT", _SHA + "deadbeef")
    assert build_info.resolve_commit(tmp_path) == _SHA[:12]

    monkeypatch.delenv("GIT_COMMIT")
    monkeypatch.setenv("BUILD_COMMIT", _SHA)
    assert build_info.resolve_commit(tmp_path) == _SHA[:12]


def test_get_commit_is_cached_and_clearable(monkeypatch):
    monkeypatch.setenv("GIT_COMMIT", _SHA)
    build_info.get_commit.cache_clear()
    try:
        assert build_info.get_commit() == _SHA[:12]
        assert build_info.get_commit() == _SHA[:12]
        assert build_info.get_commit.cache_info().hits >= 1
    finally:
        build_info.get_commit.cache_clear()
