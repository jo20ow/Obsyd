"""GB via Elexon (backend/power/elexon.py): the parsers against captured
payload shapes, the fuel→PSR mapping's contract with the CO₂ factor table, the
zone-registry split that keeps GB out of every ENTSO-E iteration, and the
fetch→parse→upsert pipeline end to end (fetch monkeypatched — no network)."""
from __future__ import annotations

import pytest

from backend.models.energy import PowerGenMix, PowerGrid
from backend.power import elexon
from backend.power.elexon import (
    FUEL_TO_PSR,
    parse_demand,
    parse_fuelinst,
    parse_mid,
    parse_system_prices,
)
from backend.power.hourly_store import read_hourly

DAY = "2026-09-10"


# ─── parsers ──────────────────────────────────────────────────────────────────


def test_mid_is_volume_weighted_and_day_filtered():
    payload = {"data": [
        {"startTime": f"{DAY}T00:00:00Z", "price": 100.0, "volume": 1000.0},
        {"startTime": f"{DAY}T00:30:00Z", "price": 200.0, "volume": 3000.0},
        # neighbour day rows arrive in window queries — must be dropped
        {"startTime": "2026-09-11T00:00:00Z", "price": 999.0, "volume": 9999.0},
    ]}
    hourly, raw = parse_mid(payload, DAY)
    assert hourly == {0: (100 * 1000 + 200 * 3000) / 4000}  # 175, not the naive 150
    assert len(raw) == 2  # raw half-hours keep only the day's rows too


def test_demand_hourly_mean():
    payload = {"data": [
        {"startTime": f"{DAY}T05:00:00Z", "initialDemandOutturn": 20000},
        {"startTime": f"{DAY}T05:30:00Z", "initialDemandOutturn": 22000},
    ]}
    assert parse_demand(payload, DAY) == {5: 21000.0}


def test_system_prices_single_price():
    payload = {"data": [
        {"startTime": f"{DAY}T10:00:00Z", "systemSellPrice": 90.0},
        {"startTime": f"{DAY}T10:30:00Z", "systemSellPrice": 110.0},
    ]}
    hourly, raw = parse_system_prices(payload, DAY)
    assert hourly == {10: 100.0} and len(raw) == 2


def test_fuelinst_maps_sums_and_skips():
    rows = []
    # two 5-min ticks each for CCGT and OCGT in hour 12 — must SUM into B04
    for minute, ccgt, ocgt in ((0, 10000, 500), (5, 11000, 700)):
        rows.append({"startTime": f"{DAY}T12:{minute:02d}:00Z", "fuelType": "CCGT", "generation": ccgt})
        rows.append({"startTime": f"{DAY}T12:{minute:02d}:00Z", "fuelType": "OCGT", "generation": ocgt})
    rows.append({"startTime": f"{DAY}T12:00:00Z", "fuelType": "INTFR", "generation": 2000})   # flow, not gen
    rows.append({"startTime": f"{DAY}T12:00:00Z", "fuelType": "MYSTERY", "generation": 500})  # unmapped
    out = parse_fuelinst({"data": rows}, DAY)
    assert out["B04"] == {12: 10500.0 + 600.0}  # mean(CCGT) + mean(OCGT)
    assert "INTFR" not in str(out) and "MYSTERY" not in str(out)


# ─── contracts with the rest of the desk ─────────────────────────────────────


def test_every_mapped_fuel_is_priced_by_the_co2_engine():
    """GB's CO₂ intensity derives from these gen.* series — a fuel mapped to a
    PSR the factor table doesn't price (or deliberately exclude as storage)
    would silently poison the GB estimate."""
    from backend.power.co2 import EXCLUDED_STORAGE_PSRS, LIFECYCLE_G_PER_KWH

    for psr in set(FUEL_TO_PSR.values()):
        assert psr in LIFECYCLE_G_PER_KWH or psr in EXCLUDED_STORAGE_PSRS


def test_gb_is_registered_but_outside_the_entsoe_family():
    from backend.power.zones import ENTSOE_ZONES, ZONE_REGISTRY

    assert ZONE_REGISTRY["GB"]["eic"] is None
    assert ZONE_REGISTRY["GB"]["price_symbol"] is None  # no day-ahead feed exists
    assert "GB" not in ENTSOE_ZONES
    # and the subset rule really is "has an EIC", not a hand-kept list:
    assert all(v["eic"] for v in ENTSOE_ZONES.values())


