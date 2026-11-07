"""ZIP virtual-folder support.

Archives are indexed by the shared crawler; this module only resolves a member
path and extracts the one file a viewer asked for into a local, bounded cache.
It never extracts an entire archive and never trusts a member path: the cache
filename is derived from a hash, so Zip Slip is structurally impossible.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import time
import uuid
import zipfile
from pathlib import Path

from media_library import writes
from media_library.config import get_settings


def split_zip_path(path: str) -> tuple[str, str] | None:
    """Split ``/lib/album.zip/sub/photo.jpg`` into archive and member."""
    lower = path.lower()
    marker = lower.find(".zip" + os.sep)
    if marker == -1:
        return None
    archive = path[: marker + 4]
    inner = path[marker + 5 :]
    return archive, inner


def is_zip_member(path: str) -> bool:
    return split_zip_path(path) is not None


def _safe_inner(inner: str) -> str:
    clean = (inner or "").replace("\\", "/").strip("/")
    parts = [part for part in clean.split("/") if part not in ("", ".")]
    if not parts or any(part == ".." for part in parts):
        raise writes.WriteError("Invalid archive member", 400)
    return "/".join(parts)


def _cache_path(archive: str) -> Path:
    digest = hashlib.sha256(archive.encode("utf-8", "surrogatepass")).hexdigest()[:16]
    return get_settings().zip_cache_dir / f"v2_{digest}"


def _cache_root(archive: str) -> Path:
    root = _cache_path(archive)
    root.mkdir(parents=True, exist_ok=True)
    return root


def cache_dir_for(archive: str) -> Path:
    """The extraction-cache directory for one archive (used by tests/tools)."""
    return _cache_path(archive)


def _cache_target(archive: str, inner: str) -> Path:
    digest = hashlib.sha256(inner.encode("utf-8", "surrogatepass")).hexdigest()[:20]
    suffix = Path(inner).suffix.lower()[:12]
    return _cache_root(archive) / f"{digest}{suffix}"


def extract_member(archive: str, inner: str) -> str:
    """Extract one member to the local cache and return the cached path."""
    member = _safe_inner(inner)
    if not os.path.isfile(archive):
        raise writes.WriteError("ZIP archive not found", 404)
    target = _cache_target(archive, member)
    if target.is_file():
        try:
            os.utime(target, None)  # LRU touch
        except OSError:
            pass
        return str(target)

    temp = target.with_name(f".tmp-{uuid.uuid4().hex}-{target.name}")
    try:
        with zipfile.ZipFile(archive) as archive_file:
            try:
                info = archive_file.getinfo(member)
            except KeyError:
                raise writes.WriteError("Member not found in archive", 404) from None
            with archive_file.open(info) as source, open(temp, "wb") as output:
                shutil.copyfileobj(source, output, 1024 * 1024)
        if not temp.is_file() or temp.stat().st_size != info.file_size:
            raise writes.WriteError("Archive member could not be extracted", 500)
        os.replace(temp, target)
    except zipfile.BadZipFile as exc:
        raise writes.WriteError(f"Broken ZIP archive: {exc}", 400) from exc
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
    _trim_cache()
    return str(target)


def _trim_cache() -> None:
    root = get_settings().zip_cache_dir
    cap = int(get_settings().zip_cache_max_size_gb * 1024**3)
    if cap <= 0 or not root.is_dir():
        return
    entries: list[tuple[float, int, Path]] = []
    total = 0
    for child in root.iterdir():
        if not child.is_dir():
            continue
        size = 0
        try:
            for file in child.rglob("*"):
                if file.is_file():
                    size += file.stat().st_size
        except OSError:
            continue
        total += size
        entries.append((child.stat().st_mtime, size, child))
    if total <= cap:
        return
    target = int(cap * 0.9)
    for _mtime, size, child in sorted(entries):
        if total <= target:
            break
        shutil.rmtree(child, ignore_errors=True)
        total -= size


def extract_archive(archive_path: str, destination: str) -> int:
    """Extract a whole archive safely into ``destination``; returns file count."""
    dest = Path(destination)
    dest.mkdir(parents=True, exist_ok=True)
    count = 0
    with zipfile.ZipFile(archive_path) as archive_file:
        for info in archive_file.infolist():
            name = info.filename.replace("\\", "/")
            parts = [part for part in name.split("/") if part not in ("", ".")]
            if not parts or any(part == ".." for part in parts):
                continue
            target = dest.joinpath(*parts)
            try:
                target.resolve().relative_to(dest.resolve())
            except ValueError:
                continue
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive_file.open(info) as source, open(target, "wb") as output:
                shutil.copyfileobj(source, output, 1024 * 1024)
            count += 1
    return count


def cache_usage() -> dict[str, int]:
    root = get_settings().zip_cache_dir
    total = 0
    files = 0
    if root.is_dir():
        for child in root.iterdir():
            if not child.is_dir():
                continue
            for file in child.rglob("*"):
                try:
                    if file.is_file():
                        total += file.stat().st_size
                        files += 1
                except OSError:
                    continue
    return {"files": files, "bytes": total}


def purge_cache(archive: str) -> None:
    shutil.rmtree(_cache_path(archive), ignore_errors=True)


# Re-exported for callers that only need the clock in tests.
def _now() -> float:
    return time.time()
