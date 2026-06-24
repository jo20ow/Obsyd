"""ENTSO-E cross-border physical electricity flows (A11).

Fetches Actual Cross-Border Physical Flow (documentType=A11) between
pairs of bidding zones defined in POWER_BORDERS (zones.py).

One A11 query covers one direction: out_Domain (exporter) → in_Domain
(importer).  Net flow on a border is:

    net_mw(A→B) = mean_mw(out=A, in=B) − mean_mw(out=B, in=A)

Positive net_mw means net physical flow from_zone → to_zone.

Caches raw XML under source "entsoe_flows", key
"<in_eic>_<out_eic>_<YYYY-MM>", month-bucketed.

Shares token / base URL / XML helpers with backend.gas.entsoe to avoid
duplication (same pattern as entsoe_grid.py).
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone

import httpx
from sqlalchemy.orm import Session

from backend.config import settings
from backend.gas import raw_cache
from backend.gas.entsoe import ENTSOE_BASE, _localname, _parse_utc, _token
from backend.models.energy import PowerFlow
from backend.power.zones import POWER_BORDERS, POWER_ZONES

logger = logging.getLogger(__name__)

_RESOLUTION_HOURS = {"PT60M": 1.0, "PT30M": 0.5, "PT15M": 0.25}


# ─── parser ──────────────────────────────────────────────────────────────────


def parse_physical_flow(xml_text: str) -> dict[str, float]:
    """Parse an A11 GL_MarketDocument into {YYYY-MM-DD: daily_mean_mw}.

    Walks TimeSeries → Period → Point, reads <quantity> (MW), buckets each
    hourly value to its UTC calendar day, and returns the daily MEAN in MW.

    The same walk as parse_load (A65) — the only difference is the document
    type; the XML structure and <quantity> tag are identical.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ValueError(f"ENTSO-E A11 XML parse error: {exc}") from exc

    by_day: dict[str, list[float]] = {}

    for ts in root.iter():
        if _localname(ts.tag) != "TimeSeries":
            continue
        for period in (e for e in ts.iter() if _localname(e.tag) == "Period"):
            start_el = next(
                (e for e in period.iter() if _localname(e.tag) == "start"), None
            )
            res_el = next(
                (e for e in period.iter() if _localname(e.tag) == "resolution"), None
            )
            if start_el is None or res_el is None:
                continue
            start = _parse_utc(start_el.text)
            res_hours = _RESOLUTION_HOURS.get((res_el.text or "").strip())
            if start is None or res_hours is None:
                continue
            for point in (e for e in period.iter() if _localname(e.tag) == "Point"):
                pos = next(
                    (e.text for e in point if _localname(e.tag) == "position"), None
                )
                qty = next(
                    (e.text for e in point if _localname(e.tag) == "quantity"), None
                )
                if pos is None or qty is None:
                    continue
                try:
                    ts_time = start + timedelta(hours=res_hours * (int(pos) - 1))
                    mw = float(qty)
                except (ValueError, TypeError):
                    continue
                day = ts_time.astimezone(timezone.utc).strftime("%Y-%m-%d")
                by_day.setdefault(day, []).append(mw)

    return {day: sum(vals) / len(vals) for day, vals in by_day.items() if vals}


# ─── fetch ────────────────────────────────────────────────────────────────────


async def _fetch_border_month(
    in_eic: str,
    out_eic: str,
    month_start: date,
    *,
    overwrite: bool = False,
) -> str:
    """Fetch one directional border flow for a calendar month (raw XML, cached).

    Cached under source "entsoe_flows", key "<in_eic>_<out_eic>_<YYYY-MM>".
    Returns raw XML string (empty string on HTTP errors).
    """
    nxt = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1)
    period_start = f"{month_start:%Y%m%d}0000"
    period_end = f"{nxt:%Y%m%d}0000"

    cache_key = f"{in_eic}_{out_eic}_{month_start:%Y-%m}"

    async def _do() -> dict:
        params = {
            "securityToken": _token(),
            "documentType": "A11",
            "in_Domain": in_eic,
            "out_Domain": out_eic,
            "periodStart": period_start,
            "periodEnd": period_end,
        }
        async with httpx.AsyncClient(timeout=90) as client:
            resp = await client.get(ENTSOE_BASE, params=params)
            resp.raise_for_status()
            return {"xml": resp.text}

    payload = await raw_cache.fetch_or_cache(
        "entsoe_flows", cache_key, month_start, _do, overwrite=overwrite
    )
    return payload.get("xml", "")


