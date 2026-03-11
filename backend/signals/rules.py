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
FLOW_ANOMALY_STD_DEVS = 2.0
FLOW_ANOMALY_PCT_FALLBACK = 0.30  # 30% deviation when <7 days of data
FLOW_ANOMALY_MIN_DAYS = 3  # minimum days for percentage-based fallback
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


def _upsert_alert(db: Session, rule: str, zone: str, severity: str, title: str, detail: str):
    """Create a new alert or update the most recent existing one within the dedup window.

    If multiple duplicates exist (same rule + zone within DEDUP_HOURS), keeps only the
    most recent and deletes the rest to prevent duplicate buildup.
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
        # Delete any older duplicates
        for old in existing[1:]:
            db.delete(old)
        db.commit()
        return

    db.add(Alert(rule=rule, zone=zone, severity=severity, title=title, detail=detail))
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
    """
    Chokepoint Flow Anomaly.

    With 7+ days: trigger when daily tanker count deviates > 2σ from rolling average.
    With 3-6 days: trigger when count deviates > 30% from average (percentage fallback).
    With <3 days: suppress (insufficient data).
    """
    recent = (
        db.query(GeofenceEvent.tanker_count)
        .filter(GeofenceEvent.zone == zone)
        .order_by(GeofenceEvent.date.desc())
        .limit(7)
        .all()
    )

    n_days = len(recent)
    if n_days < FLOW_ANOMALY_MIN_DAYS:
        return

    counts = [r.tanker_count for r in recent]
    mean = sum(counts) / len(counts)

    if mean == 0:
        return

    if n_days >= 7:
        # Standard deviation method
        variance = sum((c - mean) ** 2 for c in counts) / (len(counts) - 1)
        std_dev = variance**0.5
        if std_dev == 0:
            return
        z_score = abs(current_count - mean) / std_dev
        if z_score <= FLOW_ANOMALY_STD_DEVS:
            return
        direction = "increase" if current_count > mean else "decrease"
        detail = (
            f"Current: {current_count} tankers, 7-day avg: {mean:.1f}, "
            f"z-score: {z_score:.2f} (threshold: {FLOW_ANOMALY_STD_DEVS})"
        )
    else:
        # Percentage fallback for bootstrap period (3-6 days)
        pct_deviation = abs(current_count - mean) / mean
        if pct_deviation <= FLOW_ANOMALY_PCT_FALLBACK:
            return
        direction = "increase" if current_count > mean else "decrease"
        detail = (
            f"Current: {current_count} tankers, {n_days}-day avg: {mean:.1f}, "
            f"deviation: {pct_deviation * 100:.0f}% (threshold: {FLOW_ANOMALY_PCT_FALLBACK * 100:.0f}%)"
        )

    _upsert_alert(
        db,
        rule="flow_anomaly",
        zone=zone,
        severity="warning",
        title=f"Anomalous {direction} in {zone} transit",
        detail=detail,
    )
    logger.info(f"Alert: flow_anomaly in {zone} ({direction})")


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
