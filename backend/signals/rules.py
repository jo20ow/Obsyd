"""
Heuristic signal rules for alert generation.

All alerts are rule-based, transparent, and traceable.
No ML/black-box models in the MVP.
"""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session
from sqlalchemy import func

from backend.models.alerts import Alert
from backend.models.vessels import GeofenceEvent
from backend.models.prices import EIAPrice

logger = logging.getLogger(__name__)

# Thresholds
FLOATING_STORAGE_MIN_SLOW_MOVERS = 10  # minimum when < 7 days of data
FLOATING_STORAGE_SPIKE_PCT = 0.5  # 50% above 7-day average
FLOW_ANOMALY_STD_DEVS = 2.0
CUSHING_DRAWDOWN_THRESHOLD = -3000  # thousand barrels (= -3M barrels)

DEDUP_HOURS = 6  # suppress duplicate alerts within this window


def _recent_alert_exists(db: Session, rule: str, zone: str) -> Alert | None:
    """Check if an identical alert (same rule + zone) exists within DEDUP_HOURS."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=DEDUP_HOURS)
    return (
        db.query(Alert)
        .filter(Alert.rule == rule, Alert.zone == zone, Alert.created_at > cutoff)
        .first()
    )


def _upsert_alert(db: Session, rule: str, zone: str, severity: str, title: str, detail: str):
    """Create a new alert or update the timestamp of an existing one within the dedup window."""
    existing = _recent_alert_exists(db, rule, zone)
    if existing:
        existing.created_at = datetime.now(timezone.utc)
        existing.detail = detail
        db.commit()
        return

    db.add(Alert(rule=rule, zone=zone, severity=severity, title=title, detail=detail))
    db.commit()


def check_floating_storage(db: Session, zone: str, slow_movers: int, avg_slow_7d: float | None):
    """
    Floating Storage / Queue Detection.

    Trigger: Number of slow tankers (SOG < 0.5 kn) exceeds the 7-day average
    by more than 50%. If < 7 days of data, use a fixed minimum of 10 tankers.
    """
    if avg_slow_7d is not None:
        threshold = avg_slow_7d * (1 + FLOATING_STORAGE_SPIKE_PCT)
        if slow_movers <= threshold:
            return
        detail = (
            f"{slow_movers} tanker(s) with SOG < 0.5 kn, "
            f"7-day avg: {avg_slow_7d:.1f}, threshold: {threshold:.0f} (+50%)"
        )
    else:
        if slow_movers < FLOATING_STORAGE_MIN_SLOW_MOVERS:
            return
        detail = (
            f"{slow_movers} tanker(s) with SOG < 0.5 kn "
            f"(min threshold: {FLOATING_STORAGE_MIN_SLOW_MOVERS}, insufficient history for 7-day avg)"
        )

    _upsert_alert(
        db,
        rule="floating_storage",
        zone=zone,
        severity="warning",
        title=f"Possible floating storage in {zone}",
        detail=detail,
    )
    logger.info(f"Alert: floating_storage in {zone}")


def check_flow_anomaly(db: Session, zone: str, current_count: int):
    """
    Chokepoint Flow Anomaly.

    Trigger: Daily tanker count deviates > 2 std deviations from 7-day rolling average.
    """
    recent = (
        db.query(GeofenceEvent.tanker_count)
        .filter(GeofenceEvent.zone == zone)
        .order_by(GeofenceEvent.date.desc())
        .limit(7)
        .all()
    )

    if len(recent) < 7:
        return

    counts = [r.tanker_count for r in recent]
    mean = sum(counts) / len(counts)
    variance = sum((c - mean) ** 2 for c in counts) / len(counts)
    std_dev = variance**0.5

    if std_dev == 0:
        return

    z_score = abs(current_count - mean) / std_dev
    if z_score > FLOW_ANOMALY_STD_DEVS:
        direction = "increase" if current_count > mean else "decrease"
        _upsert_alert(
            db,
            rule="flow_anomaly",
            zone=zone,
            severity="warning",
            title=f"Anomalous {direction} in {zone} transit",
            detail=(
                f"Current: {current_count} tankers, 7-day avg: {mean:.1f}, "
                f"z-score: {z_score:.2f} (threshold: {FLOW_ANOMALY_STD_DEVS})"
            ),
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
            db.query(GeofenceEvent)
            .filter(GeofenceEvent.zone == "houston")
            .order_by(GeofenceEvent.date.desc())
            .first()
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
