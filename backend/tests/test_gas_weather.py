"""Open-Meteo HDD ingestion tests."""

from __future__ import annotations

from backend.gas import weather
from backend.models.gas import GasWeather


def test_hdd_formula():
    assert weather.hdd(5.5) == 10.0          # 15.5 - 5.5
    assert weather.hdd(15.5) == 0.0
    assert weather.hdd(20.0) == 0.0          # warm → no heating
    assert weather.hdd(-4.5) == 20.0


async def test_ingest_country_population_weights(db_session, monkeypatch):
    # Two-city basket with weights 3 and 1 → weighted mean temp = (3*0 + 1*8)/4 = 2.0
    monkeypatch.setattr(weather, "CITY_BASKETS", {"XX": [(1.0, 1.0, 3.0), (2.0, 2.0, 1.0)]})
    temps = {(1.0, 1.0): {"2026-01-01": 0.0}, (2.0, 2.0): {"2026-01-01": 8.0}}

    async def fake(lat, lon, start, end, *, overwrite=False):
        return temps[(lat, lon)]

    monkeypatch.setattr(weather, "fetch_city_temps", fake)
    await weather.ingest_country(db_session, "XX", "2026-01-01", "2026-01-01")
    row = db_session.get(GasWeather, ("2026-01-01", "XX"))
    assert row.t_mean == 2.0
    assert row.hdd == 13.5  # 15.5 - 2.0


async def test_ingest_country_is_idempotent(db_session, monkeypatch):
    monkeypatch.setattr(weather, "CITY_BASKETS", {"XX": [(1.0, 1.0, 1.0)]})

    async def fake(lat, lon, start, end, *, overwrite=False):
        return {"2026-01-01": 0.0}

    monkeypatch.setattr(weather, "fetch_city_temps", fake)
    await weather.ingest_country(db_session, "XX", "2026-01-01", "2026-01-01")
    await weather.ingest_country(db_session, "XX", "2026-01-01", "2026-01-01")
    assert db_session.query(GasWeather).count() == 1
