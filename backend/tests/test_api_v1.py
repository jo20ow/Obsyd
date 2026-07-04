"""Public data API v1: /api/v1/series (JSON+CSV, hourly+daily), catalog, meta, limits."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from backend.auth.ratelimit import reset_limits
from backend.database import get_db
from backend.main import app
from backend.models.energy import PowerHourly, SeriesDim, ZoneDim  # noqa: F401 — register tables
from backend.power.hourly_store import upsert_hourly

_BASE = int(datetime(2026, 6, 1, tzinfo=UTC).timestamp())
_H = 3600


@pytest.fixture(autouse=True)
def _isolate():
    reset_limits()
    yield
    app.dependency_overrides.clear()
    reset_limits()


def _client(db):
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


def _seed(db, n=6):
    upsert_hourly(db, "load.actual", "DE_LU",
                  [(_BASE + i * _H, 50_000.0 + i * 100) for i in range(n)], unit="MW")


def test_series_json_returns_points(db_session):
    _seed(db_session)
    body = _client(db_session).get(
        "/api/v1/series?series=load.actual&zone=DE_LU&start=2026-06-01&end=2026-06-02"
    ).json()
    assert body["available"] is True
    assert body["unit"] == "MW"
    assert body["count"] == 6
    assert body["data"][0] == {"datetime_utc": "2026-06-01T00:00:00+00:00", "value": 50_000.0}


def test_series_daily_aggregates_mean(db_session):
    _seed(db_session, n=24)  # a full day
    body = _client(db_session).get(
        "/api/v1/series?series=load.actual&zone=DE_LU&start=2026-06-01&end=2026-06-02&resolution=daily"
    ).json()
    assert body["resolution"] == "daily"
    assert body["count"] == 1
    assert body["data"][0]["date"] == "2026-06-01"
    assert body["data"][0]["value"] == pytest.approx(50_000.0 + 11.5 * 100)  # mean of 0..23 steps


def test_series_csv_streams_download(db_session):
    _seed(db_session)
    resp = _client(db_session).get(
        "/api/v1/series?series=load.actual&zone=DE_LU&start=2026-06-01&end=2026-06-02&format=csv"
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert "attachment" in resp.headers["content-disposition"]
    lines = resp.text.strip().splitlines()
    assert lines[0] == "datetime_utc,value"
    assert len(lines) == 7  # header + 6 rows


def test_series_unknown_returns_empty(db_session):
    body = _client(db_session).get("/api/v1/series?series=nope&zone=DE_LU").json()
    assert body["available"] is False
    assert body["count"] == 0


def test_series_bad_datetime_400(db_session):
    r = _client(db_session).get("/api/v1/series?series=load.actual&zone=DE_LU&start=notadate")
    assert r.status_code == 400


def test_meta_lists_sources_and_zones(db_session):
    _seed(db_session)
    body = _client(db_session).get("/api/v1/meta").json()
    assert body["license"] == "AGPL-3.0-or-later"
    assert any(s["source"].startswith("ENTSO-E") for s in body["attribution"])
    assert {z["key"] for z in body["zones"]} == {"DE_LU", "FR", "NL"}
    assert any(s["key"] == "load.actual" for s in body["series"])


def test_catalog_reports_coverage(db_session):
    _seed(db_session)
    body = _client(db_session).get("/api/v1/series/catalog").json()
    assert body["available"] is True
    assert body["coverage"]["from"] == "2026-06-01T00:00:00+00:00"
    assert body["series_count"] >= 1


def test_status_reports_coverage(db_session):
    from datetime import timedelta

    from backend.models.energy import PowerPriceDaily
    # A recent DE_LU day-ahead row → its per-zone freshness probe is fresh.
    recent = (datetime.now(UTC).date() - timedelta(days=1)).isoformat()
    db_session.add(PowerPriceDaily(date=recent, zone="DE_LU", mean_price=50.0,
                                   min_price=10.0, max_price=90.0, negative_hours=0))
    db_session.commit()
    body = _client(db_session).get("/api/v1/status").json()
    keys = {s["key"]: s for s in body["sources"]}
    assert "power_dayahead:DE_LU" in keys
    assert keys["power_dayahead:DE_LU"]["fresh"] is True
    assert keys["power_dayahead:DE_LU"]["last_seen"] == recent
    # Other zones have no data → overall not healthy, but the view lists them.
    assert body["healthy"] is False
    assert body["total"] >= 6  # 3 zones × (dayahead+grid) + flows/gas/ttf


def test_zones_lists_registry_with_flags(db_session):
    body = _client(db_session).get("/api/v1/zones").json()
    assert body["default"] == "DE_LU"
    assert set(body["enabled_keys"]) == {"DE_LU", "FR", "NL"}
    z = {x["key"]: x for x in body["zones"]}
    assert len(z) >= 27  # full registry, not just enabled
    assert z["DE_LU"]["enabled"] is True and z["DE_LU"]["has_flows"] is True
    assert z["IT_NORD"]["enabled"] is False and z["IT_NORD"]["has_flows"] is False  # ec_country=None
    assert z["ES"]["has_flows"] is True


def test_status_empty_is_not_healthy(db_session):
    body = _client(db_session).get("/api/v1/status").json()
    assert body["healthy"] is False
    assert body["fresh_count"] == 0
    assert body["total"] > 0


def test_rate_limit_returns_429(db_session, monkeypatch):
    import backend.routes.api_v1 as v1
    monkeypatch.setattr(v1, "RATE_PER_MIN", 2)
    _seed(db_session)
    c = _client(db_session)
    url = "/api/v1/series?series=load.actual&zone=DE_LU"
    assert c.get(url).status_code == 200
    assert c.get(url).status_code == 200
    assert c.get(url).status_code == 429  # third within the window
