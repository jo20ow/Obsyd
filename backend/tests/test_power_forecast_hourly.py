"""Hourly day-ahead residual-load forecast (D+1): parse → build → persist → expose.

The daily-mean forecast already exists (test_power_forecast.py). This adds the
hour-by-hour shape for tomorrow — the price-driving forward curve (evening ramp,
midday solar trough, Dunkelflaute windows).
"""
from __future__ import annotations

import json
from datetime import date, timedelta

from fastapi.testclient import TestClient

from backend.main import app
from backend.models.energy import PowerLoadForecast
from backend.power import entsoe_grid as grid_mod
from backend.power.entsoe_grid import (
    build_hourly_forecast,
    parse_generation_hourly,
    parse_load_hourly,
)

# Reuse the GL_MarketDocument builders from the grid parser tests.
from backend.tests.test_power_grid import _a65, _a75_gen, _gen_ts, _load_ts

# ── parse_load_hourly ─────────────────────────────────────────────────────────


def test_parse_load_hourly_returns_24_hours():
    xml = _a65(_load_ts("2026-04-01T00:00Z", 50_000.0, n=24))
    result = parse_load_hourly(xml)
    assert set(result) == {"2026-04-01"}
    hours = result["2026-04-01"]
    assert len(hours) == 24
    assert hours[0] == 50_000.0
    assert hours[23] == 50_000.0


def test_parse_load_hourly_aggregates_quarter_hours():
    # PT15M: 96 quarter-hour slots collapse to 24 hourly means.
    xml = _a65(_load_ts("2026-04-01T00:00Z", 42_000.0, n=96, res="PT15M"))
    hours = parse_load_hourly(xml)["2026-04-01"]
    assert len(hours) == 24
    assert hours[0] == 42_000.0


# ── parse_generation_hourly ───────────────────────────────────────────────────


def test_parse_generation_hourly_keys_by_psr_and_hour():
    xml = _a75_gen(
        _gen_ts("B16", "2026-04-01T00:00Z", 8_000.0)   # solar
        + _gen_ts("B19", "2026-04-01T00:00Z", 15_000.0)  # wind onshore
    )
    result = parse_generation_hourly(xml)["2026-04-01"]
    assert result["B16"][12] == 8_000.0
    assert result["B19"][0] == 15_000.0
    assert len(result["B16"]) == 24


# ── build_hourly_forecast (pure residual combiner) ────────────────────────────


def test_build_hourly_forecast_computes_residual_per_hour():
    load = {h: 60_000.0 for h in range(24)}
    gen = {
        "B16": {h: 10_000.0 for h in range(24)},  # solar
        "B18": {h: 5_000.0 for h in range(24)},   # wind offshore
        "B19": {h: 20_000.0 for h in range(24)},  # wind onshore
    }
    series = build_hourly_forecast(load, gen)
    assert len(series) == 24
    assert series[0] == {
        "hour": 0,
        "load_mw": 60_000.0,
        "wind_mw": 25_000.0,   # 5000 + 20000
        "solar_mw": 10_000.0,
        "residual_mw": 25_000.0,  # 60000 - 25000 - 10000
    }
    assert [p["hour"] for p in series] == list(range(24))


def test_build_hourly_forecast_residual_none_without_renewables():
    load = {h: 60_000.0 for h in range(24)}
    series = build_hourly_forecast(load, {})  # no wind/solar
    assert series[0]["residual_mw"] is None
    assert series[0]["wind_mw"] is None
    assert series[0]["solar_mw"] is None
    assert series[0]["load_mw"] == 60_000.0


# ── ingest persists the hourly series (JSON-in-Text) ──────────────────────────


async def test_ingest_persists_hourly_forecast(db_session, monkeypatch):
    """ingest_load_forecast stores the 24h series on PowerLoadForecast.hourly_forecast."""
    day = "2026-04-01"

    async def fake_fetch(eic, month_start, doctype, extra_params, **kw):
        if doctype == "A65":
            return _a65(_load_ts(f"{day}T00:00Z", 60_000.0))
        return _a75_gen(
            _gen_ts("B16", f"{day}T00:00Z", 10_000.0)
            + _gen_ts("B19", f"{day}T00:00Z", 20_000.0)
        )

    monkeypatch.setattr(grid_mod.settings, "entsoe_api_token", "x")
    monkeypatch.setattr(grid_mod, "_fetch_zone_month", fake_fetch)

    await grid_mod.ingest_load_forecast(db_session, [day], zone="DE_LU", overwrite=True)

    row = db_session.query(PowerLoadForecast).filter_by(date=day, zone="DE_LU").first()
    assert row is not None
    assert row.hourly_forecast is not None
    series = json.loads(row.hourly_forecast)
    assert len(series) == 24
    assert series[0]["load_mw"] == 60_000.0
    assert series[0]["residual_mw"] == 30_000.0  # 60000 - 20000 - 10000


# ── endpoint ──────────────────────────────────────────────────────────────────


def test_load_forecast_hourly_endpoint_returns_tomorrow(db_session):
    tom = (date.today() + timedelta(days=1)).isoformat()
    series = [
        {"hour": h, "load_mw": 55_000.0, "wind_mw": 12_000.0,
         "solar_mw": 8_000.0, "residual_mw": 35_000.0}
        for h in range(24)
    ]
    db_session.add(PowerLoadForecast(
        date=tom, zone="DE_LU", forecast_mw=55_000.0,
        wind_forecast_mw=12_000.0, solar_forecast_mw=8_000.0,
        hourly_forecast=json.dumps(series),
    ))
    db_session.commit()

    body = TestClient(app).get("/api/power/load-forecast/hourly?zone=DE_LU").json()
    assert body["available"] is True
    assert body["date"] == tom
    assert body["unit"] == "MW"
    assert len(body["data"]) == 24
    assert body["data"][0]["residual_mw"] == 35_000.0


def test_load_forecast_hourly_unavailable_when_empty(db_session):
    body = TestClient(app).get("/api/power/load-forecast/hourly?zone=DE_LU").json()
    assert body["available"] is False
