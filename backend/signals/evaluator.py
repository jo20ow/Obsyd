"""
Signal evaluator — runs the curated anomaly radar against current data.

Called every 5 minutes by the scheduler. REFOCUS 2026-07-03: the European
electricity+gas desk runs only the curated radar detectors
(run_all_detectors → power/gas). The former oil/maritime checks (anchored
vessels, flow anomaly, Cushing drawdown, crack spread, convergence) moved to
the sibling project along with the AIS/oil verticals.
"""

import logging

from backend.database import SessionLocal

logger = logging.getLogger(__name__)


async def evaluate_signals():
    """Run the curated anomaly radar (power/gas detectors) and upsert alerts."""
    db = SessionLocal()
    try:
        from backend.signals.detectors import run_all_detectors

        n = run_all_detectors(db)
        logger.info("Anomaly radar: %d alerts upserted", n)
    except Exception as e:
        logger.error("Signal evaluation failed: %s", e)
    finally:
        db.close()
