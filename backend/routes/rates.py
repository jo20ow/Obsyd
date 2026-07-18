"""Rates read endpoints — the US Treasury yield curve for the cross-asset terminal.

GET /api/rates/curve            — latest yield per tenor + 10Y-2Y spread, FREE
GET /api/rates/history?series=  — one tenor's daily history (charting), FREE

Built on the existing FRED collector (FREDSeries); constant-maturity treasury
yields are free, complete, public-domain — the canonical rates object. Envelope
matches the rest of the app: {"available": bool, "data": [...]}.
"""

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models.prices import FREDSeries

router = APIRouter(prefix="/api/rates", tags=["rates"])

# US Treasury constant-maturity curve: (FRED series id, tenor label, years for the x-axis).
CURVE_TENORS: list[tuple[str, str, float]] = [
    ("DGS1MO", "1M", 1 / 12),
    ("DGS3MO", "3M", 0.25),
    ("DGS6MO", "6M", 0.5),
    ("DGS1", "1Y", 1.0),
    ("DGS2", "2Y", 2.0),
    ("DGS3", "3Y", 3.0),
    ("DGS5", "5Y", 5.0),
    ("DGS7", "7Y", 7.0),
    ("DGS10", "10Y", 10.0),
    ("DGS20", "20Y", 20.0),
    ("DGS30", "30Y", 30.0),
]
_TENOR_SERIES = {sid for sid, _l, _y in CURVE_TENORS}


def _latest(db: Session, series_id: str):
    return (
        db.query(FREDSeries)
        .filter(FREDSeries.series_id == series_id)
        .order_by(FREDSeries.date.desc())
        .first()
    )


@router.get("/curve")
def get_curve(db: Session = Depends(get_db)):
    """Latest yield per tenor (ascending by maturity) + the 10Y-2Y inversion spread."""
    points = []
    as_of = None
    for sid, label, years in CURVE_TENORS:
        row = _latest(db, sid)
        if row is None:
            continue
        points.append({"series_id": sid, "tenor": label, "years": round(years, 4), "yield": row.value, "date": row.date})
        if as_of is None or row.date > as_of:
            as_of = row.date
    if not points:
        return {"available": False, "reason": "No yield-curve data yet — check back shortly."}

    by = {p["series_id"]: p["yield"] for p in points}
    spread = round(by["DGS10"] - by["DGS2"], 2) if "DGS10" in by and "DGS2" in by else None
    return {
        "available": True,
        "as_of": as_of,
        "unit": "percent",
        "data": points,
        "spread_10y2y": spread,
        "inverted": spread is not None and spread < 0,
    }


@router.get("/history")
def get_history(
    series: str = Query(..., description="FRED tenor series, e.g. DGS10"),
    days: int = Query(365, ge=1, le=3650),
    db: Session = Depends(get_db),
):
    """One tenor's daily yield history (ascending) — for the sparkline/chart."""
    sid = series.upper()
    if sid not in _TENOR_SERIES:
        return {"available": False, "series": sid, "reason": f"unknown series: {sid}"}
    start = (datetime.utcnow().date() - timedelta(days=days)).isoformat()
    rows = (
        db.query(FREDSeries)
        .filter(FREDSeries.series_id == sid, FREDSeries.date >= start)
        .order_by(FREDSeries.date.asc())
        .all()
    )
    if not rows:
        return {"available": False, "series": sid, "reason": "No history yet."}
    return {
        "available": True,
        "series": sid,
        "unit": "percent",
        "data": [{"date": r.date, "yield": r.value} for r in rows],
    }
