"""
Voyage Detector — Simple Zone-to-Zone Transit Detection.

When the same MMSI appears in different geofence zones with a gap
of 6+ hours, it indicates a transit (voyage). Runs every 2 hours.

Constraints:
  - Transit must be 6-720 hours (ignore edge effects and stale data)
  - Dedup: no duplicate voyage for same MMSI + origin + dest within 48h
"""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func

from backend.database import SessionLocal
from backend.models.vessels import VesselPosition, VoyageEvent

logger = logging.getLogger(__name__)

MIN_TRANSIT_HOURS = 6
MAX_TRANSIT_HOURS = 720
DEDUP_HOURS = 48
LOOKBACK_DAYS = 14


async def detect_voyages():
    """Scan vessel_positions for zone-to-zone transits."""
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=LOOKBACK_DAYS)

        # Get all MMSIs with positions in the lookback window
        mmsis = (
            db.query(VesselPosition.mmsi)
            .filter(VesselPosition.timestamp >= cutoff)
            .group_by(VesselPosition.mmsi)
            .having(func.count(VesselPosition.id) >= 2)
            .all()
        )

        new_count = 0

        for (mmsi,) in mmsis:
            # Get all positions for this MMSI, ordered by time
            positions = (
                db.query(
                    VesselPosition.zone, VesselPosition.timestamp, VesselPosition.ship_name, VesselPosition.ship_type
                )
                .filter(
                    VesselPosition.mmsi == mmsi,
                    VesselPosition.timestamp >= cutoff,
                )
                .order_by(VesselPosition.timestamp.asc())
                .all()
            )

            if len(positions) < 2:
                continue

            # Group consecutive positions by zone
            segments = []
            current_zone = positions[0].zone
            seg_start = positions[0].timestamp
            seg_end = positions[0].timestamp
            last_name = positions[0].ship_name
            last_type = positions[0].ship_type

            for pos in positions[1:]:
                if pos.zone == current_zone:
                    seg_end = pos.timestamp
                    last_name = pos.ship_name or last_name
                    last_type = pos.ship_type or last_type
                else:
                    segments.append(
                        {
                            "zone": current_zone,
                            "first_seen": seg_start,
                            "last_seen": seg_end,
                        }
                    )
                    current_zone = pos.zone
                    seg_start = pos.timestamp
                    seg_end = pos.timestamp
                    last_name = pos.ship_name or last_name
                    last_type = pos.ship_type or last_type

            # Don't forget the last segment
            segments.append(
                {
                    "zone": current_zone,
                    "first_seen": seg_start,
                    "last_seen": seg_end,
                }
            )

            # Detect transitions between consecutive segments
            for i in range(len(segments) - 1):
                origin = segments[i]
                dest = segments[i + 1]

                # Must be different zones
                if origin["zone"] == dest["zone"]:
                    continue

                transit_secs = (dest["first_seen"] - origin["last_seen"]).total_seconds()
                transit_hours = transit_secs / 3600

                # Filter: transit must be 6-720 hours
                if transit_hours < MIN_TRANSIT_HOURS or transit_hours > MAX_TRANSIT_HOURS:
                    continue

                # Dedup: check for existing voyage within 48h
                dedup_cutoff = dest["first_seen"] - timedelta(hours=DEDUP_HOURS)
                existing = (
                    db.query(VoyageEvent)
                    .filter(
                        VoyageEvent.mmsi == mmsi,
                        VoyageEvent.origin_zone == origin["zone"],
                        VoyageEvent.destination_zone == dest["zone"],
                        VoyageEvent.destination_first_seen >= dedup_cutoff,
                    )
                    .first()
                )
                if existing:
                    continue

                db.add(
                    VoyageEvent(
                        mmsi=mmsi,
                        ship_name=last_name,
                        ship_type=last_type,
                        origin_zone=origin["zone"],
                        origin_first_seen=origin["first_seen"],
                        origin_last_seen=origin["last_seen"],
                        destination_zone=dest["zone"],
                        destination_first_seen=dest["first_seen"],
                        transit_hours=round(transit_hours, 1),
                        status="arrived",
                    )
                )
                new_count += 1

        db.commit()
        total = db.query(VoyageEvent).count()
        logger.info(f"Voyage detection: {new_count} new voyages detected, {total} total")
    except Exception as e:
        db.rollback()
        logger.error(f"Voyage detection failed: {e}")
    finally:
        db.close()
