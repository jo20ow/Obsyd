"""ENTSO-E imbalance prices (documentType A85) → hourly series `imbalance.price`.

A85 is delivered as a ZIP archive (one inner XML per request) and is keyed by CONTROL
AREA, not bidding zone. For single-TSO countries the bidding-zone EIC works as the
control-area domain; Germany (DE_LU) has four control areas with one uniform reBAP,
published under the COUNTRY EIC (see _CONTROL_AREA_OVERRIDE). Prices are per 15-min
settlement period → averaged to hourly-canonical UTC, plus raw as imbalance.price.qh.
"""
from __future__ import annotations

import io
import logging
import xml.etree.ElementTree as ET
import zipfile
from datetime import date, datetime, timedelta, timezone

import httpx
from sqlalchemy.orm import Session

from backend.config import settings
from backend.gas import raw_cache
from backend.gas.entsoe import ENTSOE_BASE, _localname, _parse_utc, _token
from backend.power.hourly_store import upsert_day_hours, upsert_hourly
from backend.power.zones import ZONE_REGISTRY

logger = logging.getLogger(__name__)

_RESOLUTION_HOURS = {"PT60M": 1.0, "PT30M": 0.5, "PT15M": 0.25}

# Zones whose A85 domain differs from their bidding-zone EIC. Germany has four
# control areas with one uniform reBAP; spiked 2026-07-11 against the live API:
# the CA EICs and the DE_LU bidding-zone EIC all return Acknowledgement 999,
# but the COUNTRY EIC serves the full 96-slot reBAP.
_CONTROL_AREA_OVERRIDE = {"DE_LU": "10Y1001A1001A83F"}


def control_area_eic(zone: str) -> str | None:
    """Control-area domain EIC for A85 (single-TSO zones = the bidding EIC)."""
    if zone in _CONTROL_AREA_OVERRIDE:
        return _CONTROL_AREA_OVERRIDE[zone]
    return ZONE_REGISTRY.get(zone, {}).get("eic")


def parse_imbalance_prices(xml_text: str) -> dict[str, dict[int, float]]:
    """Parse an A85 imbalance-prices document into {YYYY-MM-DD: {hour_utc: mean_price}}.

    Reads the Point-level `imbalance_Price.amount` (the single/applicable imbalance price;
    any nested Financial_Price long/short breakdown is ignored). 15-min slots are averaged
    to hourly. An Acknowledgement document (no TimeSeries) yields {}.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ValueError(f"ENTSO-E A85 XML parse error: {exc}") from exc

    by_day_hour: dict[str, dict[int, list[float]]] = {}
    for ts in root.iter():
        if _localname(ts.tag) != "TimeSeries":
            continue
        for period in (e for e in ts.iter() if _localname(e.tag) == "Period"):
            start_el = next((e for e in period.iter() if _localname(e.tag) == "start"), None)
            res_el = next((e for e in period.iter() if _localname(e.tag) == "resolution"), None)
            if start_el is None or res_el is None:
                continue
            start = _parse_utc(start_el.text)
            res_hours = _RESOLUTION_HOURS.get((res_el.text or "").strip())
            if start is None or res_hours is None:
                continue
            for point in (e for e in period.iter() if _localname(e.tag) == "Point"):
                pos = next((e.text for e in point if _localname(e.tag) == "position"), None)
                # DIRECT child only — not the nested Financial_Price amounts.
                amt = next((e.text for e in point if _localname(e.tag) == "imbalance_Price.amount"), None)
                if pos is None or amt is None:
                    continue
                try:
                    t = start + timedelta(hours=res_hours * (int(pos) - 1))
                    v = float(amt)
                except (ValueError, TypeError):
                    continue
                utc = t.astimezone(timezone.utc)
                by_day_hour.setdefault(utc.strftime("%Y-%m-%d"), {}).setdefault(utc.hour, []).append(v)

    return {
        day: {h: sum(v) / len(v) for h, v in hours.items() if v}
        for day, hours in by_day_hour.items()
    }


def parse_imbalance_quarter_hourly(xml_text: str) -> list[tuple[int, float]]:
    """Parse an A85 document into raw settlement-period points [(epoch_sec, EUR/MWh)].

    Imbalance settles in 15-minute periods — that resolution IS the product; the
    hourly mean above dilutes exactly the ±1000 €/MWh quarter-hours imbalance
    watchers care about. Only PT15M TimeSeries contribute (hourly documents are
    ignored rather than duplicated onto slots); overlapping series are averaged
    per timestamp, matching the hourly parser.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ValueError(f"ENTSO-E A85 XML parse error: {exc}") from exc

    by_ts: dict[int, list[float]] = {}
    for ts in root.iter():
        if _localname(ts.tag) != "TimeSeries":
            continue
        for period in (e for e in ts.iter() if _localname(e.tag) == "Period"):
            start_el = next((e for e in period.iter() if _localname(e.tag) == "start"), None)
            res_el = next((e for e in period.iter() if _localname(e.tag) == "resolution"), None)
            if start_el is None or res_el is None:
                continue
            if (res_el.text or "").strip() != "PT15M":
                continue
            start = _parse_utc(start_el.text)
            if start is None:
                continue
            for point in (e for e in period.iter() if _localname(e.tag) == "Point"):
                pos = next((e.text for e in point if _localname(e.tag) == "position"), None)
                # DIRECT child only — not the nested Financial_Price amounts.
                amt = next((e.text for e in point if _localname(e.tag) == "imbalance_Price.amount"), None)
                if pos is None or amt is None:
                    continue
                try:
                    slot = start + timedelta(minutes=15 * (int(pos) - 1))
                    v = float(amt)
                except (ValueError, TypeError):
                    continue
                epoch = int(slot.astimezone(timezone.utc).timestamp())
                by_ts.setdefault(epoch, []).append(v)

    return [(t, sum(vs) / len(vs)) for t, vs in sorted(by_ts.items())]


