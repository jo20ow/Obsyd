"""
AISHub HTTP Polling Collector.

Polls AISHub API for worldwide vessel positions once per minute.
Filters server-side for tankers (ship type 80-89) within our geofence zones.

API docs: https://www.aishub.net/api
Endpoint: https://data.aishub.net/ws.php
Rate limit: 1 request per minute per user.

Strategy: One global API call per minute (no bounding box), then filter
each position against our 6 geofence zones. This maximises coverage —
no wasted calls on zones that happen to be empty.
"""

import asyncio
import logging
from datetime import datetime, timezone

import httpx

from backend.config import settings
from backend.database import SessionLocal
from backend.geofences.zones import find_zone, is_tanker
from backend.models.vessels import VesselPosition

logger = logging.getLogger(__name__)

AISHUB_URL = "https://data.aishub.net/ws.php"
POLL_INTERVAL = 60  # seconds between requests (1 req/min rate limit)
REQUEST_TIMEOUT = 30  # global call returns more data, allow extra time

_poll_task: asyncio.Task | None = None


def _parse_global(data: list[dict]) -> list[VesselPosition]:
    """Parse AISHub global response, keep only tankers inside a geofence zone."""
    positions = []
    for row in data:
        ship_type = int(row.get("TYPE", 0) or 0)
        if not is_tanker(ship_type):
            continue

        try:
            lat = float(row["LATITUDE"])
            lon = float(row["LONGITUDE"])
        except (KeyError, ValueError, TypeError):
            continue

        zone = find_zone(lat, lon)
        if zone is None:
            continue

        mmsi = str(row.get("MMSI", ""))
        if not mmsi:
            continue

        sog = float(row.get("SOG", 0) or 0) / 10.0  # AISHub SOG is in 1/10 knot
        cog = float(row.get("COG", 0) or 0) / 10.0  # AISHub COG is in 1/10 degree
        heading = float(row.get("HEADING", 0) or 0)
        if heading == 511:
            heading = cog

        ship_name = str(row.get("NAME", "")).strip()

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


async def _fetch_global(client: httpx.AsyncClient) -> dict[str, int]:
    """Fetch global positions, filter for tankers in zones. Returns per-zone counts."""
    params = {
        "username": settings.aishub_api_key or settings.aishub_username,
        "format": "1",
        "output": "json",
        "compress": "0",
    }

    try:
        resp = await client.get(AISHUB_URL, params=params, timeout=REQUEST_TIMEOUT)
    except httpx.HTTPError as e:
        logger.warning(f"AISHub: global request failed: {e}")
        return {}

    if resp.status_code != 200:
        logger.warning(f"AISHub: HTTP {resp.status_code}")
        return {}

    try:
        body = resp.json()
    except Exception:
        logger.warning("AISHub: invalid JSON response")
        return {}

    # AISHub returns [metadata, [vessel1, vessel2, ...]]
    if not isinstance(body, list) or len(body) < 2:
        if isinstance(body, list) and len(body) == 1:
            meta = body[0]
            if meta.get("ERROR"):
                logger.warning(f"AISHub: API error: {meta.get('ERROR_MESSAGE', 'unknown')}")
        return {}

    meta = body[0]
    if meta.get("ERROR"):
        logger.warning(f"AISHub: API error: {meta.get('ERROR_MESSAGE', 'unknown')}")
        return {}

    vessels_data = body[1]
    if not isinstance(vessels_data, list):
        return {}

    logger.debug(f"AISHub: received {len(vessels_data)} global positions")

    positions = _parse_global(vessels_data)
    if not positions:
        return {}

    # Count per zone for logging
    zone_counts: dict[str, int] = {}
    for p in positions:
        zone_counts[p.zone] = zone_counts.get(p.zone, 0) + 1

    db = SessionLocal()
    try:
        db.add_all(positions)
        db.commit()
        return zone_counts
    except Exception as e:
        db.rollback()
        logger.error(f"AISHub: DB write failed: {e}")
        return {}
    finally:
        db.close()


async def _poll_loop():
    """Main polling loop. One global call per minute."""
    async with httpx.AsyncClient() as client:
        while True:
            try:
                from backend.collectors.aisstream import aisstream_connected
                if aisstream_connected:
                    logger.debug("AISHub: aisstream active, skipping poll")
                    await asyncio.sleep(POLL_INTERVAL)
                    continue

                zone_counts = await _fetch_global(client)
                if zone_counts:
                    total = sum(zone_counts.values())
                    breakdown = ", ".join(f"{z}={c}" for z, c in sorted(zone_counts.items()))
                    logger.info(f"AISHub: {total} tankers stored ({breakdown})")

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
    logger.info("AISHub: background polling task started (global mode)")


def stop_aishub():
    """Cancel the AISHub polling task."""
    global _poll_task
    if _poll_task and not _poll_task.done():
        _poll_task.cancel()
        logger.info("AISHub: background polling task stopped")
    _poll_task = None
