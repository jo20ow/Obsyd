"""
PortWatch API endpoints.

Uses the standalone portwatch_store for data access —
fetches from IMF PortWatch ArcGIS API and caches in local SQLite.

Geofence AIS data extends the PortWatch history for recent days
where IMF data is not yet available (3-5 day publication delay).
"""

from fastapi import APIRouter, Path, Query
from sqlalchemy import distinct, func

from backend.collectors.portwatch_store import (
    CHOKEPOINTS,
    fetch_chokepoint_data,
    fetch_disruptions,
    query_active_disruptions,
    query_chokepoint_averages,
    query_chokepoint_history,
    store_chokepoint_data,
    store_disruptions,
)
from backend.database import SessionLocal
from backend.models.vessels import VesselPosition
from backend.signals.vessel_weight import compute_weighted_count

router = APIRouter(prefix="/api/portwatch", tags=["portwatch"])

# Reverse map: portid -> name
CP_NAMES = {v.lower().replace(" ", "-"): k for k, v in CHOKEPOINTS.items()}
# Also allow short names
CP_ALIASES = {
    "hormuz": "chokepoint6",
    "suez": "chokepoint1",
    "malacca": "chokepoint5",
    "panama": "chokepoint2",
    "cape": "chokepoint7",
    "strait-of-hormuz": "chokepoint6",
    "suez-canal": "chokepoint1",
    "malacca-strait": "chokepoint5",
    "panama-canal": "chokepoint2",
    "cape-of-good-hope": "chokepoint7",
}


def _resolve_chokepoint(name: str) -> str | None:
    """Resolve a chokepoint name/alias to a portid."""
    name_lower = name.lower().replace(" ", "-")
    if name_lower in CP_ALIASES:
        return CP_ALIASES[name_lower]
    if name_lower in CP_NAMES:
        return CP_NAMES[name_lower]
    # Try direct portid
    if name_lower in CHOKEPOINTS:
        return name_lower
    return None


def _ensure_data(days: int = 30):
    """Fetch and store chokepoint data if not already cached."""
    data = fetch_chokepoint_data(days=days)
    store_chokepoint_data(data)
    return data


@router.get("/chokepoints")
async def get_chokepoints():
    """Current daily values for all chokepoints."""
    data = _ensure_data(days=7)

    # Group by portid, take most recent date per chokepoint
    latest = {}
    for row in data:
        pid = row["portid"]
        if pid not in latest or row["date"] > latest[pid]["date"]:
            latest[pid] = row

    return {
        "source": "IMF PortWatch",
        "chokepoints": list(latest.values()),
    }


@router.get("/chokepoints/{name}/history")
async def get_chokepoint_history(
    name: str = Path(
        description="Chokepoint name (e.g. 'hormuz', 'suez', 'malacca', 'panama', 'cape')", pattern=r"^[a-z_]+$"
    ),
    days: int = Query(365, ge=1, le=2700),
):
    """Time series for a single chokepoint from local DB cache,
    extended with own AIS geofence data for recent days."""
    portid = _resolve_chokepoint(name)
    if not portid:
        return {"error": f"Unknown chokepoint: {name}", "valid": list(CP_ALIASES.keys())}

    history = query_chokepoint_history(portid, days=days)

    # Mark PortWatch rows
    for h in history:
        h["source"] = "portwatch"

    # Extend with AIS geofence data for days after last PortWatch date
    last_pw_date = history[-1]["date"] if history else "1970-01-01"
    zone_name = name.lower()  # geofence zones use short names: hormuz, suez, etc.

    ais_days = _query_ais_daily(zone_name, after_date=last_pw_date)
    history.extend(ais_days)

    return {
        "source": "IMF PortWatch + AIS",
        "chokepoint": CHOKEPOINTS.get(portid, name),
        "portid": portid,
        "days": len(history),
        "history": history,
    }


def _query_ais_daily(zone_name: str, after_date: str) -> list[dict]:
    """Query vessel_positions for daily counts in a zone after a given date.

    Note: vessel_positions only contains tankers (ship_type 80-89) because the
    geofence AIS collector filters for tankers. So the unique MMSI count here
    corresponds to PortWatch's n_tanker, not n_total.
    We set n_tanker = AIS count (accurate) and n_total = None (unknown).
    """
    db = SessionLocal()
    try:
        rows = (
            db.query(
                func.date(VesselPosition.timestamp).label("day"),
                func.count(distinct(VesselPosition.mmsi)).label("tanker_count"),
            )
            .filter(
                VesselPosition.zone == zone_name,
                func.date(VesselPosition.timestamp) > after_date,
            )
            .group_by("day")
            .order_by("day")
            .all()
        )
        if not rows:
            return []

        result = []
        for r in rows:
            result.append(
                {
                    "portid": None,
                    "portname": zone_name,
                    "date": r.day,
                    "n_total": None,
                    "n_tanker": r.tanker_count,
                    "capacity": 0,
                    "capacity_tanker": 0,
                    "source": "ais",
                }
            )
        return result
    finally:
        db.close()


