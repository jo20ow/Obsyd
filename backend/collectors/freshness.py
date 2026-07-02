"""Shared collector-freshness spec — one source of truth for /api/health/collectors
and the daily collector watchdog, so they can never drift.

Two probe kinds:
  * ``is_date_string=False`` — compare an INGESTION timestamp column
    (fetched_at / created_at / timestamp) to now. "Did the collector write recently?"
  * ``is_date_string=True`` — compare max(DELIVERY-DATE string "YYYY-MM-DD") to today.
    Product-critical sources (ENTSO-E, Energy-Charts, gas, yfinance) are re-written
    every night with overwrite=True, so an ingestion-timestamp probe looks fresh even
    when the data is days stale — only the data's own date reveals a frozen frontier.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as _date
from datetime import datetime, timedelta, timezone

from sqlalchemy import func

from backend.models.energy import EnergyPrice, PowerFlow, PowerGrid, PowerPriceDaily
from backend.models.gas import GasBalance
from backend.models.prices import EIAPrice, FREDSeries
from backend.models.sentiment import GDELTVolume
from backend.models.vessels import VesselPosition


@dataclass(frozen=True)
class FreshnessSpec:
    key: str
    model: type
    column: str                 # attribute name on model to take max() of
    max_age: timedelta
    is_date_string: bool = False # True → column is "YYYY-MM-DD" delivery-date string
    filter_col: str | None = None
    filter_val: str | None = None


# Windows are matched to each source's publication cadence (day-ahead is daily but
# frontier-based; realised grid lags ~1d; gas confirms 1-2d late; yfinance ~3 trading days).
SPECS: list[FreshnessSpec] = [
    # Ingestion-timestamp probes (legacy 4).
    FreshnessSpec("eia", EIAPrice, "fetched_at", timedelta(days=14)),
    FreshnessSpec("fred", FREDSeries, "fetched_at", timedelta(days=7)),
    FreshnessSpec("ais", VesselPosition, "timestamp", timedelta(hours=2)),
    FreshnessSpec("gdelt", GDELTVolume, "created_at", timedelta(hours=24)),
    # Delivery-date probes (product-critical — the ones that were unmonitored).
    FreshnessSpec("power_dayahead", PowerPriceDaily, "date", timedelta(days=2),
                  is_date_string=True, filter_col="zone", filter_val="DE_LU"),
    FreshnessSpec("power_grid", PowerGrid, "date", timedelta(days=3),
                  is_date_string=True, filter_col="zone", filter_val="DE_LU"),
    FreshnessSpec("power_flows", PowerFlow, "date", timedelta(days=3), is_date_string=True),
    FreshnessSpec("gas_balance", GasBalance, "date", timedelta(days=3), is_date_string=True),
    FreshnessSpec("ttf", EnergyPrice, "date", timedelta(days=4),
                  is_date_string=True, filter_col="symbol", filter_val="TTF"),
    FreshnessSpec("copper", EnergyPrice, "date", timedelta(days=4),
                  is_date_string=True, filter_col="symbol", filter_val="COPPER"),
]


def _spec_max(db, spec: FreshnessSpec):
    q = db.query(func.max(getattr(spec.model, spec.column)))
    if spec.filter_col is not None:
        q = q.filter(getattr(spec.model, spec.filter_col) == spec.filter_val)
    return q.scalar()


def evaluate_freshness(db, *, now: datetime | None = None) -> dict[str, dict]:
    """Return {key: {"fresh": bool, "last_seen": str|None, "max_age_days": float}} for every spec."""
    if now is None:
        now = datetime.now(timezone.utc)
    today = now.date()
    # The ingestion columns are naive UTC (datetime.utcnow); compare naively to avoid
    # aware/naive subtraction errors.
    now_naive = now.replace(tzinfo=None)

    out: dict[str, dict] = {}
    for spec in SPECS:
        latest = _spec_max(db, spec)
        fresh = False
        last_seen = None
        if latest is not None:
            if spec.is_date_string:
                last_seen = str(latest)
                try:
                    d = _date.fromisoformat(last_seen[:10])
                    fresh = (today - d) <= spec.max_age
                except ValueError:
                    fresh = False
            else:
                last_seen = latest.isoformat()
                fresh = (now_naive - latest) <= spec.max_age
        out[spec.key] = {
            "fresh": fresh,
            "last_seen": last_seen,
            "max_age_days": spec.max_age.total_seconds() / 86400,
        }
    return out
