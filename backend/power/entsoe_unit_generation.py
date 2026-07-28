"""Actual generation per generation UNIT (ENTSO-E A73/A16) → `unit_generation`.

WHAT A73 IS, AND IS NOT (probe 2026-07-28, backend/scripts/probe_entsoe.py --doctype a73)
----------------------------------------------------------------------------------------
Hourly-ish output per named plant — the drill-down behind the zone-level gen.<PSR>
mix. But only for the PUBLISHED population: ENTSO-E's ~100 MW threshold, and only
dispatchable fuels (probe psrTypes: B02/B03/B04/B05/B06/B10/B11/B12/B17 — no wind,
no solar, no nuclear for the German control areas). 85 units answered across the
four German CTAs against 133 in the A71/A33 registry. This is NOT the fleet, and
output ÷ nameplate is UTILIZATION, not availability (A77 says what is unavailable).

DE-LU = FOUR CONTROL AREAS, NOT THE BIDDING ZONE. The DE-LU BZN EIC
(10Y1001A1001A82H) answers "No matching data found"; only the CTA EICs deliver —
50Hertz 18 units, Amprion 35, TenneT 19, TransnetBW 13 (probe counts). A73_ZONES
maps each ingest zone to its answering domain list; to extend to another zone,
re-run the probe against its BZN EIC first and fall back to its CTAs if silent:

    .venv/bin/python -m backend.scripts.probe_entsoe --doctype a73

PUBLICATION LAG ~6 DAYS. D-1 and D-3 answer a clean 200-Acknowledgement, D-7
delivers fully. The scheduler therefore re-fetches a rolling window with
overwrite=True so a day that was empty at first pass fills in later — the
write-once cache must never freeze the still-filling frontier. The product must
say "latest published day", never "live".

THE "NO DATA" SHAPE IS A 200-ACK, NOT A 400. Unlike A09/A61 (whose emptiness
arrives as HTTP 400 + Acknowledgement), A73 answers HTTP 200 carrying an
Acknowledgement_MarketDocument. So: a 200-ACK is genuine emptiness and is CACHED;
any >= 400 raises and caches NOTHING (a parameter bug must not become permanent
emptiness on disk).

WINDOWING: an 8-day window is accepted (probe: 530 KB, no 1-day limit), there is
NO pagination (explicit offset=0/100 return the identical document), no ZIP.
Fetched in 7-day chunks. Resolution is mostly PT60M with some PT15M; several
TimeSeries can cover the same unit (68 TS / 35 units at Amprion) — per-TS hourly
means are averaged per unit-hour.

CURVETYPE A03 — THE SMOKE'S DISCOVERY (2026-07-28, live cached documents).
The probe counted points; the smoke read them. Every one of the 151 TimeSeries
across the four CTAs is curveType A03 — a STEP FUNCTION: a Period spans the whole
requested window, a point is published only where the value CHANGES (27/38
50Hertz TS have position gaps; constant-output units publish a single point that
holds for days), and the last point holds to the Period's own end. A sequential
A01-style read produced a board where 85 units existed on chunk-start days and 5
on the frontier — position 1 lands on the chunk start, everything else was
dropped. The parser therefore expands steps via entsoe_exchange._period_slots
(A09's parser — A01 is its degenerate case). The Period `end` is the TSO's OWN
published data end (e.g. Thursday 14:00 mid-window), so the expansion never
fabricates beyond publication.

PER-CTA LAG SKEW (same smoke): TenneT published to D-2 while 50Hertz/Amprion/
TransnetBW stopped at D-5 (the regulation allows up to D+5) — the zone's "latest
published hour" is therefore the FASTEST CTA's frontier, and most units
legitimately have no data there. The read side must surface each unit at ITS OWN
latest published hour with its own lag (per-unit freshness) — sampling everyone
at the zone frontier nulls most of the board — and never drop a trailing unit.

CONSUMPTION TimeSeries (outBiddingZone_Domain — pumped-storage pumping) are
EXCLUDED, mirroring the A75 discrimination in backend/gas/entsoe.py::
parse_generation. Honesty note: the smoke found ZERO consumption TS in the live
documents (all 151 are inBiddingZone; German A73 apparently publishes generation
only) — the guard is defensive, kept because A75 proves ENTSO-E does publish
both directions for other doctypes/zones and a future zone extension must not
silently book pumping as output.

Storage is the dedicated `unit_generation` table, deliberately NOT power_hourly —
see the UnitGeneration model docstring for the full register (catalog pollution,
foreign natural key, hot-table hygiene).
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from xml.etree import ElementTree as ET

import httpx
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from backend.config import settings
from backend.gas import raw_cache
from backend.gas.entsoe import ENTSOE_BASE, _localname, _token
from backend.models.energy import UnitGeneration
from backend.power.entsoe_exchange import _period_slots

logger = logging.getLogger(__name__)

A73_DOCTYPE = "A73"
A73_PROCESS = "A16"  # realised

#: Globally unique raw-cache source (the same doctrine every ENTSO-E collector
#: repeats): "entsoe_gen_total_forecast" is A71, "entsoe_a61" is NTC — sharing a
#: source would serve the wrong document back from disk.
CACHE_SOURCE = "entsoe_a73"

#: Probe-proven window size (8 days accepted; 7 keeps the chunk arithmetic simple).
CHUNK_DAYS = 7

#: Ingest zone → list of (label, in_Domain EIC) actually ANSWERING for it.
#: Swept by the probe 2026-07-28: the DE-LU bidding-zone EIC answers nothing for
#: A73 — only the four German control areas do, so DE_LU is served as their union
#: (unit EICs are disjoint across CTAs). Config-extensible: add a zone here after
#: probing which domain(s) answer for it (BZN first, CTAs as fallback — re-probe
#: recipe in the module docstring).
A73_ZONES: dict[str, list[tuple[str, str]]] = {
    "DE_LU": [
        ("50Hertz", "10YDE-VE-------2"),
        ("Amprion", "10YDE-RWENET---I"),
        ("TenneT-DE", "10YDE-EON------1"),
        ("TransnetBW", "10YDE-ENBW-----N"),
    ],
}

# Rows per multi-row INSERT — hourly_store's batching idiom, mirrored locally
# (4 cols × 2000 = 8000 bind params, well under SQLite's limit, and the write
# lock is released between chunks).
_BATCH = 2000


def parse_unit_generation(xml_text: str) -> dict[str, dict[int, float]]:
    """A73 GL_MarketDocument → {unit_eic: {epoch_hour: mw}}, hourly means.

    Pure function, namespace-agnostic (_localname). Per GENERATION TimeSeries the
    unit is `registeredResource.mRID`.

    curveType is A03 in the wild (smoke 2026-07-28 — see module docstring): a
    point is published only where the value CHANGES and HOLDS until the next one,
    the last one to the Period's end. Slots are therefore expanded via
    entsoe_exchange._period_slots (the A09 step parser; sequential A01 is its
    degenerate case), then averaged onto the top-of-hour grid: first WITHIN a
    TimeSeries (a PT15M hour = mean of its four quarter slots), then ACROSS
    TimeSeries covering the same unit-hour — so a PT60M and a PT15M series weigh
    equally instead of 1-vs-4 raw points, and a republished period averages
    instead of double-counting (the A09/A25 rule).

    CONSUMPTION TimeSeries are EXCLUDED: a pumped-storage unit may publish BOTH a
    generation (inBiddingZone_Domain) and a consumption (outBiddingZone_Domain)
    TimeSeries; reading both would book pumping as generation. Same discrimination
    as backend/gas/entsoe.py::parse_generation (A75, prod-verified there). The
    2026-07-28 smoke found zero consumption TS in the live German documents —
    the guard is defensive, not decorative (see module docstring).
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ValueError(f"ENTSO-E A73 XML parse error: {exc}") from exc

    # unit -> hour -> [per-TimeSeries hourly means]
    per_unit: dict[str, dict[int, list[float]]] = {}
    for ts in root.iter():
        if _localname(ts.tag) != "TimeSeries":
            continue
        if any(_localname(e.tag).startswith("outBiddingZone_Domain") for e in ts.iter()):
            continue  # consumption (pumping) — not generation
        unit = next((e.text for e in ts.iter()
                     if _localname(e.tag) == "registeredResource.mRID"), None)
        if not unit:
            continue
        by_hour: dict[int, list[float]] = defaultdict(list)
        for period in (e for e in ts.iter() if _localname(e.tag) == "Period"):
            for epoch, value in _period_slots(period):
                by_hour[epoch].append(value)
        if not by_hour:
            continue
        target = per_unit.setdefault(unit, {})
        for hour_ts, slots in by_hour.items():
            # First mean WITHIN the TimeSeries (the PT15M quarter average) …
            target.setdefault(hour_ts, []).append(sum(slots) / len(slots))

    # … then mean ACROSS TimeSeries.
    return {u: {h: sum(v) / len(v) for h, v in hours.items()}
            for u, hours in per_unit.items()}


