from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models.vessels import VesselPosition, GeofenceEvent
from backend.geofences.zones import ZONES

router = APIRouter(prefix="/api/vessels", tags=["vessels"])


@router.get("/positions")
async def get_vessel_positions(
    zone: str = Query(None, description="Filter by geofence zone name"),
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
):
    """Get recent vessel positions within geofences."""
    query = db.query(VesselPosition).order_by(VesselPosition.timestamp.desc())
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
        }
        for z in ZONES
    ]
