"""
IMF PortWatch Collector.

Fetches daily port activity and chokepoint transit data from the
IMF PortWatch ArcGIS Feature Services (public, no API key required).

Source: IMF PortWatch (https://portwatch.imf.org)

Data endpoints:
  - Daily_Ports_Data: port calls, import/export volumes by vessel type
  - Daily_Chokepoints_Data: transit counts and capacity by vessel type
"""

import logging
from datetime import datetime, timezone

import httpx

from backend.database import SessionLocal
from backend.models.ports import PortActivity, Disruption

logger = logging.getLogger(__name__)

PORTS_URL = (
    "https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services"
    "/Daily_Ports_Data/FeatureServer/0/query"
)
CHOKEPOINTS_URL = (
    "https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services"
    "/Daily_Chokepoints_Data/FeatureServer/0/query"
)
DISRUPTIONS_URL = (
    "https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services"
    "/portwatch_disruptions_database/FeatureServer/0/query"
)

# IMF PortWatch port IDs for our 5 key ports
PORTS = {
    "port1114": "Rotterdam",
    "port362": "Al Fujayrah",
    "port1201": "Singapore",
    "port481": "Houston",
    "port955": "Port Hedland",
}

# IMF PortWatch chokepoint IDs mapped to our geofence zone names
CHOKEPOINTS = {
    "chokepoint6": {"name": "Strait of Hormuz", "zone": "hormuz"},
    "chokepoint1": {"name": "Suez Canal", "zone": "suez"},
    "chokepoint5": {"name": "Malacca Strait", "zone": "malacca"},
    "chokepoint2": {"name": "Panama Canal", "zone": "panama"},
    "chokepoint7": {"name": "Cape of Good Hope", "zone": "cape"},
}

REQUEST_TIMEOUT = 30


