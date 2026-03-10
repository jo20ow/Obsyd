"""
Floating Storage Detector.

Identifies tankers stationary (avg SOG < 0.5 kn) for 7+ consecutive days.
Runs every 6 hours via scheduler.
"""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func

from backend.database import SessionLocal
from backend.models.vessels import FloatingStorageEvent, VesselPosition

logger = logging.getLogger(__name__)

MIN_DAYS = 7
MAX_AVG_SOG = 0.5
RESOLVE_SOG = 2.0  # resolve when recent avg SOG > 2 kn


async def detect_floating_storage():
    """Scan vessel_positions for floating storage candidates."""
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=MIN_DAYS)

        # Find MMSIs with positions spanning 7+ days and avg SOG < 0.5
        candidates = (
            db.query(
                VesselPosition.mmsi,
                func.min(VesselPosition.timestamp).label("first_seen"),
                func.max(VesselPosition.timestamp).label("last_seen"),
                func.avg(VesselPosition.sog).label("avg_sog"),
                func.count(VesselPosition.id).label("pos_count"),
            )
            .filter(VesselPosition.timestamp >= cutoff)
            .group_by(VesselPosition.mmsi)
            .having(func.avg(VesselPosition.sog) < MAX_AVG_SOG)
            .having(
                (
                    func.julianday(func.max(VesselPosition.timestamp))
                    - func.julianday(func.min(VesselPosition.timestamp))
                )
                >= MIN_DAYS
            )
            .all()
        )

        new_count = 0
        updated_count = 0

        for c in candidates:
            duration = (c.last_seen - c.first_seen).total_seconds() / 86400

            # Get latest position details for this MMSI
            latest = (
                db.query(VesselPosition)
                .filter(VesselPosition.mmsi == c.mmsi)
                .order_by(VesselPosition.timestamp.desc())
                .first()
            )
            if not latest:
                continue

            # Check if we already track this MMSI
            existing = (
                db.query(FloatingStorageEvent)
                .filter(
                    FloatingStorageEvent.mmsi == c.mmsi,
                    FloatingStorageEvent.status == "active",
                )
                .first()
            )

            if existing:
                # Update existing event
                existing.last_seen = c.last_seen
                existing.duration_days = round(duration, 1)
                existing.avg_sog = round(c.avg_sog, 3)
                existing.latitude = latest.latitude
                existing.longitude = latest.longitude
                existing.zone = latest.zone
                updated_count += 1
            else:
                # New floating storage event
                db.add(
                    FloatingStorageEvent(
                        mmsi=c.mmsi,
                        ship_name=latest.ship_name,
                        ship_type=latest.ship_type,
                        zone=latest.zone,
                        latitude=latest.latitude,
                        longitude=latest.longitude,
                        first_seen=c.first_seen,
                        last_seen=c.last_seen,
                        duration_days=round(duration, 1),
                        avg_sog=round(c.avg_sog, 3),
                        status="active",
                    )
                )
                new_count += 1

        # Resolve events where vessel is now moving (avg SOG > 2 kn in last 24h)
        resolved_count = 0
        active_events = db.query(FloatingStorageEvent).filter(FloatingStorageEvent.status == "active").all()
        recent_cutoff = now - timedelta(hours=24)

        for evt in active_events:
            recent_avg = (
                db.query(func.avg(VesselPosition.sog))
                .filter(
                    VesselPosition.mmsi == evt.mmsi,
                    VesselPosition.timestamp >= recent_cutoff,
                )
                .scalar()
            )
            if recent_avg is not None and recent_avg > RESOLVE_SOG:
                evt.status = "resolved"
                evt.last_seen = now
                resolved_count += 1

        db.commit()
        total_active = db.query(FloatingStorageEvent).filter(FloatingStorageEvent.status == "active").count()
        logger.info(
            f"Floating storage: {new_count} new, {updated_count} updated, "
            f"{resolved_count} resolved, {total_active} active"
        )
    except Exception as e:
        db.rollback()
        logger.error(f"Floating storage detection failed: {e}")
    finally:
        db.close()
