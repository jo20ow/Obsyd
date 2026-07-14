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


def test_series_parquet_roundtrip(db_session):
    pytest.importorskip("pyarrow")
    import io

    import pyarrow.parquet as pq
    _seed(db_session)
    resp = _client(db_session).get(
        "/api/v1/series?series=load.actual&zone=DE_LU&start=2026-06-01&end=2026-06-02&format=parquet"
    )
    assert resp.status_code == 200
    assert "parquet" in resp.headers["content-disposition"]
    table = pq.read_table(io.BytesIO(resp.content))
    assert table.num_rows == 6
    assert set(table.column_names) == {"datetime_utc", "value"}


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


def test_genmix_wide_by_fuel(db_session):
    # 24h of solar (B16) + wind onshore (B19) on one UTC day → one daily row, readable fuels.
    upsert_hourly(db_session, "gen.B16", "DE_LU", [(_BASE + i * _H, 5_000.0) for i in range(24)], unit="MW")
    upsert_hourly(db_session, "gen.B19", "DE_LU", [(_BASE + i * _H, 10_000.0) for i in range(24)], unit="MW")
    body = _client(db_session).get(
        "/api/v1/genmix?zone=DE_LU&start=2026-06-01&end=2026-06-02&resolution=daily"
    ).json()
    assert body["available"] is True
    assert set(body["fuels"]) == {"Solar", "Wind Onshore"}
    row = body["data"][0]
    assert row["t"] == "2026-06-01"
    assert row["Solar"] == 5_000.0
    assert row["Wind Onshore"] == 10_000.0


