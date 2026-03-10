"""
Signal evaluator — runs all rule checks against current data.

Called periodically by the scheduler to generate alerts.
"""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.database import SessionLocal
from backend.geofences.zones import ZONES
from backend.models.vessels import GeofenceEvent, VesselPosition
from backend.signals.rules import (
    _upsert_alert,
    check_anchored_vessels,
    check_cushing_drawdown,
    check_flow_anomaly,
)

STALE_DAYS = 7  # ignore geofence events older than this

logger = logging.getLogger(__name__)


def _compute_zone_stats(db: Session, zone_name: str) -> dict:
    """Compute current slow-mover count and 7-day average for a zone."""
    # Current slow movers: latest position per MMSI, then count SOG < 0.5
    latest_ids = (
        db.query(func.max(VesselPosition.id))
        .filter(VesselPosition.zone == zone_name)
        .group_by(VesselPosition.mmsi)
        .scalar_subquery()
    )
    positions = db.query(VesselPosition).filter(VesselPosition.id.in_(latest_ids)).all()

    slow_movers = sum(1 for p in positions if p.sog < 0.5)

    # 7-day history: count slow movers from geofence events
    events = (
        db.query(GeofenceEvent.slow_movers)
        .filter(GeofenceEvent.zone == zone_name)
        .order_by(GeofenceEvent.date.desc())
        .limit(7)
        .all()
    )

    if len(events) >= 7:
        avg_slow_7d = sum(e.slow_movers for e in events) / len(events)
    else:
        avg_slow_7d = None  # insufficient history

    return {"count": len(positions), "slow_movers": slow_movers, "avg_slow_7d": avg_slow_7d}


async def evaluate_signals():
    """Run all signal rules against current database state."""
    db = SessionLocal()
    try:
        # 1. Anchored vessels: check each zone for slow-moving tankers
        for zone in ZONES:
            stats = _compute_zone_stats(db, zone["name"])
            if stats["slow_movers"] > 0:
                check_anchored_vessels(
                    db,
                    zone["name"],
                    stats["slow_movers"],
                    stats["count"],
                    stats["avg_slow_7d"],
                )

        # 2. Flow anomaly: check geofence event history per zone
        stale_cutoff = (datetime.now(timezone.utc) - timedelta(days=STALE_DAYS)).strftime("%Y-%m-%d")
        for zone in ZONES:
            latest_event = (
                db.query(GeofenceEvent)
                .filter(GeofenceEvent.zone == zone["name"], GeofenceEvent.date >= stale_cutoff)
                .order_by(GeofenceEvent.date.desc())
                .first()
            )
            if latest_event:
                check_flow_anomaly(db, zone["name"], latest_event.tanker_count)

        # 3. Cushing drawdown
        check_cushing_drawdown(db)

        # 4. Crack spread alert
        try:
            _check_crack_spread(db)
        except Exception as e:
            logger.warning(f"Crack spread alert check failed: {e}")

        # 5. Rerouting alert
        try:
            _check_rerouting(db)
        except Exception as e:
            logger.warning(f"Rerouting alert check failed: {e}")

        # 6. Convergence alert — multi-signal
        try:
            _check_convergence(db)
        except Exception as e:
            logger.warning(f"Convergence alert check failed: {e}")

        logger.info("Signal evaluation complete")
    except Exception as e:
        logger.error(f"Signal evaluation failed: {e}")
    finally:
        db.close()


def _check_crack_spread(db: Session):
    """Alert when crack spread is extreme (>90th or <10th percentile)."""
    import asyncio

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Already in async context — skip (will run next cycle)
            return
    except RuntimeError:
        pass

    # Use cached data only — don't trigger a fetch
    from backend.signals.crack_spread import _crack_cache

    if not _crack_cache:
        return

    spread = _crack_cache.get("spread_321")
    pct = _crack_cache.get("percentile_1y")
    ts = _crack_cache.get("timestamp", "unknown")

    if spread is None or pct is None:
        return

    if pct >= 90:
        _upsert_alert(
            db,
            rule="crack_spread_high",
            zone="global",
            severity="warning",
            title=f"Crack spread at ${spread}/bbl — {pct}th percentile",
            detail=(
                f"3:2:1 crack spread ${spread}/bbl is at the {pct}th percentile "
                f"vs 1-year range. High spread = strong refining margins = bullish crude demand. "
                f"(data: {ts})"
            ),
        )
    elif pct <= 10:
        _upsert_alert(
            db,
            rule="crack_spread_low",
            zone="global",
            severity="warning",
            title=f"Crack spread at ${spread}/bbl — {pct}th percentile",
            detail=(
                f"3:2:1 crack spread ${spread}/bbl is at the {pct}th percentile "
                f"vs 1-year range. Low spread = weak refining margins = bearish crude demand. "
                f"(data: {ts})"
            ),
        )


