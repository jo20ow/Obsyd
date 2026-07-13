"""Scheduled cross-border exchanges (ENTSO-E A09) — and the parser they need.

WHAT THIS CLOSES
----------------
The desk's border layer is built on Fraunhofer Energy-Charts, which reports by COUNTRY. So
the 18 sub-zones — NO1-5, SE1-4, DK1/2, every IT_* — have had **no border data at all**, and
DE_LU↔DK1 existed only as an aggregate `flow.DK` with no price behind it. A09 answers per
BIDDING ZONE: 63 borders across 36 of 37 zones, including the internal ones no country-level
source can represent even in principle (NO1↔NO2, SE3↔SE4, IT_NORD↔IT_CENTRO_NORD).

It also gives the desk a quantity it has never had: **loop flow = physical − scheduled**. What
the market agreed to move, versus what the wires actually carried.

THE PARSER IS THE POINT (curveType A03)
---------------------------------------
Every other ENTSO-E series this repo ingests is curveType A01: one point per slot, sequential.
A09 is **A03 — a variable-sized block, a step function.** A point is published ONLY where the
value changes; it HOLDS until the next published position, and the last one holds to the end
of the Period.

    DE→FR, 2026-07-01: published positions 1, 142, 143, 144, 145, 167, …
                       — 26 of 192 PT15M slots.

Hand that document to `parse_load_hourly` or `parse_imbalance_quarter_hourly` and 86% of the
timeline vanishes; the "hourly mean" that comes out is an average of whichever one or two
quarter-hours happened to be published in that hour. It is not wrong by a little.

`parse_step_series` expands the steps back into a dense grid. A01 is the degenerate case (every
position published) and still parses, so this is strictly more general than what it replaces.

THE SIGN
--------
A09 is DIRECTED: two requests per border, and the net is A→B minus B→A. Quantities are
non-negative magnitudes per direction — asking one leg only would report a 500 MW export while
800 MW came the other way.

Stored as `sched.<TO>` under zone `<FROM>` on the canonical SORTED pair, with `net > 0` meaning
`<FROM>` exports — byte-identical to the `flow.<TO>` convention, so borders.py generalises by
prefix. Kept in its OWN namespace: scheduled and physical MW are different quantities, and a
single `flow.*` namespace holding both would make loop flow uncomputable and every existing
number ambiguous.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from xml.etree import ElementTree as ET

import httpx
from sqlalchemy.orm import Session

from backend.config import settings
from backend.gas import raw_cache
from backend.gas.entsoe import ENTSOE_BASE, _localname, _parse_utc, _token
from backend.power.border_registry import SCHEDULED_BORDERS
from backend.power.hourly_store import upsert_hourly
from backend.power.zones import ZONE_REGISTRY

logger = logging.getLogger(__name__)

#: `A09` is ALSO a docStatus in this codebase (_WITHDRAWN_STATUSES in entsoe_outages.py) and
#: `B09` is a psrType ("Geothermal"). grep will lie to you; the constants are named.
SCHEDULED_EXCHANGE_DOCTYPE = "A09"

#: Total scheduled — the leg comparable to physical flow. WITHOUT this parameter one document
#: carries TWO TimeSeries (A01 day-ahead AND A05 total) and a parser that walks all of them
#: averages the two into a quantity that is neither.
CONTRACT_TOTAL = "A05"

#: NOT "entsoe_gen_total_forecast" — that source is already taken by A71 + processType A01
#: (the day-ahead generation forecast). Sharing it would serve back the wrong document.
CACHE_SOURCE = "entsoe_scheduled_exchange"

SERIES_PREFIX = "sched."

_RESOLUTION_MINUTES = {"PT60M": 60, "PT30M": 30, "PT15M": 15}


def parse_step_series(xml_text: str) -> dict[int, float]:
    """A09 document → {epoch_hour: MW}, densified and averaged onto the UTC hour grid.

    curveType A03 publishes a point only where the value CHANGES. Each published position
    HOLDS until the next one, and the final position holds to the Period's end — so the slots
    between publications are not missing data, they are the same value repeated, and dropping
    them (as every A01 parser in this repo would) deletes most of the timeline.

    Resolution is mixed in the wild: PT15M on continental borders, PT60M on the Nordic ones
    and across all 2023 history. Both densify to the same hourly grid.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ValueError(f"ENTSO-E A09 XML parse error: {exc}") from exc

    by_hour: dict[int, list[float]] = defaultdict(list)
    for ts in root.iter():
        if _localname(ts.tag) != "TimeSeries":
            continue
        for period in (e for e in ts.iter() if _localname(e.tag) == "Period"):
            for epoch, value in _period_slots(period):
                by_hour[epoch].append(value)

    # Overlapping TimeSeries (revisions, or several contracts) are averaged per hour, the same
    # rule parse_imbalance_prices uses. A sum here would double-count a republished period.
    return {h: sum(v) / len(v) for h, v in by_hour.items()}


