"""Power vertical detector — negative day-ahead price hours per zone.

Negative prices flag renewable oversupply (the grid pays to offload power). The
hour count is persisted per (date, zone) in ``PowerPriceDaily.negative_hours``.
"""

from __future__ import annotations

from backend.models.energy import PowerPriceDaily
from backend.signals.detectors.base import DetectorResult, trailing_zscore

# Negative day-ahead hours happen routinely in solar/wind-heavy zones — so flag only when
# TODAY is unusually high vs the zone's own recent norm (relative), not on a flat hour count.
NP_WINDOW = 45           # trailing days of negative-hour history per zone
NP_MIN_HOURS = 3         # ignore one or two negative hours (normal noise)
NP_WARN_Z = 2.0
NP_CRIT_Z = 3.0


def detect_negative_prices(db) -> list[DetectorResult]:
    zones = [z for (z,) in db.query(PowerPriceDaily.zone).distinct().all()]
    results: list[DetectorResult] = []
    for zone in zones:
        rows = (
            db.query(PowerPriceDaily)
            .filter(PowerPriceDaily.zone == zone)
            .order_by(PowerPriceDaily.date.desc())
            .limit(NP_WINDOW + 1)
            .all()
        )
        if not rows:
            continue
        row = rows[0]
        current = row.negative_hours
        if current < NP_MIN_HOURS:
            continue
        baseline = [r.negative_hours for r in rows[1:]]
        stat = trailing_zscore(current, baseline)
        if stat is None:
            continue  # too little history → no trustworthy "unusual" judgement yet
        z, mean, _, n = stat
        if z < NP_WARN_Z:
            continue
        results.append(
            DetectorResult(
                rule="negative_prices",
                zone=zone,
                vertical="power",
                severity="critical" if z >= NP_CRIT_Z else "warning",
                title=f"{zone}: unusually many negative-price hours ({current}h, {z:+.1f}σ)",
                detail=(
                    f"{current}h below 0 EUR/MWh on {row.date} vs ~{mean:.0f}h normal for {zone} "
                    f"(z {z:+.2f}; renewable oversupply / inflexible generation)."
                ),
            )
        )
    return results