def _check_rerouting(db: Session):
    """Alert when rerouting index exceeds thresholds."""
    from backend.signals.tonnage_proxy import compute_rerouting_index

    data = compute_rerouting_index(days=365)
    if not data.get("available"):
        return

    current = data.get("current", {})
    state = current.get("state")
    ratio_pct = current.get("ratio_pct")

    if state == "high_rerouting" and ratio_pct is not None:
        _upsert_alert(
            db,
            rule="rerouting_high",
            zone="global",
            severity="warning",
            title=f"Rerouting index at {ratio_pct:.0f}% — HIGH",
            detail=(
                f"Cape share at {ratio_pct:.0f}%. "
                f"High rerouting typically indicates Suez/Red Sea disruption. "
                f"Tanker demand increases with longer voyages via Cape. "
                f"(data: {datetime.now(timezone.utc).isoformat()})"
            ),
        )


def _check_convergence(db: Session):
    """
    Convergence alert — fires when 3+ signals point in the same direction.

    Each data source includes its timestamp so traders can assess freshness.
    """
    signals = []
    now = datetime.now(timezone.utc)

    # 1. Chokepoint anomaly active?
    from backend.signals.historical_lookup import find_anomalies

    for cp in ["hormuz", "malacca", "suez"]:
        try:
            result = find_anomalies(cp, threshold_pct=40.0)
            drop = result.get("current", {}).get("drop_pct", 0)
            if drop < -35:
                signals.append(
                    f"{cp.capitalize()} {drop:+.0f}% transit (data: {result.get('current', {}).get('date', 'unknown')})"
                )
        except Exception:
            logger.debug("Convergence check: %s anomaly lookup failed", cp)

    # 2. Market structure — backwardation deepening?
    from backend.signals.crack_spread import _crack_cache

    try:
        # Use a simple sync check of cache state
        from backend.routes.briefing import _briefing_cache

        if _briefing_cache:
            mkt = _briefing_cache.get("market_structure", {})
            if mkt and mkt.get("summary") == "backwardation":
                wti_spread = mkt.get("curves", {}).get("WTI", {}).get("spread_pct")
                if wti_spread is not None and wti_spread < -2:
                    signals.append(f"Backwardation {wti_spread:+.1f}% WTI (data: {mkt.get('timestamp', 'unknown')})")
    except Exception:
        logger.debug("Convergence check: market structure lookup failed")

    # 3. Rerouting elevated?
    from backend.signals.tonnage_proxy import compute_rerouting_index

    try:
        rdata = compute_rerouting_index(days=365)
        if rdata.get("available"):
            rc = rdata["current"]
            if rc.get("state") in ("elevated", "high_rerouting"):
                signals.append(
                    f"Rerouting {rc.get('ratio_pct', 0):.0f}% Cape share ({rc['state'].upper()}) "
                    f"(data: {now.isoformat()})"
                )
    except Exception:
        logger.debug("Convergence check: rerouting index lookup failed")

    # 4. Crack spread rising?
    if _crack_cache:
        pct = _crack_cache.get("percentile_1y")
        if pct is not None and pct >= 75:
            signals.append(
                f"Crack spread ${_crack_cache.get('spread_321')}/bbl ({pct}th pct) "
                f"(data: {_crack_cache.get('timestamp', 'unknown')})"
            )

    # Trigger convergence if 3+ signals
    if len(signals) >= 3:
        detail_lines = "\n".join(f"• {s}" for s in signals)
        _upsert_alert(
            db,
            rule="convergence",
            zone="global",
            severity="critical",
            title=f"CONVERGENCE: {len(signals)} physical indicators align",
            detail=(
                f"Multiple physical indicators point to supply tightness:\n"
                f"{detail_lines}\n"
                f"All signals are deterministic rule-based checks, not predictions."
            ),
        )
