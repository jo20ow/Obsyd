from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func

from backend.database import get_db
from backend.models.prices import EIAPrice, FREDSeries
from backend.models.vessels import VesselPosition
from backend.models.sentiment import GDELTVolume

router = APIRouter()


@router.get("/health")
async def health_check():
    return {"status": "ok", "service": "obsyd"}


@router.get("/api/health/collectors")
async def collector_status(db: Session = Depends(get_db)):
    """Check which data collectors have recent data."""
    eia_count = db.query(func.count(EIAPrice.id)).scalar()
    fred_count = db.query(func.count(FREDSeries.id)).scalar()
    ais_count = db.query(func.count(VesselPosition.id)).scalar()
    gdelt_count = db.query(func.count(GDELTVolume.id)).scalar()

    return {
        "eia": eia_count > 0,
        "fred": fred_count > 0,
        "ais": ais_count > 0,
        "gdelt": gdelt_count > 0,
    }
