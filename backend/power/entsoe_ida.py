"""ENTSO-E intraday auction prices (SIDC IDA1-3) — the one legally free
intraday price in Europe, and the probe that showed how little of it there is.

Since 2024-06-13 the three pan-European intraday auctions clear 15-minute
products; their prices MAY be published on the Transparency Platform as
documentType A44 with contract_MarketAgreement.type A07, the auctions told
apart by classificationSequence_AttributeInstanceComponent.position (1..3).
Submission is evidently voluntary: the 2026-09 probe of twenty zones found
EXACTLY ONE publishing — Spain, and only from 2026-01 (2024/2025 answer
empty). Continuous intraday (EPEX/Nord Pool) stays licensed and NO-GO. Full
probe record: docs/findings/2026-09-13-ida-intraday-coverage.md — re-run the
probe there before assuming this list is still current.

Series (per publishing zone):
    price.ida1.qh / price.ida2.qh / price.ida3.qh   (EUR/MWh, raw 15-min)

Raw quarter-hours only, no hourly means: the QH product IS what these
auctions trade (unlike day-ahead, which needed an hourly series for its
pre-2025 history). IDA1/IDA2 cover the full CET delivery day, IDA3 the
afternoon half from 10:00 UTC — the differing spans are the market's, not a
gap. A44 A03-curves omit repeated values; positions are resolved exactly like
entsoe_prices' quarter-hour parser.
"""
from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from datetime import date, timedelta, timezone

import httpx
from sqlalchemy.orm import Session

from backend.gas import raw_cache
from backend.gas.entsoe import ENTSOE_BASE, _localname, _parse_utc, _token
from backend.power.hourly_store import upsert_hourly

logger = logging.getLogger(__name__)

#: Zones that actually publish IDA results on the TP (probe 2026-09; see module
#: docstring). This collector's OWN zone list, per the HYDRO_ZONES pattern —
#: extend it when a re-probe finds new publishers.
IDA_ZONES: dict[str, str] = {
    "ES": "10YES-REE------0",
}

#: ES publication floor found by probing (Oct 2024 and all of 2025 answer empty).
IDA_HISTORY_FLOOR = "2026-01-01"

SEQUENCES = (1, 2, 3)


def parse_ida_prices(xml_text: str) -> dict[int, list[tuple[int, float]]]:
    """{auction sequence: [(epoch_sec, EUR/MWh)]} from an A44/A07 document.

    Same position/resolution rules as parse_day_ahead_quarter_hourly; a
    TimeSeries without a classificationSequence is skipped (an auction price
    without an auction is not attributable). Duplicate points per (seq, ts)
    are averaged, matching the family's duplicate-TimeSeries handling.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ValueError(f"ENTSO-E IDA XML parse error: {exc}") from exc

    by_seq: dict[int, dict[int, list[float]]] = {s: {} for s in SEQUENCES}
    for ts in root.iter():
        if _localname(ts.tag) != "TimeSeries":
            continue
        seq_el = next(
            (e for e in ts.iter()
             if _localname(e.tag) == "classificationSequence_AttributeInstanceComponent.position"),
            None,
        )
        try:
            seq = int((seq_el.text or "").strip()) if seq_el is not None else None
        except ValueError:
            seq = None
        if seq not in by_seq:
            continue
        for period in (e for e in ts.iter() if _localname(e.tag) == "Period"):
            start_el = next((e for e in period.iter() if _localname(e.tag) == "start"), None)
            res_el = next((e for e in period.iter() if _localname(e.tag) == "resolution"), None)
            if start_el is None or res_el is None or (res_el.text or "").strip() != "PT15M":
                continue
            start = _parse_utc(start_el.text)
            if start is None:
                continue
            for point in (e for e in period.iter() if _localname(e.tag) == "Point"):
                pos = next((e.text for e in point if _localname(e.tag) == "position"), None)
                price = next((e.text for e in point if _localname(e.tag) == "price.amount"), None)
                if pos is None or price is None:
                    continue
                try:
                    slot = start + timedelta(minutes=15 * (int(pos) - 1))
                    val = float(price)
                except (ValueError, TypeError):
                    continue
                epoch = int(slot.astimezone(timezone.utc).timestamp())
                by_seq[seq].setdefault(epoch, []).append(val)

    return {
        s: [(t, sum(ps) / len(ps)) for t, ps in sorted(pts.items())]
        for s, pts in by_seq.items() if pts
    }


async def _fetch_zone_month(eic: str, month_start: date, *, overwrite: bool = False) -> str:
    period_start = f"{month_start:%Y%m%d}0000"
    nxt = (month_start.replace(day=28) + timedelta(days=5)).replace(day=1)
    period_end = f"{nxt:%Y%m%d}0000"

    async def _do() -> dict:
        async with httpx.AsyncClient(timeout=90) as client:
            resp = await client.get(ENTSOE_BASE, params={
                "securityToken": _token(),
                "documentType": "A44",
                "contract_MarketAgreement.type": "A07",
                "in_Domain": eic,
                "out_Domain": eic,
                "periodStart": period_start,
                "periodEnd": period_end,
            })
            resp.raise_for_status()
            return {"xml": resp.text}

    payload = await raw_cache.fetch_or_cache(
        "entsoe_ida", f"{eic}_{month_start:%Y-%m}", month_start, _do, overwrite=overwrite
    )
    return payload.get("xml", "")


async def ingest_ida(db: Session, months: list[date], *, overwrite: bool = False) -> dict:
    """Fetch + upsert IDA prices for every publishing zone over the given month
    starts. An Acknowledgement (no data) is a cacheable fact, not an error —
    submission is voluntary and absence is the norm (see module docstring)."""
    written = {"points": 0, "zones": 0}
    for zone, eic in IDA_ZONES.items():
        zone_points = 0
        for month_start in months:
            try:
                xml = await _fetch_zone_month(eic, month_start, overwrite=overwrite)
            except httpx.HTTPError as exc:
                logger.error("ida fetch %s %s failed: %s", zone, month_start, exc)
                continue
            if not xml or "Acknowledgement" in xml[:400]:
                continue
            for seq, points in parse_ida_prices(xml).items():
                zone_points += upsert_hourly(
                    db, f"price.ida{seq}.qh", zone, points, unit="EUR/MWh"
                )
        if zone_points:
            written["zones"] += 1
            written["points"] += zone_points
    logger.info("ida ingest: %s", written)
    return written
