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
_DOS_SEVERITY = {"TIGHT": "warning", "COMFORTABLE": "info"}  # IN_LINE → suppress

# Floating storage: compare a zone's CURRENT count to its OWN trailing norm, not a flat
# threshold — a permanent anchorage (Malacca/Singapore, Houston) is structurally high and a
# flat count would cry wolf there. Only an unusual *buildup* vs the zone's own history alerts.
FS_WINDOW_DAYS = 90      # trailing baseline length
FS_MIN_NORMAL = 2.0      # skip zones that normally hold ~0-1 tankers (tiny-n noise)
FS_WARN_Z = 2.0
FS_CRIT_Z = 3.0


def detect_days_of_supply(db) -> list[DetectorResult]:
    row = db.query(DaysOfSupplyHistory).order_by(DaysOfSupplyHistory.date.desc()).first()
    if row is None:
        return []
    severity = _DOS_SEVERITY.get(row.assessment or "")
    if severity is None:
        return []
    dev = row.deviation if row.deviation is not None else 0.0
    days = row.commercial_days if row.commercial_days is not None else 0.0
    return [
        DetectorResult(
            rule="days_of_supply",
            zone="us",
            vertical="oil",
            severity=severity,
            title=f"US crude days-of-supply {row.assessment.lower()}",
            detail=f"{days:.1f} days cover, {dev:+.1f}d vs 5-year seasonal average (as of {row.date}).",
        )
    ]


def detect_supply_demand_divergence(db) -> list[DetectorResult]:
    row = db.query(SupplyDemandBalance).order_by(SupplyDemandBalance.date.desc()).first()
    if row is None or not row.divergence_type:
        return []
    # Only a genuine divergence is anomalous; "CONFIRMED" means the sources agree.
    if "DIVERGENCE" not in row.divergence_type:
        return []
    return [
        DetectorResult(
            rule="supply_demand_divergence",
            zone="us",
            vertical="oil",
            severity="warning",
            title="EIA balance vs AIS divergence",
            detail=(row.divergence_detail or row.divergence_type) + f" (as of {row.date}).",
        )
    ]


def detect_freight_divergence(db) -> list[DetectorResult]:
    row = db.query(FreightProxyHistory).order_by(FreightProxyHistory.date.desc()).first()
    if row is None or not row.divergence_flag:
        return []
    return [
        DetectorResult(
            rule="freight_divergence",
            zone="tanker",
            vertical="oil",
            severity="info",
            title=f"Freight proxy {row.divergence_flag.lower()}",
            detail=f"Tanker-equity freight proxy diverging from rerouting / Brent (as of {row.date}).",
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
