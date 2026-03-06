"""
Signal evaluator — runs all rule checks against current data.

Called periodically by the scheduler to generate alerts.
"""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session
from sqlalchemy import func

from backend.database import SessionLocal
from backend.models.vessels import VesselPosition, GeofenceEvent
from backend.geofences.zones import ZONES
from backend.signals.rules import (
    check_anchored_vessels,
    check_flow_anomaly,
    check_cushing_drawdown,
)

STALE_DAYS = 7  # ignore geofence events older than this

logger = logging.getLogger(__name__)


def _compute_zone_stats(db: Session, zone_name: str) -> dict:
    """Compute current slow-mover count and 7-day average for a zone."""
    # Current slow movers: latest position per MMSI, then count SOG < 0.5
    latest_ids = (
        db.query(func.max(VesselPosition.id))
        .filter(VesselPosition.zone == zone_name)
        .group_by(VesselPosition.mmsi)
        .subquery()
    )
    positions = (
        db.query(VesselPosition)
        .filter(VesselPosition.id.in_(latest_ids))
        .all()
    )

    slow_movers = sum(1 for p in positions if p.sog < 0.5)

    # 7-day history: count slow movers from geofence events
    events = (
        db.query(GeofenceEvent.slow_movers)
        .filter(GeofenceEvent.zone == zone_name)
        .order_by(GeofenceEvent.date.desc())
        .limit(7)
        .all()
    )

    if len(events) >= 7:
        avg_slow_7d = sum(e.slow_movers for e in events) / len(events)
    else:
        avg_slow_7d = None  # insufficient history

    return {"count": len(positions), "slow_movers": slow_movers, "avg_slow_7d": avg_slow_7d}


async def evaluate_signals():
    """Run all signal rules against current database state."""
    db = SessionLocal()
    try:
        # 1. Anchored vessels: check each zone for slow-moving tankers
        for zone in ZONES:
            stats = _compute_zone_stats(db, zone["name"])
            if stats["slow_movers"] > 0:
                check_anchored_vessels(
                    db, zone["name"], stats["slow_movers"],
                    stats["count"], stats["avg_slow_7d"],
                )

        # 2. Flow anomaly: check geofence event history per zone
        stale_cutoff = (datetime.now(timezone.utc) - timedelta(days=STALE_DAYS)).strftime("%Y-%m-%d")
        for zone in ZONES:
            latest_event = (
                db.query(GeofenceEvent)
                .filter(GeofenceEvent.zone == zone["name"],
                        GeofenceEvent.date >= stale_cutoff)
                .order_by(GeofenceEvent.date.desc())
                .first()
            )
            if latest_event:
                check_flow_anomaly(db, zone["name"], latest_event.tanker_count)

        # 3. Cushing drawdown
        check_cushing_drawdown(db)

        logger.info("Signal evaluation complete")
    except Exception as e:
        logger.error(f"Signal evaluation failed: {e}")
    finally:
        db.close()
