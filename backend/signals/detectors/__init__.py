"""Cross-vertical anomaly radar — curated detector registry + runner.

IMPORTANT — do not confuse with the Pro user rule-builder. There are two alert
subsystems in this codebase:

  * ANONYMOUS radar (this package) — curated, always-on detectors that write to
    the anonymous ``Alert`` table and surface on the on-site feed. No owner, no
    Pro gate, no email.
  * Pro RULE-BUILDER (``backend/signals/user_alert_rules.py`` +
    ``backend/notifications/alert_runner.py``, the ALERTS tab) — per-user
    configured rules with their own inbox + email. NOT touched here.

Each detector is a pure ``(db) -> list[DetectorResult]`` (DB reads only, no
network, descriptive not predictive). ``run_all_detectors`` is the only writer:
it runs each detector defensively and upserts results into the ``Alert``
backbone via ``_upsert_alert``.
"""

from __future__ import annotations

import logging
from datetime import date as _date

from sqlalchemy.orm import Session

from backend.signals.detectors.base import is_stale
from backend.signals.detectors.gas import detect_gas_balance
from backend.signals.detectors.power import (
    detect_dunkelflaute,
    detect_episode_rank,
    detect_forced_outages,
    detect_hydro_deviations,
    detect_imbalance_extremes,
    detect_negative_prices,
    detect_price_spikes,
    detect_record_breaks,
)
from backend.signals.rules import _upsert_alert

logger = logging.getLogger(__name__)

# Curated detector registry — REFOCUSED 2026-07-03 to the European electricity desk
# (electrons + their gas fuel). The oil/sentiment detectors (detectors/oil.py,
# detectors/sentiment.py) stay in the tree but are unwired here; they move to the
# sibling project in Phase 2. The radar now surfaces only power/gas anomalies.
# Extended 2026-07-12 with the depth-roadmap data: imbalance peaks, day-ahead
# spikes (both tails), hydro vs seasonal band, fresh all-time records.
DETECTORS = [
    detect_gas_balance,
    detect_negative_prices,
    detect_dunkelflaute,
    detect_forced_outages,
    detect_imbalance_extremes,
    detect_price_spikes,
    detect_hydro_deviations,
    detect_record_breaks,
    detect_episode_rank,
]


# How many days a vertical's latest data may lag wall-clock before a result is
# treated as stale and suppressed. Tuned to each source's publication cadence:
# power day-ahead/grid is ~daily (D+1 auction, ~1d realised lag), gas AGSI ~daily
# with a few days' lag, GDELT sentiment daily, oil analytics ride EIA's weekly
# WPSR. A result whose as_of is unknown (None) is never suppressed here — the
# rerouting/chokepoint detectors carry their own freshness logic upstream.
_MAX_AGE_DAYS = {
    "power": 3,
    "gas": 4,
    "sentiment": 3,
    "oil": 10,
    "metals": 45,
}
_DEFAULT_MAX_AGE_DAYS = 7


def run_all_detectors(db: Session, *, today: _date | None = None) -> int:
    """Run every detector and upsert its results. Returns the number of alerts upserted.

    Each detector is isolated: one raising never suppresses the others. A result
    whose underlying data is stale (older than the vertical's tolerance) is dropped
    so a frozen collector goes quiet instead of surfacing old data as a live anomaly.
    """
    emitted = 0
    for detector in DETECTORS:
        try:
            for result in detector(db):
                max_age = (
                    result.max_age_days
                    if result.max_age_days is not None
                    else _MAX_AGE_DAYS.get(result.vertical, _DEFAULT_MAX_AGE_DAYS)
                )
                if result.as_of is not None and is_stale(result.as_of, max_age, today=today):
                    logger.warning(
                        "suppressing stale %s/%s alert (data as of %s)",
                        result.vertical, result.rule, result.as_of,
                    )
                    continue
                _upsert_alert(
                    db,
                    rule=result.rule,
                    zone=result.zone,
                    severity=result.severity,
                    title=result.title,
                    detail=result.detail,
                    vertical=result.vertical,
                )
                emitted += 1
        except Exception:
            logger.exception("anomaly detector %s failed", getattr(detector, "__name__", detector))
    return emitted