def _period_slots(period) -> list[tuple[int, float]]:
    """One Period → [(epoch_hour, MW)] with the step function expanded across every slot."""
    start_el = next((e for e in period.iter() if _localname(e.tag) == "start"), None)
    end_el = next((e for e in period.iter() if _localname(e.tag) == "end"), None)
    res_el = next((e for e in period.iter() if _localname(e.tag) == "resolution"), None)
    if start_el is None or res_el is None:
        return []
    minutes = _RESOLUTION_MINUTES.get((res_el.text or "").strip())
    start = _parse_utc(start_el.text)
    if minutes is None or start is None:
        return []

    published: dict[int, float] = {}
    for point in (e for e in period.iter() if _localname(e.tag) == "Point"):
        pos = next((e.text for e in point if _localname(e.tag) == "position"), None)
        qty = next((e.text for e in point if _localname(e.tag) == "quantity"), None)
        if pos is None or qty is None:
            continue
        try:
            published[int(pos)] = float(qty)
        except (ValueError, TypeError):
            continue
    if not published:
        return []

    # How many slots the Period actually spans. The last published value holds to the END, so
    # without the end element we would truncate the series at its final change — which for a
    # border that settles early in the day is most of the day.
    end = _parse_utc(end_el.text) if end_el is not None else None
    if end is not None:
        total = max(1, int((end - start).total_seconds() // (minutes * 60)))
    else:
        total = max(published)

    out: list[tuple[int, float]] = []
    held: float | None = None
    for slot in range(1, total + 1):
        if slot in published:
            held = published[slot]          # a step
        if held is None:
            continue                        # nothing published yet — genuinely absent
        moment = start + timedelta(minutes=minutes * (slot - 1))
        hour = moment.replace(minute=0, second=0, microsecond=0)
        out.append((int(hour.astimezone(timezone.utc).timestamp()), held))
    return out


def _month_bounds(month_start: date) -> tuple[str, str]:
    nxt = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1)
    return f"{month_start:%Y%m%d}0000", f"{nxt:%Y%m%d}0000"


async def _fetch_exchange_month(
    out_zone: str, in_zone: str, month_start: date, *, overwrite: bool = False
) -> str:
    """One directed border-month of A09, disk-cached. Returns "" on a clean no-data ACK."""
    out_eic = ZONE_REGISTRY[out_zone]["eic"]
    in_eic = ZONE_REGISTRY[in_zone]["eic"]
    period_start, period_end = _month_bounds(month_start)

    async def _do() -> dict:
        params = {
            "securityToken": _token(),
            "documentType": SCHEDULED_EXCHANGE_DOCTYPE,
            "contract_MarketAgreement.Type": CONTRACT_TOTAL,
            "out_Domain": out_eic,
            "in_Domain": in_eic,
            "periodStart": period_start,
            "periodEnd": period_end,
        }
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.get(ENTSOE_BASE, params=params)
            if resp.status_code >= 400:
                # A border with no schedule in this month answers 400 with an Acknowledgement.
                # That is data, not an error: cache the emptiness so we never ask again.
                return {"xml": ""}
            return {"xml": resp.text}

    payload = await raw_cache.fetch_or_cache(
        CACHE_SOURCE,
        f"{out_zone}_{in_zone}_{month_start:%Y-%m}",
        month_start,
        _do,
        overwrite=overwrite,
    )
    return payload.get("xml", "")


async def ingest_scheduled_exchanges(
    db: Session,
    months: list[date],
    *,
    borders: list[tuple[str, str]] | None = None,
    overwrite: bool = False,
) -> dict:
    """Net scheduled exchange per border-month → `sched.<TO>` under zone `<FROM>`."""
    if not settings.entsoe_api_token:
        return {"skipped": "no token"}

    borders = borders or SCHEDULED_BORDERS
    written = 0
    covered = 0
    for a, b in borders:  # canonical, sorted: net > 0 means `a` exports to `b`
        for month in months:
            try:
                a_to_b = parse_step_series(
                    await _fetch_exchange_month(a, b, month, overwrite=overwrite))
                b_to_a = parse_step_series(
                    await _fetch_exchange_month(b, a, month, overwrite=overwrite))
            except (httpx.HTTPError, ValueError) as exc:
                logger.warning("scheduled %s-%s %s: %s", a, b, month, exc)
                continue
            net = net_exchange(a_to_b, b_to_a)
            if not net:
                continue
            written += upsert_hourly(db, f"{SERIES_PREFIX}{b}", a,
                                     sorted(net.items()), unit="MW")
            covered += 1
    db.commit()
    return {"borders": len(borders), "border_months": covered, "written": written}


def net_exchange(a_to_b: dict[int, float], b_to_a: dict[int, float]) -> dict[int, float]:
    """Net MW from a's side. Positive = `a` exports.

    Both legs are non-negative magnitudes: A09 reports each direction separately. Storing one
    leg alone would report a 500 MW export in an hour when 800 MW came the other way, so an
    hour that appears in only ONE leg still nets against an implicit zero on the other.
    """
    return {
        h: a_to_b.get(h, 0.0) - b_to_a.get(h, 0.0)
        for h in set(a_to_b) | set(b_to_a)
    }


def months_between(start: date, end: date) -> list[date]:
    out, m = [], start.replace(day=1)
    while m <= end:
        out.append(m)
        m = (m.replace(day=28) + timedelta(days=4)).replace(day=1)
    return out


def recent_months(days: int, *, today: date | None = None) -> list[date]:
    today = today or datetime.now(timezone.utc).date()
    return months_between(today - timedelta(days=days), today)


# ─── A25/B09: the market net position ─────────────────────────────────────────
#
# A09 above says what each BORDER was scheduled to carry. A25 says what the ZONE'S NET
# position was — the SDAC day-ahead allocation, from the auction rather than summed off the
# borders. Different quantity, and the 18 sub-zones get their first one ever.

NET_POSITION_DOCTYPE = "A25"

#: NOT the psrType B09 ("Geothermal", entsoe_grid.py). Same two characters, different registry.
NET_POSITION_BUSINESS_TYPE = "B09"

#: MANDATORY. Without it the API answers "Mandatory parameter Contract_MarketAgreement.Type is
#: missing" — the request is simply refused.
CONTRACT_DAILY = "A01"

NET_POSITION_CACHE_SOURCE = "entsoe_netpos"
NET_POSITION_SERIES = "netpos.dayahead"

#: GR, IE_SEM and CH answer with a clean "No matching data found" Acknowledgement. Excluded by
#: name, not hidden: a zone that merely fails to appear looks like a bug.
NET_POSITION_UNSUPPORTED = ("GR", "IE_SEM", "CH")


def parse_net_position(xml_text: str, zone_eic: str) -> dict[int, float]:
    """A25/B09 → {epoch_hour: MW}, SIGNED. Positive = the zone is a net EXPORTER.

    THE TRAP. The quantity is an UNSIGNED MAGNITUDE. The sign lives in the DOMAIN PAIR.

    The document partitions the timeline into disjoint curveType-A03 TimeSeries and flips the
    pair every time the zone changes direction:

        out_Domain.mRID == zone_eic  → an EXPORT block  (counter-domain: "REGION_CODE-----")
        in_Domain.mRID  == zone_eic  → an IMPORT block

    Measured on PL over 2026-07-01/02: 23 export blocks and 7 import blocks, and every one of
    the 172 quantities >= 0. A parser that reads <quantity> and ignores the domains reports
    Poland exporting 1.65 GW during the hours it was importing — plausible, well-formed, and
    inverted.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ValueError(f"ENTSO-E A25 XML parse error: {exc}") from exc

    by_hour: dict[int, list[float]] = defaultdict(list)
    for ts in root.iter():
        if _localname(ts.tag) != "TimeSeries":
            continue
        out_dom = next((e.text for e in ts.iter()
                        if _localname(e.tag) == "out_Domain.mRID"), None)
        in_dom = next((e.text for e in ts.iter()
                       if _localname(e.tag) == "in_Domain.mRID"), None)
        if out_dom == zone_eic:
            sign = 1.0     # the zone is the source: export
        elif in_dom == zone_eic:
            sign = -1.0    # the zone is the sink: import
        else:
            continue       # a block about neither side of this zone — not ours to read
        for period in (e for e in ts.iter() if _localname(e.tag) == "Period"):
            for epoch, value in _period_slots(period):
                by_hour[epoch].append(sign * value)

    return {h: sum(v) / len(v) for h, v in by_hour.items()}


async def _fetch_net_position_week(zone: str, week_start: date, *, overwrite: bool = False) -> str:
    """One zone-week of A25. Weekly, not monthly: a one-MONTH window did not return in 90 s."""
    eic = ZONE_REGISTRY[zone]["eic"]
    week_end = week_start + timedelta(days=7)

    async def _do() -> dict:
        params = {
            "securityToken": _token(),
            "documentType": NET_POSITION_DOCTYPE,
            "businessType": NET_POSITION_BUSINESS_TYPE,
            "contract_MarketAgreement.Type": CONTRACT_DAILY,
            "in_Domain": eic,
            "out_Domain": eic,
            "periodStart": f"{week_start:%Y%m%d}0000",
            "periodEnd": f"{week_end:%Y%m%d}0000",
        }
        async with httpx.AsyncClient(timeout=180) as client:
            resp = await client.get(ENTSOE_BASE, params=params)
            if resp.status_code >= 400:
                return {"xml": ""}   # a clean "no data" ACK is data: cache the emptiness
            return {"xml": resp.text}

    payload = await raw_cache.fetch_or_cache(
        NET_POSITION_CACHE_SOURCE, f"{zone}_{week_start:%Y-%m-%d}", week_start, _do,
        overwrite=overwrite,
    )
    return payload.get("xml", "")


async def ingest_net_positions(
    db: Session, weeks: list[date], *, zones: list[str] | None = None,
    overwrite: bool = False,
) -> dict:
    """Signed day-ahead market net position per zone → `netpos.dayahead`."""
    if not settings.entsoe_api_token:
        return {"skipped": "no token"}

    zones = zones or [z for z in ZONE_REGISTRY if z not in NET_POSITION_UNSUPPORTED]
    written = covered = 0
    for zone in zones:
        eic = ZONE_REGISTRY[zone]["eic"]
        points: dict[int, float] = {}
        for week in weeks:
            try:
                xml = await _fetch_net_position_week(zone, week, overwrite=overwrite)
            except (httpx.HTTPError, ValueError) as exc:
                logger.warning("netpos %s %s: %s", zone, week, exc)
                continue
            if xml:
                points.update(parse_net_position(xml, eic))
        if not points:
            continue
        written += upsert_hourly(db, NET_POSITION_SERIES, zone,
                                 sorted(points.items()), unit="MW")
        covered += 1
    db.commit()
    return {"zones": len(zones), "with_data": covered, "written": written,
            "unsupported": list(NET_POSITION_UNSUPPORTED)}


def recent_weeks(days: int, *, today: date | None = None) -> list[date]:
    today = today or datetime.now(timezone.utc).date()
    start = today - timedelta(days=days)
    weeks, w = [], start - timedelta(days=start.weekday())
    while w <= today:
        weeks.append(w)
        w += timedelta(days=7)
    return weeks
