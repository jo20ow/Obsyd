from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models.weather import WeatherAlert
from backend.collectors.noaa import fetch_marine_conditions

router = APIRouter(prefix="/api/weather", tags=["weather"])


@router.get("/alerts")
async def get_weather_alerts(db: Session = Depends(get_db)):
    """Get active hurricane/tropical storm alerts from NOAA."""
    rows = db.query(WeatherAlert).order_by(WeatherAlert.created_at.desc()).all()
    return [
        {
            "id": r.id,
            "alert_id": r.alert_id,
            "event": r.event,
            "severity": r.severity,
            "headline": r.headline,
            "description": r.description,
            "area": r.area,
            "latitude": r.latitude,
            "longitude": r.longitude,
            "onset": r.onset,
            "expires_at": r.expires_at,
        }
        for r in rows
    ]


@router.get("/marine")
async def get_marine_conditions():
    """Get current wave and wind conditions for geofence zones from Open-Meteo."""
    conditions = await fetch_marine_conditions()
    return {
        "source": "Open-Meteo",
        "zones": conditions,
    }
