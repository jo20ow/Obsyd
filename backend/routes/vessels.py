from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import func

from backend.database import get_db
from backend.models.vessels import VesselPosition, GeofenceEvent, GlobalVesselPosition
from backend.geofences.zones import ZONES, NO_AIS_COVERAGE
from backend.signals.vessel_weight import classify_vessel, compute_weighted_count

router = APIRouter(prefix="/api/vessels", tags=["vessels"])


@router.get("/positions")
async def get_vessel_positions(
    zone: str = Query(None, description="Filter by geofence zone name"),
    limit: int = Query(500, ge=1, le=2000),
    db: Session = Depends(get_db),
):
    """Get the latest position per vessel (MMSI) within geofences."""
    # Subquery: latest timestamp per MMSI
    latest = (
        db.query(VesselPosition.mmsi, func.max(VesselPosition.id).label("max_id"))
        .group_by(VesselPosition.mmsi)
        .subquery()
    )

    query = (
        db.query(VesselPosition)
        .join(latest, VesselPosition.id == latest.c.max_id)
        .order_by(VesselPosition.timestamp.desc())
    )

    if zone:
        query = query.filter(VesselPosition.zone == zone)

    rows = query.limit(limit).all()
    return [
        {
            "mmsi": r.mmsi,
            "ship_name": r.ship_name,
            "ship_type": r.ship_type,
            "lat": r.latitude,
            "lon": r.longitude,
            "sog": r.sog,
            "cog": r.cog,
            "zone": r.zone,
            "timestamp": r.timestamp.isoformat(),
        }
        for r in rows
    ]


@router.get("/global")
async def get_global_vessels(
    limit: int = Query(5000, ge=1, le=10000),
    db: Session = Depends(get_db),
):
    """Get all vessels from AISHub global snapshot (not just tankers/zones)."""
    rows = db.query(GlobalVesselPosition).limit(limit).all()
    return [
        {
            "mmsi": r.mmsi,
            "ship_name": r.ship_name,
            "ship_type": r.ship_type,
            "lat": r.latitude,
            "lon": r.longitude,
            "sog": r.sog,
            "cog": r.cog,
            "is_tanker": bool(r.is_tanker),
            "zone": r.zone,
        }
        for r in rows
    ]


@router.get("/geofence-events")
async def get_geofence_events(
    zone: str = Query(None, description="Filter by geofence zone name"),
    limit: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
):
    """Get aggregated geofence events (daily tanker counts, dwell times)."""
    query = db.query(GeofenceEvent).order_by(GeofenceEvent.date.desc())
    if zone:
        query = query.filter(GeofenceEvent.zone == zone)
    rows = query.limit(limit).all()
    return [
        {
            "zone": r.zone,
            "date": r.date,
            "tanker_count": r.tanker_count,
            "avg_dwell_hours": r.avg_dwell_hours,
            "slow_movers": r.slow_movers,
        }
        for r in rows
    ]


@router.get("/zones")
async def list_zones():
    """List all configured geofence zones."""
    return [
        {
            "name": z["name"],
            "display_name": z["display_name"],
            "bounds": z["bounds"],
            "description": z["description"],
            "no_ais_coverage": z["name"] in NO_AIS_COVERAGE,
        }
        for z in ZONES
    ]


@router.get("/weighted")
async def get_weighted_vessels(
    zone: str = Query(..., description="Geofence zone name (e.g. 'hormuz', 'suez')"),
    db: Session = Depends(get_db),
):
    """Weighted tanker count for a zone using ship-class heuristics.

    Computes on-the-fly from current vessel_positions — no schema changes.
    Weight factors: VLCC=3x, Suezmax=2x, Aframax=1x, Product/Tanker=0.5x.
    """
    # Latest position per MMSI in the given zone
    latest = (
        db.query(VesselPosition.mmsi, func.max(VesselPosition.id).label("max_id"))
        .filter(VesselPosition.zone == zone)
        .group_by(VesselPosition.mmsi)
        .subquery()
    )

    rows = (
        db.query(VesselPosition)
        .join(latest, VesselPosition.id == latest.c.max_id)
        .order_by(VesselPosition.timestamp.desc())
        .all()
    )

    # Build vessel list with classification
    vessels = []
    for r in rows:
        cls_name, weight = classify_vessel(r.ship_name, r.ship_type)
        vessels.append({
            "mmsi": r.mmsi,
            "ship_name": r.ship_name,
            "ship_type": r.ship_type,
            "class": cls_name,
            "weight": weight,
            "lat": r.latitude,
            "lon": r.longitude,
            "sog": r.sog,
            "zone": r.zone,
            "timestamp": r.timestamp.isoformat(),
        })

    # Compute weighted totals from raw vessel data
    summary = compute_weighted_count([
        {"ship_name": v["ship_name"], "ship_type": v["ship_type"]}
        for v in vessels
    ])

    return {
        "zone": zone,
        "raw_count": summary["raw_count"],
        "weighted_count": summary["weighted_count"],
        "by_class": summary["by_class"],
        "vessels": vessels,
    }
