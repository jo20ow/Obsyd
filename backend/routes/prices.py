from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models.prices import EIAPrice, FREDSeries
from backend.collectors.eia import collect_eia, EIA_SERIES
from backend.collectors.fred import collect_fred, FRED_SERIES
from backend.collectors.alphavantage import fetch_live_commodities
from backend.collectors.portwatch_store import query_oil_prices

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


FUNDAMENTALS_SERIES = [
    "PET.WPULEUS3.W",   # Refinery Utilization
    "PET.WCRIMUS2.W",   # Crude Imports
    "PET.WCREXUS2.W",   # Crude Exports
    "PET.WCSSTUS1.W.SPR",  # SPR
]


@router.get("/eia/fundamentals")
async def get_eia_fundamentals(
    limit: int = Query(52, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """Get EIA fundamentals: refinery utilization, imports, exports, SPR."""
    result = {}
    for sid in FUNDAMENTALS_SERIES:
        rows = (
            db.query(EIAPrice)
            .filter(EIAPrice.series_id == sid)
            .order_by(EIAPrice.period.desc())
            .limit(limit)
            .all()
        )
        result[sid] = [
            {"period": r.period, "value": r.value, "unit": r.unit, "description": r.description}
            for r in rows
        ]
    return result


@router.get("/live")
async def get_live_prices():
    """Get latest commodity prices. Alpha Vantage (15min cache) with FRED daily fallback."""
    prices = await fetch_live_commodities()
    source = "alphavantage" if prices else None

    if not prices:
        # Fallback: FRED daily prices from portwatch SQLite
        oil = query_oil_prices(days=10)
        fred_prices = {}
        for series_id, label in [("DCOILWTICO", "WTI"), ("DCOILBRENTEU", "BRENT")]:
            data = oil.get(series_id, [])
            if len(data) >= 2:
                latest = data[-1]
                prev = data[-2]
                change = latest["value"] - prev["value"]
                change_pct = (change / prev["value"]) * 100 if prev["value"] else 0
                fred_prices[label] = {
                    "symbol": series_id,
                    "date": latest["date"],
                    "current": latest["value"],
                    "previous_close": prev["value"],
                    "change": round(change, 4),
                    "change_pct": round(change_pct, 4),
                }
        if fred_prices:
            prices = fred_prices
            source = "fred"

    return {"available": bool(prices), "source": source, "prices": prices}


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


@router.get("/oil")
async def get_oil_prices(
    days: int = Query(365, ge=1, le=1825),
):
    """Get WTI + Brent daily prices from FRED (via obsyd.db/fred_series)."""
    cached = query_oil_prices(days=days)

    return {
        "source": "FRED (Federal Reserve Economic Data)",
        "series": {
            "DCOILWTICO": {"name": "WTI Crude Oil", "data": cached.get("DCOILWTICO", [])},
            "DCOILBRENTEU": {"name": "Brent Crude Oil", "data": cached.get("DCOILBRENTEU", [])},
        },
    }