async def _fetch_port_data(client: httpx.AsyncClient, days: int = 7) -> list[dict]:
    """Fetch recent daily data for our key ports."""
    port_ids = "','".join(PORTS.keys())
    params = {
        "where": f"portid IN ('{port_ids}')",
        "outFields": "portid,portname,date,portcalls,portcalls_tanker,"
                     "import_tanker,export_tanker,import,export",
        "orderByFields": "date DESC",
        "resultRecordCount": str(days * len(PORTS)),
        "f": "json",
    }

    try:
        resp = await client.get(PORTS_URL, params=params, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        return data.get("features", [])
    except Exception as e:
        logger.error(f"PortWatch: ports fetch failed: {e}")
        return []


async def _fetch_chokepoint_data(client: httpx.AsyncClient, days: int = 7) -> list[dict]:
    """Fetch recent daily data for our key chokepoints."""
    cp_ids = "','".join(CHOKEPOINTS.keys())
    params = {
        "where": f"portid IN ('{cp_ids}')",
        "outFields": "portid,portname,date,n_total,n_tanker,capacity,capacity_tanker",
        "orderByFields": "date DESC",
        "resultRecordCount": str(days * len(CHOKEPOINTS)),
        "f": "json",
    }

    try:
        resp = await client.get(CHOKEPOINTS_URL, params=params, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        return data.get("features", [])
    except Exception as e:
        logger.error(f"PortWatch: chokepoints fetch failed: {e}")
        return []


async def _fetch_disruptions(client: httpx.AsyncClient, days: int = 90) -> list[dict]:
    """Fetch recent disruption events."""
    from datetime import timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    cutoff_year = cutoff.year
    params = {
        "where": f"year >= {cutoff_year} OR todate IS NULL",
        "outFields": "eventid,eventname,eventtype,alertlevel,fromdate,todate,"
                     "affectedports,country,htmldescription",
        "orderByFields": "fromdate DESC",
        "resultRecordCount": "500",
        "f": "json",
    }

    try:
        resp = await client.get(DISRUPTIONS_URL, params=params, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        return data.get("features", [])
    except Exception as e:
        logger.error(f"PortWatch: disruptions fetch failed: {e}")
        return []


def _parse_date(date_val) -> str | None:
    """Parse date from ArcGIS response. Ports use 'YYYY-MM-DD', chokepoints use epoch ms."""
    if date_val is None:
        return None
    if isinstance(date_val, str):
        return date_val[:10]
    if isinstance(date_val, (int, float)):
        try:
            dt = datetime.fromtimestamp(date_val / 1000, tz=timezone.utc)
            return dt.strftime("%Y-%m-%d")
        except (ValueError, OSError):
            return None
    return None


async def collect_portwatch():
    """Collect port activity, chokepoint data, and disruptions from IMF PortWatch."""
    async with httpx.AsyncClient() as client:
        port_features = await _fetch_port_data(client)
        chokepoint_features = await _fetch_chokepoint_data(client)
        disruption_features = await _fetch_disruptions(client)

    records = []
    disruption_records = []

    for f in port_features:
        a = f.get("attributes", {})
        date_str = _parse_date(a.get("date"))
        if not date_str:
            continue
        records.append(PortActivity(
            port_id=a.get("portid", ""),
            port_name=a.get("portname", ""),
            date=date_str,
            kind="port",
            vessel_count=a.get("portcalls") or 0,
            vessel_count_tanker=a.get("portcalls_tanker") or 0,
            import_total=a.get("import") or 0,
            export_total=a.get("export") or 0,
            import_tanker=a.get("import_tanker") or 0,
            export_tanker=a.get("export_tanker") or 0,
        ))

    for f in chokepoint_features:
        a = f.get("attributes", {})
        date_str = _parse_date(a.get("date"))
        if not date_str:
            continue
        records.append(PortActivity(
            port_id=a.get("portid", ""),
            port_name=a.get("portname", ""),
            date=date_str,
            kind="chokepoint",
            vessel_count=a.get("n_total") or 0,
            vessel_count_tanker=a.get("n_tanker") or 0,
            capacity=a.get("capacity") or 0.0,
            capacity_tanker=a.get("capacity_tanker") or 0.0,
        ))

    alert_map = {"RED": 3, "ORANGE": 2, "GREEN": 1}
    for f in disruption_features:
        a = f.get("attributes", {})
        start = _parse_date(a.get("fromdate"))
        if not start:
            continue
        disruption_records.append(Disruption(
            event_id=str(a.get("eventid", "")),
            event_name=a.get("eventname", ""),
            event_type=a.get("eventtype", ""),
            alertlevel=alert_map.get(a.get("alertlevel", ""), 0),
            start_date=start,
            end_date=_parse_date(a.get("todate")),
            affected_port_id=a.get("affectedports") or "",
            affected_port_name="",
            country=a.get("country") or "",
            description=a.get("htmldescription") or "",
        ))

    if not records and not disruption_records:
        logger.warning("PortWatch: no records to store")
        return

    db = SessionLocal()
    try:
        # Upsert activity: delete existing rows for the same dates, then insert fresh
        if records:
            dates = {r.date for r in records}
            pid_set = {r.port_id for r in records}
            db.query(PortActivity).filter(
                PortActivity.date.in_(dates),
                PortActivity.port_id.in_(pid_set),
            ).delete(synchronize_session=False)
            db.add_all(records)

        # Upsert disruptions: delete by event_id, then insert fresh
        if disruption_records:
            event_ids = {r.event_id for r in disruption_records}
            db.query(Disruption).filter(
                Disruption.event_id.in_(event_ids),
            ).delete(synchronize_session=False)
            db.add_all(disruption_records)

        db.commit()
        logger.info(
            f"PortWatch: stored {len(records)} activity records "
            f"({len(port_features)} port, {len(chokepoint_features)} chokepoint), "
            f"{len(disruption_records)} disruptions"
        )
    except Exception as e:
        db.rollback()
        logger.error(f"PortWatch: DB write failed: {e}")
    finally:
        db.close()
