"""
APScheduler setup for periodic data collection.

Schedule:
  - EIA: Weekly (Wednesday, after EIA publishes WPSR)
  - FRED: Daily
  - Live prices: Every 4 hours
  - GDELT: Every 2 hours (avoid 429 rate limiting)
  - Finnhub: Every 2 hours at :45 (energy news headlines)
  - FIRMS: Every 6 hours
  - Fleet summary: Daily 23:55 UTC
  - Geofence daily: Daily 23:50 UTC (end-of-day rollup)
  - Retention: Daily 04:00 UTC (thin old vessel_positions)
  - NOAA: Every 30 min (hurricane/tropical alerts)
"""

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from backend.collectors.eia import collect_eia
from backend.collectors.finnhub_news import collect_finnhub_news
from backend.collectors.firms import collect_firms
from backend.collectors.fleet_summary import create_daily_fleet_summary
from backend.collectors.fred import collect_fred
from backend.collectors.gdelt import collect_gdelt_sentiment, collect_gdelt_volume, collect_gdelt_volume_secondary
from backend.collectors.geofence_aggregator import aggregate_geofence_daily, aggregate_geofence_events
from backend.collectors.jodi import collect_jodi
from backend.collectors.noaa import collect_noaa_alerts
from backend.collectors.portwatch import collect_portwatch
from backend.collectors.portwatch_store import fetch_chokepoint_data, store_chokepoint_data
from backend.collectors.retention import run_retention
from backend.database import SessionLocal
from backend.notifications.daily_email import send_daily_email
from backend.providers.price_provider import get_live_prices as refresh_live_prices
from backend.signals.evaluator import evaluate_signals
from backend.signals.floating_storage import detect_floating_storage
from backend.signals.sentiment_scorer import compute_sentiment_score

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


async def _run_portwatch_daily():
    try:
        rows = fetch_chokepoint_data(days=7)
        if rows:
            store_chokepoint_data(rows)
            logger.info(f"PortWatch daily: stored {len(rows)} chokepoint records")
    except Exception as e:
        logger.error(f"PortWatch daily backfill failed: {e}")


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

    # NOAA: hurricane/tropical alerts every 30 min
    scheduler.add_job(
        collect_noaa_alerts,
        CronTrigger(minute="*/30"),
        id="noaa_30min",
        replace_existing=True,
    )

    # GDELT: primary keywords every 2 hours (was 15min, caused 429s)
    scheduler.add_job(
        collect_gdelt_volume,
        CronTrigger(minute=0, hour="*/2"),
        id="gdelt_primary_2h",
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

    # NASA FIRMS: thermal hotspots every 6h
    scheduler.add_job(collect_firms, CronTrigger(hour="*/6", minute=15), id="firms_6h", replace_existing=True)

    # PortWatch chokepoint backfill: daily at 06:00 UTC
    scheduler.add_job(
        _run_portwatch_daily,
        CronTrigger(hour=6, minute=0),
        id="portwatch_daily_backfill",
        replace_existing=True,
    )

    # Finnhub news: every 2 hours at :45
    scheduler.add_job(
        collect_finnhub_news,
        CronTrigger(minute=45, hour="*/2"),
        id="finnhub_news_2h",
        replace_existing=True,
    )

    # Geofence aggregation: hourly
    scheduler.add_job(
        aggregate_geofence_events,
        CronTrigger(minute=5),
        id="geofence_hourly",
        replace_existing=True,
    )

    # Geofence daily aggregation: 23:50 UTC (final end-of-day rollup)
    scheduler.add_job(
        aggregate_geofence_daily,
        CronTrigger(hour=23, minute=50),
        id="geofence_daily",
        replace_existing=True,
    )

    # Floating storage detection: every 6 hours
    scheduler.add_job(
        detect_floating_storage,
        CronTrigger(hour="*/6", minute=30),
        id="floating_storage",
        replace_existing=True,
    )

    # Sentiment risk score: every 6 hours
    scheduler.add_job(
        compute_sentiment_score,
        CronTrigger(hour="*/6", minute=10),
        id="sentiment_6h",
        replace_existing=True,
    )

    # Live prices: refresh yfinance cache every 4 hours
    scheduler.add_job(
        refresh_live_prices,
        CronTrigger(hour="2,6,10,14,18,22", minute=0),
        id="live_price_refresh",
        replace_existing=True,
    )

    # Signals: evaluate every 5 minutes
    scheduler.add_job(
        evaluate_signals,
        CronTrigger(minute="*/5"),
        id="signals_5min",
        replace_existing=True,
    )

    # Daily fleet summary: 23:55 UTC (before day rollover)
    scheduler.add_job(
        create_daily_fleet_summary,
        CronTrigger(hour=23, minute=55),
        id="fleet_summary_daily",
        replace_existing=True,
    )

    # Daily email snapshot: 06:45 UTC
    scheduler.add_job(
        send_daily_email,
        CronTrigger(hour=6, minute=45),
        id="daily_email",
        replace_existing=True,
    )

    # Smart retention: daily 04:00 UTC (thin old vessel_positions)
    scheduler.add_job(
        run_retention,
        CronTrigger(hour=4, minute=0),
        id="retention_daily",
        replace_existing=True,
    )

    scheduler.start()
    logger.info(
        "Scheduler started: EIA (weekly Wed), FRED (daily), "
        "PortWatch (weekly Tue), GDELT (every 2h), Finnhub (every 2h), "
        "JODI (monthly 15th), Live prices (every 4h), Signals (every 5min), "
        "Fleet summary (daily 23:55), Geofence daily (23:50), "
        "Floating storage (every 6h), Daily email (06:45), Retention (daily 04:00) | "
        "FIRMS (every 6h) | NOAA (every 30min)"
    )


def stop_scheduler():
    if scheduler.running:
        scheduler.shutdown()
        logger.info("Scheduler stopped")