@router.get("/disruptions")
async def get_disruptions():
    """Active disruption events."""
    data = fetch_disruptions(days=365)
    store_disruptions(data)
    active = query_active_disruptions()

    return {
        "source": "IMF PortWatch",
        "total_recent": len(data),
        "active": len(active),
        "disruptions": active,
    }


@router.get("/summary")
async def get_summary():
    """Dashboard overview: current values + anomaly vs 30-day average.

    Includes weighted tanker counts from AIS geofence data where available.
    """
    data = _ensure_data(days=35)

    # Latest day per chokepoint
    latest = {}
    for row in data:
        pid = row["portid"]
        if pid not in latest or row["date"] > latest[pid]["date"]:
            latest[pid] = row

    # 30-day averages from stored data
    store_chokepoint_data(data)
    averages = query_chokepoint_averages(days=30)
    avg_map = {a["portid"]: a for a in averages}

    # Active disruptions
    dis_data = fetch_disruptions(days=365)
    store_disruptions(dis_data)
    active_disruptions = query_active_disruptions()

    # Compute weighted AIS counts per zone (on-the-fly, no schema change)
    weighted_by_zone = _compute_zone_weights()

    # Map portid -> zone name for matching
    portid_to_zone = {v: k for k, v in CP_ALIASES.items() if k in ("hormuz", "suez", "malacca", "panama", "cape")}

    summary = []
    for pid, cur in latest.items():
        avg = avg_map.get(pid, {})
        avg_total = avg.get("avg_total", 0)
        avg_tanker = avg.get("avg_tanker", 0)

        anomaly_total = round((cur["n_total"] - avg_total) / avg_total * 100, 1) if avg_total else 0.0
        anomaly_tanker = round((cur["n_tanker"] - avg_tanker) / avg_tanker * 100, 1) if avg_tanker else 0.0

        entry = {
            "portid": pid,
            "name": cur["portname"],
            "date": cur["date"],
            "n_total": cur["n_total"],
            "n_tanker": cur["n_tanker"],
            "capacity": cur["capacity"],
            "avg_total_30d": avg_total,
            "avg_tanker_30d": avg_tanker,
            "anomaly_total_pct": anomaly_total,
            "anomaly_tanker_pct": anomaly_tanker,
        }

        # Attach weighted AIS data if available for this chokepoint
        zone_name = portid_to_zone.get(pid)
        if zone_name and zone_name in weighted_by_zone:
            entry["ais_weighted"] = weighted_by_zone[zone_name]

        summary.append(entry)

    summary.sort(key=lambda x: abs(x["anomaly_total_pct"]), reverse=True)

    return {
        "source": "IMF PortWatch",
        "chokepoints": summary,
        "active_disruptions": len(active_disruptions),
        "disruptions": active_disruptions,
    }


def _compute_zone_weights() -> dict[str, dict]:
    """Compute weighted tanker counts from current vessel_positions per zone.

    Returns {zone_name: {raw_count, weighted_count, by_class}} for zones
    that have AIS data.
    """
    db = SessionLocal()
    try:
        # Latest position per MMSI (across all zones)
        latest = (
            db.query(
                VesselPosition.mmsi,
                func.max(VesselPosition.id).label("max_id"),
            )
            .group_by(VesselPosition.mmsi)
            .subquery()
        )

        rows = db.query(VesselPosition).join(latest, VesselPosition.id == latest.c.max_id).all()

        # Group by zone
        by_zone: dict[str, list[dict]] = {}
        for r in rows:
            zone = r.zone
            if not zone:
                continue
            by_zone.setdefault(zone, []).append(
                {
                    "ship_name": r.ship_name,
                    "ship_type": r.ship_type,
                }
            )

        # Compute weighted counts per zone
        result = {}
        for zone_name, vessels in by_zone.items():
            result[zone_name] = compute_weighted_count(vessels)

        return result
    except Exception:
        return {}
    finally:
        db.close()