def test_genmix_csv_streams_wide_download(db_session):
    # Same wide shape as the JSON view, streamed as a CSV download: header = t + sorted fuels.
    upsert_hourly(db_session, "gen.B16", "DE_LU", [(_BASE + i * _H, 5_000.0) for i in range(24)], unit="MW")
    upsert_hourly(db_session, "gen.B19", "DE_LU", [(_BASE + i * _H, 10_000.0) for i in range(24)], unit="MW")
    resp = _client(db_session).get(
        "/api/v1/genmix?zone=DE_LU&start=2026-06-01&end=2026-06-02&resolution=daily&format=csv"
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert "attachment" in resp.headers["content-disposition"]
    lines = resp.text.strip().splitlines()
    assert lines[0] == "t,Solar,Wind Onshore"
    assert lines[1] == "2026-06-01,5000.0,10000.0"


def test_genmix_empty_zone(db_session):
    body = _client(db_session).get("/api/v1/genmix?zone=FR").json()
    assert body["available"] is False


def test_snapshot_aligned_matrix(db_session):
    # Per-zone hourly values aligned to one timestamp grid, for the map scrubber.
    upsert_hourly(db_session, "price.dayahead", "DE_LU", [(_BASE + i * _H, 40.0 + i) for i in range(3)], unit="EUR/MWh")
    upsert_hourly(db_session, "price.dayahead", "FR", [(_BASE + i * _H, 30.0 + i) for i in range(3)], unit="EUR/MWh")
    body = _client(db_session).get(
        "/api/v1/snapshot?series=price.dayahead&start=2026-06-01&end=2026-06-02"
    ).json()
    assert body["available"] is True
    assert len(body["timestamps"]) == 3
    assert body["zones"]["DE_LU"] == [40.0, 41.0, 42.0]
    assert body["zones"]["FR"] == [30.0, 31.0, 32.0]


def test_snapshot_unknown_series_empty(db_session):
    body = _client(db_session).get("/api/v1/snapshot?series=nope.nope").json()
    assert body["available"] is False


def test_capacity_endpoint(db_session):
    from backend.models.energy import InstalledCapacity
    db_session.add_all([
        InstalledCapacity(zone="DE_LU", year=2025, psr_type="Solar", capacity_mw=50_000.0),
        InstalledCapacity(zone="DE_LU", year=2025, psr_type="Wind Onshore", capacity_mw=60_000.0),
    ])
    db_session.commit()
    body = _client(db_session).get("/api/v1/capacity?zone=DE_LU").json()  # latest year
    assert body["available"] is True
    assert body["year"] == 2025
    assert body["total_mw"] == 110_000.0
    assert body["data"][0]["psr_type"] == "Wind Onshore"  # sorted desc by capacity


def test_capacity_endpoint_empty(db_session):
    body = _client(db_session).get("/api/v1/capacity?zone=FR").json()
    assert body["available"] is False


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


def test_rate_limit_covers_genmix_and_snapshot(db_session, monkeypatch):
    """The expensive aggregation endpoints share /series' per-IP budget — they
    were the unthrottled ones."""
    import backend.routes.api_v1 as v1
    from backend.auth.ratelimit import reset_limits

    reset_limits()
    monkeypatch.setattr(v1, "RATE_PER_MIN", 2)
    _seed(db_session)
    c = _client(db_session)
    assert c.get("/api/v1/genmix?zone=DE_LU").status_code == 200
    assert c.get("/api/v1/snapshot?series=load.actual").status_code == 200
    assert c.get("/api/v1/genmix?zone=DE_LU").status_code == 429
    reset_limits()


# ─── the published future, and the partial day ────────────────────────────────
#
# The desk showed DE-LU at 132.6 EUR/MWh in the Prices chart and 123.8 in the day-ahead panel,
# on the same day, for the same zone. Both read the same store. The difference: /api/v1/series
# defaulted `end` to NOW and so cut today's already-published day-ahead curve at the current
# hour — nine hours of it, all night ones — and then averaged the stump into a "daily" value and
# printed it as the card's latest price. A day-ahead auction clears the WHOLE delivery day at
# noon D-1; truncating it at wall-clock is not caution, it is a wrong number.


def _seed_published_day(db, *, hours=24, value=lambda h: 100.0 + h):
    """A full delivery day of day-ahead prices — including the hours still ahead of `now`."""
    day = int(datetime(2026, 6, 2, tzinfo=UTC).timestamp())
    upsert_hourly(db, "price.dayahead", "DE_LU",
                  [(day + h * _H, value(h)) for h in range(hours)], unit="EUR/MWh")


def test_the_published_future_is_not_truncated_at_now(db_session):
    """The auction for the CURRENT day cleared yesterday at noon: every hour of it exists, including
    the ones still ahead of the clock. With `end` defaulting to now, the desk got only the hours
    that had already elapsed — and charted their mean as the day's price."""
    now = datetime.now(UTC)
    midnight = int(now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
    upsert_hourly(db_session, "price.dayahead", "DE_LU",
                  [(midnight + h * _H, 100.0 + h) for h in range(24)], unit="EUR/MWh")

    body = _client(db_session).get(
        f"/api/v1/series?series=price.dayahead&zone=DE_LU&start={now:%Y-%m-%d}"
    ).json()

    assert body["count"] == 24, "the hours after now are published, not speculation"


def test_a_daily_point_says_how_many_hours_it_averaged(db_session):
    """The honest fix for the day that IS still filling in: the mean carries its own n."""
    _seed_published_day(db_session, hours=9)
    body = _client(db_session).get(
        "/api/v1/series?series=price.dayahead&zone=DE_LU&start=2026-06-02&resolution=daily"
    ).json()
    point = body["data"][0]
    assert point["hours"] == 9, "a 9-hour mean must not present itself as a day"
    assert point["value"] == pytest.approx(104.0)


def test_a_complete_day_says_24(db_session):
    _seed_published_day(db_session)
    body = _client(db_session).get(
        "/api/v1/series?series=price.dayahead&zone=DE_LU&start=2026-06-02&resolution=daily"
    ).json()
    assert body["data"][0]["hours"] == 24
    assert body["data"][0]["value"] == pytest.approx(111.5)


def test_daily_csv_carries_the_hour_count_too(db_session):
    _seed_published_day(db_session, hours=9)
    text = _client(db_session).get(
        "/api/v1/series?series=price.dayahead&zone=DE_LU&start=2026-06-02&resolution=daily&format=csv"
    ).text
    assert text.splitlines()[0] == "date,value,hours"
    assert text.splitlines()[1].endswith(",9")
