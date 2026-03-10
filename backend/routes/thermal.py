from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.auth.dependencies import require_pro
from backend.collectors.firms import PROXIMITY_KM, REFINERIES, _haversine_km, collect_firms
from backend.database import get_db
from backend.models.thermal import ThermalHotspot

router = APIRouter(prefix="/api/thermal", tags=["thermal"])


@router.get("/hotspots")
async def get_hotspots(
    area: str = Query(None, description="Filter by area name"),
    db: Session = Depends(get_db),
):
    """Get current thermal hotspot detections."""
    query = db.query(ThermalHotspot)
    if area:
        query = query.filter(ThermalHotspot.area_name == area)
    rows = query.all()
    return [
        {
            "lat": r.latitude,
            "lon": r.longitude,
            "brightness": r.brightness,
            "confidence": r.confidence,
            "area_name": r.area_name,
            "satellite": r.satellite,
            "acq_date": r.acq_date,
            "acq_time": r.acq_time,
        }
        for r in rows
    ]


@router.get("/refineries")
async def get_refinery_status(db: Session = Depends(get_db)):
    """Get refinery thermal status: active/inactive based on nearby hotspots."""
    hotspots = db.query(ThermalHotspot).all()

    result = []
    for ref in REFINERIES:
        nearby = [
            h
            for h in hotspots
            if h.area_name == ref["area"]
            and _haversine_km(ref["lat"], ref["lon"], h.latitude, h.longitude) <= PROXIMITY_KM
        ]
        result.append(
            {
                "name": ref["name"],
                "lat": ref["lat"],
                "lon": ref["lon"],
                "area": ref["area"],
                "active": len(nearby) > 0,
                "hotspot_count": len(nearby),
                "max_brightness": max((h.brightness for h in nearby), default=0),
            }
        )

    return result


@router.post("/collect")
async def trigger_firms_collection(_user=Depends(require_pro)):
    """Manually trigger FIRMS data collection."""
    await collect_firms()
    return {"status": "ok", "message": "FIRMS collection complete"}
