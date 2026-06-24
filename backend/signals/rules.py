"""
Heuristic signal rules for alert generation.

All alerts are rule-based, transparent, and traceable.
No ML/black-box models in the MVP.
"""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from backend.models.alerts import Alert
from backend.models.prices import EIAPrice
from backend.models.vessels import GeofenceEvent

logger = logging.getLogger(__name__)

# Thresholds
FLOW_ANOMALY_STD_DEVS = 2.0       # z-score threshold vs the zone's own trailing baseline
FLOW_BASELINE_DAYS = 30           # trailing window (longer than the old 7d → stabler "normal")
FLOW_MIN_HISTORY = 16             # enough history for a trustworthy baseline + onset check
CUSHING_DRAWDOWN_THRESHOLD = -3000  # thousand barrels (= -3M barrels)

# Regional anchor baselines — zone-specific thresholds for anchored vessel alerts.
# normal_anchored_pct: typical % of tankers anchored in this zone (SOG < 0.5 kn)
# anomaly_threshold_pct: trigger alert above this % anchored
# Zones not listed use a generic 50% above 7-day average.
ZONE_ANCHOR_BASELINES = {
    "malacca": {"normal_pct": 85, "threshold_pct": 95},
    "houston": {"normal_pct": 60, "threshold_pct": 80},
    "hormuz": {"normal_pct": 40, "threshold_pct": 70},
    "cape": {"normal_pct": 20, "threshold_pct": 50},
    "suez": {"normal_pct": 30, "threshold_pct": 60},
    "panama": {"normal_pct": 50, "threshold_pct": 75},
}

DEDUP_HOURS = 24  # suppress duplicate alerts within this window


