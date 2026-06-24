"""Oil/maritime vertical detectors that wrap persisted analytics flags.

These read the persisted output of the analytics layer (days-of-supply,
supply-demand balance, freight proxy) and the floating-storage event table.
They complement — they do NOT replace — the legacy in-line maritime checks in
``evaluator.py`` (anchored vessels, flow anomaly, cushing, crack, convergence).
"""

from __future__ import annotations

from datetime import timedelta

from backend.models.analytics import (
    DaysOfSupplyHistory,
    FreightProxyHistory,
    SupplyDemandBalance,
)
from backend.models.vessels import FloatingStorageEvent
from backend.signals.detectors.base import DetectorResult, trailing_zscore

# Days-of-supply: both extremes are notable for a radar; tight is the urgent one.
# These three read CATEGORICAL flags from the analytics layer, so they're sharpened by
# ONSET-detection rather than a z-score: fire on the TRANSITION into the abnormal state, not
# every day it persists. A radar pings on change; the steady state lives on the dashboard panel.


def _latest_two(db, model):
    return db.query(model).order_by(model.date.desc()).limit(2).all()

# Floating storage: compare a zone's CURRENT count to its OWN trailing norm, not a flat
# threshold — a permanent anchorage (Malacca/Singapore, Houston) is structurally high and a
# flat count would cry wolf there. Only an unusual *buildup* vs the zone's own history alerts.
FS_WINDOW_DAYS = 90      # trailing baseline length
FS_MIN_NORMAL = 2.0      # skip zones that normally hold ~0-1 tankers (tiny-n noise)
FS_WARN_Z = 2.0
FS_CRIT_Z = 3.0


def detect_days_of_supply(db) -> list[DetectorResult]:
    """Fire when US crude inventories newly turn TIGHT (onset), not every day they stay tight."""
    rows = _latest_two(db, DaysOfSupplyHistory)
    if not rows or rows[0].assessment != "TIGHT":
        return []
    if len(rows) > 1 and rows[1].assessment == "TIGHT":
        return []  # already tight last reading → persistence, not a new event
    cur = rows[0]
    dev = cur.deviation if cur.deviation is not None else 0.0
    days = cur.commercial_days if cur.commercial_days is not None else 0.0
    return [
        DetectorResult(
            rule="days_of_supply",
            zone="us",
            vertical="oil",
            severity="warning",
            title="US crude days-of-supply turned tight",
            detail=f"{days:.1f} days cover, {dev:+.1f}d vs 5-year seasonal average (as of {cur.date}).",
        )
    ]


def _has_divergence(row) -> bool:
    return bool(row.divergence_type and "DIVERGENCE" in row.divergence_type)


def detect_supply_demand_divergence(db) -> list[DetectorResult]:
    """Fire when EIA-balance vs AIS newly diverges (onset); 'CONFIRMED' = sources agree → no alert."""
    rows = _latest_two(db, SupplyDemandBalance)
    if not rows or not _has_divergence(rows[0]):
        return []
    if len(rows) > 1 and _has_divergence(rows[1]):
        return []  # divergence already present last reading → persistence
    cur = rows[0]
    return [
        DetectorResult(
            rule="supply_demand_divergence",
            zone="us",
            vertical="oil",
            severity="warning",
            title="EIA balance vs AIS newly diverging",
            detail=(cur.divergence_detail or cur.divergence_type) + f" (as of {cur.date}).",
        )
    ]


def detect_freight_divergence(db) -> list[DetectorResult]:
    """Fire when the freight proxy newly diverges or changes regime (onset), not on persistence."""
    rows = _latest_two(db, FreightProxyHistory)
    if not rows or not rows[0].divergence_flag:
        return []
    if len(rows) > 1 and rows[1].divergence_flag == rows[0].divergence_flag:
        return []  # same divergence flag as last reading → persistence
    cur = rows[0]
    return [
        DetectorResult(
            rule="freight_divergence",
            zone="tanker",
            vertical="oil",
            severity="info",
            title=f"Freight proxy {cur.divergence_flag.lower()}",
            detail=f"Tanker-equity freight proxy newly diverging from rerouting / Brent (as of {cur.date}).",
        )
    ]


