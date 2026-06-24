"""Spark spread collector — daily EUR/MWh margin for gas-fired power generation.

Computes the clean (CO₂-free) spark spread:
    spark_spread = power_price − gas_price × heat_rate
where:
    power_price — EnergyPrice symbol POWER_DE (daily mean EUR/MWh, ENTSO-E A44)
    gas_price   — EnergyPrice symbol TTF (daily close EUR/MWh, yfinance)
    heat_rate   — 1 / settings.gas_ccgt_efficiency  (default: 1/0.50 = 2.0 MWh_gas/MWh_el)

CO₂ / clean-spark spread is deferred: `co2_price` and `clean_spark_spread` are
always stored as None until a reliable EUA ticker is confirmed.

One idempotent upsert per calendar day: if both POWER_DE and TTF have a row for
the same date, a SparkSpreadHistory row is created or updated.  Dates where either
price is missing are silently skipped (inner-join semantics).

Scheduled as part of `_run_power_daily` in backend/collectors/scheduler.py.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from backend.config import settings
from backend.database import SessionLocal
from backend.models.energy import EnergyPrice, SparkSpreadHistory

logger = logging.getLogger(__name__)


def _compute_and_upsert(db: Session) -> dict:
    """Inner sync function: align POWER_DE + TTF on date, compute spread, upsert.

    Returns {"computed": n, "written": n} where `written` counts new rows and
    updated rows combined.
    """
    heat_rate = 1.0 / settings.gas_ccgt_efficiency

    # Pull both price series as dicts keyed by date string
    power_rows = (
        db.query(EnergyPrice)
        .filter(EnergyPrice.symbol == "POWER_DE")
        .all()
    )
    ttf_rows = (
        db.query(EnergyPrice)
        .filter(EnergyPrice.symbol == "TTF")
        .all()
    )

    power_by_date: dict[str, float] = {r.date: r.close for r in power_rows}
    ttf_by_date: dict[str, float] = {r.date: r.close for r in ttf_rows}

    # Inner join: only dates where both prices exist
    common_dates = sorted(set(power_by_date) & set(ttf_by_date))

    if not common_dates:
        logger.info("spark_spreads: no overlapping POWER_DE/TTF dates — nothing to compute")
        return {"computed": 0, "written": 0}

    written = 0
    for date_str in common_dates:
        power_price = power_by_date[date_str]
        gas_price = ttf_by_date[date_str]
        spark = round(power_price - gas_price * heat_rate, 4)

        existing = (
            db.query(SparkSpreadHistory)
            .filter(SparkSpreadHistory.date == date_str)
            .first()
        )
        if existing:
            # Update only if something changed (prices may be revised)
            if (
                existing.power_price != power_price
                or existing.gas_price != gas_price
                or existing.heat_rate != heat_rate
                or existing.spark_spread != spark
            ):
                existing.power_price = power_price
                existing.gas_price = gas_price
                existing.heat_rate = heat_rate
                existing.spark_spread = spark
                written += 1
        else:
            db.add(
                SparkSpreadHistory(
                    date=date_str,
                    power_price=power_price,
                    gas_price=gas_price,
                    heat_rate=heat_rate,
                    spark_spread=spark,
                    co2_price=None,          # deferred
                    clean_spark_spread=None,  # deferred
                )
            )
            written += 1

    db.commit()

    total = db.query(SparkSpreadHistory).count()
    logger.info(
        "spark_spreads: %d common dates, %d rows written (total: %d, heat_rate=%.4f)",
        len(common_dates),
        written,
        total,
        heat_rate,
    )
    return {"computed": len(common_dates), "written": written}


async def collect_spark_spreads() -> dict:
    """Async entry point for the scheduler and one-off backfill calls.

    Opens its own DB session so it can be awaited directly from a coroutine
    or from `_run_power_daily` in the scheduler.
    """
    db = SessionLocal()
    try:
        result = _compute_and_upsert(db)
        return result
    except Exception as exc:
        db.rollback()
        logger.error("spark_spreads: collection failed: %s", exc)
        raise
    finally:
        db.close()
