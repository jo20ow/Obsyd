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

from backend.analytics.disruption_score import compute_disruption_score
from backend.analytics.eia_prediction import compute_eia_prediction
from backend.analytics.tonne_miles import compute_tonne_miles
from backend.collectors.crack_spreads import collect_crack_spreads
from backend.collectors.eia import collect_eia
from backend.collectors.equities import collect_equities
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
from backend.collectors.sts_collector import collect_sts_events
from backend.database import SessionLocal
from backend.notifications.daily_email import send_daily_email
from backend.providers.price_provider import get_live_prices as refresh_live_prices
from backend.signals.evaluator import evaluate_signals
from backend.signals.floating_storage import detect_floating_storage
from backend.signals.sentiment_scorer import compute_sentiment_score
from backend.signals.voyage_detector import detect_voyages

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()

# Shared job defaults: recover from missed runs, prevent overlap
JOB_DEFAULTS = {
    "misfire_grace_time": 3600,  # run jobs up to 1h late
    "coalesce": True,  # if multiple runs missed, execute only once
    "max_instances": 1,  # prevent parallel runs of the same job
    "replace_existing": True,
}


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
        **JOB_DEFAULTS,
    )

    # FRED: daily at 18:00 UTC (after US markets update)
    scheduler.add_job(
        _run_fred,
        CronTrigger(hour=18, minute=0),
        id="fred_daily",
        **JOB_DEFAULTS,
    )

    # PortWatch: weekly Tuesday 12:00 UTC
    scheduler.add_job(
        collect_portwatch,
        CronTrigger(day_of_week="tue", hour=12, minute=0),
        id="portwatch_weekly",
        **JOB_DEFAULTS,
    )

    # NOAA: hurricane/tropical alerts every 30 min
    scheduler.add_job(
        collect_noaa_alerts,
        CronTrigger(minute="*/30"),
        id="noaa_30min",
        **JOB_DEFAULTS,
    )

    # GDELT: primary keywords every 2 hours (was 15min, caused 429s)
    scheduler.add_job(
        collect_gdelt_volume,
        CronTrigger(minute=0, hour="*/2"),
        id="gdelt_primary_2h",
        **JOB_DEFAULTS,
    )
    scheduler.add_job(
        collect_gdelt_volume_secondary,
        CronTrigger(minute=30),
        id="gdelt_secondary_hourly",
        **JOB_DEFAULTS,
    )
    scheduler.add_job(
        collect_gdelt_sentiment,
        CronTrigger(hour=14, minute=0),
        id="gdelt_sentiment_daily",
        **JOB_DEFAULTS,
    )

    # JODI: monthly on 15th at 10:00 UTC (data usually available mid-month)
    scheduler.add_job(
        collect_jodi,
        CronTrigger(day=15, hour=10, minute=0),
        id="jodi_monthly",
        **JOB_DEFAULTS,
    )

    # NASA FIRMS: thermal hotspots every 6h
    scheduler.add_job(
        collect_firms,
        CronTrigger(hour="*/6", minute=15),
        id="firms_6h",
        **JOB_DEFAULTS,
    )

    # PortWatch chokepoint backfill: daily at 06:00 UTC
    scheduler.add_job(
        _run_portwatch_daily,
        CronTrigger(hour=6, minute=0),
        id="portwatch_daily_backfill",
        **JOB_DEFAULTS,
    )

    # Finnhub news: every 2 hours at :45
    scheduler.add_job(
        collect_finnhub_news,
        CronTrigger(minute=45, hour="*/2"),
        id="finnhub_news_2h",
        **JOB_DEFAULTS,
    )

    # Geofence aggregation: hourly
    scheduler.add_job(
        aggregate_geofence_events,
        CronTrigger(minute=5),
        id="geofence_hourly",
        **JOB_DEFAULTS,
    )

    # Geofence daily aggregation: 23:50 UTC (final end-of-day rollup)
    scheduler.add_job(
        aggregate_geofence_daily,
        CronTrigger(hour=23, minute=50),
        id="geofence_daily",
        **JOB_DEFAULTS,
    )

    # Floating storage detection: every 6 hours
    scheduler.add_job(
        detect_floating_storage,
        CronTrigger(hour="*/6", minute=30),
        id="floating_storage",
        **JOB_DEFAULTS,
    )

    # Sentiment risk score: every 6 hours
    scheduler.add_job(
        compute_sentiment_score,
        CronTrigger(hour="*/6", minute=10),
        id="sentiment_6h",
        **JOB_DEFAULTS,
    )

    # Live prices: refresh yfinance cache every 4 hours
    scheduler.add_job(
        refresh_live_prices,
        CronTrigger(hour="2,6,10,14,18,22", minute=0),
        id="live_price_refresh",
        **JOB_DEFAULTS,
    )

    # Signals: evaluate every 5 minutes
    scheduler.add_job(
        evaluate_signals,
        CronTrigger(minute="*/5"),
        id="signals_5min",
        **JOB_DEFAULTS,
    )

    # Daily fleet summary: 23:55 UTC (before day rollover)
    scheduler.add_job(
        create_daily_fleet_summary,
        CronTrigger(hour=23, minute=55),
        id="fleet_summary_daily",
        **JOB_DEFAULTS,
    )

    # Crack spread history: daily 22:00 UTC (after US market close)
    scheduler.add_job(
        collect_crack_spreads,
        CronTrigger(hour=22, minute=0),
        id="crack_spreads_daily",
        **JOB_DEFAULTS,
    )

    # Equities snapshot: daily 22:30 UTC (after crack spreads)
    scheduler.add_job(
        collect_equities,
        CronTrigger(hour=22, minute=30),
        id="equities_daily",
        **JOB_DEFAULTS,
    )

    # Daily briefing email: 07:00 UTC (before European market open)
    scheduler.add_job(
        send_daily_email,
        CronTrigger(hour=7, minute=0),
        id="daily_email",
        **JOB_DEFAULTS,
    )

    # Voyage detection: every 2 hours at :20
    scheduler.add_job(
        detect_voyages,
        CronTrigger(hour="*/2", minute=20),
        id="voyage_detection_2h",
        **JOB_DEFAULTS,
    )

    # STS detection: every 4 hours at :40
    scheduler.add_job(
        collect_sts_events,
        CronTrigger(hour="*/4", minute=40),
        id="sts_detection_4h",
        **JOB_DEFAULTS,
    )

    # Tonne-Miles Index: every 6 hours at :50
    scheduler.add_job(
        compute_tonne_miles,
        CronTrigger(hour="*/6", minute=50),
        id="tonne_miles_6h",
        **JOB_DEFAULTS,
    )

    # Disruption Score: every 2 hours at :55
    scheduler.add_job(
        compute_disruption_score,
        CronTrigger(hour="*/2", minute=55),
        id="disruption_score_2h",
        **JOB_DEFAULTS,
    )

    # EIA Prediction: weekly Tuesday 12:00 UTC (before Wednesday EIA release)
    scheduler.add_job(
        compute_eia_prediction,
        CronTrigger(day_of_week="tue", hour=12, minute=0),
        id="eia_prediction_weekly",
        **JOB_DEFAULTS,
    )

    # Smart retention: daily 04:00 UTC (thin old vessel_positions)
    scheduler.add_job(
        run_retention,
        CronTrigger(hour=4, minute=0),
        id="retention_daily",
        **JOB_DEFAULTS,
    )

    scheduler.start()
    logger.info(
        "Scheduler started: EIA (weekly Wed), FRED (daily), "
        "PortWatch (weekly Tue), GDELT (every 2h), Finnhub (every 2h), "
        "JODI (monthly 15th), Live prices (every 4h), Signals (every 5min), "
        "Fleet summary (daily 23:55), Geofence daily (23:50), "
        "Floating storage (every 6h), Voyages (every 2h), "
        "Crack spreads (daily 22:00), Equities (daily 22:30), "
        "STS detection (every 4h), Daily email (07:00), "
        "Retention (daily 04:00) | FIRMS (every 6h) | NOAA (every 30min) | "
        "Tonne-Miles (every 6h) | Disruption Score (every 2h) | EIA Prediction (weekly Tue)"
    )


def stop_scheduler():
    if scheduler.running:
        scheduler.shutdown()
        logger.info("Scheduler stopped")
