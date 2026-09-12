"""Two derived statistics promoted to first-class exported series.

Both numbers already existed inside the desk — the point of this module is the
promotion: once a value lives in `power_hourly` it gets /api/v1/series, the
catalog, CSV/Parquet, the snapshot, the Python client and the records engine
for free, and THAT is what "the data gets more valuable" means here. Neither
computes anything new; both mirror an existing single source of truth, so the
panel and the export can never disagree.

price.negative_hours  (one point per day, ts = 00:00 UTC, unit "h")
    Mirrors PowerPriceDaily.negative_hours — the resolution-weighted count
    (since SDAC's 15-minute switch four negative quarters are one hour) that
    the day-ahead ingest has maintained per zone since #22. The most
    press-cited European power statistic, and nobody serves it as an API
    across the zones — including this desk, until now. Zones without a
    day-ahead feed (GB) simply have no rows and therefore no series: absence
    is structural, never zero-filled.

capture.<PSR>.price / capture.<PSR>.factor  (one point per month, ts = 1st
    00:00 UTC; EUR/MWh and a ratio)
    Mirrors compute_capture (backend/power/capture.py) — the SAME engine the
    /api/power/capture panel reads, called here and stored, so every guard it
    enforces (whole-month baseload denominator, MIN_DAYS day-count sample
    guard, complete-months-only, no factor through a non-positive baseload)
    holds for the export by construction. Only months the engine reports are
    written: a month it withholds stays absent rather than becoming a 0.

Recompute doctrine (records.py): every run rebuilds its window from the
current sources; the nightly covers the revision horizon, the backfill script
covers history, and both call the same functions. Excluded from the revision
ledger like every derived series (their restatements are their inputs').
"""
from __future__ import annotations

import logging
from datetime import UTC, date, datetime

from sqlalchemy.orm import Session

from backend.models.energy import PowerPriceDaily
from backend.power.capture import CAPTURE_FUELS, compute_capture
from backend.power.hourly_store import day_hour_ts, upsert_hourly

logger = logging.getLogger(__name__)

NEGATIVE_HOURS_SERIES = "price.negative_hours"


def store_negative_hours(db: Session, zone: str, start_day: str | None = None) -> int:
    """Mirror the daily negative-hours count into the hourly store. Returns
    points written. `start_day` bounds the recompute window (None = all)."""
    q = db.query(PowerPriceDaily.date, PowerPriceDaily.negative_hours).filter(
        PowerPriceDaily.zone == zone
    )
    if start_day:
        q = q.filter(PowerPriceDaily.date >= start_day)
    points = [(day_hour_ts(d, 0), float(n)) for d, n in q.all()]
    if not points:
        return 0
    return upsert_hourly(db, NEGATIVE_HOURS_SERIES, zone, points, unit="h")


def _month_ts(month: str) -> int:
    y, m = month.split("-")
    return int(datetime(int(y), int(m), 1, tzinfo=UTC).timestamp())


def store_capture(db: Session, zone: str, months: int = 3, *, today: date | None = None) -> int:
    """Persist compute_capture's monthly capture price + value factor per fuel.
    Returns points written across all series."""
    out = compute_capture(db, zone, months=months, today=today)
    if not out.get("available"):
        return 0
    written = 0
    for fuel in out.get("fuels", []):
        psr = fuel["psr"]
        if psr not in CAPTURE_FUELS:  # defensive: the engine defines the universe
            continue
        prices = []
        factors = []
        for row in fuel["data"]:
            ts = _month_ts(row["month"])
            if row.get("capture_price") is not None:
                prices.append((ts, row["capture_price"]))
            if row.get("value_factor") is not None:
                factors.append((ts, row["value_factor"]))
        if prices:
            written += upsert_hourly(db, f"capture.{psr}.price", zone, prices, unit="EUR/MWh")
        if factors:
            written += upsert_hourly(db, f"capture.{psr}.factor", zone, factors, unit="ratio")
    return written


# ── day-ahead vs imbalance spread ─────────────────────────────────────────────

SPREAD_SERIES = "spread.da_imbalance"


def store_da_imbalance_spread(db: Session, zone: str, start_ts: int | None = None,
                              end_ts: int | None = None) -> int:
    """imbalance.price − price.dayahead per hour → spread.da_imbalance.

    The European reading of gridstatus' DART spread: what an hour of being OUT
    of balance cost versus having bought it a day ahead. Positive = the
    imbalance settlement was dearer than the auction. Only hours where BOTH
    legs exist are written — a spread with one leg missing is not a spread,
    and GB (no day-ahead auction feed) therefore has none by construction.
    Both legs are EUR for every zone that has both, so no currency mixing.
    """
    from backend.power.hourly_store import read_hourly

    da = dict(read_hourly(db, "price.dayahead", zone, start_ts, end_ts))
    if not da:
        return 0
    points = [
        (ts, imb - da[ts])
        for ts, imb in read_hourly(db, "imbalance.price", zone, start_ts, end_ts)
        if ts in da
    ]
    if not points:
        return 0
    return upsert_hourly(db, SPREAD_SERIES, zone, points, unit="EUR/MWh")
