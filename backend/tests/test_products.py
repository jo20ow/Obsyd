"""Base / Peak / Off-peak in market time.

The tests are mostly about the CLOCK. The store is UTC; the products are CET. Get
that wrong and every peak price on the desk is quietly built from the wrong hours
— which is exactly what bucketing by UTC date does.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from backend.power.hourly_store import upsert_hourly
from backend.power.products import (
    compute_products,
    day_products,
    is_peak_hour,
    market_day,
)


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    from backend.main import app

    app.dependency_overrides.clear()


def _client(db):
    from backend.database import get_db
    from backend.main import app

    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


def _utc(s: str) -> int:
    return int(datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp())


# ─── the clock ────────────────────────────────────────────────────────────────


def test_the_last_utc_hours_belong_to_the_next_delivery_day():
    """THE bug that bucketing by UTC date hides: 22:00 UTC in summer is already
    midnight in CET, i.e. the next delivery day."""
    assert market_day(_utc("2026-07-15T21:00"))[0] == "2026-07-15"  # 23:00 CEST
    assert market_day(_utc("2026-07-15T22:00"))[0] == "2026-07-16"  # 00:00 CEST
    # winter: CET = UTC+1, so the boundary moves an hour
    assert market_day(_utc("2026-01-15T22:00"))[0] == "2026-01-15"  # 23:00 CET
    assert market_day(_utc("2026-01-15T23:00"))[0] == "2026-01-16"  # 00:00 CET


def test_local_hour_is_market_time_not_utc():
    day, hour, weekday = market_day(_utc("2026-07-15T06:00"))  # 08:00 CEST
    assert (day, hour) == ("2026-07-15", 8)
    assert weekday == 2  # a Wednesday
    assert is_peak_hour(hour, weekday) is True


def test_peak_is_weekdays_only():
    assert is_peak_hour(12, 4) is True    # Friday noon
    assert is_peak_hour(12, 5) is False   # Saturday noon — no peak product exists
    assert is_peak_hour(7, 2) is False    # before 08:00
    assert is_peak_hour(20, 2) is False   # 20:00 is exclusive


# ─── the products ─────────────────────────────────────────────────────────────


def test_base_peak_offpeak_split_a_weekday():
    hours = {h: (100.0 if 8 <= h < 20 else 40.0) for h in range(24)}
    p = day_products(hours, weekday=2)
    assert p["peak"] == 100.0 and p["peak_hours"] == 12
    assert p["off_peak"] == 40.0
    assert p["base"] == pytest.approx((100 * 12 + 40 * 12) / 24)
    assert p["peak_premium"] == pytest.approx(100.0 / 70.0, rel=1e-3)


def test_a_weekend_has_no_peak_product_rather_than_a_zero():
    hours = {h: 50.0 for h in range(24)}
    p = day_products(hours, weekday=6)
    assert p["weekend"] is True
    assert p["peak"] is None and p["peak_premium"] is None, "the product does not exist"
    assert p["off_peak"] == 50.0 and p["base"] == 50.0


def test_peak_premium_is_undefined_through_a_zero_base():
    """A ratio through zero is a number, not a meaning — a day whose base is 0
    (or negative) gets no premium."""
    hours = {h: (60.0 if 8 <= h < 20 else -60.0) for h in range(24)}
    p = day_products(hours, weekday=1)
    assert p["base"] == 0.0
    assert p["peak_premium"] is None


def test_evening_ramp_is_the_steepest_three_hour_rise():
    hours = {h: 30.0 for h in range(24)}
    hours[17], hours[18], hours[19], hours[20] = 40.0, 90.0, 150.0, 160.0
    p = day_products(hours, weekday=1)
    assert p["evening_ramp"] == pytest.approx(hours[20] - hours[17])


def test_negative_hours_are_counted_on_the_delivery_day():
    hours = {h: (-5.0 if h < 6 else 50.0) for h in range(24)}
    p = day_products(hours, weekday=1)
    assert p["negative_hours"] == 6


# ─── DB-backed ────────────────────────────────────────────────────────────────


def _seed_prices(db, zone="DE_LU", days=5, peak=120.0, off=40.0):
    """Prices keyed by UTC hour, shaped so the PEAK sits in CET peak hours."""
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    start = now - timedelta(days=days)
    points = []
    ts = int(start.timestamp())
    for i in range(days * 24):
        t = ts + i * 3600
        _day, hour, weekday = market_day(t)
        points.append((t, peak if is_peak_hour(hour, weekday) else off))
    upsert_hourly(db, "price.dayahead", zone, points, unit="EUR/MWh")


def test_compute_products_reads_peak_from_market_hours(db_session):
    _seed_prices(db_session)
    out = compute_products(db_session, "DE_LU", days=5)
    assert out["available"] is True
    assert out["market_tz"] == "CET/CEST"

    weekdays = [r for r in out["data"] if not r["weekend"]]
    assert weekdays, "the window must contain at least one weekday"
    for r in weekdays:
        assert r["peak"] == 120.0, "the peak product reads the CET peak hours"
        assert r["off_peak"] == 40.0
        assert r["peak_hours"] == 12


def test_partial_days_are_dropped_not_averaged(db_session):
    """We read an extra UTC day to cover the CET boundary; a 'base' built from
    four hours is not a base."""
    _seed_prices(db_session, days=3)
    out = compute_products(db_session, "DE_LU", days=3)
    assert all(r["hours"] >= 20 for r in out["data"])


def test_unknown_and_empty_zones_are_honest(db_session):
    assert compute_products(db_session, "ZZ")["available"] is False
    out = compute_products(db_session, "FR")
    assert out["available"] is False and "No hourly day-ahead prices" in out["reason"]


def test_route(db_session):
    _seed_prices(db_session)
    body = _client(db_session).get("/api/power/products?zone=DE_LU&days=5").json()
    assert body["available"] is True
    assert "CET" in body["peak_definition"]
    assert body["latest"]["base"] is not None
