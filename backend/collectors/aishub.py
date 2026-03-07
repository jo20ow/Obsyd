"""
AISHub HTTP Polling Collector.

Polls AISHub API for worldwide vessel positions once per minute.
Stores ALL vessels in global_vessel_positions (replaced each cycle),
and tankers within geofence zones in vessel_positions (appended).

API docs: https://www.aishub.net/api
Endpoint: https://data.aishub.net/ws.php
Rate limit: 1 request per minute per user.
"""

import asyncio
import logging
from datetime import datetime, timezone

import httpx

from backend.collectors.ais_hygiene import filter_and_count, get_stats
from backend.config import settings
from backend.database import SessionLocal
from backend.geofences.zones import ZONES, find_zone, is_tanker
from backend.models.vessels import GlobalVesselPosition, VesselPosition

logger = logging.getLogger(__name__)

AISHUB_URL = "https://data.aishub.net/ws.php"
POLL_INTERVAL = 60  # seconds between requests (1 req/min rate limit)
REQUEST_TIMEOUT = 30  # global call returns more data, allow extra time

_poll_task: asyncio.Task | None = None


def _parse_row(row: dict) -> dict | None:
    """Parse a single AISHub vessel row into a dict. Returns None on bad data."""
    try:
        lat = float(row["LATITUDE"])
        lon = float(row["LONGITUDE"])
    except (KeyError, ValueError, TypeError):
        return None

    mmsi = str(row.get("MMSI", ""))
    if not mmsi:
        return None

    ship_type = int(row.get("TYPE", 0) or 0)
    sog = float(row.get("SOG", 0) or 0) / 10.0
    cog = float(row.get("COG", 0) or 0) / 10.0
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

    zone = find_zone(lat, lon)

    return {
        "mmsi": mmsi,
        "ship_name": ship_name,
        "ship_type": ship_type,
        "lat": lat,
        "lon": lon,
        "sog": sog,
        "cog": cog,
        "heading": heading,
        "is_tanker": is_tanker(ship_type),
        "zone": zone,
        "ts": ts,
    }


async def _fetch_global(client: httpx.AsyncClient) -> dict[str, int]:
    """Fetch global positions, store all + zone tankers. Returns per-zone tanker counts."""
    params = {
        "username": (settings.aishub_api_key.get_secret_value() if settings.aishub_api_key else None)
        or settings.aishub_username,
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

    # Parse all rows
    global_positions = []
    zone_positions = []
    zone_counts: dict[str, int] = {}

    for row in vessels_data:
        parsed = _parse_row(row)
        if parsed is None:
            continue

        # Global table: all vessels (snapshot, replaced each cycle)
        global_positions.append(
            GlobalVesselPosition(
                mmsi=parsed["mmsi"],
                ship_name=parsed["ship_name"],
                ship_type=parsed["ship_type"],
                latitude=parsed["lat"],
                longitude=parsed["lon"],
                sog=parsed["sog"],
                cog=parsed["cog"],
                is_tanker=parsed["is_tanker"],
                zone=parsed["zone"]["name"] if parsed["zone"] else None,
            )
        )

        # Zone table: only tankers inside a geofence (appended for history)
        # Apply hygiene filters before storing (plausibility + dedup)
        if parsed["is_tanker"] and parsed["zone"]:
            zone_name = parsed["zone"]["name"]
            if filter_and_count(
                parsed["mmsi"],
                parsed["lat"],
                parsed["lon"],
                parsed["sog"],
                parsed["ship_type"],
                parsed["ts"],
            ):
                zone_counts[zone_name] = zone_counts.get(zone_name, 0) + 1
                zone_positions.append(
                    VesselPosition(
                        mmsi=parsed["mmsi"],
                        ship_name=parsed["ship_name"],
                        ship_type=parsed["ship_type"],
                        latitude=parsed["lat"],
                        longitude=parsed["lon"],
                        sog=parsed["sog"],
                        cog=parsed["cog"],
                        heading=parsed["heading"],
                        zone=zone_name,
                        timestamp=parsed["ts"],
                    )
                )

    db = SessionLocal()
    try:
        # Replace global snapshot (delete old, insert new)
        db.query(GlobalVesselPosition).delete()
        if global_positions:
            db.add_all(global_positions)

        # Append zone tankers
        if zone_positions:
            db.add_all(zone_positions)

        db.commit()
        stats = get_stats()
        logger.info(
            f"AISHub: {len(global_positions)} global, "
            f"{len(zone_positions)} zone tankers stored "
            f"(hygiene: {stats['rejected']} rejected, {stats['deduped']} deduped)"
        )
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
                    breakdown = ", ".join(f"{z}={c}" for z, c in sorted(zone_counts.items()))
                    logger.info(f"AISHub: zone tankers: {breakdown}")

                # Log zones with no coverage (terrestrial AIS limitation)
                all_zone_names = {z["name"] for z in ZONES}
                covered = set(zone_counts.keys())
                no_coverage = all_zone_names - covered
                if no_coverage:
                    logger.info(
                        f"AISHub: no coverage for {', '.join(sorted(no_coverage))} (terrestrial AIS limitation)"
                    )

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
