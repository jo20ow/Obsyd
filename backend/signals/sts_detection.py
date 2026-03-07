"""
STS (Ship-to-Ship) Transfer Detection + Dark Activity Tracking.

Detects:
1. STS candidates — tankers anchored (SOG < 1 kn) inside known STS hotspots
2. Dark activity — tankers whose AIS signal disappeared >48h ago
3. Proximity pairs — two tankers within ~500m of each other in STS zones

Uses vessel_positions table (geofenced AIS data).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from math import radians, cos, sin, sqrt, atan2

from sqlalchemy import func, distinct
from sqlalchemy.orm import Session

from backend.models.vessels import VesselPosition
from backend.geofences.zones import STS_HOTSPOTS, point_in_zone
from backend.signals.vessel_weight import classify_vessel


# Thresholds
STS_SOG_THRESHOLD = 1.0       # knots — below this = anchored/drifting
DARK_HOURS_THRESHOLD = 48     # hours — no signal for this long = "dark"
PROXIMITY_KM = 0.5            # km — vessels closer than this may be doing STS


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two points in km."""
    R = 6371.0
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return R * 2 * atan2(sqrt(a), sqrt(1 - a))


def detect_sts_candidates(db: Session) -> list[dict]:
    """Find tankers anchored in STS hotspot zones.

    Returns list of vessels with SOG < 1 kn inside an STS hotspot.
    """
    # Get latest position per MMSI
    latest = (
        db.query(VesselPosition.mmsi, func.max(VesselPosition.id).label("max_id"))
        .group_by(VesselPosition.mmsi)
        .subquery()
    )

    rows = (
        db.query(VesselPosition)
        .join(latest, VesselPosition.id == latest.c.max_id)
        .filter(VesselPosition.sog < STS_SOG_THRESHOLD)
        .all()
    )

    candidates = []
    for r in rows:
        # Check if position falls in any STS hotspot
        for hotspot in STS_HOTSPOTS:
            if point_in_zone(r.latitude, r.longitude, hotspot):
                cls_name, weight = classify_vessel(r.ship_name, r.ship_type)
                candidates.append({
                    "mmsi": r.mmsi,
                    "ship_name": r.ship_name,
                    "ship_type": r.ship_type,
                    "class": cls_name,
                    "lat": r.latitude,
                    "lon": r.longitude,
                    "sog": r.sog,
                    "zone": r.zone,
                    "sts_hotspot": hotspot["name"],
                    "sts_display": hotspot["display_name"],
                    "timestamp": r.timestamp.isoformat(),
                    "age_hours": round(
                        (datetime.now(timezone.utc) - r.timestamp.replace(tzinfo=timezone.utc)).total_seconds() / 3600, 1
                    ),
                })
                break

    return candidates


def detect_dark_vessels(db: Session) -> list[dict]:
    """Find tankers whose last AIS position is older than DARK_HOURS_THRESHOLD.

    These vessels may have turned off their AIS transponder (sanctions evasion,
    STS transfers, etc.) or simply moved out of coverage.
    """
    cutoff = datetime.utcnow() - timedelta(hours=DARK_HOURS_THRESHOLD)

    # Get latest position per MMSI
    latest = (
        db.query(VesselPosition.mmsi, func.max(VesselPosition.id).label("max_id"))
        .group_by(VesselPosition.mmsi)
        .subquery()
    )

    rows = (
        db.query(VesselPosition)
        .join(latest, VesselPosition.id == latest.c.max_id)
        .filter(VesselPosition.timestamp < cutoff)
        .all()
    )

    dark = []
    for r in rows:
        age_hours = (datetime.utcnow() - r.timestamp).total_seconds() / 3600
        cls_name, _ = classify_vessel(r.ship_name, r.ship_type)

        # Check if last known position was in an STS hotspot
        in_sts = None
        for hotspot in STS_HOTSPOTS:
            if point_in_zone(r.latitude, r.longitude, hotspot):
                in_sts = hotspot["display_name"]
                break

        dark.append({
            "mmsi": r.mmsi,
            "ship_name": r.ship_name,
            "class": cls_name,
            "last_lat": r.latitude,
            "last_lon": r.longitude,
            "last_sog": r.sog,
            "last_zone": r.zone,
            "last_seen": r.timestamp.isoformat(),
            "dark_hours": round(age_hours, 1),
            "last_in_sts_hotspot": in_sts,
        })

    # Sort by dark_hours descending (longest gap first)
    dark.sort(key=lambda x: x["dark_hours"], reverse=True)
    return dark


def detect_proximity_pairs(db: Session) -> list[dict]:
    """Find pairs of tankers within PROXIMITY_KM of each other in STS hotspots.

    Only considers vessels with SOG < 3 kn (slow/anchored) to reduce false positives.
    """
    # Get latest position per MMSI, slow movers only
    latest = (
        db.query(VesselPosition.mmsi, func.max(VesselPosition.id).label("max_id"))
        .group_by(VesselPosition.mmsi)
        .subquery()
    )

    rows = (
        db.query(VesselPosition)
        .join(latest, VesselPosition.id == latest.c.max_id)
        .filter(VesselPosition.sog < 3.0)
        .all()
    )

    # Filter to vessels in STS zones
    sts_vessels = []
    for r in rows:
        for hotspot in STS_HOTSPOTS:
            if point_in_zone(r.latitude, r.longitude, hotspot):
                cls_name, _ = classify_vessel(r.ship_name, r.ship_type)
                sts_vessels.append({
                    "mmsi": r.mmsi,
                    "ship_name": r.ship_name,
                    "class": cls_name,
                    "lat": r.latitude,
                    "lon": r.longitude,
                    "sog": r.sog,
                    "hotspot": hotspot["name"],
                    "timestamp": r.timestamp,
                })
                break

    # Find pairs within PROXIMITY_KM
    pairs = []
    seen = set()
    for i, v1 in enumerate(sts_vessels):
        for v2 in sts_vessels[i + 1:]:
            if v1["hotspot"] != v2["hotspot"]:
                continue
            pair_key = tuple(sorted([v1["mmsi"], v2["mmsi"]]))
            if pair_key in seen:
                continue

            dist = _haversine_km(v1["lat"], v1["lon"], v2["lat"], v2["lon"])
            if dist <= PROXIMITY_KM:
                seen.add(pair_key)
                pairs.append({
                    "vessel_1": {
                        "mmsi": v1["mmsi"],
                        "ship_name": v1["ship_name"],
                        "class": v1["class"],
                        "lat": v1["lat"],
                        "lon": v1["lon"],
                        "sog": v1["sog"],
                    },
                    "vessel_2": {
                        "mmsi": v2["mmsi"],
                        "ship_name": v2["ship_name"],
                        "class": v2["class"],
                        "lat": v2["lat"],
                        "lon": v2["lon"],
                        "sog": v2["sog"],
                    },
                    "distance_km": round(dist, 3),
                    "hotspot": v1["hotspot"],
                })

    return pairs


def get_sts_summary(db: Session) -> dict:
    """Full STS intelligence summary combining all detection methods."""
    candidates = detect_sts_candidates(db)
    dark = detect_dark_vessels(db)
    pairs = detect_proximity_pairs(db)

    return {
        "sts_candidates": candidates,
        "sts_candidate_count": len(candidates),
        "dark_vessels": dark,
        "dark_vessel_count": len(dark),
        "proximity_pairs": pairs,
        "proximity_pair_count": len(pairs),
        "hotspots": [
            {"name": h["name"], "display_name": h["display_name"], "bounds": h["bounds"]}
            for h in STS_HOTSPOTS
        ],
    }
