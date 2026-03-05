"""
Heuristic signal rules for alert generation.

All alerts are rule-based, transparent, and traceable.
No ML/black-box models in the MVP.
"""

import logging
from datetime import datetime

from sqlalchemy.orm import Session
from sqlalchemy import func

from backend.models.alerts import Alert
from backend.models.vessels import GeofenceEvent
from backend.models.prices import EIAPrice

logger = logging.getLogger(__name__)

# Thresholds
FLOATING_STORAGE_SOG_THRESHOLD = 0.5  # knots
FLOATING_STORAGE_HOURS_THRESHOLD = 48
FLOW_ANOMALY_STD_DEVS = 2.0
CUSHING_DRAWDOWN_THRESHOLD = -3000  # thousand barrels (= -3M barrels)


def check_floating_storage(db: Session, zone: str, slow_movers: int, avg_dwell: float):
    """
    Floating Storage / Queue Detection.

    Trigger: Tankers within geofence with SOG < 0.5 kn for > 48 hours.
    """
    if slow_movers > 0 and avg_dwell > FLOATING_STORAGE_HOURS_THRESHOLD:
        alert = Alert(
            rule="floating_storage",
            zone=zone,
            severity="warning",
            title=f"Possible floating storage in {zone}",
            detail=(
                f"{slow_movers} tanker(s) with SOG < {FLOATING_STORAGE_SOG_THRESHOLD} kn, "
                f"avg dwell time {avg_dwell:.1f}h (threshold: {FLOATING_STORAGE_HOURS_THRESHOLD}h)"
            ),
        )
        db.add(alert)
        db.commit()
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
        alert = Alert(
            rule="flow_anomaly",
            zone=zone,
            severity="warning",
            title=f"Anomalous {direction} in {zone} transit",
            detail=(
                f"Current: {current_count} tankers, 7-day avg: {mean:.1f}, "
                f"z-score: {z_score:.2f} (threshold: {FLOW_ANOMALY_STD_DEVS})"
            ),
        )
        db.add(alert)
        db.commit()
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

        alert = Alert(
            rule="cushing_drawdown",
            zone="cushing",
            severity="critical",
            title="Large Cushing crude oil drawdown",
            detail=(
                f"Weekly change: {week_change:+,.0f} thousand barrels "
                f"({cushing[1].period} -> {cushing[0].period}).{houston_detail}"
            ),
        )
        db.add(alert)
        db.commit()
        logger.info("Alert: cushing_drawdown")
