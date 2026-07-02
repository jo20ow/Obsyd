"""Generation-data coverage guard for renewable-share metrics.

ENTSO-E A75 (actual generation per production type) is materially incomplete for
some bidding zones — notably NL, where reported generation covers only ~half of
load and wind+solar look artificially tiny. Renewable share = (wind+solar)/load
is then meaningless, and a naive Dunkelflaute check (share < 15%) cries wolf.

The share is only trustworthy when the reported generation *total* is a plausible
fraction of load. We treat it as unreliable when reported generation covers less
than ``COVERAGE_MIN_RATIO`` of load for that day, or when there is no generation-mix
data at all to validate against (fail safe: if we cannot prove coverage, we do not
trust the share). This is data-driven, so it auto-heals if ENTSO-E backfills a zone.
"""

from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.models.energy import PowerGenMix

# Reported generation must cover at least this fraction of load for the renewable
# share to be trusted. Even a heavy net importer's domestic generation stays well
# above this; NL's broken A75 coverage (~0.49) sits clearly below it.
COVERAGE_MIN_RATIO = 0.6


def generation_total_mw(db: Session, date: str, zone: str) -> float | None:
    """Sum of reported A75 generation (MW) for (date, zone), or None if none present."""
    total = (
        db.query(func.sum(PowerGenMix.gen_mw))
        .filter(PowerGenMix.date == date, PowerGenMix.zone == zone)
        .scalar()
    )
    return float(total) if total is not None else None


def renewable_share_reliable(db: Session, date: str, zone: str, load_mw: float | None) -> bool:
    """True if generation coverage is high enough to trust the renewable share.

    False when load is missing/zero, when no generation mix is available, or when
    reported generation covers < COVERAGE_MIN_RATIO of load.
    """
    if not load_mw or load_mw <= 0:
        return False
    total = generation_total_mw(db, date, zone)
    if total is None:
        return False
    return total >= COVERAGE_MIN_RATIO * load_mw
