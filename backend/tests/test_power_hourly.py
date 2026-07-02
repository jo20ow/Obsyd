"""Hourly day-ahead curve: parse → persist (JSON-in-Text) → expose."""
from __future__ import annotations

import json

from fastapi.testclient import TestClient

from backend.main import app
from backend.models.energy import PowerPriceDaily
from backend.power.entsoe_prices import _upsert_daily, parse_day_ahead_stats

# Reuse the A44 XML builders from the price parser tests.
from backend.tests.test_power_prices import _a44, _ts


def test_parse_stats_includes_hourly_series():
    prices = [40.0 + i for i in range(24)]  # 24 distinct hourly prices
    xml = _a44(_ts("2026-05-01T00:00Z", "2026-05-02T00:00Z", prices))
    stats = parse_day_ahead_stats(xml)["2026-05-01"]
    assert "hourly" in stats
    hourly = stats["hourly"]
    assert len(hourly) == 24
    assert hourly[0] == {"hour": 0, "price": 40.0}
    assert hourly[-1] == {"hour": 23, "price": 63.0}
    assert [h["hour"] for h in hourly] == list(range(24))  # ordered by hour


def test_upsert_daily_persists_hourly_json(db_session):
    stats = {
        "mean": 50.0, "min": 40.0, "max": 63.0, "negative_hours": 0,
        "hourly": [{"hour": h, "price": 40.0 + h} for h in range(24)],
    }
    _upsert_daily(db_session, "2026-05-01", "DE_LU", stats)
    db_session.commit()
    row = db_session.query(PowerPriceDaily).filter_by(date="2026-05-01", zone="DE_LU").first()
    assert row is not None
    assert json.loads(row.hourly_prices) == stats["hourly"]


def test_upsert_daily_without_hourly_is_safe(db_session):
    # Older stats dicts (no "hourly" key) must not break the upsert.
    _upsert_daily(db_session, "2026-05-02", "DE_LU", {"mean": 10, "min": 5, "max": 15, "negative_hours": 0})
    db_session.commit()
    row = db_session.query(PowerPriceDaily).filter_by(date="2026-05-02", zone="DE_LU").first()
    assert row is not None and row.hourly_prices is None


class _Client:
    def __init__(self, db):
        from backend.database import get_db
        app.dependency_overrides[get_db] = lambda: db
        self.c = TestClient(app)

    def __enter__(self):
        return self.c

    def __exit__(self, *a):
        app.dependency_overrides.clear()


def test_hourly_endpoint_returns_latest_day_series(db_session):
    hourly = [{"hour": h, "price": 40.0 + h} for h in range(24)]
    db_session.add(PowerPriceDaily(
        date="2026-05-01", zone="DE_LU", mean_price=51.5, min_price=40, max_price=63,
        negative_hours=0, hourly_prices=json.dumps(hourly),
    ))
    db_session.commit()
    with _Client(db_session) as c:
        body = c.get("/api/power/day-ahead/hourly?zone=DE_LU").json()
    assert body["available"] is True
    assert body["zone"] == "DE_LU"
    assert body["date"] == "2026-05-01"
    assert len(body["data"]) == 24
    assert body["data"][0] == {"hour": 0, "price": 40.0}


def test_hourly_endpoint_unavailable_when_missing(db_session):
    with _Client(db_session) as c:
        body = c.get("/api/power/day-ahead/hourly?zone=DE_LU").json()
    assert body["available"] is False
