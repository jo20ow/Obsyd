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

    monkeypatch.setattr(elexon, "fetch_day", fake_fetch_day)
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
