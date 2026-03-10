"""
Geofence Event Aggregator.

Reads vessel_positions, groups by zone and day,
produces GeofenceEvent records for the signal engine.

Runs hourly via scheduler, daily final rollup at 23:50 UTC,
+ backfill on startup.
"""

import logging
from datetime import datetime, timezone

from sqlalchemy import case, distinct, func
from sqlalchemy.orm import Session

from backend.database import SessionLocal
from backend.models.vessels import GeofenceEvent, VesselPosition

logger = logging.getLogger(__name__)


def _aggregate_day(db: Session, date_str: str):
    """Aggregate vessel_positions for a single day into geofence_events."""
    # Query: per zone, count unique MMSIs, count slow movers, estimate dwell
    results = (
        db.query(
            VesselPosition.zone,
            func.count(distinct(VesselPosition.mmsi)).label("tanker_count"),
            func.count(
                distinct(
                    case(
                        (VesselPosition.sog < 0.5, VesselPosition.mmsi),
                        else_=None,
                    )
                )
            ).label("slow_movers"),
            func.count(VesselPosition.id).label("position_count"),
        )
        .filter(func.date(VesselPosition.timestamp) == date_str)
        .group_by(VesselPosition.zone)
        .all()
    )

    created = 0
    for row in results:
        zone_name = row.zone
        if not zone_name:
            continue

        # Estimate avg dwell hours: positions per unique vessel * ~1 min per position / 60
        # This is a rough proxy — each position report ≈ 1 minute of presence
        tanker_count = row.tanker_count or 1
        avg_dwell_hours = round((row.position_count / tanker_count) / 60.0, 1)

        # Upsert: update if exists, insert if not
        existing = (
            db.query(GeofenceEvent).filter(GeofenceEvent.zone == zone_name, GeofenceEvent.date == date_str).first()
        )

        if existing:
            existing.tanker_count = row.tanker_count
            existing.slow_movers = row.slow_movers
            existing.avg_dwell_hours = avg_dwell_hours
        else:
            db.add(
                GeofenceEvent(
                    zone=zone_name,
                    date=date_str,
                    tanker_count=row.tanker_count,
                    slow_movers=row.slow_movers,
                    avg_dwell_hours=avg_dwell_hours,
                )
            )
            created += 1

    if results:
        db.commit()

    return created


async def aggregate_geofence_events():
    """Aggregate today's vessel_positions into geofence_events."""
    db = SessionLocal()
    try:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        created = _aggregate_day(db, today)
        logger.info(f"Geofence aggregation: {created} new events for {today}")
    except Exception as e:
        db.rollback()
        logger.error(f"Geofence aggregation failed: {e}")
    finally:
        db.close()


async def aggregate_geofence_daily():
    """End-of-day final aggregation at 23:50 UTC.

    Re-aggregates today and yesterday (catches late-arriving positions
    that the hourly job may have missed for the previous day).
    """
    db = SessionLocal()
    try:
        from datetime import timedelta

        now = datetime.now(timezone.utc)
        today = now.strftime("%Y-%m-%d")
        yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")

        created_today = _aggregate_day(db, today)
        created_yesterday = _aggregate_day(db, yesterday)

        logger.info(
            f"Geofence daily aggregation: "
            f"{created_today} new events for {today}, "
            f"{created_yesterday} new events for {yesterday}"
        )
    except Exception as e:
        db.rollback()
        logger.error(f"Geofence daily aggregation failed: {e}")
    finally:
        db.close()


async def backfill_geofence_events():
    """Backfill geofence_events for all days with vessel_positions data."""
    db = SessionLocal()
    try:
        # Find all distinct dates in vessel_positions
        dates = (
            db.query(func.distinct(func.date(VesselPosition.timestamp)))
            .order_by(func.date(VesselPosition.timestamp))
            .all()
        )

        total_created = 0
        for (date_str,) in dates:
            if date_str:
                created = _aggregate_day(db, date_str)
                total_created += created

        logger.info(f"Geofence backfill complete: {total_created} new events across {len(dates)} days")
    except Exception as e:
        db.rollback()
        logger.error(f"Geofence backfill failed: {e}")
    finally:
        db.close()
