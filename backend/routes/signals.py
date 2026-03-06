"""Signal analysis endpoints."""

from fastapi import APIRouter, Query

from backend.signals.correlation import compute_correlations
from backend.signals.historical_lookup import find_anomalies

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


@router.get("/historical")
async def get_historical_anomalies(
    chokepoint: str = Query("hormuz", description="Chokepoint name"),
    threshold: float = Query(40.0, ge=10, le=90, description="Drop threshold %"),
):
    """Find historical chokepoint anomalies with Brent price correlation."""
    return find_anomalies(chokepoint, threshold_pct=threshold)