def test_elexon_freshness_probe_exists_and_dayahead_probe_does_not():
    from backend.collectors.freshness import SPECS

    keys = {s.key for s in SPECS}
    assert "elexon_gb" in keys
    assert "power_dayahead:GB" not in keys  # would be born red — GB has no auction


# ─── pipeline ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ingest_writes_series_and_daily_rows(db_session, monkeypatch):
    async def fake_fetch_day(client, day, *, overwrite=False):
        hh = lambda h, m: f"{day}T{h:02d}:{m:02d}:00Z"  # noqa: E731
        return {
            "mid": {"data": [{"startTime": hh(h, m), "price": 150.0, "volume": 1000.0}
                             for h in range(24) for m in (0, 30)]},
            "demand": {"data": [{"startTime": hh(h, m), "initialDemandOutturn": 24000}
                                for h in range(24) for m in (0, 30)]},
            "sysprice": {"data": [{"startTime": hh(h, m), "systemSellPrice": 120.0}
                                  for h in range(24) for m in (0, 30)]},
            "fuelinst": {"data": [{"startTime": hh(h, 0), "fuelType": f, "generation": g}
                                  for h in range(24)
                                  for f, g in (("CCGT", 8000), ("WIND", 6000), ("NUCLEAR", 4000))]},
        }

    async def no_embedded(client, days, *, overwrite=False):
        return {}  # metered-only pass — the NESO fold has its own test below

    monkeypatch.setattr(elexon, "fetch_day", fake_fetch_day)
    import backend.power.neso as neso_mod
    monkeypatch.setattr(neso_mod, "fetch_embedded", no_embedded)
    result = await elexon.ingest_elexon(db_session, [DAY])
    assert result["daily_rows"] == 1

    assert len(read_hourly(db_session, "price.mid", "GB")) == 24
    assert len(read_hourly(db_session, "price.mid.hh", "GB")) == 48
    assert len(read_hourly(db_session, "load.actual", "GB")) == 24
    # residual = load − wind: 24000 − 6000, every hour
    assert {v for _, v in read_hourly(db_session, "residual.actual", "GB")} == {18000.0}

    grid = db_session.query(PowerGrid).filter_by(zone="GB", date=DAY).one()
    assert grid.load_mw == 24000.0 and grid.wind_mw == 6000.0
    mix = {m.psr_type: m.gen_mw for m in
           db_session.query(PowerGenMix).filter_by(zone="GB", date=DAY).all()}
    assert mix["Fossil Gas"] == 8000.0 and mix["Nuclear"] == 4000.0

    # idempotent: a second run overwrites in place, no duplicate daily rows
    await elexon.ingest_elexon(db_session, [DAY])
    assert db_session.query(PowerGrid).filter_by(zone="GB", date=DAY).count() == 1


@pytest.mark.asyncio
async def test_cache_keys_carry_the_day(monkeypatch):
    """The raw cache buckets by (source, key, MONTH): a day-less key hands every
    later day of the month day one's blob. Regression guard for the first GB
    backfill's one-real-day-per-month bug."""
    import httpx

    seen: list[str] = []

    async def fake_cache(source, key, dt, coro, *, overwrite=False):
        seen.append(key)
        return {"data": []}

    monkeypatch.setattr(elexon, "fetch_or_cache", fake_cache)
    async with httpx.AsyncClient() as client:
        await elexon.fetch_day(client, "2026-03-01")
        await elexon.fetch_day(client, "2026-03-02")
    assert len(seen) == 8 and len(set(seen)) == 8
    assert all("2026-03-0" in k for k in seen)


# ─── NESO embedded estimates (backend/power/neso.py) ─────────────────────────


def test_period_to_utc_handles_bst_and_dst_days():
    from backend.power.neso import period_to_utc

    # Winter (GMT): period 1 of Jan 15 starts at midnight UTC
    assert period_to_utc("2026-01-15", 1).isoformat() == "2026-01-15T00:00:00+00:00"
    # Summer (BST): period 1 of Jul 1 starts at 23:00 UTC the evening before
    assert period_to_utc("2026-07-01", 1).isoformat() == "2026-06-30T23:00:00+00:00"
    # DST-start day (2026-03-29, 46 periods): the day ENDS at 23:00 UTC —
    # period 46 is the last half hour, 22:30 UTC
    assert period_to_utc("2026-03-29", 46).isoformat() == "2026-03-29T22:30:00+00:00"