async def _fetch_units_window(
    cta_eic: str, start_date: date, end_date: date, *, overwrite: bool = False
) -> str:
    """One control-area window of A73, disk-cached. Returns "" on a clean 200-ACK.

    THE 200-ACK IS THE TRAP (probe-verified): A73's "no data" is HTTP 200 with an
    Acknowledgement_MarketDocument body — the still-filling frontier answers this
    every day until the ~6-day lag catches up. That is data, cache it. Anything
    >= 400 raises and caches nothing: a 400 here is a malformed request, and caching
    it would make a parameter bug permanent emptiness on disk.
    """

    async def _do() -> dict:
        params = {
            "securityToken": _token(),
            "documentType": A73_DOCTYPE,
            "processType": A73_PROCESS,
            "in_Domain": cta_eic,
            "periodStart": f"{start_date:%Y%m%d}0000",
            "periodEnd": f"{end_date:%Y%m%d}0000",
        }
        async with httpx.AsyncClient(timeout=180) as client:
            resp = await client.get(ENTSOE_BASE, params=params)
            if resp.status_code == 200 and "Acknowledgement_MarketDocument" in resp.text:
                return {"xml": ""}  # genuine "nothing published yet" — cacheable
            resp.raise_for_status()
            return {"xml": resp.text}

    payload = await raw_cache.fetch_or_cache(
        CACHE_SOURCE,
        f"{cta_eic}_{start_date}",
        start_date,
        _do,
        overwrite=overwrite,
    )
    return payload.get("xml", "")


