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
from backend.collectors.portwatch import collect_portwatch
from backend.collectors.noaa import collect_noaa_alerts
from backend.collectors.gdelt import collect_gdelt_volume, collect_gdelt_volume_secondary, collect_gdelt_sentiment
from backend.collectors.jodi import collect_jodi
from backend.signals.evaluator import evaluate_signals
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

    # PortWatch: weekly Tuesday 12:00 UTC
    scheduler.add_job(
        collect_portwatch,
        CronTrigger(day_of_week="tue", hour=12, minute=0),
        id="portwatch_weekly",
        replace_existing=True,
    )

    # NOAA: weather alerts every 30 minutes
    scheduler.add_job(
        collect_noaa_alerts,
        CronTrigger(minute="*/30"),
        id="noaa_30min",
        replace_existing=True,
    )

    # GDELT: primary keywords every 15 min, secondary hourly, sentiment daily
    scheduler.add_job(
        collect_gdelt_volume,
        CronTrigger(minute="*/15"),
        id="gdelt_primary_15min",
        replace_existing=True,
    )
    scheduler.add_job(
        collect_gdelt_volume_secondary,
        CronTrigger(minute=30),
        id="gdelt_secondary_hourly",
        replace_existing=True,
    )
    scheduler.add_job(
        collect_gdelt_sentiment,
        CronTrigger(hour=14, minute=0),
        id="gdelt_sentiment_daily",
        replace_existing=True,
    )

    # JODI: monthly on 15th at 10:00 UTC (data usually available mid-month)
    scheduler.add_job(
        collect_jodi,
        CronTrigger(day=15, hour=10, minute=0),
        id="jodi_monthly",
        replace_existing=True,
    )

    # Signals: evaluate every 5 minutes
    scheduler.add_job(
        evaluate_signals,
        CronTrigger(minute="*/5"),
        id="signals_5min",
        replace_existing=True,
    )

    scheduler.start()
    logger.info(
        "Scheduler started: EIA (weekly Wed), FRED (daily), "
        "PortWatch (weekly Tue), NOAA (every 30min), "
        "GDELT (every 15min), JODI (monthly 15th), Signals (every 5min)"
    )


def stop_scheduler():
    if scheduler.running:
        scheduler.shutdown()
        logger.info("Scheduler stopped")
