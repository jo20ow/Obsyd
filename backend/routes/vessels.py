from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import func

from backend.database import get_db
from backend.models.vessels import VesselPosition, GeofenceEvent, GlobalVesselPosition
from backend.geofences.zones import ZONES, NO_AIS_COVERAGE

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
