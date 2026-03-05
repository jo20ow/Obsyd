from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models.prices import EIAPrice, FREDSeries
from backend.config import settings
from backend.collectors.eia import collect_eia, EIA_SERIES
from backend.collectors.fred import collect_fred, FRED_SERIES
from backend.collectors.alphavantage import fetch_live_commodities
from backend.collectors.finnhub import fetch_forex_prices

router = APIRouter(prefix="/api/prices", tags=["prices"])


@router.get("/eia")
async def get_eia_prices(
    series_id: str = Query(None, description="Filter by EIA series ID"),
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
):
    """Get stored EIA price/inventory data."""
    query = db.query(EIAPrice).order_by(EIAPrice.period.desc())
    if series_id:
        query = query.filter(EIAPrice.series_id == series_id)
    rows = query.limit(limit).all()
    return [
        {
            "series_id": r.series_id,
            "period": r.period,
            "value": r.value,
            "unit": r.unit,
            "description": r.description,
        }
        for r in rows
    ]


@router.get("/eia/series")
async def list_eia_series():
    """List available EIA series."""
    return {k: v["description"] for k, v in EIA_SERIES.items()}


@router.post("/eia/collect")
async def trigger_eia_collection(db: Session = Depends(get_db)):
    """Manually trigger EIA data collection."""
    await collect_eia(db)
    return {"status": "ok", "message": "EIA collection complete"}


@router.get("/live")
async def get_live_prices():
    """Get daily commodity prices from Alpha Vantage (BYOK, cached 15min)."""
    prices = await fetch_live_commodities()
    return {"available": bool(settings.alpha_vantage_api_key), "prices": prices}


@router.get("/forex")
async def get_forex_prices():
    """Get live forex rates from Finnhub (BYOK)."""
    prices = await fetch_forex_prices()
    return {"available": bool(settings.finnhub_api_key), "prices": prices}


@router.get("/fred")
async def get_fred_data(
    series_id: str = Query(None, description="Filter by FRED series ID"),
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
):
    """Get stored FRED macro data."""
    query = db.query(FREDSeries).order_by(FREDSeries.date.desc())
    if series_id:
        query = query.filter(FREDSeries.series_id == series_id)
    rows = query.limit(limit).all()
    return [
        {
            "series_id": r.series_id,
            "date": r.date,
            "value": r.value,
            "description": r.description,
        }
        for r in rows
    ]


@router.get("/fred/series")
async def list_fred_series():
    """List available FRED series."""
    return {k: v["description"] for k, v in FRED_SERIES.items()}


@router.post("/fred/collect")
async def trigger_fred_collection(db: Session = Depends(get_db)):
    """Manually trigger FRED data collection."""
    await collect_fred(db)
    return {"status": "ok", "message": "FRED collection complete"}
