"""Signal analysis endpoints."""

from fastapi import APIRouter, Query

from backend.signals.correlation import compute_correlations
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


@router.get("/historical")
async def get_historical_anomalies(
    chokepoint: str = Query("hormuz", description="Chokepoint name"),
    threshold: float = Query(40.0, ge=10, le=90, description="Drop threshold %"),
):
    """Find historical chokepoint anomalies with Brent price correlation."""
    return find_anomalies(chokepoint, threshold_pct=threshold)
