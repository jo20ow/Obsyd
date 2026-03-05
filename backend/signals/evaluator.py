"""
Signal evaluator — runs all rule checks against current data.

Called periodically by the scheduler to generate alerts.
"""

import logging

from sqlalchemy.orm import Session
from sqlalchemy import func

from backend.database import SessionLocal
from backend.models.vessels import VesselPosition, GeofenceEvent
from backend.geofences.zones import ZONES
from backend.signals.rules import (
    check_floating_storage,
    check_flow_anomaly,
    check_cushing_drawdown,
)

logger = logging.getLogger(__name__)


def _compute_zone_stats(db: Session, zone_name: str) -> dict:
    """Compute current stats for a zone from vessel positions."""
    positions = (
        db.query(VesselPosition)
        .filter(VesselPosition.zone == zone_name)
        .order_by(VesselPosition.timestamp.desc())
        .limit(500)
        .all()
    )

    if not positions:
        return {"count": 0, "slow_movers": 0}

    slow = sum(1 for p in positions if p.sog < 0.5)
    return {"count": len(positions), "slow_movers": slow}


async def evaluate_signals():
    """Run all signal rules against current database state."""
    db = SessionLocal()
    try:
        # 1. Floating storage: check each zone for slow-moving tankers
        for zone in ZONES:
            stats = _compute_zone_stats(db, zone["name"])
            if stats["slow_movers"] > 0:
                # Use a rough avg dwell estimate based on slow mover ratio
                avg_dwell = 72.0 if stats["slow_movers"] > 3 else 24.0
                check_floating_storage(
                    db, zone["name"], stats["slow_movers"], avg_dwell
                )

        # 2. Flow anomaly: check geofence event history per zone
        for zone in ZONES:
            latest_event = (
                db.query(GeofenceEvent)
                .filter(GeofenceEvent.zone == zone["name"])
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
