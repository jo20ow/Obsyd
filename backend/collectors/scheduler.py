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

from backend.analytics.days_of_supply import compute_days_of_supply
from backend.analytics.disruption_score import compute_disruption_score
from backend.analytics.eia_prediction import compute_eia_prediction
from backend.analytics.freight_proxy import compute_freight_proxy
from backend.analytics.supply_demand import compute_supply_demand
from backend.analytics.tonne_miles import compute_tonne_miles
from backend.analytics.validation.scorecards import recompute_scorecards_job
from backend.collectors.crack_spreads import collect_crack_spreads
from backend.collectors.eia import collect_eia
from backend.collectors.eia_international import ingest_eia_international
from backend.collectors.energy_prices import collect_energy_prices
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
from backend.collectors.spark_spreads import collect_spark_spreads
from backend.collectors.sts_collector import collect_sts_events
from backend.collectors.usgs_minerals import ingest_usgs_minerals
from backend.collectors.worldbank import ingest_worldbank
from backend.database import SessionLocal
from backend.metals.usgs_copper import ingest_copper_supply
from backend.notifications.alert_runner import process_alert_rules
from backend.notifications.collector_watchdog import check_collectors
from backend.notifications.daily_email import send_daily_email
from backend.providers.price_provider import get_live_prices as refresh_live_prices
from backend.signals.alert_outcomes import snapshot_and_backfill_outcomes
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


def _gas_recent_days(n: int = 7) -> list[str]:
    """The last n days ending yesterday (ENTSOG/GIE confirm with a 1-2d lag)."""
    from datetime import datetime, timedelta, timezone

    end = datetime.now(timezone.utc).date() - timedelta(days=1)
    return [(end - timedelta(days=i)).isoformat() for i in range(n)][::-1]


async def _run_gas_daily():
    """Daily EU gas balance: refresh the last week (provisional→confirmed),
    recompute demand + the residual engine. ENTSO-E skips without a token."""
    from backend.gas.balance import compute_and_persist
    from backend.gas.demand import compute_demand_model
    from backend.gas.entsoe import ingest_power_burn
    from backend.gas.entsog import ingest_flows
    from backend.gas.gie import ingest_lng, ingest_storage
    from backend.gas.weather import ingest_weather

    db = SessionLocal()
    try:
        days = _gas_recent_days(7)
        # overwrite=True re-fetches the recent window so provisional data is
        # replaced once confirmed.
        for step, coro in (
            ("entsog", ingest_flows(db, days, overwrite=True)),
            ("agsi", ingest_storage(db, days, overwrite=True)),
            ("alsi", ingest_lng(db, days, overwrite=True)),
            ("weather", ingest_weather(db, days, overwrite=True)),
            ("entsoe", ingest_power_burn(db, days, overwrite=True)),
        ):
            try:
                await coro
            except Exception as e:
                logger.error("gas daily %s failed: %s", step, e)
        await compute_demand_model(db)
        compute_and_persist(db)
        logger.info("gas daily: refreshed %s..%s + recomputed demand/balance", days[0], days[-1])
    except Exception as e:
        logger.error("gas daily failed: %s", e)
    finally:
        db.close()


def _power_recent_days(n: int = 7, *, today=None, ahead: int = 1) -> list[str]:
    """The n days ending `ahead` days after today, for ENTSO-E A44 ingestion.

    Day-ahead prices for delivery day D (and D+1) are published the afternoon
    before, so the window must reach *tomorrow* (`ahead=1`) to capture the
    published frontier — ending at yesterday left the desk 1-2 days behind even
    when the collector ran fine. Future days with no data yet parse to nothing
    (idempotent, harmless). `today` is injectable for tests.
    """
    from datetime import datetime, timedelta, timezone

    if today is None:
        today = datetime.now(timezone.utc).date()
    end = today + timedelta(days=ahead)
    return [(end - timedelta(days=i)).isoformat() for i in range(n)][::-1]


