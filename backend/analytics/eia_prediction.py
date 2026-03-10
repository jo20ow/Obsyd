"""
EIA Inventory Prediction — AIS-based leading indicator for weekly EIA report.

Uses Houston zone tanker activity to predict US crude inventory builds/draws.
More tankers → more imports → BUILD likely.
Fewer tankers → less imports → DRAW likely.

Scheduled: weekly, Tuesday 12:00 UTC (one day before EIA release).
"""

import logging
import math
from datetime import datetime, timedelta, timezone

from backend.database import SessionLocal
from backend.models.analytics import EIAPredictionHistory
from backend.models.prices import EIAPrice
from backend.models.vessels import GeofenceEvent

logger = logging.getLogger(__name__)

HOUSTON_ZONE = "houston"

# EIA series for crude stock change
CUSHING_SERIES = "PET.WCSSTUS1.W"
IMPORTS_SERIES = "PET.WCRIMUS2.W"


def _get_houston_tanker_stats(db, target_date: datetime) -> dict:
    """Get Houston zone tanker stats for 7 days ending on target_date."""
    end = target_date.strftime("%Y-%m-%d")
    start = (target_date - timedelta(days=7)).strftime("%Y-%m-%d")

    events = (
        db.query(GeofenceEvent)
        .filter(
            GeofenceEvent.zone == HOUSTON_ZONE,
            GeofenceEvent.date >= start,
            GeofenceEvent.date <= end,
        )
        .all()
    )

    if not events:
        return {"count": 0, "anchored_ratio": 0, "days": 0}

    total_tankers = sum(e.tanker_count or 0 for e in events)
    total_slow = sum(e.slow_movers or 0 for e in events)
    n_days = len(events)

    avg_tankers = total_tankers / n_days if n_days > 0 else 0
    avg_anchored_ratio = total_slow / total_tankers if total_tankers > 0 else 0

    return {
        "count": round(avg_tankers, 1),
        "anchored_ratio": round(avg_anchored_ratio, 3),
        "days": n_days,
    }


def _get_houston_30d_baseline(db, target_date: datetime) -> dict:
    """Get 30-day baseline for Houston zone."""
    end = (target_date - timedelta(days=7)).strftime("%Y-%m-%d")
    start = (target_date - timedelta(days=37)).strftime("%Y-%m-%d")

    events = (
        db.query(GeofenceEvent)
        .filter(
            GeofenceEvent.zone == HOUSTON_ZONE,
            GeofenceEvent.date >= start,
            GeofenceEvent.date <= end,
        )
        .all()
    )

    if not events:
        return {"avg_count": 0, "avg_anchored_ratio": 0}

    total_tankers = sum(e.tanker_count or 0 for e in events)
    total_slow = sum(e.slow_movers or 0 for e in events)
    n_days = len(events)

    return {
        "avg_count": total_tankers / n_days if n_days > 0 else 0,
        "avg_anchored_ratio": total_slow / total_tankers if total_tankers > 0 else 0,
    }


def _get_eia_stock_changes(db, weeks: int = 52) -> list[dict]:
    """Get weekly Cushing stock levels and compute week-over-week changes."""
    rows = (
        db.query(EIAPrice)
        .filter(EIAPrice.series_id == CUSHING_SERIES)
        .order_by(EIAPrice.period.desc())
        .limit(weeks + 1)
        .all()
    )

    if len(rows) < 2:
        return []

    rows = list(reversed(rows))
    changes = []
    for i in range(1, len(rows)):
        change = rows[i].value - rows[i - 1].value
        changes.append(
            {
                "date": rows[i].period,
                "value": rows[i].value,
                "change": change,
            }
        )

    return changes


def _pearson_correlation(xs: list[float], ys: list[float]) -> float:
    """Compute Pearson correlation coefficient."""
    n = len(xs)
    if n < 3:
        return 0.0

    mean_x = sum(xs) / n
    mean_y = sum(ys) / n

    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    var_x = sum((x - mean_x) ** 2 for x in xs)
    var_y = sum((y - mean_y) ** 2 for y in ys)

    denom = math.sqrt(var_x * var_y)
    return cov / denom if denom > 0 else 0.0


