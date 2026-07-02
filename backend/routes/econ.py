"""Economic calendar read endpoint — the ECO "what's coming this week" view.

GET /api/econ/calendar?days=21 — upcoming curated macro releases (schedule only), FREE.

Consensus/forecast is deliberately absent (survey estimates are licensed, not free);
this is the honest free half — the release schedule from FRED. Not investment advice.
"""

from fastapi import APIRouter, Query

from backend.econ.fred_calendar import get_calendar

router = APIRouter(prefix="/api/econ", tags=["econ"])


@router.get("/calendar")
async def calendar(days: int = Query(21, ge=1, le=90)):
    """Upcoming major US macro releases for the next `days` days (schedule, no consensus)."""
    data = await get_calendar(days_ahead=days)
    if not data:
        return {"available": False, "reason": "Release calendar unavailable — check back shortly."}
    return {"available": True, "days": days, "source": "FRED release schedule", "data": data}