def detect_floating_storage(db) -> list[DetectorResult]:
    """Alert per zone with an UNUSUAL floating-storage buildup vs that zone's own history.

    "Active on day D" = an event whose [first_seen, last_seen] window covers D — this is
    reconstructable historically (unlike the current-only `status` flag, which also retains
    stale 'active' rows whose last_seen is weeks old). The current count is taken on the latest
    day present in the data and z-scored against the prior FS_WINDOW_DAYS days for that zone.
    """
    rows = db.query(
        FloatingStorageEvent.zone, FloatingStorageEvent.first_seen, FloatingStorageEvent.last_seen
    ).all()
    if not rows:
        return []

    anchor = max(ls for _, _, ls in rows).date()
    by_zone: dict[str, list[tuple]] = {}
    for zone, first_seen, last_seen in rows:
        by_zone.setdefault(zone or "global", []).append((first_seen.date(), last_seen.date()))

    results: list[DetectorResult] = []
    for zone, evs in by_zone.items():
        def active_on(d):
            return sum(1 for fs, ls in evs if fs <= d <= ls)

        current = active_on(anchor)
        baseline = [active_on(anchor - timedelta(days=o)) for o in range(1, FS_WINDOW_DAYS + 1)]
        stat = trailing_zscore(current, baseline)
        if stat is None:
            continue
        z, mean, std, n = stat
        # Only an unusually HIGH buildup is an anomaly, and only in zones that normally hold a
        # meaningful number (skip permanent near-empty zones where 1→3 looks like a big z).
        if mean < FS_MIN_NORMAL or z < FS_WARN_Z:
            continue
        results.append(
            DetectorResult(
                rule="floating_storage",
                zone=zone,
                vertical="oil",
                severity="critical" if z >= FS_CRIT_Z else "warning",
                title=f"Unusual floating-storage buildup in {zone}: {current} tankers ({z:+.1f}σ)",
                detail=(
                    f"{current} tankers in floating-storage pattern vs ~{mean:.0f} normal for {zone} "
                    f"(z {z:+.2f} over {n}d, as of {anchor})."
                ),
            )
        )
    return results


def detect_rerouting(db) -> list[DetectorResult]:
    """Suez→Cape rerouting index above its normal state (consolidates the legacy evaluator check).

    `compute_rerouting_index` is already baseline-aware (current vs 30d/365d) and a pure local
    DB read, so we just surface its state descriptively.
    """
    from backend.signals.tonnage_proxy import compute_rerouting_index

    data = compute_rerouting_index(days=365)
    if not data.get("available"):
        return []
    cur = data.get("current", {})
    severity = cur.get("severity")  # high_rerouting→warning, elevated→info, normal→None
    if not severity:
        return []
    ratio_pct = cur.get("ratio_pct") or 0.0
    base_30d = (cur.get("baseline_30d") or 0.0) * 100
    return [
        DetectorResult(
            rule="rerouting_high",
            zone="global",
            vertical="oil",
            severity=severity,
            title=f"Rerouting index at {ratio_pct:.0f}% — {cur.get('state', '').replace('_', ' ').upper()}",
            detail=(
                f"Cape share {ratio_pct:.0f}% vs ~{base_30d:.0f}% 30d-norm; elevated Cape routing "
                f"typically signals Suez/Red Sea avoidance (longer voyages, higher tanker demand)."
            ),
        )
    ]


def _chokepoint_zone(name: str) -> str:
    skip = {"strait", "of", "the", "canal"}
    words = [w for w in (name or "").lower().split() if w not in skip]
    return words[0] if words else (name or "global").lower()


def detect_chokepoint(db) -> list[DetectorResult]:
    """PortWatch chokepoint transit anomalies (consolidates the live /api/alerts/portwatch route).

    `check_chokepoint_anomalies` is already YoY-baseline-aware with seasonal-low suppression and a
    ±30% threshold — a pure local DB read.
    """
    from backend.signals.portwatch_alerts import check_chokepoint_anomalies

    results: list[DetectorResult] = []
    for a in check_chokepoint_anomalies():
        disruption = f" // {a['disruption_name']}" if a.get("disruption_name") else ""
        results.append(
            DetectorResult(
                rule="chokepoint_anomaly",
                zone=_chokepoint_zone(a.get("chokepoint", "")),
                vertical="oil",
                severity="critical" if a.get("alert_level") == "critical" else "warning",
                title=f"{a['chokepoint']}: {a['anomaly_pct']:+.0f}% {a['direction']}",
                detail=(
                    f"{a['n_total']} vessels vs {a['baseline_avg']} ({a['baseline_type']} baseline)"
                    f"{disruption}."
                ),
            )
        )
    return results
