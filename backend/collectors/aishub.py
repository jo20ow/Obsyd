"""
AISHub HTTP Polling Collector.

Polls AISHub API for vessel positions within our geofence bounding boxes.
Acts as fallback when aisstream.io WebSocket is unavailable.

API docs: https://www.aishub.net/api
Endpoint: https://data.aishub.net/ws.php
Rate limit: 1 request per minute per user.

Strategy: One zone per minute, rotating through all 6 zones.
Full cycle every 6 minutes. Pause polling when aisstream
WebSocket is active (aisstream takes priority).
"""

import asyncio
import logging
from datetime import datetime, timezone

import httpx

from backend.config import settings
from backend.database import SessionLocal
from backend.geofences.zones import ZONES, is_tanker
from backend.models.vessels import VesselPosition

logger = logging.getLogger(__name__)

AISHUB_URL = "https://data.aishub.net/ws.php"
POLL_INTERVAL = 60  # seconds between requests (1 req/min rate limit)
REQUEST_TIMEOUT = 15

_poll_task: asyncio.Task | None = None


def _parse_vessels(data: list[dict], zone: dict) -> list[VesselPosition]:
    """Parse AISHub response rows into VesselPosition objects, filtering for tankers."""
    positions = []
    for row in data:
        ship_type = int(row.get("TYPE", 0) or 0)
        if not is_tanker(ship_type):
            continue

        mmsi = str(row.get("MMSI", ""))
        if not mmsi:
            continue

        try:
            lat = float(row["LATITUDE"])
            lon = float(row["LONGITUDE"])
        except (KeyError, ValueError, TypeError):
            continue

        sog = float(row.get("SOG", 0) or 0) / 10.0  # AISHub SOG is in 1/10 knot
        cog = float(row.get("COG", 0) or 0) / 10.0  # AISHub COG is in 1/10 degree
        heading = float(row.get("HEADING", 0) or 0)
        if heading == 511:
            heading = cog

        ship_name = str(row.get("NAME", "")).strip()

        # AISHub TIME is epoch seconds
        time_val = row.get("TIME")
        if time_val:
            try:
                ts = datetime.fromtimestamp(int(time_val), tz=timezone.utc)
            except (ValueError, TypeError, OSError):
                ts = datetime.now(timezone.utc)
        else:
            ts = datetime.now(timezone.utc)

        positions.append(VesselPosition(
            mmsi=mmsi,
            ship_name=ship_name,
            ship_type=ship_type,
            latitude=lat,
            longitude=lon,
            sog=sog,
            cog=cog,
            heading=heading,
            zone=zone["name"],
            timestamp=ts,
        ))

    return positions


async def _fetch_zone(client: httpx.AsyncClient, zone: dict) -> int:
    """Fetch vessels for a single geofence zone. Returns count of tankers stored."""
    (lat_min, lon_min), (lat_max, lon_max) = zone["bounds"]

    params = {
        "username": settings.aishub_api_key or settings.aishub_username,
        "format": "1",
        "output": "json",
        "compress": "0",
        "latmin": str(lat_min),
        "latmax": str(lat_max),
        "lonmin": str(lon_min),
        "lonmax": str(lon_max),
    }

    try:
        resp = await client.get(AISHUB_URL, params=params, timeout=REQUEST_TIMEOUT)
    except httpx.HTTPError as e:
        logger.warning(f"AISHub: request failed for {zone['name']}: {e}")
        return 0

    if resp.status_code != 200:
        logger.warning(f"AISHub: HTTP {resp.status_code} for {zone['name']}")
        return 0

    try:
        body = resp.json()
    except Exception:
        logger.warning(f"AISHub: invalid JSON for {zone['name']}")
        return 0

    # AISHub returns a list: first element is metadata, second is vessel array
    # Format: [{"ERROR": false, ...}, [vessel1, vessel2, ...]]
    if not isinstance(body, list) or len(body) < 2:
        # Check for error response
        if isinstance(body, list) and len(body) == 1:
            meta = body[0]
            if meta.get("ERROR"):
                logger.warning(f"AISHub: API error for {zone['name']}: {meta.get('ERROR_MESSAGE', 'unknown')}")
        return 0

    meta = body[0]
    if meta.get("ERROR"):
        logger.warning(f"AISHub: API error for {zone['name']}: {meta.get('ERROR_MESSAGE', 'unknown')}")
        return 0

    vessels_data = body[1]
    if not isinstance(vessels_data, list):
        return 0

    positions = _parse_vessels(vessels_data, zone)
    if not positions:
        return 0

    db = SessionLocal()
    try:
        db.add_all(positions)
        db.commit()
        return len(positions)
    except Exception as e:
        db.rollback()
        logger.error(f"AISHub: DB write failed for {zone['name']}: {e}")
        return 0
    finally:
        db.close()


async def _poll_loop():
    """Main polling loop. One zone per minute, rotating through all 6."""
    zone_idx = 0

    async with httpx.AsyncClient() as client:
        while True:
            try:
                from backend.collectors.aisstream import aisstream_connected
                if aisstream_connected:
                    logger.debug("AISHub: aisstream active, skipping poll")
                    await asyncio.sleep(POLL_INTERVAL)
                    continue

                zone = ZONES[zone_idx]
                count = await _fetch_zone(client, zone)
                if count > 0:
                    logger.info(f"AISHub: {zone['name']} — {count} tankers stored")

                zone_idx = (zone_idx + 1) % len(ZONES)

            except asyncio.CancelledError:
                logger.info("AISHub: poll task cancelled")
                return
            except Exception as e:
                logger.error(f"AISHub: unexpected error in poll loop: {e}")

            await asyncio.sleep(POLL_INTERVAL)


def start_aishub():
    """Start the AISHub polling background task."""
    global _poll_task
    if not (settings.aishub_api_key or settings.aishub_username):
        logger.info("AISHub: no API key configured, skipping")
        return

    loop = asyncio.get_event_loop()
    _poll_task = loop.create_task(_poll_loop())
    logger.info("AISHub: background polling task started")


def stop_aishub():
    """Cancel the AISHub polling task."""
    global _poll_task
    if _poll_task and not _poll_task.done():
        _poll_task.cancel()
        logger.info("AISHub: background polling task stopped")
    _poll_task = None
