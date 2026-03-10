"""
AISStream.io WebSocket Collector.

Connects to the aisstream.io WebSocket API and receives live AIS data
for vessels within our 6 geofence bounding boxes. Filters for tankers
(AIS ship type 80-89) and stores positions in the database.

Subscription includes both PositionReport (lat/lon/sog/cog) and
ShipStaticData (ship type) messages, since ship type is only available
in static data reports.

URL confirmed from aisstream.io/documentation:
  wss://stream.aisstream.io/v0/stream
Subscription must be sent within 3 seconds of connecting.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone

import websockets

from backend.collectors.ais_hygiene import filter_and_count
from backend.config import settings
from backend.database import SessionLocal
from backend.geofences.zones import ZONES, find_zone, is_tanker
from backend.models.vessels import VesselPosition
from backend.signals.vessel_enrichment import upsert_vessel_registry

logger = logging.getLogger(__name__)

WS_URL = "wss://stream.aisstream.io/v0/stream"
WS_OPEN_TIMEOUT = 15
WS_CLOSE_TIMEOUT = 5
WS_PING_INTERVAL = 20
WS_PING_TIMEOUT = 10

RECONNECT_BASE = 5
RECONNECT_MAX = 120

# Track known tanker MMSIs from ShipStaticData messages
# Maps mmsi -> ship_type (e.g. 80, 81, 84 …)
_tanker_mmsis: dict[int, int] = {}

_ws_task: asyncio.Task | None = None

# Shared flag: True when aisstream WebSocket is connected and receiving data.
# AISHub collector checks this to pause polling when aisstream is active.
aisstream_connected: bool = False


def _build_subscription() -> dict:
    """Build the aisstream subscription message with our geofence bounding boxes."""
    bboxes = [zone["bounds"] for zone in ZONES]
    return {
        "APIKey": settings.aisstream_api_key.get_secret_value(),
        "BoundingBoxes": bboxes,
        "FilterMessageTypes": ["PositionReport", "ShipStaticData"],
    }


def _handle_static_data(msg: dict):
    """Extract ship type from ShipStaticData, track tanker MMSIs, and enrich registry."""
    meta = msg.get("MetaData", {})
    mmsi = meta.get("MMSI")
    if mmsi is None:
        return

    static = msg.get("Message", {}).get("ShipStaticData", {})
    ship_type = static.get("Type", 0)

    if is_tanker(ship_type):
        _tanker_mmsis[mmsi] = ship_type
    else:
        _tanker_mmsis.pop(mmsi, None)
        return  # Only enrich tankers

    # Extract dimensions for vessel registry
    dim = static.get("Dimension", {})
    length = None
    beam = None
    if dim:
        a = dim.get("A", 0) or 0
        b = dim.get("B", 0) or 0
        c = dim.get("C", 0) or 0
        d = dim.get("D", 0) or 0
        if a + b > 0:
            length = float(a + b)
        if c + d > 0:
            beam = float(c + d)

    draft = static.get("MaximumStaticDraught", 0) or 0
    if draft:
        draft = float(draft) / 10.0  # AIS draft is in 1/10 meters

    imo_raw = static.get("ImoNumber", 0)
    imo = str(imo_raw) if imo_raw and imo_raw > 0 else None

    ship_name = meta.get("ShipName", "").strip()
    destination = static.get("Destination", "").strip()

    # Upsert into VesselRegistry (fire-and-forget, non-blocking)
    try:
        upsert_vessel_registry(
            str(mmsi),
            ship_name=ship_name,
            ship_type=ship_type,
            imo=imo,
            length=length,
            beam=beam,
            draft=draft if draft and draft > 0 else None,
            destination=destination or None,
        )
    except Exception as exc:
        logger.debug("Vessel registry upsert failed for MMSI %s: %s", mmsi, exc)


def _parse_position_report(msg: dict) -> dict | None:
    """Parse a PositionReport for a known tanker. Returns dict or None."""
    meta = msg.get("MetaData", {})
    mmsi = meta.get("MMSI")
    if mmsi is None or mmsi not in _tanker_mmsis:
        return None

    report = msg.get("Message", {}).get("PositionReport", {})
    lat = report.get("Latitude", meta.get("latitude"))
    lon = report.get("Longitude", meta.get("longitude"))

    if lat is None or lon is None:
        return None

    zone = find_zone(lat, lon)
    if zone is None:
        return None

    sog = report.get("Sog", 0.0)
    cog = report.get("Cog", 0.0)
    heading = report.get("TrueHeading", 0.0)
    if heading == 511:
        heading = cog

    ship_name = meta.get("ShipName", "").strip()
    time_str = meta.get("time_utc", "")

    try:
        if time_str:
            ts = datetime.fromisoformat(time_str.replace(" +0000 UTC", "+00:00"))
        else:
            ts = datetime.now(timezone.utc)
    except (ValueError, TypeError):
        ts = datetime.now(timezone.utc)

    ship_type = _tanker_mmsis.get(mmsi, 80)

    if not filter_and_count(str(mmsi), lat, lon, sog, ship_type, ts):
        return None

    return {
        "mmsi": str(mmsi),
        "ship_name": ship_name,
        "lat": lat,
        "lon": lon,
        "sog": sog,
        "cog": cog,
        "heading": heading,
        "zone": zone["name"],
        "ts": ts,
        "ship_type": ship_type,
    }


def _db_write_position(data: dict):
    """Write a single vessel position to DB (runs in thread pool)."""
    db = SessionLocal()
    try:
        db.add(
            VesselPosition(
                mmsi=data["mmsi"],
                ship_name=data["ship_name"],
                ship_type=data.get("ship_type", 80),
                latitude=data["lat"],
                longitude=data["lon"],
                sog=data["sog"],
                cog=data["cog"],
                heading=data["heading"],
                zone=data["zone"],
                timestamp=data["ts"],
            )
        )
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error(f"DB write failed for MMSI {data['mmsi']}: {e}")
    finally:
        db.close()


async def _ws_loop():
    """Main WebSocket loop with exponential backoff reconnection."""
    backoff = RECONNECT_BASE
    msg_count = 0

    while True:
        try:
            logger.info(f"AISStream: connecting to {WS_URL} (timeout={WS_OPEN_TIMEOUT}s)")
            async with websockets.connect(
                WS_URL,
                open_timeout=WS_OPEN_TIMEOUT,
                close_timeout=WS_CLOSE_TIMEOUT,
                ping_interval=WS_PING_INTERVAL,
                ping_timeout=WS_PING_TIMEOUT,
            ) as ws:
                # Subscription must be sent within 3 seconds per aisstream docs
                sub = _build_subscription()
                await ws.send(json.dumps(sub))
                logger.info(
                    f"AISStream: connected, subscribed with "
                    f"{len(sub['BoundingBoxes'])} bounding boxes, "
                    f"tracking {len(_tanker_mmsis)} known tankers"
                )

                backoff = RECONNECT_BASE  # reset on successful connection
                msg_count = 0
                global aisstream_connected
                aisstream_connected = True

                async for raw in ws:
                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        continue

                    msg_type = msg.get("MessageType", "")

                    if msg_type == "ShipStaticData":
                        _handle_static_data(msg)
                    elif msg_type == "PositionReport":
                        data = _parse_position_report(msg)
                        if data:
                            await asyncio.to_thread(_db_write_position, data)

                    msg_count += 1
                    if msg_count % 1000 == 0:
                        logger.info(f"AISStream: {msg_count} messages processed, {len(_tanker_mmsis)} tankers tracked")

        except asyncio.CancelledError:
            logger.info("AISStream: task cancelled")
            aisstream_connected = False
            return
        except websockets.ConnectionClosed as e:
            aisstream_connected = False
            logger.warning(f"AISStream: connection closed (code={e.code}), reconnecting in {backoff}s")
        except TimeoutError:
            aisstream_connected = False
            logger.warning(
                f"AISStream: handshake timed out after {WS_OPEN_TIMEOUT}s, "
                f"retrying in {backoff}s (server may be temporarily unreachable)"
            )
        except OSError as e:
            aisstream_connected = False
            logger.error(f"AISStream: network error ({e}), retrying in {backoff}s")
        except Exception as e:
            aisstream_connected = False
            logger.error(f"AISStream: unexpected error ({type(e).__name__}: {e}), retrying in {backoff}s")

        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, RECONNECT_MAX)


def start_aisstream():
    """Start the WebSocket client as a background asyncio task."""
    global _ws_task
    if not settings.aisstream_api_key:
        logger.info("AISStream: no API key configured, skipping")
        return

    loop = asyncio.get_event_loop()
    _ws_task = loop.create_task(_ws_loop())
    logger.info("AISStream: background task started")


def stop_aisstream():
    """Cancel the background WebSocket task."""
    global _ws_task
    if _ws_task and not _ws_task.done():
        _ws_task.cancel()
        logger.info("AISStream: background task stopped")
    _ws_task = None
