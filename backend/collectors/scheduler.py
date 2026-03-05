"""
APScheduler setup for periodic data collection.

Schedule:
  - EIA: Weekly (Wednesday, after EIA publishes WPSR)
  - FRED: Daily
"""

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from backend.collectors.eia import collect_eia
from backend.collectors.fred import collect_fred
from backend.database import SessionLocal

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


async def _run_eia():
    db = SessionLocal()
    try:
        await collect_eia(db)
    except Exception as e:
        logger.error(f"EIA collection failed: {e}")
    finally:
        db.close()


async def _run_fred():
    db = SessionLocal()
    try:
        await collect_fred(db)
    except Exception as e:
        logger.error(f"FRED collection failed: {e}")
    finally:
        db.close()


def start_scheduler():
    """Register jobs and start the scheduler."""
    # EIA WPSR: published Wednesdays ~10:30 ET, collect at 11:00 ET (15:00 UTC)
    scheduler.add_job(
        _run_eia,
        CronTrigger(day_of_week="wed", hour=15, minute=0),
        id="eia_weekly",
        replace_existing=True,
    )

    # FRED: daily at 18:00 UTC (after US markets update)
    scheduler.add_job(
        _run_fred,
        CronTrigger(hour=18, minute=0),
        id="fred_daily",
        replace_existing=True,
    )

    scheduler.start()
    logger.info("Scheduler started: EIA (weekly Wed), FRED (daily)")


def stop_scheduler():
    if scheduler.running:
        scheduler.shutdown()
        logger.info("Scheduler stopped")