def upsert_unit_generation(db: Session, zone: str, rows: list[tuple[str, int, float]]) -> int:
    """Batched idempotent upsert of (unit_eic, ts_utc, mw) rows for one ingest zone.

    INSERT … ON CONFLICT DO UPDATE on the (unit_eic, ts_utc) PK — hourly_store's
    idiom mirrored locally (unit_generation is deliberately not a power_hourly
    series, see the model docstring). Re-running with the same keys overwrites
    mw in place; None values are skipped. Caller commits.
    """
    payload = [
        {"unit_eic": u, "ts_utc": int(t), "mw": float(v), "zone": zone}
        for u, t, v in rows
        if v is not None
    ]
    if not payload:
        return 0
    written = 0
    for i in range(0, len(payload), _BATCH):
        chunk = payload[i : i + _BATCH]
        stmt = sqlite_insert(UnitGeneration).values(chunk)
        stmt = stmt.on_conflict_do_update(
            index_elements=["unit_eic", "ts_utc"],
            set_={"mw": stmt.excluded.mw, "zone": stmt.excluded.zone},
        )
        db.execute(stmt)
        written += len(chunk)
    return written


def _window_chunks(start: date, end: date, *, days: int = CHUNK_DAYS) -> list[tuple[date, date]]:
    """[start, end) split into [start, end) chunks of at most `days` days."""
    out: list[tuple[date, date]] = []
    cur = start
    while cur < end:
        nxt = min(cur + timedelta(days=days), end)
        out.append((cur, nxt))
        cur = nxt
    return out


async def ingest_unit_generation_window(
    db: Session,
    start: date,
    end: date,
    *,
    zones: list[str] | None = None,
    overwrite: bool = False,
) -> dict:
    """Fetch + upsert per-unit generation for [start, end), 7-day chunks per CTA.

    Zone-gated on A73_ZONES: a zone without an A73 domain config is skipped loudly
    (the registry knows more zones than answer this doctype). Unit EICs are disjoint
    across a zone's CTAs, so the per-zone merge is a plain dict update.
    """
    if not settings.entsoe_api_token:
        logger.warning("entsoe_unit_generation: ENTSOE_API_TOKEN not set — skipping")
        return {"skipped": "no token"}

    zone_keys = list(zones) if zones is not None else list(A73_ZONES)
    written = 0
    units: set[str] = set()
    for zone in zone_keys:
        domains = A73_ZONES.get(zone)
        if not domains:
            logger.warning("entsoe_unit_generation: zone %s has no A73 domain config — skipping", zone)
            continue
        merged: dict[str, dict[int, float]] = {}
        for label, eic in domains:
            for c_start, c_end in _window_chunks(start, end):
                try:
                    xml = await _fetch_units_window(eic, c_start, c_end, overwrite=overwrite)
                except httpx.HTTPError as exc:
                    logger.warning("unit generation %s/%s %s: %s", zone, label, c_start, exc)
                    continue
                if not xml:
                    continue  # a cached clean 200-ACK — the frontier has not published yet
                try:
                    parsed = parse_unit_generation(xml)
                except ValueError as exc:
                    logger.warning("unit generation %s/%s %s: %s", zone, label, c_start, exc)
                    continue
                for unit, hours in parsed.items():
                    merged.setdefault(unit, {}).update(hours)
        rows = [(u, t, v) for u, hours in merged.items() for t, v in hours.items()]
        written += upsert_unit_generation(db, zone, rows)
        units |= set(merged)
    db.commit()
    return {"zones": len(zone_keys), "units": len(units), "written": written}


async def ingest_unit_generation(
    db: Session,
    days_back: int = 12,
    *,
    zones: list[str] | None = None,
    overwrite: bool = False,
) -> dict:
    """Rolling-window ingest: [today − days_back, today) for the configured zones.

    days_back=12 (scheduler default) comfortably spans the ~6-day publication lag
    plus weekend/holiday slack, and the scheduler passes overwrite=True so a day
    that answered a 200-ACK at first pass is re-asked until it fills in — the
    write-once cache must never freeze the still-filling frontier.
    """
    today = datetime.now(timezone.utc).date()
    return await ingest_unit_generation_window(
        db, today - timedelta(days=days_back), today, zones=zones, overwrite=overwrite
    )
