from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models.vessels import VesselRegistry, VoyageEvent

router = APIRouter(prefix="/api/voyages", tags=["voyages"])


@router.get("/recent")
async def get_recent_voyages(
    days: int = Query(30, ge=1, le=365),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """Recent detected voyages (zone-to-zone transits)."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    rows = (
        db.query(VoyageEvent)
        .filter(VoyageEvent.destination_first_seen >= cutoff)
        .order_by(VoyageEvent.destination_first_seen.desc())
        .limit(limit)
        .all()
    )

    result = []
    for r in rows:
        # Try to get ship_class from registry
        reg = db.query(VesselRegistry).filter(VesselRegistry.mmsi == r.mmsi).first()
        result.append(
            {
                "mmsi": r.mmsi,
                "ship_name": r.ship_name,
                "ship_type": r.ship_type,
                "ship_class": reg.ship_class if reg else None,
                "origin_zone": r.origin_zone,
                "destination_zone": r.destination_zone,
                "origin_first_seen": r.origin_first_seen.isoformat() if r.origin_first_seen else None,
                "origin_last_seen": r.origin_last_seen.isoformat() if r.origin_last_seen else None,
                "destination_first_seen": r.destination_first_seen.isoformat() if r.destination_first_seen else None,
                "transit_hours": r.transit_hours,
                "status": r.status,
            }
        )

    return {"days": days, "count": len(result), "voyages": result}


@router.get("/flow-matrix")
async def get_flow_matrix(
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
):
    """Zone-to-zone flow matrix: count of voyages per route."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    rows = (
        db.query(
            VoyageEvent.origin_zone,
            VoyageEvent.destination_zone,
            func.count(VoyageEvent.id).label("count"),
        )
        .filter(VoyageEvent.destination_first_seen >= cutoff)
        .group_by(VoyageEvent.origin_zone, VoyageEvent.destination_zone)
        .all()
    )

    matrix = {}
    for origin, dest, count in rows:
        key = f"{origin}\u2192{dest}"
        matrix[key] = count

    return {"days": days, "matrix": matrix}
