"""
APScheduler setup for periodic data collection.

REFOCUS 2026-07-03 — Obsyd is the European electricity+gas desk ("gridstatus.io
for Europe"). Only power/gas + shared (email/alerts/scorecards/retention/watchdog)
jobs run here. The non-power collectors (AIS/oil, portwatch, gdelt/sentiment, jodi,
firms, noaa, crack/equities, analytics, metals, atlas, crypto, edgar, news) moved
to the sibling project and are no longer scheduled.

Schedule (UTC):
  - Power day-ahead + spark: daily 22:30
  - Energy prices (TTF/…): daily 22:15
  - EU gas balance: daily 10:00; gas registry: weekly Mon 03:30
  - Signals (radar): every 5 min; scorecards: weekly Mon 05:00
  - Live prices: every 4h; user alert rules: every 30 min
  - Daily email: Mon-Fri 07:00 (pre-refresh 06:45)
  - Retention: daily 04:00; collector watchdog: daily 09:00
"""

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from backend.analytics.validation.scorecards import recompute_scorecards_job
from backend.collectors.energy_prices import collect_energy_prices
from backend.collectors.retention import run_retention
from backend.collectors.spark_spreads import collect_spark_spreads
from backend.database import SessionLocal
from backend.notifications.alert_runner import process_alert_rules
from backend.notifications.collector_watchdog import check_collectors
from backend.notifications.daily_email import send_daily_email
from backend.providers.price_provider import get_live_prices as refresh_live_prices
from backend.signals.evaluator import evaluate_signals

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()

# Shared job defaults: recover from missed runs, prevent overlap
JOB_DEFAULTS = {
    "misfire_grace_time": 3600,  # run jobs up to 1h late
    "coalesce": True,  # if multiple runs missed, execute only once
    "max_instances": 1,  # prevent parallel runs of the same job
    "replace_existing": True,
}


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


def _power_recent_days(n: int = 7, *, today=None, ahead: int = 1) -> list[str]:
    """The n days ending `ahead` days after today, for ENTSO-E A44 ingestion.

    Day-ahead prices for delivery day D (and D+1) are published the afternoon
    before, so the window must reach *tomorrow* (`ahead=1`) to capture the
    published frontier. `today` is injectable for tests.
    """
    from datetime import datetime, timedelta, timezone

    if today is None:
        today = datetime.now(timezone.utc).date()
    end = today + timedelta(days=ahead)
    return [(end - timedelta(days=i)).isoformat() for i in range(n)][::-1]


async def _run_power_daily():
    """Daily electricity + spark spread refresh for all supported bidding zones
    (DE_LU, FR, NL): day-ahead prices, load/wind/solar grid, load+residual forecast,
    cross-border flows, then the DE-LU spark spread."""
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


def _intraday_days(today=None) -> list[str]:
    """Yesterday + today (UTC) — the window where actual load/generation are still
    filling in / being revised through the day. `today` injectable for tests."""
    from datetime import datetime, timedelta, timezone

    if today is None:
        today = datetime.now(timezone.utc).date()
    return [(today - timedelta(days=1)).isoformat(), today.isoformat()]


async def _run_power_intraday():
    """Near-real-time refresh (every ~30 min): actual load + generation (→ residual,
    per-fuel) and cross-border flows for the current window, so the desk shows TODAY
    filling in hour by hour instead of only after the nightly run. Day-ahead prices and
    forecasts don't change intraday and stay on the daily / midday jobs."""
    from backend.power.entsoe_grid import ingest_grid
    from backend.power.zones import POWER_ZONES

    db = SessionLocal()
    try:
        days = _intraday_days()
        for zone_key, zone_cfg in POWER_ZONES.items():
            try:
                await ingest_grid(db, days, eic=zone_cfg["eic"], zone=zone_key, overwrite=True)
            except Exception as exc:
                logger.error("power intraday grid [%s] failed: %s", zone_key, exc)
        try:
            from backend.power.energy_charts_flows import ingest_cbpf

            await ingest_cbpf(db, days, overwrite=True)
        except Exception as exc:
            logger.error("power intraday flows failed: %s", exc)
    except Exception as exc:
        logger.error("_run_power_intraday outer failed: %s", exc)
    finally:
        db.close()


async def _run_power_prices_midday():
    """Midday day-ahead price refresh (~after the 12:45 CET auction) so tomorrow's
    prices appear hours earlier than the 22:30 nightly run — for all enabled zones."""
    from datetime import datetime, timedelta, timezone

    from backend.power.entsoe_prices import ingest_day_ahead
    from backend.power.zones import POWER_ZONES

    today = datetime.now(timezone.utc).date()
    days = [today.isoformat(), (today + timedelta(days=1)).isoformat()]
    db = SessionLocal()
    try:
        for zone_key, zone_cfg in POWER_ZONES.items():
            try:
                await ingest_day_ahead(
                    db, days, eic=zone_cfg["eic"],
                    symbol=zone_cfg["price_symbol"], zone=zone_key, overwrite=True,
                )
            except Exception as exc:
                logger.error("power midday price [%s] failed: %s", zone_key, exc)
    except Exception as exc:
        logger.error("_run_power_prices_midday outer failed: %s", exc)
    finally:
        db.close()


