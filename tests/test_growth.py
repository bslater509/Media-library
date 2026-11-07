"""Growth-history coverage: the daily rollup, its endpoint, and the worker.

``growth_history`` keeps one row per UTC calendar day; a same-day recompute
upserts over the row. The refresh route answers 503 on a read-only index, and
the owned worker's day-rollover decision is pure so tests can inject time
instead of sleeping.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from media_library import analytics as analytics_module
from media_library import config as api_config
from media_library import growth
from media_library.app import create_app

DAY_A = 1720000000.0  # 2024-07-03 in UTC
DAY_B = DAY_A + 86400.0  # 2024-07-04
DAY_C = DAY_B + 86400.0  # 2024-07-05


def _day(ts: float) -> str:
    return growth.current_day(ts)


def _recorded_days() -> list[str]:
    days: list[str] = []
    for ts in (DAY_A, DAY_B, DAY_C):
        growth.record_today(ts)
        days.append(growth.current_day(ts))
    return days


def _rows() -> dict[str, tuple[float, int, int, int]]:
    with sqlite3.connect(api_config.get_settings().db_path) as conn:
        return {
            row[0]: (row[1], row[2], row[3], row[4])
            for row in conn.execute(
                "SELECT day, recorded_at, files_count, video_count, "
                "image_count FROM growth_history"
            )
        }


def _seed_files(rows: list[tuple[str, str, int]]) -> None:
    """Seed ``files`` rows in the style of the other suites."""
    with sqlite3.connect(api_config.get_settings().db_path) as conn:
        for index, (name, kind, size) in enumerate(rows):
            conn.execute(
                "INSERT INTO files (path, name, folder_path, type, ext, size, "
                "size_formatted, mtime, has_thumb) VALUES "
                "(?, ?, ?, ?, ?, ?, 'size', 1.0, 0)",
                (
                    f"/lib/item{index}{name}",
                    name,
                    "/lib",
                    kind,
                    Path(name).suffix,
                    size,
                ),
            )


@pytest.fixture
def client(tmp_path, monkeypatch):
    library = tmp_path / "library"
    library.mkdir()
    monkeypatch.setenv("LIBRARY_DIR", str(library))
    api_config.reload_settings()
    analytics_module.invalidate()
    try:
        with TestClient(create_app()) as test_client:
            yield test_client
    finally:
        analytics_module.invalidate()
        api_config.reload_settings()


def test_refresh_upserts_today_and_counts_every_type(client):
    _seed_files(
        [
            ("a.mp4", "video", 1000),
            ("b.mp4", "video", 2000),
            ("c.jpg", "image", 500),
            ("d.png", "image", 300),
            ("e.xyz", "other", 123),
        ]
    )

    point = growth.record_today()
    day = growth.current_day()
    assert point["day"] == day
    assert point["files_count"] == 5
    assert point["video_count"] == 2
    assert point["image_count"] == 2
    assert point["bytes_total"] == 1000 + 2000 + 500 + 300 + 123

    rows = _rows()
    assert list(rows) == [day]
    assert rows[day][0] == pytest.approx(point["recorded_at"])

    # A same-day recompute upserts over the row instead of adding one.
    later = point["recorded_at"] + 3600.0
    with sqlite3.connect(api_config.get_settings().db_path) as conn:
        conn.execute("DELETE FROM files WHERE ext = '.xyz'")

    second = growth.record_today(later)
    assert second["files_count"] == 4
    assert second["bytes_total"] == 1000 + 2000 + 500 + 300
    rows = _rows()
    assert list(rows) == [day]
    assert rows[day][0] == pytest.approx(later)


def test_worker_ticks_without_sleeping_across_a_rollover():
    """The "should I record now" decision is pure and time-injectable."""
    assert growth.should_record(None, None, DAY_A) is True

    worker = growth.GrowthWorker(wake_seconds=growth.WAKE_SECONDS)
    assert worker.safe_tick(DAY_A) is True
    assert worker.recorded_day == _day(DAY_A)
    assert worker.last_success_at == pytest.approx(DAY_A)
    assert worker.last_error is None

    # Same day, cadence not elapsed: no second row.
    assert growth.should_record(_day(DAY_A), DAY_A, DAY_A + 60.0) is False
    assert worker.safe_tick(DAY_A + 60.0) is False

    # Same day, cadence elapsed: refreshes today's row in place.
    assert growth.should_record(_day(DAY_A), DAY_A, DAY_A + growth.WAKE_SECONDS) is True
    assert worker.safe_tick(DAY_A + growth.WAKE_SECONDS) is True
    assert worker.recorded_day == _day(DAY_A)

    # A rollover is noticed on the next wake and recorded.
    assert growth.should_record(_day(DAY_A), DAY_A + growth.WAKE_SECONDS, DAY_B) is True
    assert worker.safe_tick(DAY_B) is True
    assert worker.recorded_day == _day(DAY_B)
    assert _rows().keys() == {_day(DAY_A), _day(DAY_B)}

    status = worker.status()
    assert status["started"] is False  # never started a thread here
    assert status["last_error"] is None
    assert status["recorded_day"] == _day(DAY_B)


def test_history_endpoint_orders_oldest_first_and_caps(client, tmp_path, monkeypatch):
    # Seed three recorded days plus today's row straight into the table.
    today = growth.current_day()
    with sqlite3.connect(api_config.get_settings().db_path) as conn:
        for day, ts in (
            (_day(DAY_A), DAY_A),
            (_day(DAY_B), DAY_B),
            (_day(DAY_C), DAY_C),
        ):
            conn.execute(
                "INSERT INTO growth_history (day, files_count, video_count, "
                "image_count, bytes_total, recorded_at) VALUES (?,?,?,?,?,?)",
                (day, 10, 6, 3, 900, ts),
            )
        conn.execute(
            "INSERT INTO growth_history (day, files_count, video_count, "
            "image_count, bytes_total, recorded_at) VALUES (?,?,?,?,?,?)",
            (today, 5, 1, 4, 700, DAY_C + 3600.0),
        )
        conn.execute(
            "INSERT INTO files (path, name, folder_path, type, ext, size, "
            "size_formatted, mtime) VALUES (?, ?, ?, 'video', '.mp4', 42, "
            "'42 B', 0.0)",
            ("/lib/now.mp4", "now.mp4", "/lib"),
        )

    response = client.get("/api/analytics/growth", params={"days": 2})
    assert response.status_code == 200, response.text
    payload = response.json()

    assert payload["days"] == 2
    # Today's recorded row is folded away in favour of the live counts.
    assert [point["day"] for point in payload["history"]] == [
        _day(DAY_B),
        _day(DAY_C),
    ]
    assert payload["history"][0]["live"] is False

    today_point = payload["today"]
    assert today_point["live"] is True
    assert today_point["day"] == today
    assert today_point["files_count"] == 1
    assert today_point["video_count"] == 1
    assert today_point["bytes_total"] == 42
    assert today_point["recorded_at"] is None

    assert client.get("/api/analytics/growth", params={"days": 0}).status_code == 422
    assert client.get("/api/analytics/growth", params={"days": 3651}).status_code == 422


def test_growth_endpoint_serves_an_empty_index(client):
    response = client.get("/api/analytics/growth")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["history"] == []
    assert payload["today"]["files_count"] == 0
    assert payload["today"]["bytes_total"] == 0


def test_growth_endpoint_attaches_the_worker_status(client, monkeypatch):
    class _StubWorker:
        @staticmethod
        def status() -> dict:
            return {"state": "idle"}

    monkeypatch.setattr(growth, "_worker", _StubWorker())
    payload = client.get("/api/analytics/growth").json()
    assert payload["worker"] == {"state": "idle"}


def test_refresh_route_upserts_today(client):
    _seed_files([("clip.mp4", "video", 750), ("still.jpg", "image", 250)])

    response = client.post("/api/analytics/growth/refresh")
    assert response.status_code == 200, response.text
    point = response.json()
    today = growth.current_day()
    assert point["day"] == today
    assert point["files_count"] == 2
    assert point["video_count"] == 1
    assert point["image_count"] == 1
    assert point["bytes_total"] == 1000
    assert point["live"] is False

    # The recorded row is there, and the chart endpoint folds it out again.
    listing = client.get("/api/analytics/growth").json()
    assert listing["history"] == []
    assert listing["today"]["files_count"] == 2


def test_refresh_route_503_on_read_only_index(client, monkeypatch):
    _seed_files([("still.jpg", "image", 100)])
    monkeypatch.setenv("DB_READ_ONLY", "true")
    api_config.reload_settings()

    response = client.post("/api/analytics/growth/refresh")
    assert response.status_code == 503, response.text
    assert _rows() == {}

    # The GET still answers a read-only index; no worker is registered.
    assert client.get("/api/analytics/growth").status_code == 200
