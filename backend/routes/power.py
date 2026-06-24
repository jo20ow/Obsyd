"""Electricity + spark spread read endpoints.

GET /api/power/day-ahead?days=120  — FREE
    ENTSO-E A44 daily mean EUR/MWh series for DE-LU bidding zone.
    Returns EnergyPrice(symbol="POWER_DE") rows, newest first, then reversed.

GET /api/power/spark-spread?days=120  — PRO
    Historical spark spread (power − gas × heat_rate).
    Returns SparkSpreadHistory rows with a `latest` summary object.

Both endpoints follow the `{"available": bool, "data": [...]}` envelope used
throughout the gas vertical (see backend/routes/gas.py).
"""

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.auth.dependencies import require_pro
from backend.database import get_db
from backend.models.energy import EnergyPrice, SparkSpreadHistory

router = APIRouter(prefix="/api/power", tags=["power"])


def _window(days: int) -> tuple[str, str]:
    """Return (start_iso, end_iso) for the last `days` calendar days (UTC)."""
    end = datetime.utcnow().date()
    start = end - timedelta(days=days)
    return start.isoformat(), end.isoformat()


# ─── Day-ahead prices (free) ──────────────────────────────────────────────────


@router.get("/day-ahead")
async def get_day_ahead(
    days: int = Query(120, ge=1, le=1500),
    db: Session = Depends(get_db),
):
    """ENTSO-E day-ahead electricity prices for DE-LU (EUR/MWh). Free tier."""
    date_from, date_to = _window(days)
    rows = (
        db.query(EnergyPrice)
        .filter(
            EnergyPrice.symbol == "POWER_DE",
            EnergyPrice.date >= date_from,
            EnergyPrice.date <= date_to,
        )
        .order_by(EnergyPrice.date.asc())
        .all()
    )
    if not rows:
        return {
            "available": False,
            "reason": "no POWER_DE data yet — run power backfill (ingest_day_ahead)",
        }
    return {
        "available": True,
        "symbol": "POWER_DE",
        "unit": "EUR/MWh",
        "from": date_from,
        "to": date_to,
        "data": [{"date": r.date, "close": r.close} for r in rows],
    }


# ─── Spark spread (Pro) ───────────────────────────────────────────────────────


@router.get("/spark-spread")
async def get_spark_spread(
    days: int = Query(120, ge=7, le=1500),
    db: Session = Depends(get_db),
    _user=Depends(require_pro),
):
    """Spark spread history (power − gas × heat_rate, EUR/MWh). Pro only.

    `latest` contains the most recent row for dashboard widgets.
    `data` is the full window sorted ascending for charting.
    CO₂ and clean-spark fields are included in the schema but will be null
    until EUA data ingestion is implemented.
    """
    date_from, date_to = _window(days)
    rows = (
        db.query(SparkSpreadHistory)
        .filter(
            SparkSpreadHistory.date >= date_from,
            SparkSpreadHistory.date <= date_to,
        )
        .order_by(SparkSpreadHistory.date.asc())
        .all()
    )
    if not rows:
        return {
            "available": False,
            "reason": (
                "no spark spread data yet — "
                "ingest POWER_DE via ingest_day_ahead, then run collect_spark_spreads"
            ),
        }

    def _row_dict(r: SparkSpreadHistory) -> dict:
        return {
            "date": r.date,
            "power_price": r.power_price,
            "gas_price": r.gas_price,
            "heat_rate": r.heat_rate,
            "spark_spread": r.spark_spread,
            "co2_price": r.co2_price,
            "clean_spark_spread": r.clean_spark_spread,
        }

    latest = _row_dict(rows[-1])
    return {
        "available": True,
        "unit": "EUR/MWh",
        "heat_rate_note": "1 / CCGT_efficiency; default efficiency = 0.50",
        "co2_note": "co2_price and clean_spark_spread are deferred (EUA ticker TBD)",
        "latest": latest,
        "from": date_from,
        "to": date_to,
        "data": [_row_dict(r) for r in rows],
    }
