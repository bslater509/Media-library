"""Tests for the WebP worker's memory-guard helpers.

The guard must account for the service's own cgroup limit, not just the host's
``MemAvailable`` — otherwise a fast image sweep can run the process into
``memory.high`` and the kernel throttles sockets until the server stops
answering HTTP.
"""

import math

from media_library.workers.webp_worker import (
    _available_memory_mb,
    _cgroup_headroom_mb,
    _release_memory,
)


def test_available_memory_is_a_positive_finite_number():
    avail = _available_memory_mb()
    assert isinstance(avail, float)
    assert math.isfinite(avail) or avail == float("inf")
    assert avail >= 0


def test_available_is_the_tighter_of_host_and_cgroup(monkeypatch):
    from media_library.workers import webp_worker

    monkeypatch.setattr(webp_worker, "_host_available_memory_mb", lambda: 2048.0)
    monkeypatch.setattr(webp_worker, "_cgroup_headroom_mb", lambda: 500.0)
    assert webp_worker._available_memory_mb() == 500.0

    monkeypatch.setattr(webp_worker, "_cgroup_headroom_mb", lambda: 9000.0)
    assert webp_worker._available_memory_mb() == 2048.0


def test_cgroup_headroom_is_a_number():
    headroom = _cgroup_headroom_mb()
    assert isinstance(headroom, float)
    assert headroom >= 0 or headroom == float("inf")


def test_release_memory_never_raises():
    # Should be safe even where malloc_trim / ctypes are unavailable.
    _release_memory()