def _upsert_alert(
    db: Session,
    rule: str,
    zone: str,
    severity: str,
    title: str,
    detail: str,
    vertical: str = "oil",
):
    """Create a new alert or update the most recent existing one within the dedup window.

    If multiple duplicates exist (same rule + zone within DEDUP_HOURS), keeps only the
    most recent and deletes the rest to prevent duplicate buildup.

    `vertical` tags the alert for the cross-vertical radar feed and defaults to
    "oil" so the legacy maritime call sites stay correct without changes.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=DEDUP_HOURS)
    existing = (
        db.query(Alert)
        .filter(Alert.rule == rule, Alert.zone == zone, Alert.created_at > cutoff)
        .order_by(Alert.created_at.desc())
        .all()
    )

    if existing:
        # Update the most recent one
        latest = existing[0]
        latest.created_at = datetime.now(timezone.utc)
        latest.title = title
        latest.detail = detail
        latest.severity = severity
        latest.vertical = vertical
        # Delete any older duplicates
        for old in existing[1:]:
            db.delete(old)
        db.commit()
        return

    db.add(Alert(rule=rule, zone=zone, severity=severity, title=title, detail=detail, vertical=vertical))
    db.commit()


def check_anchored_vessels(db: Session, zone: str, slow_movers: int, total_tankers: int, avg_slow_7d: float | None):
    """
    Anchored Vessel Detection with regional baselines.

    Uses zone-specific thresholds (e.g. Malacca: 95% anchored is normal due to
    anchorage waiting areas, while Cape: >50% anchored is unusual).
    Falls back to 50% above 7-day average for zones without baselines.
    """
    baseline = ZONE_ANCHOR_BASELINES.get(zone)

    if baseline and total_tankers > 0:
        # Zone-specific: compare anchored % against threshold
        anchored_pct = (slow_movers / total_tankers) * 100
        if anchored_pct <= baseline["threshold_pct"]:
            return
        detail = (
            f"{slow_movers}/{total_tankers} tankers anchored ({anchored_pct:.0f}%), "
            f"zone baseline: {baseline['normal_pct']}%, threshold: {baseline['threshold_pct']}%"
        )
    elif avg_slow_7d is not None:
        # Fallback: 50% above 7-day average
        threshold = avg_slow_7d * 1.5
        if slow_movers <= threshold:
            return
        detail = (
            f"{slow_movers} tanker(s) anchored (SOG < 0.5 kn), "
            f"7-day avg: {avg_slow_7d:.1f}, threshold: {threshold:.0f} (+50%)"
        )
    else:
        return

    _upsert_alert(
        db,
        rule="anchored_vessels",
        zone=zone,
        severity="info",
        title=f"{slow_movers} tankers anchored in {zone} (SOG < 0.5 kn)",
        detail=detail,
    )
    logger.info(f"Alert: anchored_vessels in {zone}")


def check_flow_anomaly(db: Session, zone: str, current_count: int):
    """Chokepoint Flow Anomaly — baseline-aware + onset-only.

    Fires when the latest daily tanker count deviates > FLOW_ANOMALY_STD_DEVS from the zone's
    OWN trailing baseline (FLOW_BASELINE_DAYS, longer/stabler than the old 7d), and ONLY on the
    onset: if the prior day was already anomalous, it's persistence, not a new event → suppress.
    """
    # Lazy import avoids a circular import (detectors package imports _upsert_alert from here).
    from backend.signals.detectors.base import trailing_zscore

    rows = (
        db.query(GeofenceEvent.tanker_count)
        .filter(GeofenceEvent.zone == zone)
        .order_by(GeofenceEvent.date.desc())
        .limit(FLOW_BASELINE_DAYS + 2)
        .all()
    )
    counts = [r.tanker_count for r in rows]  # newest first; counts[0] is the current day
    if len(counts) < FLOW_MIN_HISTORY:
        return  # not enough history for a trustworthy baseline yet

    today = trailing_zscore(counts[0], counts[1 : 1 + FLOW_BASELINE_DAYS])
    if today is None or abs(today[0]) < FLOW_ANOMALY_STD_DEVS:
        return
    # Onset: suppress if the prior day was already anomalous (sustained deviation, not new).
    yest = trailing_zscore(counts[1], counts[2 : 2 + FLOW_BASELINE_DAYS])
    if yest is not None and abs(yest[0]) >= FLOW_ANOMALY_STD_DEVS:
        return

    z, mean, _, n = today
    direction = "increase" if counts[0] > mean else "decrease"
    _upsert_alert(
        db,
        rule="flow_anomaly",
        zone=zone,
        severity="warning",
        title=f"Anomalous {direction} in {zone} transit ({z:+.1f}σ)",
        detail=(
            f"Current {counts[0]} tankers vs ~{mean:.0f} normal over {n}d "
            f"(z {z:+.2f}, threshold ±{FLOW_ANOMALY_STD_DEVS}σ)."
        ),
    )
    logger.info(f"Alert: flow_anomaly in {zone} ({direction}, z={z:+.2f})")


def check_cushing_drawdown(db: Session):
    """
    Cushing-EIA Divergence.

    Trigger: Cushing drawdown > 3M barrels in one week + elevated Houston tanker activity.
    """
    cushing = (
        db.query(EIAPrice)
        .filter(EIAPrice.series_id == "PET.WCSSTUS1.W")
        .order_by(EIAPrice.period.desc())
        .limit(2)
        .all()
    )

    if len(cushing) < 2:
        return

    week_change = cushing[0].value - cushing[1].value

    if week_change < CUSHING_DRAWDOWN_THRESHOLD:
        houston = (
            db.query(GeofenceEvent).filter(GeofenceEvent.zone == "houston").order_by(GeofenceEvent.date.desc()).first()
        )

        houston_detail = ""
        if houston:
            houston_detail = f" Houston tanker count: {houston.tanker_count}."

        _upsert_alert(
            db,
            rule="cushing_drawdown",
            zone="cushing",
            severity="critical",
            title="Large Cushing crude oil drawdown",
            detail=(
                f"Weekly change: {week_change:+,.0f} thousand barrels "
                f"({cushing[1].period} -> {cushing[0].period}).{houston_detail}"
            ),
        )
        logger.info("Alert: cushing_drawdown")
