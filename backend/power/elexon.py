"""Great Britain via Elexon's Insights Solution API — the desk's first
non-ENTSO-E zone.

GB left ENTSO-E's publications post-Brexit (A44 answers an Acknowledgement),
so the zone rides Elexon/BMRS instead: keyless public JSON, and a licence that
is EXPLICITLY redistribution-friendly ("copy, publish, distribute and transmit
the BMRS Data, including commercially") with one obligation — the attribution
string below must travel with the data.

    ATTRIBUTION: "Contains BMRS data © Elexon Limited copyright and database
    right" — surfaced in /api/v1/meta's attribution field and docs/API.md.

WHAT MAPS, AND WHAT HONESTLY CANNOT
-----------------------------------
  * load.actual        ← demand outturn INDO (Initial National Demand Outturn),
                         half-hourly → hourly mean. INDO is national demand as
                         GB defines it; like every zone's A65 it inherits its
                         TSO's demand definition, no adjustment is invented.
  * gen.<PSR>          ← FUELINST (5-min MW by fuel type) → hourly means, fuel
                         types mapped onto ENTSO-E PSR codes (table below) so
                         the mix panel, the coverage math and the CO₂-intensity
                         engine work for GB unchanged. TRANSMISSION-CONNECTED
                         only: embedded solar/wind are invisible to FUELINST —
                         gen.B16 does not exist for GB, and the CO₂ estimate is
                         documented as overstated at sunny middays until the
                         NESO embedded-estimate follow-up lands.
  * imbalance.price    ← settlement system price (single price since P305),
                         half-hourly → hourly mean; raw halves as
                         imbalance.price.hh (the .qh precedent, GB's cadence).
  * price.mid[.hh]     ← Market Index Data (APX provider): the volume-weighted
                         price of GB short-term trades, half-hourly. This is
                         DELIBERATELY NOT stored as price.dayahead: GB's
                         day-ahead auctions (N2EX/EPEX) are licensed and not
                         redistributable, and dressing MID up as an auction
                         price would be a lie — the desk shows GB's day-ahead
                         as structurally absent instead (the Spark-FR
                         signposting precedent).
  * residual.actual    ← load − wind (solar leg absent ⇒ 0), the
                         rebuild_residual_actual rule.
  * PowerGrid / PowerGenMix daily rows ← the same daily_from_hours derivation
                         entsoe_grid uses, so the EUROPE matrix, situation
                         header and mix panels serve GB with no special case.

FUELINST fuel type → PSR:
    BIOMASS→B01 · CCGT+OCGT→B04 (summed; ENTSO-E's own B04 cannot split them
    either) · COAL→B05 · OIL→B06 · NPSHYD→B11 · NUCLEAR→B14 · OTHER→B20 ·
    PS→B10 (storage — the CO₂ engine excludes it by design) · WIND→B19
    (FUELINST does not split on/offshore; the 11-vs-12 g/kWh factor difference
    is noise, but the mix label reads "Wind Onshore" for what is largely
    offshore metal — documented, not hidden).
    INT* rows are interconnector flows, not generation — skipped (country-level
    GB flows already exist as flow.GB via Energy-Charts).

Every payload goes through the raw cache (source "elexon", one blob per
day×dataset), and every write is the standard upsert — re-ingest is overwrite,
Elexon's own restatements land in the revision ledger like anyone else's.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta

import httpx
from sqlalchemy.orm import Session

from backend.gas.raw_cache import fetch_or_cache
from backend.power.daily import daily_from_hours, days_to_derive
from backend.power.entsoe_grid import _upsert_generation_mix, _upsert_grid
from backend.power.hourly_store import upsert_day_hours, upsert_hourly

logger = logging.getLogger(__name__)

BASE = "https://data.elexon.co.uk/bmrs/api/v1"
ZONE = "GB"
ATTRIBUTION = "Contains BMRS data © Elexon Limited copyright and database right"

#: FUELINST fuelType → ENTSO-E PSR code (see module docstring for each call).
FUEL_TO_PSR: dict[str, str] = {
    "BIOMASS": "B01",
    "CCGT": "B04",
    "OCGT": "B04",
    "COAL": "B05",
    "OIL": "B06",
    "NPSHYD": "B11",
    "NUCLEAR": "B14",
    "OTHER": "B20",
    "PS": "B10",
    "WIND": "B19",
}


async def _get_json(client: httpx.AsyncClient, path: str, **params) -> dict:
    r = await client.get(f"{BASE}{path}", params=params)
    r.raise_for_status()
    return r.json()


def _hour_of(iso_ts: str, day: str) -> int | None:
    """UTC hour 0-23 of an Elexon startTime, or None if it is not on `day`
    (window queries return neighbours; settlement 'days' are UTC here)."""
    dt = datetime.fromisoformat(iso_ts.replace("Z", "+00:00")).astimezone(UTC)
    return dt.hour if dt.strftime("%Y-%m-%d") == day else None


def _half_hour_ts(iso_ts: str) -> int:
    return int(datetime.fromisoformat(iso_ts.replace("Z", "+00:00")).timestamp())


async def fetch_day(client: httpx.AsyncClient, day: str, *, overwrite: bool = False) -> dict:
    """All four raw payloads for one UTC day, through the raw cache."""
    d = date.fromisoformat(day)
    nxt = (d + timedelta(days=1)).isoformat()

    async def mid():
        return await _get_json(client, "/balancing/pricing/market-index",
                               **{"from": f"{day}T00:00Z", "to": f"{nxt}T00:00Z",
                                  "dataProviders": "APXMIDP"})

    async def demand():
        return await _get_json(client, "/demand/outturn",
                               settlementDateFrom=day, settlementDateTo=day)

    async def sysprice():
        return await _get_json(client, f"/balancing/settlement/system-prices/{day}")

    async def fuelinst():
        # publishTime trails startTime by ~5 min; the parser re-filters by
        # startTime day, so the +1h tail costs nothing and loses nothing.
        return await _get_json(client, "/datasets/FUELINST",
                               publishDateTimeFrom=f"{day}T00:00Z",
                               publishDateTimeTo=f"{nxt}T01:00Z")

    # The raw cache buckets by (source, key, MONTH) — the key must carry the
    # DAY or every day of a month reads day one's blob (the bug the first GB
    # backfill shipped with: one real day per month, silently).
    return {
        "mid": await fetch_or_cache("elexon", f"mid-{day}", d, mid, overwrite=overwrite),
        "demand": await fetch_or_cache("elexon", f"demand-{day}", d, demand, overwrite=overwrite),
        "sysprice": await fetch_or_cache("elexon", f"sysprice-{day}", d, sysprice, overwrite=overwrite),
        "fuelinst": await fetch_or_cache("elexon", f"fuelinst-{day}", d, fuelinst, overwrite=overwrite),
    }


# ── parsers: raw payload → {hour: value} / half-hour points, one UTC day ─────


def parse_mid(payload: dict, day: str) -> tuple[dict[int, float], list[tuple[int, float]]]:
    """(hourly volume-weighted mean, raw half-hour points). Volume-weighted
    because that is what MID is — averaging two halves ignoring volume would
    quietly re-derive a different index."""
    by_hour: dict[int, list[tuple[float, float]]] = defaultdict(list)
    raw: list[tuple[int, float]] = []
    for row in payload.get("data", []):
        h = _hour_of(row["startTime"], day)
        if h is None or row.get("price") is None:
            continue
        vol = row.get("volume") or 0.0
        by_hour[h].append((row["price"], vol))
        raw.append((_half_hour_ts(row["startTime"]), row["price"]))
    hourly = {}
    for h, pairs in by_hour.items():
        wsum = sum(v for _, v in pairs)
        hourly[h] = (sum(p * v for p, v in pairs) / wsum) if wsum > 0 else sum(p for p, _ in pairs) / len(pairs)
    return hourly, raw


def parse_demand(payload: dict, day: str) -> dict[int, float]:
    by_hour: dict[int, list[float]] = defaultdict(list)
    for row in payload.get("data", []):
        h = _hour_of(row["startTime"], day)
        v = row.get("initialDemandOutturn")
        if h is not None and v is not None:
            by_hour[h].append(float(v))
    return {h: sum(vs) / len(vs) for h, vs in by_hour.items()}


def parse_system_prices(payload: dict, day: str) -> tuple[dict[int, float], list[tuple[int, float]]]:
    by_hour: dict[int, list[float]] = defaultdict(list)
    raw: list[tuple[int, float]] = []
    for row in payload.get("data", []):
        h = _hour_of(row["startTime"], day)
        v = row.get("systemSellPrice")  # single-price settlement: sell == buy
        if h is not None and v is not None:
            by_hour[h].append(float(v))
            raw.append((_half_hour_ts(row["startTime"]), float(v)))
    return {h: sum(vs) / len(vs) for h, vs in by_hour.items()}, raw


def parse_fuelinst(payload: dict, day: str) -> dict[str, dict[int, float]]:
    """{PSR: {hour: mean MW}} — 5-min instantaneous readings averaged per hour,
    CCGT+OCGT summed into B04 AFTER averaging each fuel separately (their 5-min
    grids are aligned, but summing means is order-safe even if one drops a tick)."""
    per_fuel: dict[str, dict[int, list[float]]] = defaultdict(lambda: defaultdict(list))
    unknown: set[str] = set()
    for row in payload.get("data", []):
        fuel = row.get("fuelType", "")
        if fuel.startswith("INT"):
            continue  # interconnector flow, not generation
        h = _hour_of(row["startTime"], day)
        v = row.get("generation")
        if h is None or v is None:
            continue
        if fuel not in FUEL_TO_PSR:
            unknown.add(fuel)
            continue
        per_fuel[fuel][h].append(float(v))
    if unknown:
        logger.warning("elexon FUELINST: unmapped fuel types skipped: %s", sorted(unknown))
    out: dict[str, dict[int, float]] = defaultdict(dict)
    for fuel, hours in per_fuel.items():
        psr = FUEL_TO_PSR[fuel]
        for h, vs in hours.items():
            out[psr][h] = out[psr].get(h, 0.0) + sum(vs) / len(vs)
    return dict(out)


# ── ingest ────────────────────────────────────────────────────────────────────


async def ingest_elexon(db: Session, days: list[str], *, overwrite: bool = False) -> dict:
    """Fetch + parse + upsert every GB series for the given UTC days, plus the
    PowerGrid/PowerGenMix daily rows for the finished ones. Idempotent."""
    written = {"hours": 0, "daily_rows": 0}
    load_by_day: dict[str, dict[int, float]] = {}
    gen_by_day: dict[str, dict[str, dict[int, float]]] = {}

    async with httpx.AsyncClient(timeout=90) as client:
        for day in days:
            try:
                raw = await fetch_day(client, day, overwrite=overwrite)
            except httpx.HTTPError as exc:
                logger.error("elexon fetch %s failed: %s", day, exc)
                continue

            mid_hourly, mid_raw = parse_mid(raw["mid"], day)
            demand_hourly = parse_demand(raw["demand"], day)
            imb_hourly, imb_raw = parse_system_prices(raw["sysprice"], day)
            gen = parse_fuelinst(raw["fuelinst"], day)

            if mid_hourly:
                written["hours"] += upsert_day_hours(db, "price.mid", ZONE, {day: mid_hourly}, unit="GBP/MWh")
            if mid_raw:
                upsert_hourly(db, "price.mid.hh", ZONE, mid_raw, unit="GBP/MWh")
            if demand_hourly:
                written["hours"] += upsert_day_hours(db, "load.actual", ZONE, {day: demand_hourly}, unit="MW")
            if imb_hourly:
                upsert_day_hours(db, "imbalance.price", ZONE, {day: imb_hourly}, unit="GBP/MWh")
            if imb_raw:
                upsert_hourly(db, "imbalance.price.hh", ZONE, imb_raw, unit="GBP/MWh")
            for psr, hours in gen.items():
                upsert_day_hours(db, f"gen.{psr}", ZONE, {day: hours}, unit="MW")
            # residual = load − wind − solar; GB's solar leg is structurally
            # absent (embedded) ⇒ 0, the rebuild_residual_actual rule.
            wind = gen.get("B19", {})
            resid = {h: v - wind.get(h, 0.0) for h, v in demand_hourly.items()}
            if resid:
                upsert_day_hours(db, "residual.actual", ZONE, {day: resid}, unit="MW")

            load_by_day[day] = demand_hourly
            gen_by_day[day] = gen

    # Daily rows for finished days only — a day that is not over is not a day.
    for day in days_to_derive(set(days) & (load_by_day.keys() | gen_by_day.keys())):
        # A day whose parses all came back empty is not a day — writing a
        # PowerGrid row of Nones for it would pollute the daily history.
        if not load_by_day.get(day) and not gen_by_day.get(day):
            continue
        # B10 (PS generation) STAYS in the daily mix — entsoe_grid keeps it for
        # every other zone too (only CONSUMPTION keys are excluded there); the
        # CO₂ engine does its own storage exclusion downstream.
        row = daily_from_hours(load_by_day.get(day, {}), gen_by_day.get(day, {}))
        _upsert_grid(db, day, ZONE, row)
        _upsert_generation_mix(db, day, ZONE, row["mix"])
        written["daily_rows"] += 1
    db.commit()
    logger.info("elexon ingest: %s", written)
    return written
