"""Generation-data coverage guard for renewable-share metrics.

Renewable share = (wind+solar)/load is meaningless when the reported generation is materially
incomplete: the numerator shrinks, the denominator does not, and a naive Dunkelflaute check
cries wolf. So the share is trusted only when the reported generation TOTAL is a plausible
fraction of load — and when it cannot be proven, it is not trusted (fail safe).

WHICH ZONES THIS ACTUALLY CATCHES — measured 2026-07-13, and NOT what it used to be:

    SE4             gen/load 0.36    IT_CENTRO_SUD  0.69    SE3   0.74
    NO1             0.60             IE_SEM         0.72    DK2   0.78

NL used to be the headline example here (~0.49). **It is now 1.01**: the A75 parser was
splitting inBiddingZone (generation) from outBiddingZone (consumption) incorrectly, and fixing
that healed the coverage. The guard is data-driven, so it healed with it — which is the whole
argument for making it data-driven.

A KNOWN LIMITATION, stated rather than hidden: this guard cannot tell an INCOMPLETE FEED from a
genuine NET IMPORTER. SE4 imports most of its power; its domestic generation really is about a
third of its load, and its renewable share is perfectly meaningful. The guard suppresses it
anyway. That is a false negative — the safe direction, and the one this codebase already chose —
but it IS a false negative, and with A09/A25 now ingested (generation + net import ≈ load) there
is a better guard available to whoever wants to write it.
"""

from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.models.energy import PowerGenMix

# Reported generation must cover at least this fraction of load for the renewable
# share to be trusted. Even a heavy net importer's domestic generation stays well
# above this; NL's broken A75 coverage (~0.49) sits clearly below it.
COVERAGE_MIN_RATIO = 0.6

# Per-zone overrides for zones whose A75 completeness differs from the default.
# Empty by default (all zones use COVERAGE_MIN_RATIO); populate as zones are enabled
# and their coverage is characterized. Keeps the guard tunable per bidding zone.
ZONE_COVERAGE_MIN: dict[str, float] = {}


def coverage_min_ratio(zone: str) -> float:
    """The minimum generation/load coverage ratio to trust a zone's renewable share."""
    return ZONE_COVERAGE_MIN.get(zone, COVERAGE_MIN_RATIO)


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
    return total >= coverage_min_ratio(zone) * load_mw


def reliable_days(db: Session) -> set[tuple[str, str]]:
    """{(date, zone)} whose renewable share is trustworthy — the whole record, ONE query.

    `renewable_share_reliable` above answers for a single day, which is right for a detector
    looking at today. An episode engine walks five years of 37 zones, and calling it in a loop
    would be ~67,000 round trips: the "aggregate in SQL, not in Python" trap in its purest form.

    Same rule, same constants, one grouped query. A day with no generation mix at all is absent
    from the set (fail safe: if we cannot prove coverage, we do not trust the share).
    """
    from sqlalchemy import text

    rows = db.execute(text("""
        SELECT g.date, g.zone, SUM(m.gen_mw) AS gen, g.load_mw
          FROM power_grid g
          JOIN power_gen_mix m ON m.date = g.date AND m.zone = g.zone
         WHERE g.load_mw > 0
         GROUP BY g.date, g.zone
    """)).all()

    return {
        (date, zone)
        for date, zone, gen, load in rows
        if gen is not None and gen >= coverage_min_ratio(zone) * load
    }