async def _fetch_imbalance_month(ca_eic: str, month_start: date, *, overwrite: bool = False) -> str:
    """Fetch one control area's A85 for a calendar month; unzip the archive to inner XML (cached)."""
    nxt = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1)

    async def _do() -> dict:
        params = {
            "securityToken": _token(),
            "documentType": "A85",
            "controlArea_Domain": ca_eic,
            "periodStart": f"{month_start:%Y%m%d}0000",
            "periodEnd": f"{nxt:%Y%m%d}0000",
        }
        async with httpx.AsyncClient(timeout=90) as client:
            resp = await client.get(ENTSOE_BASE, params=params)
            resp.raise_for_status()
            body = resp.content
            if body[:2] == b"PK":  # ZIP archive
                zf = zipfile.ZipFile(io.BytesIO(body))
                names = [n for n in zf.namelist() if n.endswith(".xml")]
                xml = zf.read(names[0]).decode("utf-8", "replace") if names else ""
            else:
                xml = resp.text
            return {"xml": xml}

    payload = await raw_cache.fetch_or_cache(
        "entsoe_imbalance", f"{ca_eic}_{month_start:%Y-%m}", month_start, _do, overwrite=overwrite
    )
    return payload.get("xml", "") if isinstance(payload, dict) else ""


async def ingest_imbalance(
    db: Session, days: list[str], *, zone: str = "DE_LU", overwrite: bool = False
) -> dict:
    """Fetch + upsert imbalance prices for `zone` over the month(s) spanning `days`."""
    if not days:
        return {"days": 0, "written": 0}
    ca_eic = control_area_eic(zone)
    if ca_eic is None:
        return {"skipped": f"no A85 domain for zone {zone}"}
    if not settings.entsoe_api_token:
        return {"skipped": "no token"}

    wanted = set(days)
    months = sorted({datetime.strptime(d, "%Y-%m-%d").date().replace(day=1) for d in days})
    by_day: dict[str, dict[int, float]] = {}
    qh_points: list[tuple[int, float]] = []
    for month_start in months:
        try:
            xml = await _fetch_imbalance_month(ca_eic, month_start, overwrite=overwrite)
        except httpx.HTTPError as exc:
            logger.warning("imbalance [%s %s] fetch failed: %s", zone, month_start, exc)
            continue
        if not xml:
            continue
        for day, hours in parse_imbalance_prices(xml).items():
            if day in wanted:
                by_day[day] = hours
        qh_points.extend(
            (t, v)
            for t, v in parse_imbalance_quarter_hourly(xml)
            if datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%d") in wanted
        )

    written = upsert_day_hours(db, "imbalance.price", zone, by_day, unit="EUR/MWh") if by_day else 0
    # Raw 15-min settlement points alongside the hourly mean (roadmap Block 2).
    if qh_points:
        upsert_hourly(db, "imbalance.price.qh", zone, qh_points, unit="EUR/MWh")
    return {"days": len(by_day), "written": written}
