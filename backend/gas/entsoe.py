"""ENTSO-E power burn: gas-fired electricity generation → implied gas demand.

The only MEASURED demand component. ENTSO-E Transparency Platform RESTful API
(XML), documentType A75 (Actual Generation per Production Type), psrType B04
(Fossil Gas), per bidding zone, hourly → daily GWh_el, summed across EU27.

Conversion (the one big assumption, stored alongside the raw generation):
    implied_gas_gwh = gen_gwh_el / efficiency,  efficiency ≈ 0.50 (CCGT fleet)
This carries a ~±5% SYSTEMATIC error — the fleet mixes efficient CCGTs with
old OCGTs and CHP. We store gen_gwh_el AND implied_gas_gwh AND the efficiency
used, so the assumption is auditable and re-derivable.

NOTE (token): the ENTSO-E token is granted manually (register + email request).
This module is built against the documented XML schema and unit-tested with a
captured-shape fixture; the parser/zone list should be re-checked against a
live response once a token is available. Day bucketing is by UTC calendar day
(ENTSO-E is UTC); gas-day alignment is a later refinement.
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone

import httpx
from sqlalchemy.orm import Session

from backend.config import settings
from backend.gas import raw_cache
from backend.models.gas import GasPowerBurn

logger = logging.getLogger(__name__)

ENTSOE_BASE = "https://web-api.tp.entsoe.eu/api"
PSR_FOSSIL_GAS = "B04"

# EU27 bidding zones with meaningful gas-fired generation, by EIC code.
# Zero/negligible-gas zones (most of Scandinavia/Baltics) are omitted — they
# contribute ~nothing to EU gas burn; add them here if needed.
EU27_BIDDING_ZONES: dict[str, str] = {
    "DE-LU": "10Y1001A1001A82H",
    "FR": "10YFR-RTE------C",
    "NL": "10YNL----------L",
    "BE": "10YBE----------2",
    "AT": "10YAT-APG------L",
    "ES": "10YES-REE------0",
    "PT": "10YPT-REN------W",
    "PL": "10YPL-AREA-----S",
    "CZ": "10YCZ-CEPS-----N",
    "HU": "10YHU-MAVIR----U",
    "RO": "10YRO-TEL------P",
    "GR": "10YGR-HTSO-----Y",
    "IE-SEM": "10Y1001A1001A59C",
    "IT-Nord": "10Y1001A1001A73I",
    "IT-Centro-Nord": "10Y1001A1001A70O",
    "IT-Centro-Sud": "10Y1001A1001A71M",
    "IT-Sud": "10Y1001A1001A788",
    "IT-Sicilia": "10Y1001A1001A75E",
    "IT-Sardegna": "10Y1001A1001A74G",
    "IT-Calabria": "10Y1001C--00096J",
    "BG": "10YCA-BULGARIA-R",
    "HR": "10YHR-HEP------M",
    "SI": "10YSI-ELES-----O",
    "SK": "10YSK-SEPS-----K",
    "FI": "10YFI-1--------U",
    "DK1": "10YDK-1--------W",
    "DK2": "10YDK-2--------M",
}

_RESOLUTION_HOURS = {"PT60M": 1.0, "PT30M": 0.5, "PT15M": 0.25}


def _token() -> str:
    tok = settings.entsoe_api_token
    if not tok:
        raise RuntimeError("ENTSOE_API_TOKEN not configured — see .env.example")
    return tok.get_secret_value()  # never logged


def _localname(tag: str) -> str:
    """Strip the XML namespace: '{urn:...}TimeSeries' → 'TimeSeries'."""
    return tag.rsplit("}", 1)[-1]


def parse_generation(xml_text: str) -> dict[str, float]:
    """Parse an A75 GL_MarketDocument into {YYYY-MM-DD: GWh_el}, summing all
    B04 generation TimeSeries and bucketing each point by its UTC day.

    Namespace-agnostic (matches local tag names). Quantities are MW; energy =
    MW × resolution-hours = MWh; GWh = MWh / 1000.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ValueError(f"ENTSO-E XML parse error: {exc}") from exc

    by_day_mwh: dict[str, float] = {}
    for ts in root.iter():
        if _localname(ts.tag) != "TimeSeries":
            continue
        # psrType filter (defensive — the request already filters B04)
        psr = next((e.text for e in ts.iter() if _localname(e.tag) == "psrType"), None)
        if psr is not None and psr != PSR_FOSSIL_GAS:
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
                qty = next((e.text for e in point if _localname(e.tag) == "quantity"), None)
                if pos is None or qty is None:
                    continue
                try:
                    ts_time = start + timedelta(hours=res_hours * (int(pos) - 1))
                    mwh = float(qty) * res_hours
                except (ValueError, TypeError):
                    continue
                day = ts_time.astimezone(timezone.utc).strftime("%Y-%m-%d")
                by_day_mwh[day] = by_day_mwh.get(day, 0.0) + mwh

    return {day: mwh / 1000.0 for day, mwh in by_day_mwh.items()}  # MWh → GWh


