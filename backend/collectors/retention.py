"""
Smart Retention — tiered cleanup for vessel_positions.

- 0–7 days: keep all raw data
- 7–30 days: thin to one position per MMSI per hour
- >30 days: delete (geofence_events has daily aggregates)

Runs daily at 04:00 UTC via scheduler.
"""

import logging

from sqlalchemy import text

from backend.database import SessionLocal

logger = logging.getLogger(__name__)


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

        db.commit()
        logger.info(f"Retention: thinned {thinned} rows (7-30d), deleted {deleted} rows (>30d)")

    except Exception as e:
        db.rollback()
        logger.error(f"Retention cleanup failed: {e}")
    finally:
        db.close()
