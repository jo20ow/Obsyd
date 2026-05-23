"""Silent track-record collection for alerts.

For every alert created in the `alerts` table, we capture a price snapshot
(Brent + WTI) at four horizons: T+0, T+1d, T+7d, T+30d. The data accumulates
silently in `alert_outcomes` and is NOT surfaced to users until the sample
size is large enough to be honest about (n >= 30 per rule type).

The point is to eventually have a verifiable claim like "Hormuz-transit
anomalies have preceded Brent moves >2% within 7 days in X% of N cases".
Until that claim is honest, we don't make it.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy.exc import IntegrityError

from backend.database import SessionLocal
from backend.models.alerts import Alert, AlertOutcome
from backend.models.prices import FREDSeries

logger = logging.getLogger(__name__)

# FRED daily series — most reliable free source for Brent / WTI history
_BRENT_SERIES = "DCOILBRENTEU"
_WTI_SERIES = "DCOILWTICO"

HORIZONS = (0, 1, 7, 30)


def _price_on_or_before(db, series_id: str, target_date: str) -> float | None:
    """Return the FRED price for `series_id` on `target_date` or the closest
    earlier business day (FRED skips weekends/holidays). Returns None if no
    price exists yet."""
    row = (
        db.query(FREDSeries)
        .filter(FREDSeries.series_id == series_id)
        .filter(FREDSeries.date <= target_date)
        .order_by(FREDSeries.date.desc())
        .first()
    )
    return float(row.value) if row else None


def _snapshot_for_date(db, target_date: str) -> tuple[float | None, float | None]:
    """Return (brent, wti) prices on or just before target_date (ISO YYYY-MM-DD)."""
    return (
        _price_on_or_before(db, _BRENT_SERIES, target_date),
        _price_on_or_before(db, _WTI_SERIES, target_date),
    )


def _write_outcome(db, alert_id: int, horizon_days: int, brent: float | None, wti: float | None) -> bool:
    """Insert an outcome row. Returns True on insert, False if the unique
    (alert_id, horizon_days) constraint blocks it (already recorded)."""
    outcome = AlertOutcome(
        alert_id=alert_id,
        horizon_days=horizon_days,
        brent_price=brent,
        wti_price=wti,
    )
    db.add(outcome)
    try:
        db.commit()
        return True
    except IntegrityError:
        db.rollback()
        return False


async def snapshot_and_backfill_outcomes() -> dict:
    """Run both phases. Designed for the scheduler.

    Phase 1 — T+0 snapshot for any alerts in the last 24h without a t0 row.
    Phase 2 — Backfill T+1d, T+7d, T+30d for alerts old enough where FRED
              has caught up to the target date.

    Returns counts for observability. Never raises — failures log and continue.
    """
    inserts = {h: 0 for h in HORIZONS}
    now = datetime.utcnow()
    db = SessionLocal()
    try:
        # Phase 1: T+0 for alerts in the last 24h.
        # Bounded window prevents re-processing the entire alerts table on every run.
        recent_cutoff = now - timedelta(hours=24)
        existing_t0 = {
            row[0]
            for row in db.query(AlertOutcome.alert_id)
            .filter(AlertOutcome.horizon_days == 0)
            .all()
        }
        recent_alerts = (
            db.query(Alert)
            .filter(Alert.created_at >= recent_cutoff)
            .all()
        )
        today = now.strftime("%Y-%m-%d")
        for alert in recent_alerts:
            if alert.id in existing_t0:
                continue
            brent, wti = _snapshot_for_date(db, today)
            if _write_outcome(db, alert.id, 0, brent, wti):
                inserts[0] += 1

        # Phase 2: backfill due horizons. We look at alerts in the last 35 days
        # (covers all horizons including 30d with a 5-day buffer) and fill any
        # horizon whose target date is >= today (i.e. data is now available).
        backfill_cutoff = now - timedelta(days=35)
        candidates = (
            db.query(Alert)
            .filter(Alert.created_at >= backfill_cutoff)
            .filter(Alert.created_at < recent_cutoff)
            .all()
        )
        # One query per (alert_id, horizon) would be wasteful — gather what
        # exists in one pass per horizon.
        existing_by_horizon: dict[int, set[int]] = {}
        for h in HORIZONS:
            if h == 0:
                continue
            existing_by_horizon[h] = {
                row[0]
                for row in db.query(AlertOutcome.alert_id)
                .filter(AlertOutcome.horizon_days == h)
                .all()
            }
        for alert in candidates:
            for h in HORIZONS:
                if h == 0:
                    continue
                target = alert.created_at + timedelta(days=h)
                if target > now:
                    continue
                if alert.id in existing_by_horizon[h]:
                    continue
                target_str = target.strftime("%Y-%m-%d")
                brent, wti = _snapshot_for_date(db, target_str)
                # If neither price has caught up to target_str yet, skip — try next run.
                if brent is None and wti is None:
                    continue
                if _write_outcome(db, alert.id, h, brent, wti):
                    inserts[h] += 1

        total = sum(inserts.values())
        if total:
            logger.info(
                "alert_outcomes: inserted t0=%d t1d=%d t7d=%d t30d=%d (total=%d)",
                inserts[0], inserts[1], inserts[7], inserts[30], total,
            )
        return {"inserts": inserts, "total": total}
    except Exception as e:
        logger.error("alert_outcomes: failed: %s", e)
        return {"inserts": inserts, "total": 0, "error": str(e)}
    finally:
        db.close()
