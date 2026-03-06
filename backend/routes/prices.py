from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models.prices import EIAPrice, FREDSeries
from backend.collectors.eia import collect_eia, EIA_SERIES
from backend.collectors.fred import collect_fred, FRED_SERIES
from backend.collectors.portwatch_store import query_oil_prices
from backend.providers import price_provider
from backend.providers.twelvedata_provider import SYMBOLS as TD_SYMBOLS

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
    """Get latest commodity prices via the configured price provider."""
    return await price_provider.get_live_prices()


@router.get("/commodities")
async def get_commodities():
    """Get all commodity prices grouped by category."""
    result = await price_provider.get_live_prices()
    prices = result.get("prices", {})

    energy = {k: v for k, v in prices.items() if k in ("WTI", "BRENT", "NG")}
    metals = {k: v for k, v in prices.items() if k in ("GOLD", "COPPER")}
    agriculture = {}

    return {
        "source": result.get("source"),
        "energy": energy,
        "metals": metals,
        "agriculture": agriculture,
    }


@router.get("/intraday")
async def get_intraday(
    symbol: str = Query("WTI", description="Symbol: WTI, BRENT, NG, GOLD, SILVER, COPPER"),
    interval: str = Query("15min", description="Interval: 1min, 5min, 15min, 30min, 1h, 2h, 4h"),
    outputsize: int = Query(96, ge=1, le=5000, description="Number of data points"),
):
    """Get intraday OHLCV time series for a commodity."""
    return await price_provider.get_intraday(symbol, interval, outputsize)


@router.get("/fred")
async def get_fred_data(
    series_id: str = Query(None, description="Filter by FRED series ID"),
    limit: int = Query(100, ge=1, le=5000),
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


@router.get("/chart")
async def get_chart_data(
    db: Session = Depends(get_db),
):
    """Get WTI + Brent daily prices for charting (all available data since 2019)."""
    rows = (
        db.query(FREDSeries)
        .filter(FREDSeries.series_id.in_(["DCOILWTICO", "DCOILBRENTEU"]))
        .order_by(FREDSeries.date.asc())
        .all()
    )
    result = {}
    for r in rows:
        if r.series_id not in result:
            result[r.series_id] = []
        result[r.series_id].append({"time": r.date, "value": r.value})
    return result


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
