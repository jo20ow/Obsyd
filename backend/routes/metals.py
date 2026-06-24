"""Metals API routes.

GET /api/metals/copper?months=36
  Returns U.S. copper supply time series (FREE — all USGS data is public domain).
  Response shape:
    {
      "available": bool,
      "source": "USGS Mineral Industry Surveys (public domain)",
      "unit": "metric tons",
      "data": [{"date", "us_mine_production", "us_refined_production", "us_refined_stocks"}, ...],
      "latest": {...},
      "price": [{"date", "close"}, ...],   # COPPER (HG=F) USD/lb from EnergyPrice
    }
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models.energy import EnergyPrice
from backend.models.metals import CopperSupply

router = APIRouter(prefix="/api/metals", tags=["metals"])

_SOURCE = "USGS Mineral Industry Surveys (public domain)"
_UNIT = "metric tons"


@router.get("/copper")
async def get_copper_supply(
    months: int = Query(36, ge=1, le=120),
    db: Session = Depends(get_db),
):
    """Monthly U.S. copper supply data from USGS MIS (public domain, free tier)."""
    # Fetch supply rows ordered ascending (chart-friendly)
    rows = (
        db.query(CopperSupply)
        .order_by(CopperSupply.date.asc())
        .limit(months)
        .all()
    )

    if not rows:
        return {"available": False, "source": _SOURCE, "unit": _UNIT}

    data = [
        {
            "date": r.date,
            "us_mine_production": r.us_mine_production,
            "us_refined_production": r.us_refined_production,
            "us_refined_stocks": r.us_refined_stocks,
        }
        for r in rows
    ]

    # Latest = most recent row (last in ascending list)
    latest_row = max(rows, key=lambda r: r.date)
    latest = {
        "date": latest_row.date,
        "us_mine_production": latest_row.us_mine_production,
        "us_refined_production": latest_row.us_refined_production,
        "us_refined_stocks": latest_row.us_refined_stocks,
    }

    # Copper price (HG=F) from EnergyPrice — most recent months_equivalent days
    price_rows = (
        db.query(EnergyPrice)
        .filter(EnergyPrice.symbol == "COPPER")
        .order_by(EnergyPrice.date.asc())
        .limit(months * 31)  # generous daily window to cover the monthly span
        .all()
    )
    price = [{"date": p.date, "close": p.close} for p in price_rows]

    return {
        "available": True,
        "source": _SOURCE,
        "unit": _UNIT,
        "data": data,
        "latest": latest,
        "price": price,
    }