def _parse_utc(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


# ─── fetch + ingest ──────────────────────────────────────────────────────────


async def _fetch_zone_month(eic: str, month_start: date, *, overwrite: bool) -> str:
    """Fetch one zone's B04 generation for a month (raw XML, cached)."""
    nxt = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1)
    period_start = f"{month_start:%Y%m%d}0000"
    period_end = f"{nxt:%Y%m%d}0000"

    async def _do() -> dict:
        async with httpx.AsyncClient(timeout=90) as client:
            resp = await client.get(
                ENTSOE_BASE,
                params={
                    "securityToken": _token(),
                    "documentType": "A75",
                    "processType": "A16",  # Realised
                    "psrType": PSR_FOSSIL_GAS,
                    "in_Domain": eic,
                    "periodStart": period_start,
                    "periodEnd": period_end,
                },
            )
            resp.raise_for_status()
            return {"xml": resp.text}

    payload = await raw_cache.fetch_or_cache("entsoe", f"{eic}_{month_start:%Y-%m}", month_start, _do, overwrite=overwrite)
    return payload.get("xml", "")


async def ingest_power_burn(db: Session, days: list[str], *, overwrite: bool = False) -> dict:
    """Fetch every EU27 zone for the month spanning `days`, sum gas generation
    per UTC day across zones, convert to implied gas, and upsert gas_power_burn."""
    if not days:
        return {"days": 0, "written": 0}
    if not settings.entsoe_api_token:
        logger.warning("entsoe.ingest_power_burn: ENTSOE_API_TOKEN not set — skipping power burn")
        return {"days": len(days), "written": 0, "skipped": "no token"}
    efficiency = settings.gas_ccgt_efficiency
    month_start = datetime.strptime(min(days), "%Y-%m-%d").date().replace(day=1)
    wanted = set(days)

    gen_by_day: dict[str, float] = {}
    for label, eic in EU27_BIDDING_ZONES.items():
        try:
            xml = await _fetch_zone_month(eic, month_start, overwrite=overwrite)
        except httpx.HTTPError as exc:
            logger.warning("entsoe: %s (%s) fetch failed: %s", label, eic, exc)
            continue
        if not xml:
            continue
        for day, gwh in parse_generation(xml).items():
            if day in wanted:
                gen_by_day[day] = gen_by_day.get(day, 0.0) + gwh

    written = 0
    for day, gen_gwh in gen_by_day.items():
        implied = gen_gwh / efficiency if efficiency else None
        _upsert(db, day, gen_gwh, implied, efficiency)
        written += 1
    db.commit()
    logger.info("entsoe.ingest_power_burn: %d/%d days (eff=%.2f)", written, len(days), efficiency)
    return {"days": len(days), "written": written}


def _upsert(db: Session, day: str, gen_gwh_el: float, implied_gas_gwh: float | None, efficiency: float) -> None:
    existing = db.get(GasPowerBurn, day)
    if existing:
        existing.gen_gwh_el = gen_gwh_el
        existing.implied_gas_gwh = implied_gas_gwh
        existing.efficiency = efficiency
    else:
        db.add(GasPowerBurn(date=day, gen_gwh_el=gen_gwh_el, implied_gas_gwh=implied_gas_gwh, efficiency=efficiency))
