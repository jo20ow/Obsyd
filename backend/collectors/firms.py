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
from datetime import datetime, timedelta, timezone

import httpx

from backend.config import settings
from backend.database import SessionLocal
from backend.models.alerts import Alert
from backend.models.thermal import ThermalHotspot

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
                    # NASA FIRMS API requires key in URL path (no header auth option)
                    url = f"{FIRMS_URL}/{settings.firms_api_key.get_secret_value()}/VIIRS_SNPP_NRT/{area['bbox']}/1"
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
                    # Mask API key in error message (httpx includes full URL)
                    err_msg = str(e).replace(settings.firms_api_key.get_secret_value(), "***")
                    logger.warning(f"FIRMS: fetch failed for {area['name']}: {err_msg}")
                    continue

        db.commit()

        # Check refinery anomalies
        _check_refinery_anomalies(db, all_hotspots)

    except Exception as e:
        db.rollback()
        logger.error(f"FIRMS collection failed: {e}")
    finally:
        db.close()


# Refineries run hot continuously (flaring is routine), so "any nearby hotspot = alert" floods
# the feed. Alert only when today's nearby-hotspot count is unusually high vs the refinery's own
# trailing daily norm — the same baseline-aware posture as the anomaly radar.
THERMAL_WINDOW_DAYS = 45
THERMAL_MIN_NEARBY = 2     # ignore 0-1 nearby hotspots (always-on refinery glow)
THERMAL_WARN_Z = 2.0
THERMAL_CRIT_Z = 3.0


def _refinery_nearby_by_date(db, ref: dict, window_days: int) -> dict:
    """Trailing per-day count of stored hotspots within PROXIMITY_KM of a refinery."""
    cutoff = (datetime.now(timezone.utc).date() - timedelta(days=window_days)).isoformat()
    rows = (
        db.query(ThermalHotspot.latitude, ThermalHotspot.longitude, ThermalHotspot.acq_date)
        .filter(ThermalHotspot.area_name == ref["area"], ThermalHotspot.acq_date >= cutoff)
        .all()
    )
    by_date: dict[str, int] = {}
    for lat, lon, d in rows:
        if d and _haversine_km(ref["lat"], ref["lon"], lat, lon) <= PROXIMITY_KM:
            by_date[d] = by_date.get(d, 0) + 1
    return by_date


def _check_refinery_anomalies(db, hotspots: list[dict]):
    """Alert only on UNUSUAL thermal activity near a refinery vs its own recent norm.

    Compares today's nearby-hotspot count to the refinery's trailing daily baseline; a flat
    "any hotspot = warning" is suppressed (refineries are always warm). No hotspots = no alert.
    """
    if not hotspots:
        return

    from backend.signals.detectors.base import trailing_zscore

    for ref in REFINERIES:
        nearby = [
            h
            for h in hotspots
            if h["area"] == ref["area"] and _haversine_km(ref["lat"], ref["lon"], h["lat"], h["lon"]) <= PROXIMITY_KM
        ]
        current = len(nearby)
        if current < THERMAL_MIN_NEARBY:
            continue

        by_date = _refinery_nearby_by_date(db, ref, THERMAL_WINDOW_DAYS)
        # Baseline = prior days only (drop the most recent acq_date so we don't compare today to itself).
        dates = sorted(by_date)
        baseline = [by_date[d] for d in dates[:-1]] if len(dates) > 1 else []
        stat = trailing_zscore(current, baseline)
        if stat is None:
            continue
        z, mean, _, _ = stat
        if z < THERMAL_WARN_Z:
            continue
        severity = "critical" if z >= THERMAL_CRIT_Z else "warning"
        _create_refinery_alert(db, ref, current, max(h["brightness"] for h in nearby), severity, z, mean)


def _create_refinery_alert(db, ref: dict, count: int, peak_brightness: float, severity: str, z: float, mean: float):
    """Create alert for UNUSUAL thermal activity near a refinery (with dedup)."""
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
        existing.severity = severity
        db.commit()
        return

    db.add(
        Alert(
            rule="refinery_thermal",
            zone=ref["area"],
            severity=severity,
            title=f"Unusual thermal activity near {ref['name']} ({z:+.1f}σ)",
            detail=(
                f"{count} hotspot(s) within {PROXIMITY_KM}km of {ref['name']} vs ~{mean:.0f} normal "
                f"(z {z:+.2f}, peak {peak_brightness:.0f}K). Possible elevated flaring or fire."
            ),
        )
    )
    db.commit()
    logger.info(f"Alert: refinery_thermal — {ref['name']} ({count} hotspots, z={z:+.2f})")
