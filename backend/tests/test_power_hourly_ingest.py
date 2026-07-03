"""Block 1: the ingest functions persist hourly series into power_hourly."""
from __future__ import annotations

from backend.models.energy import PowerGrid, PowerPriceDaily  # noqa: F401 — register tables
from backend.power import entsoe_grid as grid_mod
from backend.power import entsoe_prices as price_mod
from backend.power.hourly_store import read_hourly
from backend.tests.test_power_grid import _a65, _a75_gen, _gen_ts, _load_ts
from backend.tests.test_power_prices import _a44, _ts

DAY = "2026-04-01"


async def test_ingest_grid_writes_hourly_actuals(db_session, monkeypatch):
    async def fake_fetch(eic, month_start, doctype, extra_params, **kw):
        if doctype == "A65":
            return _a65(_load_ts(f"{DAY}T00:00Z", 60_000.0))
        return _a75_gen(  # A75
            _gen_ts("B16", f"{DAY}T00:00Z", 10_000.0)   # solar
            + _gen_ts("B19", f"{DAY}T00:00Z", 20_000.0)  # wind onshore
        )

    monkeypatch.setattr(grid_mod.settings, "entsoe_api_token", "x")
    monkeypatch.setattr(grid_mod, "_fetch_zone_month", fake_fetch)

    await grid_mod.ingest_grid(db_session, [DAY], zone="DE_LU", overwrite=True)

    load = read_hourly(db_session, "load.actual", "DE_LU")
    assert len(load) == 24
    assert load[0][1] == 60_000.0
    assert len(read_hourly(db_session, "gen.B16", "DE_LU")) == 24   # solar series
    assert read_hourly(db_session, "gen.B19", "DE_LU")[0][1] == 20_000.0
    resid = read_hourly(db_session, "residual.actual", "DE_LU")
    assert len(resid) == 24
    assert resid[0][1] == 30_000.0  # 60000 - 20000 - 10000


async def test_ingest_day_ahead_writes_hourly_price(db_session, monkeypatch):
    prices = [40.0 + i for i in range(24)]

    async def fake_fetch(eic, month_start, **kw):
        return _a44(_ts(f"{DAY}T00:00Z", "2026-04-02T00:00Z", prices))

    monkeypatch.setattr(price_mod.settings, "entsoe_api_token", "x")
    monkeypatch.setattr(price_mod, "_fetch_zone_month", fake_fetch)

    await price_mod.ingest_day_ahead(db_session, [DAY], zone="DE_LU", overwrite=True)

    series = read_hourly(db_session, "price.dayahead", "DE_LU")
    assert len(series) == 24
    assert series[0][1] == 40.0
    assert series[-1][1] == 63.0


async def test_ingest_load_forecast_writes_hourly_forecast_series(db_session, monkeypatch):
    async def fake_fetch(eic, month_start, doctype, extra_params, **kw):
        if doctype == "A65":
            return _a65(_load_ts(f"{DAY}T00:00Z", 55_000.0))
        return _a75_gen(  # A69 uses the same generation parser
            _gen_ts("B16", f"{DAY}T00:00Z", 8_000.0)
            + _gen_ts("B19", f"{DAY}T00:00Z", 12_000.0)
        )

    monkeypatch.setattr(grid_mod.settings, "entsoe_api_token", "x")
    monkeypatch.setattr(grid_mod, "_fetch_zone_month", fake_fetch)

    await grid_mod.ingest_load_forecast(db_session, [DAY], zone="DE_LU", overwrite=True)

    assert len(read_hourly(db_session, "load.forecast", "DE_LU")) == 24
    assert read_hourly(db_session, "wind.forecast", "DE_LU")[0][1] == 12_000.0
    resid = read_hourly(db_session, "residual.forecast", "DE_LU")
    assert resid[0][1] == 35_000.0  # 55000 - 12000 - 8000
