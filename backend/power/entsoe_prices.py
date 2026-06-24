"""ENTSO-E day-ahead electricity prices: A44 → daily mean EUR/MWh per bidding zone.

Fetches documentType A44 (Day-Ahead Prices) for a bidding zone via the ENTSO-E
Transparency Platform RESTful API (XML), parses each PT60M Point's <price.amount>
(EUR/MWh), buckets hourly values to UTC calendar days, and stores the daily mean
into EnergyPrice(symbol="POWER_DE").

The key difference from the A75/power-burn parser:
  - Tag name: `price.amount`  (not `quantity`)
  - No unit conversion: values are already EUR/MWh (not MW → MWh)
  - Aggregation: MEAN of hourly prices (not SUM of energy quantities)
  - No psrType filter (A44 documents don't carry psrType)
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
from backend.models.energy import EnergyPrice

logger = logging.getLogger(__name__)

# German-Luxembourg bidding zone EIC code (lead zone for the energy vertical)
DE_LU_EIC = "10Y1001A1001A82H"
POWER_DE_SYMBOL = "POWER_DE"

_RESOLUTION_HOURS = {"PT60M": 1.0, "PT30M": 0.5, "PT15M": 0.25}


# ─── parse ───────────────────────────────────────────────────────────────────


def parse_day_ahead_prices(xml_text: str) -> dict[str, float]:
    """Parse an A44 Publication_MarketDocument into {YYYY-MM-DD: mean_eur_per_mwh}.

    Walks TimeSeries → Period → Point, reads <price.amount> (EUR/MWh), buckets
    each hourly value to its UTC calendar day, and returns the daily MEAN.

    Namespace-agnostic (matches local tag names only).  Differs from
    parse_generation() in three ways:
      1. Reads `price.amount` instead of `quantity`.
      2. No psrType filter (A44 has no generation-type classification).
      3. Returns MEAN over hourly prices, not SUM of energies.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ValueError(f"ENTSO-E A44 XML parse error: {exc}") from exc

    # Collect raw hourly prices per UTC day: {day: [price, ...]}
    by_day: dict[str, list[float]] = {}

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
                # A44 uses <price.amount>, NOT <quantity>
                price_str = next((e.text for e in point if _localname(e.tag) == "price.amount"), None)
                if pos is None or price_str is None:
                    continue
                try:
                    ts_time = start + timedelta(hours=res_hours * (int(pos) - 1))
                    price = float(price_str)
                except (ValueError, TypeError):
                    continue
                day = ts_time.astimezone(timezone.utc).strftime("%Y-%m-%d")
                by_day.setdefault(day, []).append(price)

    # Daily mean of all hourly prices (in-day hours, not across days)
    return {day: sum(prices) / len(prices) for day, prices in by_day.items() if prices}


# ─── fetch ────────────────────────────────────────────────────────────────────


async def _fetch_zone_month(eic: str, month_start: date, *, overwrite: bool = False) -> str:
    """Fetch one zone's A44 day-ahead prices for a month (raw XML, cached)."""
    nxt = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1)
    period_start = f"{month_start:%Y%m%d}0000"
    period_end = f"{nxt:%Y%m%d}0000"

    async def _do() -> dict:
        async with httpx.AsyncClient(timeout=90) as client:
            resp = await client.get(
                ENTSOE_BASE,
                params={
                    "securityToken": _token(),
                    "documentType": "A44",
                    "in_Domain": eic,
                    "out_Domain": eic,
                    "periodStart": period_start,
                    "periodEnd": period_end,
                },
            )
            resp.raise_for_status()
            return {"xml": resp.text}

    payload = await raw_cache.fetch_or_cache(
        "entsoe_prices", f"{eic}_{month_start:%Y-%m}", month_start, _do, overwrite=overwrite
    )
    return payload.get("xml", "")


# ─── ingest ───────────────────────────────────────────────────────────────────


async def ingest_day_ahead(
    db: Session,
    days: list[str],
    *,
    eic: str = DE_LU_EIC,
    symbol: str = POWER_DE_SYMBOL,
    overwrite: bool = False,
) -> dict:
    """Fetch A44 day-ahead prices for the month(s) spanning `days`, parse, and
    upsert daily means into EnergyPrice(symbol=symbol).

    Returns {"days": n, "written": n} on success, or {"skipped": "no token"}
    if ENTSOE_API_TOKEN is not configured.
    """
    if not days:
        return {"days": 0, "written": 0}
    if not settings.entsoe_api_token:
        logger.warning(
            "entsoe_prices.ingest_day_ahead: ENTSOE_API_TOKEN not set — skipping"
        )
        return {"skipped": "no token"}

    wanted = set(days)
    # Determine the unique months needed
    months = sorted(
        {datetime.strptime(d, "%Y-%m-%d").date().replace(day=1) for d in days}
    )

    prices_by_day: dict[str, float] = {}
    for month_start in months:
        try:
            xml = await _fetch_zone_month(eic, month_start, overwrite=overwrite)
        except httpx.HTTPError as exc:
            logger.warning("entsoe_prices: %s fetch failed: %s", month_start, exc)
            continue
        if not xml:
            continue
        for day, mean_price in parse_day_ahead_prices(xml).items():
            if day in wanted:
                prices_by_day[day] = mean_price

    written = 0
    for day, mean_price in prices_by_day.items():
        _upsert(db, day, symbol, mean_price)
        written += 1
    db.commit()
    logger.info(
        "entsoe_prices.ingest_day_ahead: %d/%d days written (symbol=%s)",
        written,
        len(days),
        symbol,
    )
    return {"days": len(days), "written": written}


def _upsert(db: Session, day: str, symbol: str, close: float) -> None:
    existing = (
        db.query(EnergyPrice)
        .filter(EnergyPrice.date == day, EnergyPrice.symbol == symbol)
        .first()
    )
    if existing:
        existing.close = close
    else:
        db.add(EnergyPrice(date=day, symbol=symbol, close=close))
