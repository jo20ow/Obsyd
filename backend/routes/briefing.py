"""
Morning Briefing — aggregated intelligence endpoint.

GET /api/briefing/today
Cached for 1 hour. Collects from all data sources.
"""

import asyncio
import logging
import time
from datetime import datetime, timezone

from fastapi import APIRouter

from backend.database import SessionLocal
from backend.models.vessels import GlobalVesselPosition, GeofenceEvent
from backend.models.fleet import DailyFleetSummary
from backend.models.alerts import Alert
from backend.models.prices import FREDSeries
from backend.models.sentiment import SentimentScore
from backend.signals.historical_lookup import find_anomalies, CHOKEPOINT_NAMES
from backend.providers import yfinance_provider
from backend.signals.market_structure import get_market_structure

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/briefing", tags=["briefing"])

# Cache
_briefing_cache: dict | None = None
_briefing_cache_ts: float = 0.0
_briefing_lock = asyncio.Lock()
CACHE_TTL = 3600  # 1 hour

# Key chokepoints for the briefing (ordered by crude relevance)
# Panama excluded: no crude transit (VLCCs/Suezmax can't pass), only LNG/products
BRIEFING_CHOKEPOINTS = ["hormuz", "malacca", "suez", "cape", "bab_el_mandeb"]


async def _build_briefing() -> dict:
    """Assemble the full morning briefing from all data sources."""
    now = datetime.now(timezone.utc)
    db = SessionLocal()
    try:
        briefing = {
            "date": now.strftime("%Y-%m-%d"),
            "generated_at": now.isoformat(),
            "market_snapshot": await _market_snapshot(db),
            "market_structure": await _market_structure(),
            "anomalies": _chokepoint_anomalies(),
            "fleet_status": _fleet_status(db),
            "alerts_summary": _alerts_summary(db),
            "upcoming": _upcoming_events(now),
        }
        return briefing
    finally:
        db.close()


async def _market_snapshot(db) -> dict:
    """Current commodity prices + macro indicators."""
    snapshot = {}

    # Live commodity prices from yfinance
    try:
        result = await yfinance_provider.get_live_prices()
        prices = result.get("prices", {})
        for key in ("WTI", "BRENT", "NG", "GOLD", "SILVER", "COPPER"):
            if key in prices:
                p = prices[key]
                snapshot[key.lower()] = {
                    "price": p["current"],
                    "change_pct": p["change_pct"],
                    "name": p.get("name", key),
                }
    except Exception as e:
        logger.warning(f"Briefing: yfinance failed: {e}")

    # DXY from FRED
    dxy = db.query(FREDSeries).filter(
        FREDSeries.series_id == "DTWEXBGS"
    ).order_by(FREDSeries.date.desc()).first()
    if dxy:
        snapshot["dxy"] = {"value": dxy.value, "date": dxy.date}

    # Yield curve
    spread = db.query(FREDSeries).filter(
        FREDSeries.series_id == "T10Y2Y"
    ).order_by(FREDSeries.date.desc()).first()
    if spread:
        snapshot["yield_curve"] = {"spread": spread.value, "date": spread.date}

    # Sentiment
    sentiment = db.query(SentimentScore).order_by(
        SentimentScore.created_at.desc()
    ).first()
    if sentiment:
        snapshot["sentiment_score"] = sentiment.risk_score

    return snapshot


async def _market_structure() -> dict | None:
    """Contango/backwardation state for briefing."""
    try:
        return await get_market_structure()
    except Exception as e:
        logger.warning(f"Briefing: market structure failed: {e}")
        return None


def _chokepoint_anomalies() -> list[dict]:
    """Check all key chokepoints for current anomalies."""
    anomalies = []

    for cp in BRIEFING_CHOKEPOINTS:
        try:
            result = find_anomalies(cp, threshold_pct=40.0)
            current = result.get("current", {})
            drop = current.get("drop_pct", 0)

            if drop < -25:
                # Determine severity
                if drop < -50:
                    severity = "critical"
                elif drop < -35:
                    severity = "warning"
                else:
                    severity = "info"

                # Find historical comparisons
                past = result.get("anomalies", [])
                comparisons = []
                for evt in past[-5:]:
                    comp = {
                        "date": evt["start_date"],
                        "drop_pct": evt["max_drop_pct"],
                        "duration_days": evt.get("duration_days", 0),
                    }
                    if "brent_change_7d_pct" in evt:
                        comp["brent_change_7d"] = f"{evt['brent_change_7d_pct']:+.1f}%"
                    if "disruption_context" in evt:
                        comp["context"] = ", ".join(evt["disruption_context"][:2])
                    comparisons.append(comp)

                # Calculate avg Brent impact
                impacts_7d = [
                    e.get("brent_change_7d_pct")
                    for e in past if "brent_change_7d_pct" in e
                ]
                avg_impact = (
                    round(sum(impacts_7d) / len(impacts_7d), 1)
                    if impacts_7d else None
                )

                anomalies.append({
                    "severity": severity,
                    "chokepoint": cp,
                    "title": f"{result['chokepoint']}: {drop:+.1f}% Transit",
                    "current_value": current.get("n_total", 0),
                    "average_30d": current.get("avg_30d", 0),
                    "drop_pct": round(drop, 1),
                    "historical_count": len(past),
                    "avg_brent_impact_7d": avg_impact,
                    "historical_comparisons": comparisons,
                })
        except Exception as e:
            logger.warning(f"Briefing anomaly check for {cp} failed: {e}")

    # Sort by severity
    severity_order = {"critical": 0, "warning": 1, "info": 2}
    anomalies.sort(key=lambda a: severity_order.get(a["severity"], 3))
    return anomalies


