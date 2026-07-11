"""Negative-price detection — parser, ingest, and route tests."""
from __future__ import annotations

import pytest

# Import models at module level so Base.metadata includes power_price_daily
# before the db_session fixture calls create_all on its in-memory engine.
from backend.models.energy import PowerPriceDaily

# ─── XML helpers (same pattern as test_power_prices.py) ────────────────────

_NS = "urn:iec62325.351:tc57wg16:451-6:publicationdocument:7:0"


def _a44(ts_blocks: str, ns: str = _NS) -> str:
    return (
        f'<?xml version="1.0"?>'
        f'<Publication_MarketDocument xmlns="{ns}">'
        f"<type>A44</type>"
        f"{ts_blocks}"
        f"</Publication_MarketDocument>"
    )


def _ts(start: str, end: str, prices: list[float], res: str = "PT60M") -> str:
    pts = "".join(
        f"<Point><position>{i + 1}</position><price.amount>{p}</price.amount></Point>"
        for i, p in enumerate(prices)
    )
    return (
        f"<TimeSeries>"
        f"<Period>"
        f"<timeInterval><start>{start}</start><end>{end}</end></timeInterval>"
        f"<resolution>{res}</resolution>"
        f"{pts}"
        f"</Period>"
        f"</TimeSeries>"
    )


# ─── parse_day_ahead_stats unit tests ───────────────────────────────────────


def test_stats_all_positive():
    """All-positive day: negative_hours=0, correct min/max/mean."""
    from backend.power.entsoe_prices import parse_day_ahead_stats

    prices = [10.0, 20.0, 30.0, 40.0]  # mean=25, min=10, max=40
    xml = _a44(_ts("2026-05-01T00:00Z", "2026-05-01T04:00Z", prices))
    result = parse_day_ahead_stats(xml)
    assert "2026-05-01" in result
    day = result["2026-05-01"]
    assert day["mean"] == pytest.approx(25.0)
    assert day["min"] == pytest.approx(10.0)
    assert day["max"] == pytest.approx(40.0)
    assert day["negative_hours"] == 0


def test_stats_with_negative_hours():
    """Two negative hours → negative_hours=2, min is the most-negative value."""
    from backend.power.entsoe_prices import parse_day_ahead_stats

    prices = [-50.0, -10.0, 30.0, 80.0]  # mean=12.5, min=-50, max=80
    xml = _a44(_ts("2026-05-01T00:00Z", "2026-05-01T04:00Z", prices))
    result = parse_day_ahead_stats(xml)
    day = result["2026-05-01"]
    assert day["negative_hours"] == 2
    assert day["min"] == pytest.approx(-50.0)
    assert day["max"] == pytest.approx(80.0)
    assert day["mean"] == pytest.approx(12.5)


def test_stats_all_negative():
    """All prices negative → negative_hours = number of prices."""
    from backend.power.entsoe_prices import parse_day_ahead_stats

    prices = [-5.0, -10.0, -15.0]
    xml = _a44(_ts("2026-05-01T00:00Z", "2026-05-01T03:00Z", prices))
    result = parse_day_ahead_stats(xml)
    day = result["2026-05-01"]
    assert day["negative_hours"] == 3
    assert day["min"] == pytest.approx(-15.0)


def test_stats_mean_matches_parse_day_ahead_prices():
    """mean from parse_day_ahead_stats must equal parse_day_ahead_prices for same XML."""
    from backend.power.entsoe_prices import parse_day_ahead_prices, parse_day_ahead_stats

    prices = [20.0, -5.0, 100.0, 35.0] * 6  # 24 hours
    xml = _a44(_ts("2026-05-01T00:00Z", "2026-05-02T00:00Z", prices))
    stats = parse_day_ahead_stats(xml)
    old = parse_day_ahead_prices(xml)
    assert stats["2026-05-01"]["mean"] == pytest.approx(old["2026-05-01"])


def test_stats_empty_document():
    from backend.power.entsoe_prices import parse_day_ahead_stats

    assert parse_day_ahead_stats(_a44("")) == {}