def _compute_lag_correlation(db) -> tuple[float, int]:
    """Find optimal lag (0-7 days) for Houston tankers → EIA change correlation."""
    eia_changes = _get_eia_stock_changes(db, weeks=52)
    if len(eia_changes) < 8:
        return 0.0, 0

    best_r = 0.0
    best_lag = 0

    for lag in range(8):
        xs = []
        ys = []
        for ec in eia_changes:
            eia_date = datetime.strptime(ec["date"], "%Y-%m-%d")
            tanker_date = eia_date - timedelta(days=lag)
            stats = _get_houston_tanker_stats(db, tanker_date)
            if stats["days"] >= 3:
                xs.append(stats["count"])
                ys.append(ec["change"])

        if len(xs) >= 8:
            r = _pearson_correlation(xs, ys)
            if abs(r) > abs(best_r):
                best_r = r
                best_lag = lag

    return round(best_r, 3), best_lag


async def compute_eia_prediction():
    """Compute EIA inventory prediction. Scheduled weekly, Tuesday 12:00 UTC."""
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        today = now.strftime("%Y-%m-%d")

        # Check if already computed for today
        existing = db.query(EIAPredictionHistory).filter(EIAPredictionHistory.date == today).first()
        if existing:
            logger.info("EIA prediction: already computed for %s", today)
            return

        # 1. Houston tanker stats (last 7 days)
        stats = _get_houston_tanker_stats(db, now)
        if stats["days"] < 3:
            logger.warning("EIA prediction: insufficient Houston data (%d days)", stats["days"])
            return

        # 2. Houston 30d baseline
        baseline = _get_houston_30d_baseline(db, now)

        # 3. Make prediction
        count_above = stats["count"] > baseline["avg_count"] if baseline["avg_count"] > 0 else False
        anchored_above = (
            stats["anchored_ratio"] > baseline["avg_anchored_ratio"] if baseline["avg_anchored_ratio"] > 0 else False
        )

        if count_above and anchored_above:
            prediction = "BUILD"
        elif not count_above:
            prediction = "DRAW"
        else:
            prediction = "NEUTRAL"

        # 4. Lag correlation
        pearson_r, optimal_lag = _compute_lag_correlation(db)

        # 5. Backfill actuals for past predictions
        _backfill_actuals(db)

        # 6. Store prediction
        db.add(
            EIAPredictionHistory(
                date=today,
                prediction=prediction,
                tanker_count=round(stats["count"]),
                tanker_count_30d_avg=round(baseline["avg_count"], 1) if baseline["avg_count"] else None,
                anchored_ratio=stats["anchored_ratio"],
                anchored_ratio_30d_avg=round(baseline["avg_anchored_ratio"], 3)
                if baseline["avg_anchored_ratio"]
                else None,
                pearson_r=pearson_r,
                optimal_lag_days=optimal_lag,
            )
        )
        db.commit()

        logger.info(
            "EIA prediction: %s (tankers=%d vs avg=%.1f, anchored=%.1f%%, r=%.3f lag=%dd)",
            prediction,
            stats["count"],
            baseline["avg_count"],
            stats["anchored_ratio"] * 100,
            pearson_r,
            optimal_lag,
        )
    except Exception as e:
        logger.error("EIA prediction failed: %s", e)
        db.rollback()
    finally:
        db.close()


def _backfill_actuals(db):
    """Fill in actual EIA changes for past predictions."""
    pending = db.query(EIAPredictionHistory).filter(EIAPredictionHistory.actual_eia_change.is_(None)).all()

    eia_changes = _get_eia_stock_changes(db, weeks=52)
    change_by_week: dict[str, float] = {}
    for ec in eia_changes:
        # Map EIA date to the Tuesday before it (prediction date)
        eia_date = datetime.strptime(ec["date"], "%Y-%m-%d")
        # EIA publishes on Wednesday, our prediction is on Tuesday
        # Find the closest Wednesday to this EIA date
        for offset in range(-3, 4):
            check = eia_date + timedelta(days=offset)
            if check.weekday() == 1:  # Tuesday
                change_by_week[check.strftime("%Y-%m-%d")] = ec["change"]
                break

    for pred in pending:
        actual = change_by_week.get(pred.date)
        if actual is not None:
            pred.actual_eia_change = actual
            if pred.prediction == "BUILD":
                pred.correct = 1 if actual > 0 else 0
            elif pred.prediction == "DRAW":
                pred.correct = 1 if actual < 0 else 0
            else:
                pred.correct = 1 if abs(actual) < 1000 else 0  # Neutral = small change