async def _run_power_daily():
    """Daily electricity + spark spread refresh for all supported bidding zones.

    For each zone in POWER_ZONES (DE_LU, FR, NL):
      (a) Ingest ENTSO-E A44 day-ahead prices for the last 7 days into
          EnergyPrice(symbol=zone_cfg["price_symbol"]) + PowerPriceDaily(zone=zone).
      (b) Ingest ENTSO-E A65 load + A75 wind/solar for the last 7 days into
          PowerGrid + PowerGenMix keyed by zone.

    Then:
      (c) Recompute SparkSpreadHistory — DE-LU only (no zone column; intentional).
    """
    from backend.power.entsoe_grid import ingest_grid, ingest_load_forecast
    from backend.power.entsoe_prices import ingest_day_ahead
    from backend.power.zones import POWER_ZONES

    db = SessionLocal()
    try:
        days = _power_recent_days(7)

        for zone_key, zone_cfg in POWER_ZONES.items():
            try:
                result = await ingest_day_ahead(
                    db, days,
                    eic=zone_cfg["eic"],
                    symbol=zone_cfg["price_symbol"],
                    zone=zone_key,
                    overwrite=True,
                )
                # Fail loudly on the silent-skip path (missing/expired token) so a
                # frozen desk is a logged, monitorable condition, not a quiet no-op.
                if isinstance(result, dict) and result.get("skipped"):
                    logger.error("power daily ingest [%s] SKIPPED: %s — desk will go stale", zone_key, result)
                else:
                    logger.info("power daily ingest [%s]: %s", zone_key, result)
            except Exception as exc:
                logger.error("power daily ingest_day_ahead [%s] failed: %s", zone_key, exc)

            try:
                result = await ingest_grid(db, days, eic=zone_cfg["eic"], zone=zone_key, overwrite=True)
                logger.info("power daily grid ingest [%s]: %s", zone_key, result)
            except Exception as exc:
                logger.error("power daily ingest_grid [%s] failed: %s", zone_key, exc)

            try:
                result = await ingest_load_forecast(db, days, eic=zone_cfg["eic"], zone=zone_key, overwrite=True)
                logger.info("power daily load-forecast ingest [%s]: %s", zone_key, result)
            except Exception as exc:
                logger.error("power daily ingest_load_forecast [%s] failed: %s", zone_key, exc)

        # Cross-border physical flows (Energy-Charts CBPF, CC BY 4.0).
        try:
            from backend.power.energy_charts_flows import ingest_cbpf

            result = await ingest_cbpf(db, days, overwrite=True)
            logger.info("power daily cross-border flows (Energy-Charts): %s", result)
        except Exception as exc:
            logger.error("power daily ingest_cbpf (Energy-Charts) failed: %s", exc)

        # Spark spread uses POWER_DE (DE-LU) only — SparkSpreadHistory has no zone column.
        try:
            result = await collect_spark_spreads()
            logger.info("power daily spark spreads: %s", result)
        except Exception as exc:
            logger.error("power daily collect_spark_spreads failed: %s", exc)
    except Exception as exc:
        logger.error("_run_power_daily outer failed: %s", exc)
    finally:
        db.close()


async def _run_crypto():
    """Refresh the crypto basket (CoinGecko, no key). The one real-time free feed."""
    from backend.crypto.coingecko import collect_crypto

    db = SessionLocal()
    try:
        await collect_crypto(db)
    except Exception as exc:
        logger.error("crypto refresh failed: %s", exc)
    finally:
        db.close()


async def _run_edgar_tickers():
    """Refresh the SEC EDGAR company security-master (ticker→CIK→title)."""
    from backend.edgar.client import load_company_tickers

    db = SessionLocal()
    try:
        await load_company_tickers(db)
    except Exception as exc:
        logger.error("edgar tickers refresh failed: %s", exc)
    finally:
        db.close()


async def _run_news():
    """Pre-warm the cross-asset news topics (GDELT, rate-limited, cache-only)."""
    from backend.news.gdelt_news import refresh_all_topics

    try:
        await refresh_all_topics()
    except Exception as exc:
        logger.error("news prewarm failed: %s", exc)


async def _run_gas_registry_weekly():
    """Re-sync the ENTSOG point registry (operators rename/reclassify points)."""
    from backend.gas.entsog import sync_points

    db = SessionLocal()
    try:
        await sync_points(db, overwrite=True)
    except Exception as e:
        logger.error("gas registry sync failed: %s", e)
    finally:
        db.close()


