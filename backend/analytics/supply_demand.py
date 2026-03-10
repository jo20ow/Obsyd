"""EIA Supply-Demand Balance + AIS Divergence.

Fetches EIA STEO forecasts (global supply vs demand) and compares with
real-time AIS vessel observations from Houston zone.  The divergence between
official forecast and live shipping data is the core analytical value.
"""

import logging
from datetime import date, timedelta

import httpx
from sqlalchemy import func

from backend.config import settings
from backend.database import SessionLocal
from backend.models.analytics import SupplyDemandBalance
from backend.models.prices import EIAPrice
from backend.models.vessels import GeofenceEvent

logger = logging.getLogger(__name__)


async def compute_supply_demand():
    """Weekly supply-demand balance computation."""
    db = SessionLocal()
    try:
        today = date.today().isoformat()
        existing = db.query(SupplyDemandBalance).filter(SupplyDemandBalance.date == today).first()
        if existing:
            logger.info("Supply-demand already computed for %s", today)
            return

        # 1. STEO world supply/demand
        production, consumption = await _fetch_steo()
        implied_balance = None
        if production is not None and consumption is not None:
            implied_balance = round(production - consumption, 2)

        # 2. US imports from EIA (already in DB from weekly collector)
        us_imports = _get_latest_eia_value(db, "PET.WCRIMUS2.W")

        # 3. Houston AIS data
        houston_count, houston_avg, houston_dev = _get_houston_ais(db)

        # 4. Divergence
        div_type, div_detail = _detect_divergence(implied_balance, houston_dev)

        record = SupplyDemandBalance(
            date=today,
            world_production=production,
            world_consumption=consumption,
            implied_balance=implied_balance,
            us_imports_eia=us_imports,
            houston_ais_tankers=houston_count,
            houston_deviation=houston_dev,
            divergence_type=div_type,
            divergence_detail=div_detail,
        )
        db.add(record)
        db.commit()
        logger.info(
            "Supply-demand: balance=%s mb/d, houston=%s, div=%s",
            f"{implied_balance:.1f}" if implied_balance else "N/A",
            houston_count or "N/A",
            div_type or "none",
        )
    except Exception as e:
        logger.error("Supply-demand computation failed: %s", e)
        db.rollback()
    finally:
        db.close()


async def _fetch_steo():
    """Fetch latest STEO world production and consumption from EIA API v2."""
    if not settings.eia_api_key:
        logger.warning("No EIA API key — skipping STEO fetch")
        return None, None

    api_key = settings.eia_api_key.get_secret_value()
    base = settings.eia_base_url
    production = None
    consumption = None

    async with httpx.AsyncClient(timeout=30.0) as client:
        for series_id, label in [
            ("PAPR_WORLD", "production"),
            ("PATC_WORLD", "consumption"),
        ]:
            try:
                params = [
                    ("api_key", api_key),
                    ("frequency", "monthly"),
                    ("data[0]", "value"),
                    ("facets[seriesId][]", series_id),
                    ("sort[0][column]", "period"),
                    ("sort[0][direction]", "desc"),
                    ("length", "6"),
                ]
                resp = await client.get(f"{base}/steo/data/", params=params)
                resp.raise_for_status()
                rows = resp.json().get("response", {}).get("data", [])
                if rows:
                    val = rows[0].get("value")
                    if val is not None:
                        parsed = float(val)
                        if label == "production":
                            production = parsed
                        else:
                            consumption = parsed
                        logger.info("STEO %s: %.2f mb/d", label, parsed)
            except Exception as e:
                logger.warning("STEO %s fetch failed: %s", series_id, e)

    return production, consumption


def _get_latest_eia_value(db, series_id):
    """Most recent value for an EIA series from the DB."""
    row = db.query(EIAPrice).filter(EIAPrice.series_id == series_id).order_by(EIAPrice.period.desc()).first()
    return row.value if row else None


def _get_houston_ais(db):
    """Houston zone tanker count: 7d avg, 30d avg, deviation."""
    today = date.today()
    d7 = (today - timedelta(days=7)).isoformat()
    d30 = (today - timedelta(days=30)).isoformat()

    recent = (
        db.query(func.avg(GeofenceEvent.tanker_count))
        .filter(GeofenceEvent.zone == "houston", GeofenceEvent.date >= d7)
        .scalar()
    )
    baseline = (
        db.query(func.avg(GeofenceEvent.tanker_count))
        .filter(GeofenceEvent.zone == "houston", GeofenceEvent.date >= d30)
        .scalar()
    )

    houston_count = int(recent) if recent else None
    houston_avg = float(baseline) if baseline else None
    houston_dev = None
    if houston_count is not None and houston_avg and houston_avg > 0:
        houston_dev = round((houston_count - houston_avg) / houston_avg * 100, 1)

    return houston_count, houston_avg, houston_dev


def _detect_divergence(balance, houston_dev):
    """Detect divergence between EIA forecasts and AIS observations."""
    if houston_dev is None:
        return None, None

    if houston_dev < -10:
        if balance is not None and balance > 0:
            return (
                "EIA_AIS_DIVERGENCE",
                "Official forecast implies surplus conditions, but real-time vessel data "
                f"shows below-average Gulf Coast arrivals ({houston_dev:+.0f}% vs 30d avg).",
            )
        return (
            "EIA_AIS_DIVERGENCE",
            "Real-time AIS data shows below-average Houston tanker activity "
            f"({houston_dev:+.0f}% vs 30d avg), suggesting weaker import flows.",
        )

    if houston_dev > 10:
        if balance is not None and balance < 0:
            return (
                "EIA_AIS_DIVERGENCE",
                "EIA STEO projects a supply deficit, yet Houston AIS shows elevated "
                f"tanker arrivals ({houston_dev:+.0f}% vs avg).",
            )

    return (
        "EIA_AIS_CONFIRMED",
        f"AIS data consistent with EIA baseline — Houston activity at {houston_dev:+.0f}% vs 30d average.",
    )
