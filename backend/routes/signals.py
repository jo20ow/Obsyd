"""Signal analysis endpoints."""

from fastapi import APIRouter, Query

from backend.signals.correlation import compute_correlations

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