async def _run_metals_monthly():
    """Monthly ingest of USGS copper supply data (MIS spreadsheets)."""
    db = SessionLocal()
    try:
        result = await ingest_copper_supply(db, months_back=18)
        logger.info("metals monthly: %s", result)
    except Exception as e:
        logger.error("metals monthly ingest failed: %s", e)
    finally:
        db.close()


async def _run_eia_international():
    """Monthly ingest of EIA International per-country energy (ATLAS data layer)."""
    db = SessionLocal()
    try:
        result = await ingest_eia_international(db)
        logger.info("eia international: %s", result)
    except Exception as e:
        logger.error("eia international ingest failed: %s", e)
    finally:
        db.close()


async def _run_worldbank():
    """Monthly ingest of World Bank per-country macro indicators (ATLAS data layer)."""
    db = SessionLocal()
    try:
        result = await ingest_worldbank(db)
        logger.info("worldbank macro: %s", result)
    except Exception as e:
        logger.error("worldbank macro ingest failed: %s", e)
    finally:
        db.close()


async def _run_usgs_minerals():
    """Monthly ingest of USGS per-country mineral production (ATLAS resources layer)."""
    db = SessionLocal()
    try:
        result = await ingest_usgs_minerals(db)
        logger.info("usgs minerals: %s", result)
    except Exception as e:
        logger.error("usgs minerals ingest failed: %s", e)
    finally:
        db.close()


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

    # Energy prices (TTF, later EUA/power): daily 22:15 UTC
    scheduler.add_job(
        collect_energy_prices,
        CronTrigger(hour=22, minute=15),
        id="energy_prices_daily",
        **JOB_DEFAULTS,
    )

    # Equities snapshot: daily 22:30 UTC (after crack spreads)
    scheduler.add_job(
        collect_equities,
        CronTrigger(hour=22, minute=30),
        id="equities_daily",
        **JOB_DEFAULTS,
    )

    # Pre-email price refresh: Mon-Fri 06:45 UTC (fresh prices for daily email)
    scheduler.add_job(
        refresh_live_prices,
        CronTrigger(day_of_week="mon-fri", hour=6, minute=45),
        id="pre_email_price_refresh",
        **JOB_DEFAULTS,
    )

    # Daily briefing email: Mon-Fri 07:00 UTC (before European market open)
    scheduler.add_job(
        send_daily_email,
        CronTrigger(day_of_week="mon-fri", hour=7, minute=0),
        id="daily_email",
        **JOB_DEFAULTS,
    )

    # User-defined alert rules: evaluate every 30 minutes. Each rule has
    # a 6h cooldown after triggering, so this cadence is responsive but
    # not spammy. Cap per run inside the runner protects against runaway.
    scheduler.add_job(
        process_alert_rules,
        CronTrigger(minute="15,45"),
        id="user_alert_rules_30min",
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

    # Freight Proxy: daily 23:00 UTC (after equities snapshot at 22:30)
    scheduler.add_job(
        compute_freight_proxy,
        CronTrigger(hour=23, minute=0),
        id="freight_proxy_daily",
        **JOB_DEFAULTS,
    )

    # Supply-Demand Balance: weekly Tuesday 14:00 UTC (after PortWatch, before EIA Wed)
    scheduler.add_job(
        compute_supply_demand,
        CronTrigger(day_of_week="tue", hour=14, minute=0),
        id="supply_demand_weekly",
        **JOB_DEFAULTS,
    )

    # Days of Supply: weekly Wednesday 18:00 UTC (after EIA release ~15:30 UTC)
    scheduler.add_job(
        compute_days_of_supply,
        CronTrigger(day_of_week="wed", hour=18, minute=0),
        id="days_of_supply_weekly",
        **JOB_DEFAULTS,
    )

    # Alert outcome tracking (silent): every 2 hours at :35
    # Captures Brent/WTI snapshots at T+0, T+1d, T+7d, T+30d per alert.
    # Not surfaced in UI until sample size is honest (n>=30 per rule type).
    scheduler.add_job(
        snapshot_and_backfill_outcomes,
        CronTrigger(hour="*/2", minute=35),
        id="alert_outcomes_2h",
        **JOB_DEFAULTS,
    )

    # Smart retention: daily 04:00 UTC (thin old vessel_positions)
    scheduler.add_job(
        run_retention,
        CronTrigger(hour=4, minute=0),
        id="retention_daily",
        **JOB_DEFAULTS,
    )

    # Collector watchdog: daily 09:00 UTC — emails ops if a collector
    # stopped writing within its freshness window (see routes/health.py).
    scheduler.add_job(
        check_collectors,
        CronTrigger(hour=9, minute=0),
        id="collector_watchdog_daily",
        **JOB_DEFAULTS,
    )

    # Signal scorecards: weekly Monday 05:00 UTC — recompute each signal's
    # forward-return track record (rank IC, HAC t, hit rate) vs Brent.
    scheduler.add_job(
        recompute_scorecards_job,
        CronTrigger(day_of_week="mon", hour=5, minute=0),
        id="signal_scorecards_weekly",
        **JOB_DEFAULTS,
    )

    # EU gas balance: daily 10:00 UTC — refresh the last week of ENTSOG/GIE/
    # weather (catches provisional→confirmed) + recompute demand + residual.
    scheduler.add_job(
        _run_gas_daily,
        CronTrigger(hour=10, minute=0),
        id="gas_balance_daily",
        **JOB_DEFAULTS,
    )

    # ENTSOG point registry re-sync: weekly Monday 03:30 UTC.
    scheduler.add_job(
        _run_gas_registry_weekly,
        CronTrigger(day_of_week="mon", hour=3, minute=30),
        id="gas_registry_weekly",
        **JOB_DEFAULTS,
    )

    # Metals — USGS copper supply: monthly on the 5th at 06:00 UTC
    # (MIS files are typically released mid-to-late in the following month).
    scheduler.add_job(
        _run_metals_monthly,
        CronTrigger(day=5, hour=6, minute=0),
        id="metals_monthly",
        **JOB_DEFAULTS,
    )

    # ATLAS — EIA International per-country energy (annual data, lagging): monthly on the 6th.
    scheduler.add_job(
        _run_eia_international,
        CronTrigger(day=6, hour=6, minute=0),
        id="eia_international",
        **JOB_DEFAULTS,
    )

    # ATLAS — World Bank per-country macro (annual, lagging): monthly on the 7th.
    scheduler.add_job(
        _run_worldbank,
        CronTrigger(day=7, hour=6, minute=0),
        id="worldbank_macro",
        **JOB_DEFAULTS,
    )

    # ATLAS — USGS per-country mineral production (annual MCS release): monthly on the 8th.
    scheduler.add_job(
        _run_usgs_minerals,
        CronTrigger(day=8, hour=6, minute=0),
        id="usgs_minerals",
        **JOB_DEFAULTS,
    )

    # Power day-ahead + spark spread: daily 22:30 UTC.
    # (a) Ingest ENTSO-E A44 for DE-LU last 7 days into POWER_DE.
    # (b) Recompute SparkSpreadHistory for all aligned POWER_DE + TTF dates.
    scheduler.add_job(
        _run_power_daily,
        CronTrigger(hour=22, minute=30),
        id="power_spark_daily",
        **JOB_DEFAULTS,
    )

    # Crypto basket — the one real-time free feed; refresh every 15 min.
    scheduler.add_job(
        _run_crypto,
        CronTrigger(minute="*/15"),
        id="crypto_15min",
        **JOB_DEFAULTS,
    )

    # SEC EDGAR company security-master — refresh weekly (Sun 04:20 UTC).
    scheduler.add_job(
        _run_edgar_tickers,
        CronTrigger(day_of_week="sun", hour=4, minute=20),
        id="edgar_tickers_weekly",
        **JOB_DEFAULTS,
    )

    # Cross-asset news — pre-warm the topic feeds every 20 min (rate-limited).
    scheduler.add_job(
        _run_news,
        CronTrigger(minute="*/20"),
        id="news_prewarm_20min",
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
        "STS detection (every 4h), Daily email (Mon-Fri 07:00), "
        "Retention (daily 04:00) | FIRMS (every 6h) | NOAA (every 30min) | "
        "Tonne-Miles (every 6h) | Disruption Score (every 2h) | EIA Prediction (weekly Tue)"
    )


def stop_scheduler():
    if scheduler.running:
        scheduler.shutdown()
        logger.info("Scheduler stopped")