def test_stats_two_days():
    """Multi-day XML buckets correctly into separate day entries."""
    from backend.power.entsoe_prices import parse_day_ahead_stats

    d1 = [10.0] * 24
    d2 = [-20.0] * 24
    xml = _a44(
        _ts("2026-05-01T00:00Z", "2026-05-02T00:00Z", d1)
        + _ts("2026-05-02T00:00Z", "2026-05-03T00:00Z", d2)
    )
    result = parse_day_ahead_stats(xml)
    assert result["2026-05-01"]["negative_hours"] == 0
    assert result["2026-05-02"]["negative_hours"] == 24
    assert result["2026-05-02"]["min"] == pytest.approx(-20.0)


# ─── ingest tests ────────────────────────────────────────────────────────────


async def test_ingest_writes_power_price_daily(db_session, monkeypatch):
    """ingest_day_ahead upserts a PowerPriceDaily row alongside EnergyPrice."""
    from pydantic import SecretStr

    from backend.power import entsoe_prices

    # 3 hours: 2 positive, 1 negative
    prices = [-20.0, 40.0, 60.0]
    xml = _a44(_ts("2026-05-01T00:00Z", "2026-05-01T03:00Z", prices))

    async def fake_fetch(eic, month_start, *, overwrite=False):
        return xml

    monkeypatch.setattr(entsoe_prices, "_fetch_zone_month", fake_fetch)
    monkeypatch.setattr(entsoe_prices.settings, "entsoe_api_token", SecretStr("tok"))

    result = await entsoe_prices.ingest_day_ahead(db_session, ["2026-05-01"])
    assert result["written"] == 1

    row = (
        db_session.query(PowerPriceDaily)
        .filter_by(date="2026-05-01", zone="DE_LU")
        .first()
    )
    assert row is not None
    assert row.negative_hours == 1
    assert row.min_price == pytest.approx(-20.0)
    assert row.max_price == pytest.approx(60.0)
    assert row.mean_price == pytest.approx((-20.0 + 40.0 + 60.0) / 3)


async def test_ingest_power_price_daily_idempotent(db_session, monkeypatch):
    """Re-running ingest updates the existing PowerPriceDaily row (no duplicate)."""
    from pydantic import SecretStr

    from backend.power import entsoe_prices

    xml_v1 = _a44(_ts("2026-05-01T00:00Z", "2026-05-01T03:00Z", [-10.0, 20.0, 30.0]))
    xml_v2 = _a44(_ts("2026-05-01T00:00Z", "2026-05-01T03:00Z", [5.0, 10.0, 15.0]))
    call_n = {"n": 0}

    async def fake_fetch(eic, month_start, *, overwrite=False):
        xml = xml_v1 if call_n["n"] == 0 else xml_v2
        call_n["n"] += 1
        return xml

    monkeypatch.setattr(entsoe_prices, "_fetch_zone_month", fake_fetch)
    monkeypatch.setattr(entsoe_prices.settings, "entsoe_api_token", SecretStr("tok"))

    await entsoe_prices.ingest_day_ahead(db_session, ["2026-05-01"])
    await entsoe_prices.ingest_day_ahead(db_session, ["2026-05-01"], overwrite=True)

    rows = db_session.query(PowerPriceDaily).filter_by(date="2026-05-01", zone="DE_LU").all()
    assert len(rows) == 1
    assert rows[0].negative_hours == 0  # v2 has no negatives
    assert rows[0].min_price == pytest.approx(5.0)


# ─── route tests ─────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clear_dependency_overrides():
    """Prevent dependency_overrides leaking between test files."""
    yield
    from backend.main import app
    app.dependency_overrides.clear()


def _make_client(db):
    from fastapi.testclient import TestClient

    from backend.database import get_db
    from backend.main import app
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app, raise_server_exceptions=True)


def _seed_daily(db, rows: list[dict]) -> None:
    for r in rows:
        db.add(
            PowerPriceDaily(
                date=r["date"],
                zone="DE_LU",
                mean_price=r["mean_price"],
                min_price=r["min_price"],
                max_price=r["max_price"],
                negative_hours=r["negative_hours"],
            )
        )
    db.commit()


