"""
NOAA NWS + Open-Meteo Weather Collector.

1. NOAA NWS API (api.weather.gov): Active hurricane/tropical storm alerts
   for Gulf Coast states. Public domain, no API key. Requires User-Agent header.

2. Open-Meteo Marine + Weather APIs: Current wave height, wave period, wind speed
   for each geofence zone center. Public domain, no API key.
"""

import logging
from datetime import datetime, timezone

import httpx

from backend.database import SessionLocal
from backend.geofences.zones import ZONES
from backend.models.weather import WeatherAlert

logger = logging.getLogger(__name__)

NOAA_ALERTS_URL = "https://api.weather.gov/alerts/active"
OPEN_METEO_MARINE_URL = "https://marine-api.open-meteo.com/v1/marine"
OPEN_METEO_WEATHER_URL = "https://api.open-meteo.com/v1/forecast"

NOAA_USER_AGENT = "OBSYD/1.0 (energy-market-intelligence)"
REQUEST_TIMEOUT = 20

# NOAA alert events we care about for energy market impact
HURRICANE_EVENTS = {
    "Hurricane Warning",
    "Hurricane Watch",
    "Hurricane Local Statement",
    "Tropical Storm Warning",
    "Tropical Storm Watch",
    "Storm Surge Warning",
    "Storm Surge Watch",
    "Extreme Wind Warning",
    "Hurricane Force Wind Warning",
}

# Gulf coast + Atlantic states for NOAA alerts
NOAA_AREAS = "TX,LA,MS,AL,FL,GA,SC,NC"

# Zone center coordinates for marine/weather queries
ZONE_CENTERS = {
    "hormuz": (26.0, 56.5),
    "suez": (29.5, 32.5),
    "malacca": (2.5, 101.5),
    "panama": (9.0, -79.5),
    "cape": (-34.5, 19.0),
    "houston": (29.0, -95.0),
}


async def fetch_noaa_alerts() -> list[dict]:
    """Fetch active tropical/hurricane alerts from NOAA NWS API."""
    headers = {"User-Agent": NOAA_USER_AGENT, "Accept": "application/geo+json"}

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        try:
            resp = await client.get(
                NOAA_ALERTS_URL,
                params={"area": NOAA_AREAS, "status": "actual", "message_type": "alert"},
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()
            features = data.get("features", [])

            # Filter to hurricane/tropical events
            alerts = []
            for f in features:
                props = f.get("properties", {})
                event = props.get("event", "")
                if event in HURRICANE_EVENTS:
                    geom = f.get("geometry")
                    lat, lon = None, None
                    if geom and geom.get("type") == "Point":
                        coords = geom.get("coordinates", [])
                        if len(coords) >= 2:
                            lon, lat = coords[0], coords[1]

                    alerts.append({
                        "alert_id": props.get("id", ""),
                        "event": event,
                        "severity": props.get("severity", ""),
                        "headline": props.get("headline", ""),
                        "description": (props.get("description", "") or "")[:2000],
                        "area": (props.get("areaDesc", "") or "")[:500],
                        "latitude": lat,
                        "longitude": lon,
                        "onset": props.get("onset", ""),
                        "expires_at": props.get("expires", "") or props.get("ends", ""),
                    })

            logger.info(f"NOAA: fetched {len(features)} alerts, {len(alerts)} tropical/hurricane")
            return alerts

        except Exception as e:
            logger.error(f"NOAA: alert fetch failed: {e}")
            return []


async def fetch_marine_conditions() -> dict:
    """Fetch current wave and wind conditions for all geofence zones from Open-Meteo."""
    lats = []
    lons = []
    zone_names = []
    for name, (lat, lon) in ZONE_CENTERS.items():
        lats.append(str(lat))
        lons.append(str(lon))
        zone_names.append(name)

    results = {}

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        # Marine data (waves)
        try:
            marine_resp = await client.get(OPEN_METEO_MARINE_URL, params={
                "latitude": ",".join(lats),
                "longitude": ",".join(lons),
                "current": "wave_height,wave_direction,wave_period",
                "timezone": "UTC",
            })
            marine_resp.raise_for_status()
            marine_data = marine_resp.json()

            if isinstance(marine_data, list):
                for i, item in enumerate(marine_data):
                    if i < len(zone_names):
                        current = item.get("current", {})
                        results[zone_names[i]] = {
                            "wave_height": current.get("wave_height"),
                            "wave_direction": current.get("wave_direction"),
                            "wave_period": current.get("wave_period"),
                        }
            elif isinstance(marine_data, dict) and "current" in marine_data:
                # Single location response
                current = marine_data.get("current", {})
                results[zone_names[0]] = {
                    "wave_height": current.get("wave_height"),
                    "wave_direction": current.get("wave_direction"),
                    "wave_period": current.get("wave_period"),
                }
        except Exception as e:
            logger.error(f"Open-Meteo marine fetch failed: {e}")

        # Weather data (wind)
        try:
            weather_resp = await client.get(OPEN_METEO_WEATHER_URL, params={
                "latitude": ",".join(lats),
                "longitude": ",".join(lons),
                "current": "wind_speed_10m,wind_gusts_10m",
                "wind_speed_unit": "kn",
                "timezone": "UTC",
            })
            weather_resp.raise_for_status()
            weather_data = weather_resp.json()

            if isinstance(weather_data, list):
                for i, item in enumerate(weather_data):
                    if i < len(zone_names):
                        current = item.get("current", {})
                        zone = results.setdefault(zone_names[i], {})
                        zone["wind_speed"] = current.get("wind_speed_10m")
                        zone["wind_gusts"] = current.get("wind_gusts_10m")
            elif isinstance(weather_data, dict) and "current" in weather_data:
                current = weather_data.get("current", {})
                zone = results.setdefault(zone_names[0], {})
                zone["wind_speed"] = current.get("wind_speed_10m")
                zone["wind_gusts"] = current.get("wind_gusts_10m")
        except Exception as e:
            logger.error(f"Open-Meteo weather fetch failed: {e}")

    return results


async def collect_noaa_alerts():
    """Fetch NOAA alerts and store/update in database."""
    alerts = await fetch_noaa_alerts()

    db = SessionLocal()
    try:
        # Remove expired alerts
        now = datetime.now(timezone.utc).isoformat()
        db.query(WeatherAlert).filter(
            WeatherAlert.expires_at < now,
            WeatherAlert.expires_at != "",
        ).delete(synchronize_session=False)

        for a in alerts:
            existing = db.query(WeatherAlert).filter(
                WeatherAlert.alert_id == a["alert_id"]
            ).first()

            if existing:
                existing.severity = a["severity"]
                existing.headline = a["headline"]
                existing.expires_at = a["expires_at"]
            else:
                db.add(WeatherAlert(**a))

        db.commit()
        if alerts:
            logger.info(f"NOAA: stored {len(alerts)} tropical/hurricane alerts")
    except Exception as e:
        db.rollback()
        logger.error(f"NOAA: DB write failed: {e}")
    finally:
        db.close()
