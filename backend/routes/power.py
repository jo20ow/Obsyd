"""Electricity + spark spread read endpoints.

GET /api/power/day-ahead?days=120  — FREE
    ENTSO-E A44 daily mean EUR/MWh series for DE-LU bidding zone.
    Returns EnergyPrice(symbol="POWER_DE") rows, newest first, then reversed.

GET /api/power/spark-spread?days=120  — PRO
    Historical spark spread (power − gas × heat_rate).
    Returns SparkSpreadHistory rows with a `latest` summary object.

GET /api/power/grid?days=120  — FREE
    ENTSO-E A65 (load) + A75 (wind + solar) for DE-LU.
    Returns PowerGrid rows with residual_mw, renewable_share, dunkelflaute flag.

All endpoints follow the `{"available": bool, "data": [...]}` envelope used
throughout the gas vertical (see backend/routes/gas.py).
"""

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.auth.dependencies import require_pro
from backend.database import get_db
from backend.models.energy import EnergyPrice, PowerGrid, SparkSpreadHistory

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


# ─── Grid load + renewables (free) ───────────────────────────────────────────

#: Renewable share threshold below which a day is flagged as Dunkelflaute.
DUNKELFLAUTE_THRESHOLD = 0.15


def _compute_grid_row(r: PowerGrid) -> dict:
    """Derive residual_mw, renewable_share, and dunkelflaute flag for one row.

    None wind_mw / solar_mw are treated as 0 (they can be stored None when
    genuinely near-zero during ingest failures).
    """
    wind = r.wind_mw or 0.0
    solar = r.solar_mw or 0.0
    load = r.load_mw or 0.0

    residual_mw = load - wind - solar
    renewable_share = (wind + solar) / load if load > 0 else 0.0
    dunkelflaute = renewable_share < DUNKELFLAUTE_THRESHOLD

    return {
        "date": r.date,
        "load_mw": r.load_mw,
        "wind_mw": r.wind_mw,
        "solar_mw": r.solar_mw,
        "residual_mw": round(residual_mw, 2),
        "renewable_share": round(renewable_share, 4),
        "dunkelflaute": dunkelflaute,
    }


@router.get("/grid")
async def get_grid(
    days: int = Query(120, ge=1, le=1500),
    db: Session = Depends(get_db),
):
    """ENTSO-E grid load + wind + solar for DE-LU (daily mean MW). Free tier.

    Returns residual_mw (load − wind − solar), renewable_share, and a
    Dunkelflaute flag (renewable_share < 15%) per day.  `latest` contains
    the most recent row; `dunkelflaute_days` is the count within the window.
    """
    date_from, date_to = _window(days)
    rows = (
        db.query(PowerGrid)
        .filter(
            PowerGrid.zone == "DE_LU",
            PowerGrid.date >= date_from,
            PowerGrid.date <= date_to,
        )
        .order_by(PowerGrid.date.asc())
        .all()
    )
    if not rows:
        return {
            "available": False,
            "reason": "no grid data yet — run power grid backfill (ingest_grid)",
        }

    data = [_compute_grid_row(r) for r in rows]
    latest = data[-1]
    dunkelflaute_days = sum(1 for d in data if d["dunkelflaute"])

    return {
        "available": True,
        "zone": "DE_LU",
        "unit": "MW",
        "threshold_note": f"dunkelflaute = renewable_share < {DUNKELFLAUTE_THRESHOLD:.0%}",
        "latest": latest,
        "dunkelflaute_days": dunkelflaute_days,
        "from": date_from,
        "to": date_to,
        "data": data,
    }