async def _run_capacity_monthly():
    """Refresh installed generation capacity (ENTSO-E A68) for the current year across
    all enabled zones — monthly (capacity changes ~yearly). Feeds /api/v1/capacity."""
    from datetime import datetime, timezone

    from backend.power.entsoe_capacity import ingest_installed_capacity
    from backend.power.zones import POWER_ZONES

    year = datetime.now(timezone.utc).year
    db = SessionLocal()
    try:
        for zone_key, cfg in POWER_ZONES.items():
            try:
                await ingest_installed_capacity(db, year, eic=cfg["eic"], zone=zone_key, overwrite=True)
            except Exception as exc:
                logger.error("capacity monthly [%s] failed: %s", zone_key, exc)
    except Exception as exc:
        logger.error("_run_capacity_monthly outer failed: %s", exc)
    finally:
        db.close()


def scheduler_role_enabled(role: str) -> bool:
    """True if this process role should run the scheduler / be a DB writer.

    'ingest' and 'all' run the collectors; 'api' serves requests only (no scheduler,
    so API workers can scale without double-firing crons). Unknown roles default to
    enabled (fail-safe: never silently stop ingestion because of a typo'd env var).
    """
    return (role or "all").strip().lower() != "api"


def start_scheduler():
    """Register the electricity+gas desk + shared jobs, then start the scheduler."""
    # Power day-ahead + grid + forecasts + flows + spark: daily 22:30 UTC.
    scheduler.add_job(_run_power_daily, CronTrigger(hour=22, minute=30), id="power_spark_daily", **JOB_DEFAULTS)

    # Near-real-time: refresh actual load/generation + flows every 30 min so the desk
    # shows today filling in hour by hour (free ENTSO-E ceiling: ~1h publication lag).
    scheduler.add_job(_run_power_intraday, CronTrigger(minute="*/30"), id="power_intraday_30min", **JOB_DEFAULTS)
    # Midday day-ahead price refresh: 12:00 UTC (after the ~12:45 CET auction) so
    # tomorrow's prices appear hours before the nightly run.
    scheduler.add_job(_run_power_prices_midday, CronTrigger(hour=12, minute=0), id="power_prices_midday", **JOB_DEFAULTS)
    # Installed capacity (A68): monthly, 2nd @ 03:00 UTC — annual data, cheap to refresh.
    scheduler.add_job(_run_capacity_monthly, CronTrigger(day=2, hour=3, minute=0), id="capacity_monthly", **JOB_DEFAULTS)

    # Energy prices (TTF for the spark spread, + power ticker): daily 22:15 UTC.
    scheduler.add_job(collect_energy_prices, CronTrigger(hour=22, minute=15), id="energy_prices_daily", **JOB_DEFAULTS)

    # EU gas balance: daily 10:00 UTC; ENTSOG point registry: weekly Mon 03:30 UTC.
    scheduler.add_job(_run_gas_daily, CronTrigger(hour=10, minute=0), id="gas_balance_daily", **JOB_DEFAULTS)
    scheduler.add_job(
        _run_gas_registry_weekly,
        CronTrigger(day_of_week="mon", hour=3, minute=30),
        id="gas_registry_weekly",
        **JOB_DEFAULTS,
    )

    # Anomaly radar: evaluate every 5 minutes.
    scheduler.add_job(evaluate_signals, CronTrigger(minute="*/5"), id="signals_5min", **JOB_DEFAULTS)

    # Signal scorecards (gas_residual / power_residual / spark_spread): weekly Mon 05:00 UTC.
    scheduler.add_job(
        recompute_scorecards_job,
        CronTrigger(day_of_week="mon", hour=5, minute=0),
        id="signal_scorecards_weekly",
        **JOB_DEFAULTS,
    )

    # Live prices: refresh the yfinance cache every 4 hours (feeds the ticker).
    scheduler.add_job(
        refresh_live_prices,
        CronTrigger(hour="2,6,10,14,18,22", minute=0),
        id="live_price_refresh",
        **JOB_DEFAULTS,
    )

    # User-defined alert rules: every 30 min (6h cooldown per rule inside the runner).
    scheduler.add_job(process_alert_rules, CronTrigger(minute="15,45"), id="user_alert_rules_30min", **JOB_DEFAULTS)

    # Daily briefing email: Mon-Fri 07:00 UTC (pre-refresh prices at 06:45).
    scheduler.add_job(
        refresh_live_prices,
        CronTrigger(day_of_week="mon-fri", hour=6, minute=45),
        id="pre_email_price_refresh",
        **JOB_DEFAULTS,
    )
    scheduler.add_job(
        send_daily_email,
        CronTrigger(day_of_week="mon-fri", hour=7, minute=0),
        id="daily_email",
        **JOB_DEFAULTS,
    )

    # Retention: daily 04:00 UTC. Collector watchdog: daily 09:00 UTC.
    scheduler.add_job(run_retention, CronTrigger(hour=4, minute=0), id="retention_daily", **JOB_DEFAULTS)
    scheduler.add_job(check_collectors, CronTrigger(hour=9, minute=0), id="collector_watchdog_daily", **JOB_DEFAULTS)

    scheduler.start()


def stop_scheduler():
    scheduler.shutdown(wait=False)
