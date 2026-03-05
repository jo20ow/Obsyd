"""
EIA API v2 Collector.

Fetches energy prices and inventory data from the U.S. Energy Information Administration.
Primary source for WTI, Brent, Natural Gas prices and Cushing stock levels.
Public Domain - no rate limits, API key recommended but optional.
"""

import logging
from datetime import datetime

import httpx
from sqlalchemy.orm import Session

from backend.config import settings
from backend.models.prices import EIAPrice

logger = logging.getLogger(__name__)

# EIA API v2 series configurations
# Facets use facets[key][]=value syntax; data[0]=value requests the value column.
EIA_SERIES = {
    "PET.RWTC.W": {
        "route": "petroleum/pri/spt/data/",
        "facets": {"product": ["EPCWTI"]},
        "unit": "$/barrel",
        "description": "WTI Crude Oil Spot Price (Weekly)",
    },
    "PET.RBRTE.W": {
        "route": "petroleum/pri/spt/data/",
        "facets": {"product": ["EPCBRENT"]},
        "unit": "$/barrel",
        "description": "Brent Crude Oil Spot Price (Weekly)",
    },
    "NG.RNGWHHD.W": {
        "route": "natural-gas/pri/fut/data/",
        "facets": {"series": ["RNGWHHD"]},
        "unit": "$/MMBtu",
        "description": "Henry Hub Natural Gas Spot Price (Weekly)",
    },
    "PET.WCSSTUS1.W": {
        "route": "petroleum/stoc/wstk/data/",
        "facets": {"duoarea": ["YCUOK"], "product": ["EPC0"]},
        "unit": "thousand barrels",
        "description": "Cushing OK Crude Oil Stocks (Weekly, WPSR)",
    },
}


async def fetch_eia_series(series_key: str, limit: int = 52) -> list[dict]:
    """
    Fetch a single EIA series via API v2.

    Args:
        series_key: Key into EIA_SERIES config.
        limit: Number of data points to retrieve (default: 52 weeks = 1 year).

    Returns:
        List of dicts with 'period' and 'value' keys.
    """
    if series_key not in EIA_SERIES:
        logger.error(f"Unknown EIA series: {series_key}")
        return []

    series_cfg = EIA_SERIES[series_key]

    # Build query params with EIA v2 bracket syntax
    params: list[tuple[str, str]] = [
        ("api_key", settings.eia_api_key or ""),
        ("frequency", "weekly"),
        ("data[0]", "value"),
        ("length", str(limit)),
        ("sort[0][column]", "period"),
        ("sort[0][direction]", "desc"),
    ]
    for facet_key, facet_values in series_cfg["facets"].items():
        for val in facet_values:
            params.append((f"facets[{facet_key}][]", val))

    url = f"{settings.eia_base_url}/{series_cfg['route']}"

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
            rows = data.get("response", {}).get("data", [])
            logger.info(f"EIA {series_key}: fetched {len(rows)} data points")
            return rows
        except httpx.HTTPError as e:
            logger.error(f"EIA {series_key} fetch failed: {e}")
            return []


async def collect_eia(db: Session):
    """
    Fetch all configured EIA series and store in database.
    Called by the scheduler (weekly).
    """
    for series_key, series_cfg in EIA_SERIES.items():
        rows = await fetch_eia_series(series_key)

        for row in rows:
            period = row.get("period", "")
            value = row.get("value")
            if value is None:
                continue

            try:
                value = float(value)
            except (ValueError, TypeError):
                continue

            existing = (
                db.query(EIAPrice)
                .filter(EIAPrice.series_id == series_key, EIAPrice.period == period)
                .first()
            )

            if existing:
                existing.value = value
                existing.fetched_at = datetime.utcnow()
            else:
                db.add(
                    EIAPrice(
                        series_id=series_key,
                        period=period,
                        value=value,
                        unit=series_cfg["unit"],
                        description=series_cfg["description"],
                    )
                )

        db.commit()
        logger.info(f"EIA {series_key}: stored/updated in database")
