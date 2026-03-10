"""Signal analysis endpoints."""

from fastapi import APIRouter, Depends, Query

from backend.auth.dependencies import require_pro
from backend.database import SessionLocal
from backend.models.pro_features import CrackSpreadHistory, EquitySnapshot
from backend.signals.correlation import compute_correlations
from backend.signals.crack_spread import get_crack_spread
from backend.signals.historical_lookup import find_anomalies
from backend.signals.market_structure import get_market_structure
from backend.signals.tonnage_proxy import compute_rerouting_index

router = APIRouter(prefix="/api/signals", tags=["signals"])


@router.get("/correlation")
async def get_correlation(
    days: int = Query(365, ge=30, le=1825),
):
    """Chokepoint tanker traffic vs Brent price correlation analysis."""
    correlations = compute_correlations(days=days)
    return {
        "source": "OBSYD Correlation Engine",
        "period_days": days,
        "correlations": correlations,
    }


@router.get("/market-structure")
async def get_market_structure_endpoint():
    """Current contango/backwardation state for WTI and Brent futures curves."""
    return await get_market_structure()


@router.get("/rerouting-index")
async def get_rerouting_index(
    days: int = Query(365, ge=30, le=2600),
):
    """Cape/Suez rerouting index — detects traffic diversion from Suez to Cape of Good Hope."""
    return compute_rerouting_index(days=days)


@router.get("/crack-spread")
async def get_crack_spread_endpoint():
    """3:2:1 crack spread — refinery profitability indicator (live)."""
    return await get_crack_spread()


@router.get("/crack-spreads")
async def get_crack_spread_history(
    days: int = Query(365, ge=7, le=730),
    _user=Depends(require_pro),
):
    """Historical crack spread data with daily values. Pro only."""
    db = SessionLocal()
    try:
        rows = db.query(CrackSpreadHistory).order_by(CrackSpreadHistory.date.desc()).limit(days).all()

        # Current live values
        live = await get_crack_spread()

        history = [
            {
                "date": r.date,
                "wti": r.wti_price,
                "rbob": r.rbob_price,
                "ho": r.ho_price,
                "gasoline_crack": r.gasoline_crack,
                "heating_oil_crack": r.heating_oil_crack,
                "three_two_one": r.three_two_one_crack,
            }
            for r in reversed(rows)
        ]

        return {
            "current": live,
            "history": history,
            "count": len(history),
        }
    finally:
        db.close()


@router.get("/equities")
async def get_equities(_user=Depends(require_pro)):
    """Related energy equities with correlations. Pro only."""
    db = SessionLocal()
    try:
        # Get the most recent date with data
        latest = db.query(EquitySnapshot.date).order_by(EquitySnapshot.date.desc()).first()
        if not latest:
            return {"equities": [], "date": None}

        rows = (
            db.query(EquitySnapshot)
            .filter(EquitySnapshot.date == latest[0])
            .order_by(EquitySnapshot.sector, EquitySnapshot.ticker)
            .all()
        )

        equities = [
            {
                "ticker": r.ticker,
                "name": r.name,
                "sector": r.sector,
                "price": r.price,
                "change_pct": r.change_pct,
                "wti_corr_30d": r.wti_corr_30d,
                "brent_corr_90d": r.brent_corr_90d,
                "high_52w": r.high_52w,
                "low_52w": r.low_52w,
                "market_cap": r.market_cap,
            }
            for r in rows
        ]

        return {
            "equities": equities,
            "date": latest[0],
            "count": len(equities),
        }
    finally:
        db.close()


@router.get("/historical")
async def get_historical_anomalies(
    chokepoint: str = Query("hormuz", description="Chokepoint name"),
    threshold: float = Query(40.0, ge=10, le=90, description="Drop threshold %"),
):
    """Find historical chokepoint anomalies with Brent price correlation."""
    return find_anomalies(chokepoint, threshold_pct=threshold)
