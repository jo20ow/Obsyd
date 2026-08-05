"""
Smart Retention — tiered cleanup for vessel_positions (+ housekeeping deletes).

- 0–7 days: keep all raw data
- 7–30 days: thin to one position per MMSI per hour
- >30 days: delete (geofence_events has daily aggregates)

Also prunes anomaly alerts (>45 days) and the Honest-Record arrival log
(ingest_arrival, >INGEST_ARRIVAL_RETENTION_DAYS). The REVISION ledger
(power_revision) is deliberately never pruned: the ledger is the product.

Runs daily at 04:00 UTC via scheduler.
"""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from backend.database import SessionLocal

logger = logging.getLogger(__name__)

# ingest_arrival is fetch-cadence EVIDENCE (one row per collector batch,
# ~10^4 rows/day across 37 zones), not history — 90 days is three times the
# widest freshness window on the desk, plenty for any cadence question.
INGEST_ARRIVAL_RETENTION_DAYS = 90


async def run_retention():
    """Execute tiered retention cleanup on vessel_positions."""
    db = SessionLocal()
    try:
        # Phase 1: Thin 7–30 day old data to hourly snapshots
        r1 = db.execute(text("""
            DELETE FROM vessel_positions
            WHERE timestamp < datetime('now', '-7 days')
              AND timestamp >= datetime('now', '-30 days')
              AND id NOT IN (
                SELECT MIN(id) FROM vessel_positions
                WHERE timestamp < datetime('now', '-7 days')
                  AND timestamp >= datetime('now', '-30 days')
                GROUP BY mmsi, zone, strftime('%Y-%m-%d %H', timestamp)
              )
        """))
        thinned = r1.rowcount

        # Phase 2: Delete everything older than 30 days
        r2 = db.execute(text("""
            DELETE FROM vessel_positions
            WHERE timestamp < datetime('now', '-30 days')
        """))
        deleted = r2.rowcount

        # Phase 3: Prune old anomaly alerts so the table stays bounded. 45 days is past the
        # 30-day AlertOutcome research horizon, so outcome capture is unaffected. Drop the
        # linked outcomes first (FK), then the alerts. (The radar feed itself only shows the
        # last ~48h via /api/alerts; this is pure housekeeping for DB size.)
        db.execute(text("""
            DELETE FROM alert_outcomes
            WHERE alert_id IN (SELECT id FROM alerts WHERE created_at < datetime('now', '-45 days'))
        """))
        r3 = db.execute(text("DELETE FROM alerts WHERE created_at < datetime('now', '-45 days')"))
        pruned_alerts = r3.rowcount

        # Phase 4: Honest-Record arrival log. observed_at is epoch seconds UTC
        # (not a datetime string), so the cutoff is computed here, not in SQL.
        # power_revision is NOT touched — see the module docstring.
        cutoff = int(
            (datetime.now(timezone.utc) - timedelta(days=INGEST_ARRIVAL_RETENTION_DAYS)).timestamp()
        )
        r4 = db.execute(
            text("DELETE FROM ingest_arrival WHERE observed_at < :cutoff"), {"cutoff": cutoff}
        )
        pruned_arrivals = r4.rowcount

        db.commit()
        logger.info(
            f"Retention: thinned {thinned} rows (7-30d), deleted {deleted} positions (>30d), "
            f"pruned {pruned_alerts} alerts (>45d), "
            f"pruned {pruned_arrivals} ingest arrivals (>{INGEST_ARRIVAL_RETENTION_DAYS}d)"
        )

    except Exception as e:
        db.rollback()
        logger.error(f"Retention cleanup failed: {e}")
    finally:
        db.close()
