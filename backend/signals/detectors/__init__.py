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

from sqlalchemy.orm import Session

from backend.signals.detectors.gas import detect_gas_balance
from backend.signals.detectors.oil import (
    detect_days_of_supply,
    detect_floating_storage,
    detect_freight_divergence,
    detect_supply_demand_divergence,
)
from backend.signals.detectors.power import detect_negative_prices
from backend.signals.detectors.sentiment import detect_sentiment_risk
from backend.signals.rules import _upsert_alert

logger = logging.getLogger(__name__)

# Phase 1 registry — detectors whose flags are already persisted.
DETECTORS = [
    detect_gas_balance,
    detect_days_of_supply,
    detect_supply_demand_divergence,
    detect_freight_divergence,
    detect_floating_storage,
    detect_negative_prices,
    detect_sentiment_risk,
]


def run_all_detectors(db: Session) -> int:
    """Run every detector and upsert its results. Returns the number of alerts upserted.

    Each detector is isolated: one raising never suppresses the others.
    """
    emitted = 0
    for detector in DETECTORS:
        try:
            for result in detector(db):
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
