"""
STS Event Collector — scheduled every 4 hours.

Runs STS candidate + proximity pair detection and persists results
to the sts_events table. Handles upsert logic:
- New detections → insert with status="active"
- Existing active events still detected → update last_seen + duration
- Existing active events no longer detected → set status="resolved"
"""

import logging
from datetime import datetime, timezone

from backend.database import SessionLocal
from backend.models.pro_features import STSEvent
from backend.signals.sts_detection import detect_proximity_pairs, detect_sts_candidates

logger = logging.getLogger(__name__)


async def collect_sts_events():
    """Main entry point called by scheduler every 4 hours."""
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)

        # Detect current candidates + pairs
        candidates = detect_sts_candidates(db)
        pairs = detect_proximity_pairs(db)

        # Build set of currently-detected event keys
        current_keys: set[str] = set()

        # --- Upsert candidates ---
        for c in candidates:
            key = f"candidate:{c['mmsi']}"
            current_keys.add(key)

            existing = (
                db.query(STSEvent)
                .filter(
                    STSEvent.mmsi_1 == str(c["mmsi"]),
                    STSEvent.event_type == "candidate",
                    STSEvent.zone == c["sts_hotspot"],
                    STSEvent.status == "active",
                )
                .first()
            )

            if existing:
                existing.last_seen = now
                existing.lat = c["lat"]
                existing.lon = c["lon"]
                existing.duration_hours = (
                    now - existing.first_seen.replace(tzinfo=timezone.utc)
                ).total_seconds() / 3600
            else:
                db.add(
                    STSEvent(
                        mmsi_1=str(c["mmsi"]),
                        ship_name_1=c.get("ship_name"),
                        ship_class_1=c.get("class"),
                        mmsi_2=None,
                        ship_name_2=None,
                        ship_class_2=None,
                        event_type="candidate",
                        zone=c["sts_hotspot"],
                        lat=c["lat"],
                        lon=c["lon"],
                        distance_m=None,
                        duration_hours=0,
                        first_seen=now,
                        last_seen=now,
                        status="active",
                    )
                )

        # --- Upsert proximity pairs ---
        for p in pairs:
            v1, v2 = p["vessel_1"], p["vessel_2"]
            mmsi_pair = tuple(sorted([str(v1["mmsi"]), str(v2["mmsi"])]))
            key = f"proximity:{mmsi_pair[0]}:{mmsi_pair[1]}"
            current_keys.add(key)

            existing = (
                db.query(STSEvent)
                .filter(
                    STSEvent.mmsi_1 == mmsi_pair[0],
                    STSEvent.mmsi_2 == mmsi_pair[1],
                    STSEvent.event_type == "proximity",
                    STSEvent.status == "active",
                )
                .first()
            )

            mid_lat = (v1["lat"] + v2["lat"]) / 2
            mid_lon = (v1["lon"] + v2["lon"]) / 2
            dist_m = p["distance_km"] * 1000

            if existing:
                existing.last_seen = now
                existing.lat = mid_lat
                existing.lon = mid_lon
                existing.distance_m = dist_m
                existing.duration_hours = (
                    now - existing.first_seen.replace(tzinfo=timezone.utc)
                ).total_seconds() / 3600
            else:
                # Determine ship names/classes in sorted MMSI order
                if str(v1["mmsi"]) == mmsi_pair[0]:
                    n1, c1, n2, c2 = v1["ship_name"], v1["class"], v2["ship_name"], v2["class"]
                else:
                    n1, c1, n2, c2 = v2["ship_name"], v2["class"], v1["ship_name"], v1["class"]

                db.add(
                    STSEvent(
                        mmsi_1=mmsi_pair[0],
                        ship_name_1=n1,
                        ship_class_1=c1,
                        mmsi_2=mmsi_pair[1],
                        ship_name_2=n2,
                        ship_class_2=c2,
                        event_type="proximity",
                        zone=p["hotspot"],
                        lat=mid_lat,
                        lon=mid_lon,
                        distance_m=dist_m,
                        duration_hours=0,
                        first_seen=now,
                        last_seen=now,
                        status="active",
                    )
                )

        # --- Resolve stale active events no longer detected ---
        active_events = db.query(STSEvent).filter(STSEvent.status == "active").all()
        resolved_count = 0
        for event in active_events:
            if event.event_type == "candidate":
                key = f"candidate:{event.mmsi_1}"
            elif event.event_type == "proximity":
                key = f"proximity:{event.mmsi_1}:{event.mmsi_2}"
            else:
                continue

            if key not in current_keys:
                event.status = "resolved"
                event.last_seen = now
                if event.first_seen:
                    event.duration_hours = (now - event.first_seen.replace(tzinfo=timezone.utc)).total_seconds() / 3600
                resolved_count += 1

        db.commit()
        logger.info(
            "STS collector: %d candidates, %d pairs, %d resolved",
            len(candidates),
            len(pairs),
            resolved_count,
        )
    except Exception as e:
        logger.error("STS collection failed: %s", e)
        db.rollback()
    finally:
        db.close()
