"""Analytics endpoints — derived intelligence from existing data."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Query

from backend.analytics.market_report import get_market_report
from backend.database import SessionLocal
from backend.models.analytics import (
    DisruptionScoreHistory,
    EIAPredictionHistory,
    TonneMilesHistory,
)

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


@router.get("/tonne-miles")
async def get_tonne_miles(
    days: int = Query(90, ge=7, le=365),
):
    """Tonne-Miles Index — transport capacity consumed by routing patterns."""
    db = SessionLocal()
    try:
        rows = db.query(TonneMilesHistory).order_by(TonneMilesHistory.date.desc()).limit(days).all()

        if not rows:
            return {"available": False, "reason": "no data yet"}

        history = [
            {
                "date": r.date,
                "index": r.tonne_miles_index,
                "raw": r.tonne_miles_raw,
                "cape_share": r.cape_share,
                "avg_distance": r.avg_distance,
            }
            for r in reversed(rows)
        ]

        current = rows[0]

        # Calculate 7d and 30d changes
        change_7d = None
        change_30d = None
        if len(rows) >= 7:
            change_7d = round(current.tonne_miles_index - rows[6].tonne_miles_index, 1)
        if len(rows) >= 30:
            change_30d = round(current.tonne_miles_index - rows[29].tonne_miles_index, 1)

        return {
            "available": True,
            "current": {
                "index": current.tonne_miles_index,
                "raw": current.tonne_miles_raw,
                "cape_share": current.cape_share,
                "avg_distance": current.avg_distance,
                "date": current.date,
            },
            "change_7d": change_7d,
            "change_30d": change_30d,
            "history": history,
            "data_points": len(history),
        }
    finally:
        db.close()


@router.get("/disruption-score")
async def get_disruption_score(
    days: int = Query(90, ge=7, le=365),
):
    """Supply Disruption Composite Score — 0-100 index of supply chain stress."""
    db = SessionLocal()
    try:
        rows = (
            db.query(DisruptionScoreHistory)
            .order_by(DisruptionScoreHistory.date.desc(), DisruptionScoreHistory.id.desc())
            .limit(days)
            .all()
        )

        if not rows:
            return {"available": False, "reason": "no data yet"}

        latest = rows[0]

        history = [
            {
                "date": r.date,
                "score": r.composite_score,
            }
            for r in reversed(rows)
        ]

        # Deduplicate by date (keep latest per day)
        seen_dates = set()
        deduped = []
        for r in rows:
            if r.date not in seen_dates:
                seen_dates.add(r.date)
                deduped.append(r)
        history = [{"date": r.date, "score": r.composite_score} for r in reversed(deduped)]

        return {
            "available": True,
            "current": {
                "score": latest.composite_score,
                "date": latest.date,
            },
            "breakdown": {
                "hormuz_transit": {"score": latest.hormuz_component, "weight": 25, "label": "Hormuz Transit Drop"},
                "cape_rerouting": {"score": latest.cape_component, "weight": 20, "label": "Cape Rerouting"},
                "floating_storage": {"score": latest.storage_component, "weight": 10, "label": "Floating Storage"},
                "crack_spread": {"score": latest.crack_component, "weight": 15, "label": "Crack Spread"},
                "backwardation": {"score": latest.backwardation_component, "weight": 15, "label": "Backwardation"},
                "sentiment": {"score": latest.sentiment_component, "weight": 15, "label": "GDELT Risk"},
            },
            "history": history,
            "data_points": len(history),
        }
    finally:
        db.close()


@router.get("/market-report")
async def market_report_endpoint():
    """Market Intelligence Report — narrative analysis from live signals.

    Pro users get full report. Free users get title + severity + teaser.
    """
    report = await get_market_report()

    if not report.get("available"):
        return {"available": False, "reason": "no data yet"}

    # Full report (no auth gate — teaser logic is frontend-side)
    return report


@router.get("/eia-prediction")
async def get_eia_prediction():
    """EIA Inventory Prediction — AIS-based leading indicator."""
    db = SessionLocal()
    try:
        # Latest prediction
        latest = db.query(EIAPredictionHistory).order_by(EIAPredictionHistory.date.desc()).first()

        if not latest:
            return {"available": False, "reason": "no predictions yet"}

        # Historical hit rate
        all_preds = db.query(EIAPredictionHistory).filter(EIAPredictionHistory.correct.isnot(None)).all()

        total = len(all_preds)
        correct = sum(1 for p in all_preds if p.correct == 1)
        hit_rate = round(correct / total * 100, 0) if total > 0 else None

        # Tanker count vs 30d average
        tanker_change_pct = None
        if latest.tanker_count_30d_avg and latest.tanker_count_30d_avg > 0:
            tanker_change_pct = round(
                (latest.tanker_count - latest.tanker_count_30d_avg) / latest.tanker_count_30d_avg * 100, 1
            )

        # Next EIA release (next Wednesday)
        now = datetime.now(timezone.utc)
        days_until_wed = (2 - now.weekday()) % 7
        if days_until_wed == 0 and now.hour >= 16:
            days_until_wed = 7
        next_eia = (now + timedelta(days=days_until_wed)).strftime("%Y-%m-%d")

        # History
        history = db.query(EIAPredictionHistory).order_by(EIAPredictionHistory.date.desc()).limit(26).all()

        return {
            "available": True,
            "current": {
                "prediction": latest.prediction,
                "date": latest.date,
                "tanker_count": latest.tanker_count,
                "tanker_count_30d_avg": latest.tanker_count_30d_avg,
                "tanker_change_pct": tanker_change_pct,
                "anchored_ratio": latest.anchored_ratio,
                "pearson_r": latest.pearson_r,
                "optimal_lag_days": latest.optimal_lag_days,
            },
            "accuracy": {
                "hit_rate": hit_rate,
                "total_predictions": total,
                "correct_predictions": correct,
                "sufficient_data": total >= 8,
            },
            "next_eia_release": next_eia,
            "history": [
                {
                    "date": p.date,
                    "prediction": p.prediction,
                    "actual_change": p.actual_eia_change,
                    "correct": p.correct,
                    "tanker_count": p.tanker_count,
                }
                for p in history
            ],
        }
    finally:
        db.close()
