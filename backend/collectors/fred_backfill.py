"""
One-time FRED backfill — extends WTI/Brent history back to 2019-01-01.

Run once, then the daily collector keeps data current.
"""

import logging
from datetime import datetime

import httpx
from sqlalchemy.orm import Session

from backend.config import settings
from backend.models.prices import FREDSeries

logger = logging.getLogger(__name__)

BACKFILL_SERIES = {
    "DCOILWTICO": "WTI Crude Oil Price (Daily, FRED mirror)",
    "DCOILBRENTEU": "Brent Crude Oil Price (Daily, FRED mirror)",
}

BACKFILL_START = "2019-01-01"


async def backfill_fred(db: Session):
    """Fetch WTI/Brent from 2019 onward, insert only missing dates."""
    if not settings.fred_api_key:
        logger.warning("FRED backfill: no API key")
        return

    for series_id, description in BACKFILL_SERIES.items():
        params = {
            "series_id": series_id,
            "api_key": settings.fred_api_key,
            "file_type": "json",
            "observation_start": BACKFILL_START,
            "sort_order": "asc",
            "limit": 10000,
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(
                    f"{settings.fred_base_url}/series/observations",
                    params=params,
                )
                resp.raise_for_status()
                observations = resp.json().get("observations", [])
        except httpx.HTTPError as e:
            logger.error(f"FRED backfill {series_id} failed: {e}")
            continue

        # Get existing dates to avoid duplicates
        existing_dates = set(
            r.date for r in db.query(FREDSeries.date).filter(
                FREDSeries.series_id == series_id
            ).all()
        )

        inserted = 0
        for obs in observations:
            date = obs.get("date", "")
            value_str = obs.get("value", ".")
            if value_str == "." or date in existing_dates:
                continue
            try:
                value = float(value_str)
            except (ValueError, TypeError):
                continue

            db.add(FREDSeries(
                series_id=series_id,
                date=date,
                value=value,
                description=description,
                fetched_at=datetime.utcnow(),
            ))
            inserted += 1

        db.commit()
        logger.info(f"FRED backfill {series_id}: {inserted} new, {len(existing_dates)} existing")
