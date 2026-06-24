"""Gas vertical detector — EU gas balance residual WATCH/SIGNAL flags.

This is the gold-standard pattern: the flag already carries the level plus a
dominant-mover attribution (e.g. "SIGNAL:supply↑"), computed and persisted in
``backend/gas/balance.py``. We just surface it descriptively.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from backend.models.gas import GasBalance
from backend.signals.detectors.base import DetectorResult

# flag level → severity. Anything else ("OK"/None) emits no alert.
_LEVEL_SEVERITY = {"SIGNAL": "critical", "WATCH": "warning"}


def detect_gas_balance(db: Session) -> list[DetectorResult]:
    # Use the LATEST day's state, not the most recent historically-flagged day —
    # otherwise an old WATCH would resurface after conditions normalised.
    row = db.query(GasBalance).order_by(GasBalance.date.desc()).first()
    if row is None or not row.flag:
        return []

    # flag is "LEVEL" or "LEVEL:mover" (e.g. "SIGNAL:supply↑").
    level, _, mover = row.flag.partition(":")
    severity = _LEVEL_SEVERITY.get(level)
    if severity is None:
        return []

    z = row.z_score if row.z_score is not None else 0.0
    resid = row.residual_7d if row.residual_7d is not None else 0.0
    mover_txt = f", dominant mover {mover}" if mover else ""
    return [
        DetectorResult(
            rule="gas_balance",
            zone="EU",
            vertical="gas",
            severity=severity,
            title=f"EU gas balance {level.lower()}: residual {z:+.1f}σ vs 90d",
            detail=(
                f"7d residual {resid:+.0f} GWh/d, z-score {z:+.2f} vs trailing 90 days"
                f"{mover_txt} (as of {row.date})."
            ),
        )
    ]
