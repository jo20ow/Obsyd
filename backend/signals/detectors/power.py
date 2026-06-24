"""Power vertical detector — negative day-ahead price hours per zone.

Negative prices flag renewable oversupply (the grid pays to offload power). The
hour count is persisted per (date, zone) in ``PowerPriceDaily.negative_hours``.
"""

from __future__ import annotations

from backend.models.energy import PowerPriceDaily
from backend.signals.detectors.base import DetectorResult, severity_from_count


def detect_negative_prices(db) -> list[DetectorResult]:
    zones = [z for (z,) in db.query(PowerPriceDaily.zone).distinct().all()]
    results: list[DetectorResult] = []
    for zone in zones:
        row = (
            db.query(PowerPriceDaily)
            .filter(PowerPriceDaily.zone == zone)
            .order_by(PowerPriceDaily.date.desc())
            .first()
        )
        if row is None or row.negative_hours <= 0:
            continue
        results.append(
            DetectorResult(
                rule="negative_prices",
                zone=zone,
                vertical="power",
                severity=severity_from_count(row.negative_hours, warn_at=6, crit_at=12),
                title=f"{zone}: {row.negative_hours}h of negative day-ahead prices",
                detail=(
                    f"{row.negative_hours} hour(s) below 0 EUR/MWh on {row.date} "
                    f"(renewable oversupply / inflexible generation)."
                ),
            )
        )
    return results
