"""Analytics endpoints — derived intelligence from existing data."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query

from backend.analytics.market_report import get_market_report
from backend.auth.dependencies import get_current_user
from backend.database import SessionLocal
from backend.models.analytics import (
    DaysOfSupplyHistory,
    DisruptionScoreHistory,
    EIAPredictionHistory,
    FreightProxyHistory,
    SupplyDemandBalance,
    TonneMilesHistory,
)
from backend.models.subscription import Subscription

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
async def market_report_endpoint(user: dict | None = Depends(get_current_user)):
    """Market Intelligence Report — narrative analysis from live signals.

    Pro users get full report. Free users get catalyst + headlines teaser.
    """
    report = await get_market_report()

    if not report.get("available"):
        return {"available": False, "reason": "no data yet"}

    # Check Pro status (token + DB fallback)
    is_pro = False
    if user:
        if user.get("sub_status") == "pro":
            is_pro = True
        else:
            db = SessionLocal()
            try:
                sub = (
                    db.query(Subscription)
                    .filter(Subscription.email == user["email"], Subscription.status == "active")
                    .first()
                )
                is_pro = sub is not None
            finally:
                db.close()

    if is_pro:
        report["pro_required"] = False
        return report

    # Free tier: catalyst + headlines teaser only
    return {
        "available": True,
        "catalyst": report.get("catalyst", ""),
        "headlines": {k: v[:100] for k, v in report.get("headlines", {}).items()},
        "disruption_score": report.get("disruption_score"),
        "signals_count": report.get("signals_count"),
        "generated_at": report.get("generated_at"),
        "pro_required": True,
    }


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


@router.get("/freight-proxy")
async def get_freight_proxy(days: int = Query(90, ge=7, le=365)):
    """Implied Freight Index — tanker equity proxy for freight rates."""
    db = SessionLocal()
    try:
        rows = db.query(FreightProxyHistory).order_by(FreightProxyHistory.date.desc()).limit(days).all()
        if not rows:
            return {"available": False, "reason": "no data yet"}

        latest = rows[0]
        history = [
            {
                "date": r.date,
                "index": r.proxy_index,
                "fro": r.fro_change,
                "stng": r.stng_change,
                "dht": r.dht_change,
                "insw": r.insw_change,
            }
            for r in reversed(rows)
        ]

        return {
            "available": True,
            "current": {
                "index": latest.proxy_index,
                "date": latest.date,
                "brent_corr_30d": latest.brent_corr_30d,
                "rerouting_corr_30d": latest.rerouting_corr_30d,
                "divergence": latest.divergence_flag,
                "components": {
                    "FRO": latest.fro_change,
                    "STNG": latest.stng_change,
                    "DHT": latest.dht_change,
                    "INSW": latest.insw_change,
                },
            },
            "history": history,
            "data_points": len(history),
        }
    finally:
        db.close()


@router.get("/supply-demand")
async def get_supply_demand():
    """Global Supply-Demand Balance + AIS Divergence."""
    db = SessionLocal()
    try:
        latest = db.query(SupplyDemandBalance).order_by(SupplyDemandBalance.date.desc()).first()
        if not latest:
            return {"available": False, "reason": "no data yet"}

        history = db.query(SupplyDemandBalance).order_by(SupplyDemandBalance.date.desc()).limit(26).all()

        return {
            "available": True,
            "current": {
                "date": latest.date,
                "world_production": latest.world_production,
                "world_consumption": latest.world_consumption,
                "implied_balance": latest.implied_balance,
                "us_imports_eia": latest.us_imports_eia,
                "houston_ais_tankers": latest.houston_ais_tankers,
                "houston_deviation": latest.houston_deviation,
                "divergence_type": latest.divergence_type,
                "divergence_detail": latest.divergence_detail,
            },
            "history": [
                {
                    "date": r.date,
                    "balance": r.implied_balance,
                    "production": r.world_production,
                    "consumption": r.world_consumption,
                }
                for r in reversed(history)
            ],
        }
    finally:
        db.close()


@router.get("/days-of-supply")
async def get_days_of_supply():
    """US Days of Supply — dynamic inventory coverage metric."""
    db = SessionLocal()
    try:
        latest = db.query(DaysOfSupplyHistory).order_by(DaysOfSupplyHistory.date.desc()).first()
        if not latest:
            return {"available": False, "reason": "no data yet"}

        history = db.query(DaysOfSupplyHistory).order_by(DaysOfSupplyHistory.date.desc()).limit(52).all()

        return {
            "available": True,
            "current": {
                "date": latest.date,
                "commercial_days": latest.commercial_days,
                "total_days": latest.total_days,
                "avg_5y_days": latest.avg_5y_days,
                "deviation": latest.deviation,
                "trend_4w": latest.trend_4w,
                "assessment": latest.assessment,
                "commercial_stocks": latest.commercial_stocks,
                "spr_stocks": latest.spr_stocks,
                "product_supplied": latest.product_supplied,
            },
            "history": [
                {
                    "date": r.date,
                    "commercial_days": r.commercial_days,
                    "total_days": r.total_days,
                    "avg_5y_days": r.avg_5y_days,
                }
                for r in reversed(history)
            ],
        }
    finally:
        db.close()
