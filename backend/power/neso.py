"""NESO embedded wind & solar estimates for GB — the half of the British fleet
Elexon cannot see.

FUELINST (backend/power/elexon.py) meters transmission-connected plant only;
GB's ~24 GW of distribution-connected solar and ~6 GW of embedded wind are
invisible to it, and INDO demand is already NET of what they generate. NESO —
the National Energy System Operator — publishes half-hourly ESTIMATES of that
embedded generation (their own model, licensed under the NESO Open Data
Licence: free reuse including commercial, with attribution).

These estimates are consumed by ingest_elexon, which is deliberately the ONE
writer of every GB series (two collectors upserting the same key would be
last-writer-wins, not a sum):

    load.actual (GB) := INDO + embedded wind + embedded solar
                        — true national demand, the A65-comparable quantity.
    gen.B19     (GB) := metered transmission wind + embedded wind estimate.
    gen.B16     (GB) := embedded solar estimate (this series exists for GB
                        only because of NESO — it is the freshness probe's
                        anchor via the transparency series below).
    residual.actual  := load − wind − solar = INDO − metered wind — provably
                        identical with or without the embedded terms (they are
                        added to both sides), so the residual's meaning never
                        flips with NESO availability.

Transparency series `wind.embedded.est` / `solar.embedded.est` carry the raw
estimates as their own keys: the reader can see exactly which part of GB's mix
is modelled rather than metered, and the freshness probe watches a series only
this feed writes.

Estimates, and labelled as such — the same honesty contract as co2.intensity.

Data plumbing: CKAN datastore SQL on data.neso.energy. History lives in
per-year "Historic Demand Data" resources; they trail real time by days to
weeks, so the rolling "Demand Data Update" resource (actuals rows only —
FORECAST_ACTUAL_INDICATOR = 'A'; it also carries 14 days of 'F' forecasts)
fills the recent tail. SETTLEMENT_DATE/PERIOD are Europe/London local
(period 1 = 00:00 local; DST days have 46/50 periods) — converted to UTC
hours here, once, correctly.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

from backend.gas.raw_cache import fetch_or_cache

logger = logging.getLogger(__name__)

SQL_URL = "https://api.neso.energy/api/3/action/datastore_search_sql"
ATTRIBUTION = "Supported by National Energy SO Open Data (NESO Open Data Licence)"

#: Per-year "Historic Demand Data" resources (data.neso.energy, dataset
#: historic-demand-data). 2019 onward — the backfill horizon.
YEARLY_RESOURCES: dict[int, str] = {
    2019: "dd9de980-d724-415a-b344-d8ae11321432",
    2020: "33ba6857-2a55-479f-9308-e5c4c53d4381",
    2021: "18c69c42-f20d-46f0-84e9-e279045befc6",
    2022: "bb44a1b5-75b1-4db2-8491-257f23385006",
    2023: "bf5ab335-9b40-4ea4-b93a-ab4af7bce003",
    2024: "f6d02c0f-957b-48cb-82ee-09003f2ba759",
    2025: "b2bde559-3455-4021-b179-dfe60c0337b0",
    2026: "8a4a771c-3929-4e56-93ad-cdf13219dea5",
}
#: Rolling "Demand Data Update": recent actuals + 14d forecasts.
UPDATE_RESOURCE = "177f6fa4-ae49-4182-81ea-0c6b35f26ca6"

_LONDON = ZoneInfo("Europe/London")


def _sql(resource: str, start_day: str, end_day: str) -> str:
    return (
        'SELECT "SETTLEMENT_DATE","SETTLEMENT_PERIOD","EMBEDDED_WIND_GENERATION",'
        '"EMBEDDED_SOLAR_GENERATION","FORECAST_ACTUAL_INDICATOR" '
        f'FROM "{resource}" '
        f"WHERE \"SETTLEMENT_DATE\" >= '{start_day}' AND \"SETTLEMENT_DATE\" <= '{end_day}'"
    )


async def _query(client: httpx.AsyncClient, resource: str, start_day: str, end_day: str) -> list[dict]:
    r = await client.get(SQL_URL, params={"sql": _sql(resource, start_day, end_day)}, timeout=60)
    r.raise_for_status()
    payload = r.json()
    if not payload.get("success"):
        raise RuntimeError(f"NESO datastore error: {payload.get('error')}")
    return payload["result"]["records"]


def period_to_utc(settlement_date: str, period: int) -> datetime:
    """Settlement (local-London date, half-hour period 1-48/46/50) → UTC start.
    Local midnight plus (p−1)·30min in WALL-CLOCK terms is wrong on DST days;
    adding on the UTC timeline after converting midnight is exact for both the
    46- and 50-period days."""
    local_midnight = datetime.fromisoformat(settlement_date[:10]).replace(tzinfo=_LONDON)
    return local_midnight.astimezone(UTC) + timedelta(minutes=30 * (period - 1))


def parse_embedded(records: list[dict]) -> dict[str, dict[int, dict[str, float]]]:
    """Actuals only → {utc_day: {hour: {"wind": MW, "solar": MW}}} (hourly means
    of the half-hour estimates)."""
    acc: dict[tuple[str, int], list[tuple[float, float]]] = defaultdict(list)
    for row in records:
        if row.get("FORECAST_ACTUAL_INDICATOR", "A") != "A":
            continue
        w, s_ = row.get("EMBEDDED_WIND_GENERATION"), row.get("EMBEDDED_SOLAR_GENERATION")
        if w is None and s_ is None:
            continue
        ts = period_to_utc(str(row["SETTLEMENT_DATE"]), int(row["SETTLEMENT_PERIOD"]))
        acc[(ts.strftime("%Y-%m-%d"), ts.hour)].append((float(w or 0), float(s_ or 0)))
    out: dict[str, dict[int, dict[str, float]]] = defaultdict(dict)
    for (day, hour), vals in acc.items():
        out[day][hour] = {
            "wind": sum(v[0] for v in vals) / len(vals),
            "solar": sum(v[1] for v in vals) / len(vals),
        }
    return dict(out)


async def fetch_embedded(client: httpx.AsyncClient, days: list[str], *, overwrite: bool = False) -> dict:
    """{utc_day: {hour: {"wind","solar"}}} for the requested UTC days.

    Yearly resources first (settled history), the rolling update resource for
    whatever they don't cover yet. Queried with a one-day margin on each side:
    a UTC day borrows up to two local periods from the neighbouring settlement
    days. Cached per month like every other raw payload — the cache key carries
    the resource AND month (the elexon cache-key lesson, applied on arrival)."""
    if not days:
        return {}
    lo = (date.fromisoformat(min(days)) - timedelta(days=1)).isoformat()
    hi = (date.fromisoformat(max(days)) + timedelta(days=1)).isoformat()

    records: list[dict] = []
    seen_days: set[str] = set()
    for year in sorted({int(d[:4]) for d in (lo, hi)} | {int(d[:4]) for d in days}):
        resource = YEARLY_RESOURCES.get(year)
        if not resource:
            continue

        async def q(res=resource, a=max(lo, f"{year}-01-01"), b=min(hi, f"{year}-12-31")):
            return {"records": await _query(client, res, a, b)}

        got = await fetch_or_cache(
            "neso", f"embedded-{year}-{lo}-{hi}", date.fromisoformat(min(days)), q,
            overwrite=overwrite,
        )
        records.extend(got["records"])
    seen_days = {str(r["SETTLEMENT_DATE"])[:10] for r in records}

    # Recent tail the settled files don't carry yet → the rolling update feed.
    missing = [d for d in days if d not in seen_days]
    if missing:
        async def qu():
            return {"records": await _query(client, UPDATE_RESOURCE, min(missing), hi)}

        got = await fetch_or_cache(
            "neso", f"embedded-upd-{min(missing)}-{hi}", date.fromisoformat(min(missing)), qu,
            overwrite=overwrite,
        )
        records.extend(got["records"])

    parsed = parse_embedded(records)
    return {d: parsed[d] for d in parsed if d in set(days)}