def _fleet_status(db) -> dict:
    """Global fleet status from current snapshot + daily history."""
    status = {}

    # Current snapshot
    from sqlalchemy import func
    totals = db.query(
        func.count(GlobalVesselPosition.id).label("total"),
        func.sum(GlobalVesselPosition.is_tanker).label("tankers"),
    ).first()
    if totals:
        status["total_vessels_global"] = totals.total or 0
        status["tankers_global"] = totals.tankers or 0

    # Recent daily summaries for trend
    summaries = db.query(DailyFleetSummary).order_by(
        DailyFleetSummary.date.desc()
    ).limit(7).all()
    if summaries:
        status["daily_trend"] = [
            {
                "date": s.date,
                "tankers": s.tanker_count,
                "anchored": s.anchored_count,
                "total": s.total_vessels,
            }
            for s in reversed(summaries)
        ]

    # Anchored vessel alerts (deduplicated: 1 per zone, most recent)
    recent_alerts = db.query(Alert).filter(
        Alert.rule.in_(["anchored_vessels", "floating_storage"])
    ).order_by(Alert.created_at.desc()).limit(20).all()
    seen_zones = set()
    anchored_alerts = []
    for a in recent_alerts:
        if a.zone not in seen_zones:
            seen_zones.add(a.zone)
            anchored_alerts.append(
                {"zone": a.zone, "title": a.title, "time": a.created_at.isoformat()}
            )
    if anchored_alerts:
        status["anchored_alerts"] = anchored_alerts

    return status


def _alerts_summary(db) -> dict:
    """Summary of recent alerts by type."""
    from sqlalchemy import func
    from datetime import timedelta

    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    counts = db.query(
        Alert.rule, func.count(Alert.id)
    ).filter(
        Alert.created_at > cutoff
    ).group_by(Alert.rule).all()

    return {
        "last_24h": {rule: count for rule, count in counts},
        "total_24h": sum(c for _, c in counts),
    }


def _upcoming_events(now: datetime) -> dict:
    """Next scheduled data releases."""
    events = {}

    # EIA WPSR: Wednesdays 10:30 ET (15:30 UTC)
    weekday = now.weekday()
    if weekday <= 2:  # Mon-Wed
        days_to_wed = 2 - weekday
    else:
        days_to_wed = 9 - weekday
    next_eia = now.replace(hour=15, minute=30, second=0, microsecond=0)
    if days_to_wed > 0 or (days_to_wed == 0 and now.hour >= 16):
        from datetime import timedelta
        next_eia += timedelta(days=days_to_wed if days_to_wed > 0 else 7)
    events["eia_report"] = next_eia.strftime("%A %H:%M UTC")

    # JODI: monthly 15th
    if now.day < 15:
        events["jodi_update"] = f"{now.strftime('%B')} 15"
    else:
        month = now.month + 1
        year = now.year
        if month > 12:
            month = 1
            year += 1
        events["jodi_update"] = f"{datetime(year, month, 15).strftime('%B')} 15"

    return events


@router.get("/today")
async def get_briefing():
    """Get today's morning briefing. Cached for 1 hour."""
    global _briefing_cache, _briefing_cache_ts

    now = time.monotonic()
    if _briefing_cache and (now - _briefing_cache_ts) < CACHE_TTL:
        return _briefing_cache

    async with _briefing_lock:
        # Double-check after acquiring lock
        now = time.monotonic()
        if _briefing_cache and (now - _briefing_cache_ts) < CACHE_TTL:
            return _briefing_cache

        briefing = await _build_briefing()
        _briefing_cache = briefing
        _briefing_cache_ts = now
        return briefing