def test_route_enriched_fields_present(db_session):
    """When PowerPriceDaily rows exist, each data point has negative_hours + negative flag."""
    from datetime import date, timedelta
    today = date.today()
    d1 = (today - timedelta(days=3)).isoformat()
    d2 = (today - timedelta(days=2)).isoformat()

    _seed_daily(db_session, [
        {"date": d1, "mean_price": 50.0, "min_price": -10.0, "max_price": 90.0, "negative_hours": 2},
        {"date": d2, "mean_price": 60.0, "min_price": 5.0,  "max_price": 100.0, "negative_hours": 0},
    ])
    client = _make_client(db_session)
    resp = client.get("/api/power/day-ahead?days=120")
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is True

    row_d1 = next(r for r in body["data"] if r["date"] == d1)
    assert row_d1["negative_hours"] == 2
    assert row_d1["negative"] is True
    assert row_d1["min_price"] == pytest.approx(-10.0)

    row_d2 = next(r for r in body["data"] if r["date"] == d2)
    assert row_d2["negative"] is False
    assert row_d2["negative_hours"] == 0


def test_route_negative_days_count(db_session):
    """negative_days count equals the number of rows where negative_hours > 0."""
    from datetime import date, timedelta
    today = date.today()
    rows = [
        {"date": (today - timedelta(days=i)).isoformat(),
         "mean_price": 50.0, "min_price": -5.0, "max_price": 80.0, "negative_hours": 1}
        for i in range(1, 4)
    ]
    rows.append({"date": (today - timedelta(days=4)).isoformat(),
                 "mean_price": 50.0, "min_price": 5.0, "max_price": 80.0, "negative_hours": 0})
    _seed_daily(db_session, rows)
    client = _make_client(db_session)
    body = client.get("/api/power/day-ahead?days=120").json()
    assert body["negative_days"] == 3


def test_route_fallback_when_no_daily_table(db_session):
    """If PowerPriceDaily is empty, route falls back to EnergyPrice (available=False when both empty)."""
    client = _make_client(db_session)
    body = client.get("/api/power/day-ahead?days=120").json()
    assert body["available"] is False


def test_route_latest_has_negative_hours(db_session):
    """latest object includes negative_hours and negative fields."""
    from datetime import date, timedelta
    today = date.today()
    d1 = (today - timedelta(days=1)).isoformat()
    _seed_daily(db_session, [
        {"date": d1, "mean_price": 30.0, "min_price": -50.0, "max_price": 80.0, "negative_hours": 5},
    ])
    client = _make_client(db_session)
    body = client.get("/api/power/day-ahead?days=120").json()
    assert body["latest"]["negative_hours"] == 5
    assert body["latest"]["negative"] is True


def test_stats_negative_hours_not_double_counted_across_overlapping_series():
    """ENTSO-E returns overlapping TimeSeries for the same delivery day (contract
    revisions). Counting negative slots across ALL points double-counts them:
    prod 2026-07-07 reported 7.0 negative hours where the deduplicated auction
    had 4.5. Slots must be deduplicated per timestamp before counting."""
    from backend.tests.test_power_prices import _a44, _ts

    prices = [-5.0] * 4 + [50.0] * 92  # one negative hour in QH slots
    xml = _a44(
        _ts("2026-05-01T00:00Z", "2026-05-02T00:00Z", prices, res="PT15M")
        + _ts("2026-05-01T00:00Z", "2026-05-02T00:00Z", prices, res="PT15M")
    )
    from backend.power.entsoe_prices import parse_day_ahead_stats

    day = parse_day_ahead_stats(xml)["2026-05-01"]
    assert day["negative_hours"] == 1.0, f"double-counted: {day['negative_hours']}"


def test_stats_mean_unaffected_by_duplicate_series():
    from backend.power.entsoe_prices import parse_day_ahead_stats
    from backend.tests.test_power_prices import _a44, _ts

    xml = _a44(
        _ts("2026-05-01T00:00Z", "2026-05-02T00:00Z", [100.0] * 24)
        + _ts("2026-05-01T00:00Z", "2026-05-02T00:00Z", [110.0] * 24)
    )
    day = parse_day_ahead_stats(xml)["2026-05-01"]
    assert day["mean"] == 105.0
    assert day["min"] == 105.0  # min of per-slot means, not of raw duplicate points
    assert day["max"] == 105.0
