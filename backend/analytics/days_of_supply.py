"""Days of Supply — dynamic US crude oil inventory coverage metric.

Calculates how many days US crude inventories last at current consumption,
compares against 5-year seasonal average, and tracks the 4-week trend.
"""

import logging
from datetime import date, datetime

import httpx

from backend.config import settings
from backend.database import SessionLocal
from backend.models.analytics import DaysOfSupplyHistory

logger = logging.getLogger(__name__)

# EIA API v2 series configs
_SERIES = {
    "commercial_stocks": {
        "route": "petroleum/stoc/wstk/data/",
        "facets": {"duoarea": ["NUS"], "product": ["EPC0"], "process": ["SAE"]},
        "desc": "US Commercial Crude Stocks excl. SPR",
    },
    "spr_stocks": {
        "route": "petroleum/stoc/wstk/data/",
        "facets": {"duoarea": ["NUS"], "product": ["EPC0"], "process": ["SAS"]},
        "desc": "US SPR Stocks",
    },
    "product_supplied": {
        "route": "petroleum/sum/sndw/data/",
        "facets": {"series": ["WRPUPUS2"]},
        "desc": "US Product Supplied of Petroleum Products",
    },
}


async def compute_days_of_supply():
    """Weekly Days of Supply computation."""
    db = SessionLocal()
    try:
        today = date.today().isoformat()
        existing = db.query(DaysOfSupplyHistory).filter(DaysOfSupplyHistory.date == today).first()
        if existing:
            logger.info("Days of supply already computed for %s", today)
            return

        # Fetch EIA data (5 years for seasonal comparison)
        commercial = await _fetch_eia("commercial_stocks", limit=260)
        spr = await _fetch_eia("spr_stocks", limit=4)
        supplied = await _fetch_eia("product_supplied", limit=260)

        if not commercial or not supplied:
            logger.warning("Insufficient EIA data for days of supply")
            return

        latest_stocks = commercial[0].get("value")
        latest_spr = spr[0].get("value") if spr else None
        latest_supplied = supplied[0].get("value")
        latest_period = commercial[0].get("period", today)

        if latest_stocks is None or latest_supplied is None:
            logger.warning("Missing latest EIA values")
            return

        stocks_val = float(latest_stocks)  # thousand barrels
        spr_val = float(latest_spr) if latest_spr else 0
        supplied_val = float(latest_supplied)  # thousand barrels per day

        if supplied_val <= 0:
            logger.warning("Invalid product supplied: %s", supplied_val)
            return

        commercial_days = round(stocks_val / supplied_val, 1)
        total_days = round((stocks_val + spr_val) / supplied_val, 1)

        avg_5y = _compute_5y_average(commercial, supplied, latest_period)
        deviation = round(commercial_days - avg_5y, 1) if avg_5y else None
        trend_4w = _compute_trend(commercial, supplied)

        if deviation is not None:
            if deviation < -3:
                assessment = "TIGHT"
            elif deviation > 3:
                assessment = "COMFORTABLE"
            else:
                assessment = "IN_LINE"
        else:
            assessment = None

        record = DaysOfSupplyHistory(
            date=latest_period,
            commercial_stocks=stocks_val,
            spr_stocks=spr_val,
            product_supplied=supplied_val,
            commercial_days=commercial_days,
            total_days=total_days,
            avg_5y_days=avg_5y,
            deviation=deviation,
            trend_4w=trend_4w,
            assessment=assessment,
        )
        db.add(record)
        db.commit()

        logger.info(
            "Days of supply: %.1f days (5Y avg: %s, dev: %s, trend: %s, %s)",
            commercial_days,
            f"{avg_5y:.1f}" if avg_5y else "N/A",
            f"{deviation:+.1f}" if deviation is not None else "N/A",
            f"{trend_4w:+.1f}" if trend_4w is not None else "N/A",
            assessment or "N/A",
        )
    except Exception as e:
        logger.error("Days of supply computation failed: %s", e)
        db.rollback()
    finally:
        db.close()


async def _fetch_eia(key, limit=52):
    """Fetch EIA weekly series via API v2."""
    if not settings.eia_api_key:
        return []

    cfg = _SERIES.get(key)
    if not cfg:
        return []

    params = [
        ("api_key", settings.eia_api_key.get_secret_value()),
        ("frequency", "weekly"),
        ("data[0]", "value"),
        ("length", str(limit)),
        ("sort[0][column]", "period"),
        ("sort[0][direction]", "desc"),
    ]
    for facet_key, facet_values in cfg["facets"].items():
        for val in facet_values:
            params.append((f"facets[{facet_key}][]", val))

    url = f"{settings.eia_base_url}/{cfg['route']}"

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            rows = resp.json().get("response", {}).get("data", [])
            logger.info("EIA %s: fetched %d rows", key, len(rows))
            return rows
        except Exception as e:
            logger.warning("EIA %s fetch failed: %s", key, e)
            return []


def _compute_5y_average(stocks_data, supplied_data, current_period):
    """5-year average days of supply for the same calendar week."""
    try:
        current_dt = datetime.strptime(current_period, "%Y-%m-%d")
        current_week = current_dt.isocalendar()[1]
    except (ValueError, TypeError):
        return None

    stocks_map = {}
    for row in stocks_data:
        p = row.get("period", "")
        v = row.get("value")
        if p and v is not None:
            stocks_map[p] = float(v)

    supplied_map = {}
    for row in supplied_data:
        p = row.get("period", "")
        v = row.get("value")
        if p and v is not None:
            supplied_map[p] = float(v)

    days_values = []
    for year_offset in range(1, 6):
        target_year = current_dt.year - year_offset
        for period, stocks in stocks_map.items():
            try:
                dt = datetime.strptime(period, "%Y-%m-%d")
                if dt.year == target_year and dt.isocalendar()[1] == current_week:
                    if period in supplied_map and supplied_map[period] > 0:
                        days_values.append(stocks / supplied_map[period])
                    break
            except (ValueError, TypeError):
                continue

    return round(sum(days_values) / len(days_values), 1) if days_values else None


def _compute_trend(stocks_data, supplied_data):
    """4-week change in days of supply."""
    if len(stocks_data) < 5 or len(supplied_data) < 5:
        return None

    supplied_map = {}
    for row in supplied_data:
        p = row.get("period", "")
        v = row.get("value")
        if p and v is not None:
            supplied_map[p] = float(v)

    current = stocks_data[0]
    four_weeks = stocks_data[4] if len(stocks_data) > 4 else None
    if not four_weeks:
        return None

    c_stocks = current.get("value")
    c_period = current.get("period", "")
    f_stocks = four_weeks.get("value")
    f_period = four_weeks.get("period", "")

    if c_stocks is None or f_stocks is None:
        return None

    c_supplied = supplied_map.get(c_period)
    f_supplied = supplied_map.get(f_period)

    if not c_supplied or c_supplied <= 0 or not f_supplied or f_supplied <= 0:
        return None

    current_days = float(c_stocks) / c_supplied
    four_weeks_days = float(f_stocks) / f_supplied
    return round(current_days - four_weeks_days, 1)
