"""Tests for the baseline-aware refinery thermal anomaly check (collectors/firms.py).

Refineries run hot continuously, so the check must alert only on activity that is unusual
vs the refinery's own trailing daily norm — not on any hotspot at all.
"""

from datetime import datetime, timedelta, timezone

from backend.collectors.firms import REFINERIES, _check_refinery_anomalies
from backend.models.alerts import Alert
from backend.models.thermal import ThermalHotspot

_REF = next(r for r in REFINERIES if r["area"] == "gulf_coast")  # Baytown TX


def _seed_hotspots(db, counts_by_offset):
    """Seed `count` nearby hotspots on each (today - offset) day for the refinery."""
    anchor = datetime.now(timezone.utc).date()
    for off, c in counts_by_offset.items():
        d = (anchor - timedelta(days=off)).isoformat()
        for i in range(c):
            db.add(ThermalHotspot(
                latitude=_REF["lat"], longitude=_REF["lon"], brightness=320.0,
                confidence="high", area_name=_REF["area"], acq_date=d,
            ))
    db.commit()


def _batch(n):
    return [{"area": _REF["area"], "lat": _REF["lat"], "lon": _REF["lon"], "brightness": 335.0} for _ in range(n)]


def _thermal_alert_count(db):
    return db.query(Alert).filter(Alert.rule == "refinery_thermal").count()


def test_thermal_normal_activity_no_alert(db_session):
    # ~3 nearby hotspots/day baseline; today also ~3 → normal → no alert.
    _seed_hotspots(db_session, {o: 2 + (o % 3) for o in range(1, 31)})  # 2-4/day
    _check_refinery_anomalies(db_session, _batch(3))
    assert _thermal_alert_count(db_session) == 0


def test_thermal_spike_alerts(db_session):
    # Same calm baseline, but today 15 nearby hotspots → unusual → alert.
    _seed_hotspots(db_session, {o: 2 + (o % 3) for o in range(1, 31)})
    _check_refinery_anomalies(db_session, _batch(15))
    assert _thermal_alert_count(db_session) == 1


def test_thermal_single_hotspot_suppressed(db_session):
    # One nearby hotspot is below THERMAL_MIN_NEARBY → never alerts (always-on refinery glow).
    _seed_hotspots(db_session, {o: 3 for o in range(1, 31)})
    _check_refinery_anomalies(db_session, _batch(1))
    assert _thermal_alert_count(db_session) == 0
