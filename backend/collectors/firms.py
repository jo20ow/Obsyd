"""
NASA FIRMS Thermal Hotspot Collector.

Fetches fire/thermal hotspot data from NASA's Fire Information for
Resource Management System (FIRMS). Uses VIIRS (S-NPP + NOAA-20)
for higher resolution than MODIS.

API: https://firms.modaps.eosdis.nasa.gov/api/area
Free API key required (register at https://firms.modaps.eosdis.nasa.gov/api/).
"""

import logging
import math
from datetime import datetime, timezone

import httpx

from backend.config import settings
from backend.database import SessionLocal
from backend.models.thermal import ThermalHotspot
from backend.models.alerts import Alert

logger = logging.getLogger(__name__)

FIRMS_URL = "https://firms.modaps.eosdis.nasa.gov/api/area/csv"
REQUEST_TIMEOUT = 30

# Areas to monitor (name, west, south, east, north)
MONITOR_AREAS = [
    {"name": "gulf_coast", "bbox": "-97,28,-93,31", "display": "Gulf Coast Refineries"},
    {"name": "persian_gulf", "bbox": "48,24,56,28", "display": "Persian Gulf"},
    {"name": "singapore", "bbox": "103,1,105,3", "display": "Singapore / Malacca"},
]

# Known refinery locations for anomaly detection
# (name, lat, lon, area_name)
REFINERIES = [
    {"name": "Baytown TX (ExxonMobil)", "lat": 29.735, "lon": -95.015, "area": "gulf_coast"},
    {"name": "Port Arthur TX (Motiva)", "lat": 29.899, "lon": -93.929, "area": "gulf_coast"},
    {"name": "Galveston Bay TX (Marathon)", "lat": 29.365, "lon": -94.905, "area": "gulf_coast"},
    {"name": "Ras Tanura (Saudi Aramco)", "lat": 26.644, "lon": 50.161, "area": "persian_gulf"},
    {"name": "Jurong Island (Singapore)", "lat": 1.265, "lon": 103.700, "area": "singapore"},
]

PROXIMITY_KM = 10  # radius to match hotspot to refinery
DEDUP_HOURS = 12  # suppress duplicate refinery alerts


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance between two points in km."""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


async def collect_firms():
    """Fetch VIIRS hotspots for monitored areas and check refinery status."""
    if not settings.firms_api_key:
        logger.debug("FIRMS: no API key configured, skipping")
        return

    db = SessionLocal()
    try:
        # Clear old hotspots (keep only latest snapshot)
        db.query(ThermalHotspot).delete()

        all_hotspots = []

        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            for area in MONITOR_AREAS:
                try:
                    # VIIRS_SNPP_NRT = near-real-time VIIRS data, last 24h
                    url = f"{FIRMS_URL}/{settings.firms_api_key}/VIIRS_SNPP_NRT/{area['bbox']}/1"
                    resp = await client.get(url)
                    resp.raise_for_status()

                    lines = resp.text.strip().split("\n")
                    if len(lines) < 2:
                        logger.info(f"FIRMS: no hotspots for {area['name']}")
                        continue

                    header = lines[0].split(",")
                    col = {name: i for i, name in enumerate(header)}

                    count = 0
                    for line in lines[1:]:
                        fields = line.split(",")
                        if len(fields) < len(header):
                            continue

                        try:
                            lat = float(fields[col["latitude"]])
                            lon = float(fields[col["longitude"]])
                            brightness = float(fields[col.get("bright_ti4", col.get("brightness", 0))])
                        except (ValueError, KeyError):
                            continue

                        confidence = fields[col.get("confidence", 0)] if "confidence" in col else ""
                        acq_date = fields[col.get("acq_date", 0)] if "acq_date" in col else ""
                        acq_time = fields[col.get("acq_time", 0)] if "acq_time" in col else ""

                        hotspot = ThermalHotspot(
                            latitude=lat,
                            longitude=lon,
                            brightness=brightness,
                            confidence=confidence,
                            area_name=area["name"],
                            satellite="VIIRS",
                            acq_date=acq_date,
                            acq_time=acq_time,
                        )
                        db.add(hotspot)
                        all_hotspots.append({"lat": lat, "lon": lon, "brightness": brightness, "area": area["name"]})
                        count += 1

                    logger.info(f"FIRMS: {area['name']} — {count} hotspots")

                except httpx.HTTPError as e:
                    logger.warning(f"FIRMS: fetch failed for {area['name']}: {e}")
                    continue

        db.commit()

        # Check refinery anomalies
        _check_refinery_anomalies(db, all_hotspots)

    except Exception as e:
        db.rollback()
        logger.error(f"FIRMS collection failed: {e}")
    finally:
        db.close()


def _check_refinery_anomalies(db, hotspots: list[dict]):
    """Check if known refineries are missing their expected thermal signature."""
    for ref in REFINERIES:
        # Find hotspots within PROXIMITY_KM of this refinery
        nearby = [
            h for h in hotspots
            if h["area"] == ref["area"]
            and _haversine_km(ref["lat"], ref["lon"], h["lat"], h["lon"]) <= PROXIMITY_KM
        ]

        if len(nearby) == 0:
            # No hotspot near this refinery — possible shutdown/anomaly
            _create_refinery_alert(db, ref)


def _create_refinery_alert(db, ref: dict):
    """Create alert for missing refinery thermal signature (with dedup)."""
    from datetime import timedelta

    cutoff = datetime.now(timezone.utc) - timedelta(hours=DEDUP_HOURS)
    existing = (
        db.query(Alert)
        .filter(
            Alert.rule == "refinery_thermal",
            Alert.zone == ref["area"],
            Alert.title.contains(ref["name"]),
            Alert.created_at > cutoff,
        )
        .first()
    )

    if existing:
        existing.created_at = datetime.now(timezone.utc)
        db.commit()
        return

    db.add(Alert(
        rule="refinery_thermal",
        zone=ref["area"],
        severity="warning",
        title=f"Refinery flaring anomaly: {ref['name']}",
        detail=(
            f"No thermal hotspot detected within {PROXIMITY_KM}km of "
            f"{ref['name']} ({ref['lat']:.3f}, {ref['lon']:.3f}). "
            f"Possible shutdown or reduced operations."
        ),
    ))
    db.commit()
    logger.info(f"Alert: refinery_thermal — {ref['name']}")
