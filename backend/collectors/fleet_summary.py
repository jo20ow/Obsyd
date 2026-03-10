"""
Daily Fleet Summary — aggregates global_vessel_positions into a daily snapshot.

Runs daily at 23:55 UTC via scheduler.
"""

import logging
from datetime import datetime, timezone

from backend.database import SessionLocal
from backend.models.fleet import DailyFleetSummary
from backend.models.vessels import GlobalVesselPosition

logger = logging.getLogger(__name__)


def _classify_region(lon: float, lat: float = 0.0) -> str:
    """Rough ocean region from longitude + latitude."""
    # Mediterranean: lon -10 to 40, lat 30-47
    if -10 <= lon <= 40 and 30 <= lat <= 47:
        return "mediterranean"
    # Atlantic: lon -80 to 0 (or European Atlantic: lon -10 to 0, lat > 47)
    if -80 <= lon <= 0:
        return "atlantic"
    # Indian Ocean: lon 20-100 (below lat 30 to exclude Med/Central Asia)
    if 20 < lon <= 100 and lat < 30:
        return "indian_ocean"
    # Middle East / Persian Gulf area that's not Med
    if 40 < lon <= 100:
        return "indian_ocean"
    # Pacific: everything else
    return "pacific"


async def create_daily_fleet_summary():
    """Aggregate current global_vessel_positions into daily_fleet_summary."""
    db = SessionLocal()
    try:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        positions = db.query(GlobalVesselPosition).all()
        if not positions:
            logger.info("Fleet summary: no global positions available")
            return

        total = len(positions)
        tankers = sum(1 for p in positions if p.is_tanker)
        cargo = sum(1 for p in positions if 70 <= p.ship_type <= 79)
        container = sum(1 for p in positions if 60 <= p.ship_type <= 69)
        anchored = sum(1 for p in positions if p.sog < 0.5)

        sog_values = [p.sog for p in positions if p.sog is not None]
        avg_sog = round(sum(sog_values) / len(sog_values), 2) if sog_values else 0.0

        regions = {"atlantic": 0, "pacific": 0, "indian_ocean": 0, "mediterranean": 0}
        for p in positions:
            region = _classify_region(p.longitude, p.latitude)
            regions[region] += 1

        existing = db.query(DailyFleetSummary).filter(DailyFleetSummary.date == today).first()

        if existing:
            existing.total_vessels = total
            existing.tanker_count = tankers
            existing.cargo_count = cargo
            existing.container_count = container
            existing.avg_sog = avg_sog
            existing.anchored_count = anchored
            existing.atlantic_count = regions["atlantic"]
            existing.pacific_count = regions["pacific"]
            existing.indian_ocean_count = regions["indian_ocean"]
            existing.mediterranean_count = regions["mediterranean"]
        else:
            db.add(
                DailyFleetSummary(
                    date=today,
                    total_vessels=total,
                    tanker_count=tankers,
                    cargo_count=cargo,
                    container_count=container,
                    avg_sog=avg_sog,
                    anchored_count=anchored,
                    atlantic_count=regions["atlantic"],
                    pacific_count=regions["pacific"],
                    indian_ocean_count=regions["indian_ocean"],
                    mediterranean_count=regions["mediterranean"],
                )
            )

        db.commit()
        logger.info(
            f"Fleet summary {today}: {total} vessels, {tankers} tankers, {anchored} anchored, avg SOG {avg_sog}"
        )
    except Exception as e:
        db.rollback()
        logger.error(f"Fleet summary failed: {e}")
    finally:
        db.close()