def test_parse_embedded_filters_forecasts_and_means_halves():
    from backend.power.neso import parse_embedded

    recs = [
        {"SETTLEMENT_DATE": "2026-01-15", "SETTLEMENT_PERIOD": 1,
         "EMBEDDED_WIND_GENERATION": 2000, "EMBEDDED_SOLAR_GENERATION": 0,
         "FORECAST_ACTUAL_INDICATOR": "A"},
        {"SETTLEMENT_DATE": "2026-01-15", "SETTLEMENT_PERIOD": 2,
         "EMBEDDED_WIND_GENERATION": 3000, "EMBEDDED_SOLAR_GENERATION": 0,
         "FORECAST_ACTUAL_INDICATOR": "A"},
        {"SETTLEMENT_DATE": "2026-01-15", "SETTLEMENT_PERIOD": 3,
         "EMBEDDED_WIND_GENERATION": 9999, "EMBEDDED_SOLAR_GENERATION": 9999,
         "FORECAST_ACTUAL_INDICATOR": "F"},  # forecast row — never an outturn
    ]
    out = parse_embedded(recs)
    assert out == {"2026-01-15": {0: {"wind": 2500.0, "solar": 0.0}}}


@pytest.mark.asyncio
async def test_ingest_folds_embedded_into_load_wind_and_solar(db_session, monkeypatch):
    """The single-writer contract: with NESO data present, load = INDO +
    embedded, B19 = metered + embedded wind, B16 = embedded solar — and the
    residual still equals INDO − metered wind (the terms cancel)."""
    async def fake_fetch_day(client, day, *, overwrite=False):
        hh = lambda h, m: f"{day}T{h:02d}:{m:02d}:00Z"  # noqa: E731
        return {
            "mid": {"data": []},
            "demand": {"data": [{"startTime": hh(h, 0), "initialDemandOutturn": 20000}
                                for h in range(24)]},
            "sysprice": {"data": []},
            "fuelinst": {"data": [{"startTime": hh(h, 0), "fuelType": "WIND", "generation": 5000}
                                  for h in range(24)]},
        }

    async def fake_embedded(client, days, *, overwrite=False):
        return {d: {h: {"wind": 1000.0, "solar": 3000.0} for h in range(24)} for d in days}

    monkeypatch.setattr(elexon, "fetch_day", fake_fetch_day)
    import backend.power.neso as neso_mod
    monkeypatch.setattr(neso_mod, "fetch_embedded", fake_embedded)
    await elexon.ingest_elexon(db_session, [DAY])

    assert {v for _, v in read_hourly(db_session, "load.actual", "GB")} == {24000.0}
    assert {v for _, v in read_hourly(db_session, "gen.B19", "GB")} == {6000.0}
    assert {v for _, v in read_hourly(db_session, "gen.B16", "GB")} == {3000.0}
    assert {v for _, v in read_hourly(db_session, "solar.embedded.est", "GB")} == {3000.0}
    # residual = 24000 − 6000 − 3000 = 15000 = INDO(20000) − metered wind(5000)
    assert {v for _, v in read_hourly(db_session, "residual.actual", "GB")} == {15000.0}


def test_normalize_date_handles_both_yearly_formats():
    from backend.power.neso import normalize_date

    assert normalize_date("01-JAN-2019") == "2019-01-01"
    assert normalize_date("15-DEC-2020") == "2020-12-15"
    assert normalize_date("2026-01-01") == "2026-01-01"
    assert normalize_date("2026-01-01T00:00:00") == "2026-01-01"


def test_parse_embedded_accepts_indicator_less_yearly_rows():
    """The old yearly files carry no FORECAST_ACTUAL_INDICATOR — absence must
    mean 'actual', not 'dropped'."""
    from backend.power.neso import parse_embedded

    out = parse_embedded([
        {"SETTLEMENT_DATE": "01-JAN-2019", "SETTLEMENT_PERIOD": 1,
         "EMBEDDED_WIND_GENERATION": 1200, "EMBEDDED_SOLAR_GENERATION": 0},
    ])
    assert out == {"2019-01-01": {0: {"wind": 1200.0, "solar": 0.0}}}
