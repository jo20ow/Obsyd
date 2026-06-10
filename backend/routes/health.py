from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models.prices import EIAPrice, FREDSeries
from backend.models.sentiment import GDELTVolume
from backend.models.vessels import VesselPosition

router = APIRouter()

# Staleness thresholds per collector — matched to each source's cadence.
# A collector is "up" only if it has written within its window, so a
# silently-crashed collector flips to false instead of staying green forever.
STALENESS = {
    "eia": timedelta(days=14),  # weekly releases
    "fred": timedelta(days=7),  # daily series
    "ais": timedelta(hours=2),  # continuous stream
    "gdelt": timedelta(hours=24),  # 15-min cadence, generous window
}


@router.get("/health")
async def health_check(db: Session = Depends(get_db)):
    """Liveness + DB readiness. Returns 503 if the DB is unreachable,
    so the health-check cron restarts the service."""
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        raise HTTPException(status_code=503, detail="database unreachable")
    return {"status": "ok", "service": "obsyd"}


@router.get("/api/health/collectors")
async def collector_status(db: Session = Depends(get_db)):
    """Check which data collectors have written recently (not just ever)."""
    now = datetime.utcnow()

    last_eia = db.query(func.max(EIAPrice.fetched_at)).scalar()
    last_fred = db.query(func.max(FREDSeries.fetched_at)).scalar()
    last_ais = db.query(func.max(VesselPosition.timestamp)).scalar()
    last_gdelt = db.query(func.max(GDELTVolume.created_at)).scalar()

    def fresh(last, key):
        return last is not None and (now - last) <= STALENESS[key]

    return {
        "eia": fresh(last_eia, "eia"),
        "fred": fresh(last_fred, "fred"),
        "ais": fresh(last_ais, "ais"),
        "gdelt": fresh(last_gdelt, "gdelt"),
        "last_seen": {
            "eia": last_eia.isoformat() if last_eia else None,
            "fred": last_fred.isoformat() if last_fred else None,
            "ais": last_ais.isoformat() if last_ais else None,
            "gdelt": last_gdelt.isoformat() if last_gdelt else None,
        },
    }
