"""
FRED API Collector.

Fetches macro indicators from the Federal Reserve Economic Data API.
DXY proxy, yield curve, CPI, Fed Funds Rate.
Public Domain - API key required (free).
"""

import logging
from datetime import datetime, timezone

import httpx
from sqlalchemy.orm import Session

from backend.config import settings
from backend.models.prices import FREDSeries

logger = logging.getLogger(__name__)

# FRED series configurations
FRED_SERIES = {
    "DTWEXBGS": {
        "description": "Trade Weighted US Dollar Index (Broad)",  # DXY proxy
    },
    "DGS10": {
        "description": "10-Year Treasury Constant Maturity Rate",
    },
    "DGS2": {
        "description": "2-Year Treasury Constant Maturity Rate",
    },
    "T10Y2Y": {
        "description": "10-Year Treasury Minus 2-Year Treasury (Yield Curve)",
    },
    "CPIAUCSL": {
        "description": "Consumer Price Index (All Urban, Seasonally Adjusted)",
    },
    "FEDFUNDS": {
        "description": "Effective Federal Funds Rate",
    },
    "DCOILWTICO": {
        "description": "WTI Crude Oil Price (Daily, FRED mirror)",
    },
    "DCOILBRENTEU": {
        "description": "Brent Crude Oil Price (Daily, FRED mirror)",
    },
}


async def fetch_fred_series(
    series_id: str, limit: int = 365
) -> list[dict]:
    """
    Fetch a FRED series.

    Args:
        series_id: FRED series ID (e.g. "DTWEXBGS").
        limit: Number of observations to retrieve.

    Returns:
        List of dicts with 'date' and 'value' keys.
    """
    if not settings.fred_api_key:
        logger.warning("FRED API key not configured. Skipping fetch.")
        return []

    params = {
        "series_id": series_id,
        "api_key": settings.fred_api_key,
        "file_type": "json",
        "sort_order": "desc",
        "limit": limit,
    }

    url = f"{settings.fred_base_url}/series/observations"

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
            observations = data.get("observations", [])
            logger.info(f"FRED {series_id}: fetched {len(observations)} observations")
            return observations
        except httpx.HTTPError as e:
            logger.error(f"FRED {series_id} fetch failed: {e}")
            return []


async def collect_fred(db: Session):
    """
    Fetch all configured FRED series and store in database.
    Called by the scheduler (daily).
    """
    for series_id, series_cfg in FRED_SERIES.items():
        observations = await fetch_fred_series(series_id)

        for obs in observations:
            date = obs.get("date", "")
            value_str = obs.get("value", ".")
            if value_str == ".":
                continue  # FRED uses "." for missing values

            try:
                value = float(value_str)
            except (ValueError, TypeError):
                continue

            existing = (
                db.query(FREDSeries)
                .filter(FREDSeries.series_id == series_id, FREDSeries.date == date)
                .first()
            )

            if existing:
                existing.value = value
                existing.fetched_at = datetime.now(timezone.utc)
            else:
                db.add(
                    FREDSeries(
                        series_id=series_id,
                        date=date,
                        value=value,
                        description=series_cfg["description"],
                    )
                )

        db.commit()
        logger.info(f"FRED {series_id}: stored/updated in database")
