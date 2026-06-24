"""Oil/maritime vertical detectors that wrap persisted analytics flags.

These read the persisted output of the analytics layer (days-of-supply,
supply-demand balance, freight proxy) and the floating-storage event table.
They complement — they do NOT replace — the legacy in-line maritime checks in
``evaluator.py`` (anchored vessels, flow anomaly, cushing, crack, convergence).
"""

from __future__ import annotations

from backend.models.analytics import (
    DaysOfSupplyHistory,
    FreightProxyHistory,
    SupplyDemandBalance,
)
from backend.models.vessels import FloatingStorageEvent
from backend.signals.detectors.base import DetectorResult, severity_from_count

# Days-of-supply: both extremes are notable for a radar; tight is the urgent one.
_DOS_SEVERITY = {"TIGHT": "warning", "COMFORTABLE": "info"}  # IN_LINE → suppress


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
    """One alert per zone with an elevated count of active floating-storage events."""
    rows = (
        db.query(FloatingStorageEvent.zone)
        .filter(FloatingStorageEvent.status == "active")
        .all()
    )
    counts: dict[str, int] = {}
    for (zone,) in rows:
        counts[zone or "global"] = counts.get(zone or "global", 0) + 1

    results: list[DetectorResult] = []
    for zone, n in counts.items():
        if n < 3:
            continue
        results.append(
            DetectorResult(
                rule="floating_storage",
                zone=zone,
                vertical="oil",
                severity=severity_from_count(n, warn_at=6, crit_at=12),
                title=f"{n} tankers in floating-storage pattern in {zone}",
                detail=f"{n} tankers stationary 7+ days (potential floating storage) in {zone}.",
            )
        )
    return results
