"""A71 day-ahead TOTAL generation forecast → hourly series generation.forecast.

Same GL_MarketDocument shape as A65 (quantity points, no psrType), so the load
parsers do the work; only the document type, domain param and cache source
differ. Feeds the forecast-vs-actual view (roadmap Block 4.3).
"""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from backend.power import entsoe_grid as grid_mod
from backend.power.hourly_store import read_hourly
from backend.tests.test_power_grid import _a65, _load_ts


def _epoch(iso: str) -> int:
    from datetime import datetime, timezone

    return int(datetime.fromisoformat(iso).replace(tzinfo=timezone.utc).timestamp())


async def test_ingest_generation_forecast_writes_hourly_series(db_session, monkeypatch):
    seen = {}

    async def fake_fetch(eic, month_start, doctype, extra_params, *, overwrite=False, cache_source=None):
        seen.update(doctype=doctype, params=extra_params, cache_source=cache_source)
        return _a65(_load_ts("2026-06-01T00:00Z", 55_000.0, n=24))

    monkeypatch.setattr(grid_mod, "_fetch_zone_month", fake_fetch)
    monkeypatch.setattr(grid_mod.settings, "entsoe_api_token", SecretStr("tok"))

    r = await grid_mod.ingest_generation_forecast(db_session, ["2026-06-01"], zone="DE_LU")

    assert r == {"days": 1, "written": 24}
    assert seen["doctype"] == "A71"
    assert seen["params"]["processType"] == "A01"
    assert "in_Domain" in seen["params"]
    assert seen["cache_source"] == "entsoe_gen_total_forecast", (
        "A71 must not collide with the A65/A69 caches"
    )

    series = read_hourly(db_session, "generation.forecast", "DE_LU")
    assert len(series) == 24
    assert series[0] == (_epoch("2026-06-01T00:00"), 55_000.0)


async def test_ingest_generation_forecast_respects_wanted_days(db_session, monkeypatch):
    async def fake_fetch(eic, month_start, doctype, extra_params, *, overwrite=False, cache_source=None):
        return _a65(
            _load_ts("2026-06-01T00:00Z", 50_000.0, n=24)
            + _load_ts("2026-06-02T00:00Z", 60_000.0, n=24)
        )

    monkeypatch.setattr(grid_mod, "_fetch_zone_month", fake_fetch)
    monkeypatch.setattr(grid_mod.settings, "entsoe_api_token", SecretStr("tok"))

    await grid_mod.ingest_generation_forecast(db_session, ["2026-06-02"], zone="DE_LU")

    series = read_hourly(db_session, "generation.forecast", "DE_LU")
    assert len(series) == 24
    assert all(v == 60_000.0 for _, v in series)


async def test_ingest_generation_forecast_skips_without_token(db_session, monkeypatch):
    monkeypatch.setattr(grid_mod.settings, "entsoe_api_token", None)
    r = await grid_mod.ingest_generation_forecast(db_session, ["2026-06-01"])
    assert r == {"skipped": "no token"}