# ─── ingest ───────────────────────────────────────────────────────────────────


async def ingest_flows(
    db: Session,
    days: list[str],
    *,
    overwrite: bool = False,
) -> dict:
    """Ingest cross-border physical flows for all POWER_BORDERS.

    For each (from_zone, to_zone) pair in POWER_BORDERS:
      1. Fetch A11 with out_Domain=from_zone, in_Domain=to_zone (flow A→B).
      2. Fetch A11 with out_Domain=to_zone,   in_Domain=from_zone (flow B→A).
      3. Compute net_mw per day:
           net_mw = flow_AB[day] − flow_BA[day]
         Positive = net physical export from_zone → to_zone.
         Missing direction treated as 0 for that day.
      4. Upsert PowerFlow(from_zone, to_zone, net_mw) per day.

    Returns {"days": n, "written": n} or {"skipped": "no token"}.
    """
    if not days:
        return {"days": 0, "written": 0}
    if not settings.entsoe_api_token:
        logger.warning("entsoe_flows.ingest_flows: ENTSOE_API_TOKEN not set — skipping")
        return {"skipped": "no token"}

    wanted = set(days)
    months = sorted(
        {datetime.strptime(d, "%Y-%m-%d").date().replace(day=1) for d in days}
    )

    written = 0

    for from_zone, to_zone in POWER_BORDERS:
        from_eic = POWER_ZONES[from_zone]["eic"]
        to_eic = POWER_ZONES[to_zone]["eic"]

        # Accumulate both directions across month fetches
        flow_ab: dict[str, float] = {}  # from_zone → to_zone (out=from, in=to)
        flow_ba: dict[str, float] = {}  # to_zone → from_zone (out=to, in=from)

        for month_start in months:
            # Direction A→B: out=from_zone, in=to_zone
            try:
                xml_ab = await _fetch_border_month(
                    in_eic=to_eic,
                    out_eic=from_eic,
                    month_start=month_start,
                    overwrite=overwrite,
                )
            except httpx.HTTPError as exc:
                logger.warning(
                    "entsoe_flows: A11 %s→%s %s fetch failed: %s",
                    from_zone, to_zone, month_start, exc,
                )
                xml_ab = ""

            if xml_ab:
                for day, mean_mw in parse_physical_flow(xml_ab).items():
                    if day in wanted:
                        flow_ab[day] = mean_mw

            # Direction B→A: out=to_zone, in=from_zone
            try:
                xml_ba = await _fetch_border_month(
                    in_eic=from_eic,
                    out_eic=to_eic,
                    month_start=month_start,
                    overwrite=overwrite,
                )
            except httpx.HTTPError as exc:
                logger.warning(
                    "entsoe_flows: A11 %s→%s %s fetch failed: %s",
                    to_zone, from_zone, month_start, exc,
                )
                xml_ba = ""

            if xml_ba:
                for day, mean_mw in parse_physical_flow(xml_ba).items():
                    if day in wanted:
                        flow_ba[day] = mean_mw

        # Days that appear in either direction
        all_days = wanted & (flow_ab.keys() | flow_ba.keys())
        for day in sorted(all_days):
            net_mw = flow_ab.get(day, 0.0) - flow_ba.get(day, 0.0)
            _upsert_flow(db, day, from_zone, to_zone, net_mw)
            written += 1

    db.commit()
    logger.info(
        "entsoe_flows.ingest_flows: %d rows written across %d borders",
        written,
        len(POWER_BORDERS),
    )
    return {"days": len(days), "written": written}


def _upsert_flow(
    db: Session,
    day: str,
    from_zone: str,
    to_zone: str,
    net_mw: float,
) -> None:
    existing = (
        db.query(PowerFlow)
        .filter(
            PowerFlow.date == day,
            PowerFlow.from_zone == from_zone,
            PowerFlow.to_zone == to_zone,
        )
        .first()
    )
    if existing:
        existing.net_mw = net_mw
    else:
        db.add(
            PowerFlow(
                date=day,
                from_zone=from_zone,
                to_zone=to_zone,
                net_mw=net_mw,
            )
        )
