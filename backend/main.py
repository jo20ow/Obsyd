"""
OBSYD - Open-Source Energy Market Intelligence
FastAPI application entry point.
"""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.collectors.aishub import start_aishub, stop_aishub
from backend.collectors.aisstream import start_aisstream, stop_aisstream
from backend.collectors.finnhub_news import collect_finnhub_news
from backend.collectors.gdelt import collect_gdelt_sentiment, collect_gdelt_volume
from backend.collectors.geofence_aggregator import backfill_geofence_events
from backend.collectors.jodi import collect_jodi
from backend.collectors.portwatch import collect_portwatch
from backend.collectors.scheduler import start_scheduler, stop_scheduler
from backend.database import init_db
from backend.routes import alerts, health, ports, prices, sentiment, vessels, voyages, weather
from backend.routes import auth as auth_routes
from backend.routes import briefing as briefing_routes
from backend.routes import jodi as jodi_routes
from backend.routes import portwatch as portwatch_routes
from backend.routes import settings as settings_routes
from backend.routes import signals as signals_routes
from backend.routes import thermal as thermal_routes
from backend.routes import waitlist as waitlist_routes
from backend.routes import webhooks as webhooks_routes
from backend.signals.sentiment_scorer import compute_sentiment_score

logger = logging.getLogger(__name__)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Phase 1: DB + Scheduler (no I/O, instant)
    init_db()
    start_scheduler()
    logger.info("Startup: DB initialized, scheduler started")

    # Phase 2: AIS connections (after 2s to let DB settle)
    await asyncio.sleep(2)
    start_aisstream()
    start_aishub()
    logger.info("Startup: AIS connections started")

    # Phase 3: Background data collection (staggered, non-blocking)
    await asyncio.sleep(3)
    asyncio.create_task(collect_portwatch())
    asyncio.create_task(collect_jodi())
    logger.info("Startup: PortWatch + JODI collection started (background)")

    await asyncio.sleep(3)
    asyncio.create_task(collect_gdelt_volume())
    asyncio.create_task(collect_gdelt_sentiment())
    asyncio.create_task(compute_sentiment_score())
    asyncio.create_task(collect_finnhub_news())
    logger.info("Startup: GDELT + Finnhub + Sentiment started (background)")

    await asyncio.sleep(3)
    asyncio.create_task(backfill_geofence_events())
    logger.info("Startup: Geofence backfill started (background)")

    # FRED backfill (one-time: extends WTI/Brent back to 2019)
    await asyncio.sleep(2)
    from backend.collectors.fleet_summary import create_daily_fleet_summary
    from backend.collectors.fred_backfill import backfill_fred

    db_session = __import__("backend.database", fromlist=["SessionLocal"]).SessionLocal()
    try:
        await backfill_fred(db_session)
        logger.info("Startup: FRED backfill complete")
    except Exception as e:
        logger.warning(f"Startup: FRED backfill failed: {e}")
    finally:
        db_session.close()

    # Initial fleet summary for today
    asyncio.create_task(create_daily_fleet_summary())
    logger.info("Startup: Fleet summary scheduled")

    logger.info("Startup complete")
    yield
    stop_aishub()
    stop_aisstream()
    stop_scheduler()


app = FastAPI(
    title="OBSYD",
    description=(
        "Open-Source Energy Market Intelligence. "
        "Physical oil flows, energy inventories, macro signals, "
        "and geopolitical sentiment in one API."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",  # Vite dev server
        "http://localhost:4173",  # Vite preview
        "https://obsyd.dev",  # Production
        "https://www.obsyd.dev",  # Production www
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(prices.router)
app.include_router(vessels.router)
app.include_router(alerts.router)
app.include_router(ports.router)
app.include_router(weather.router)
app.include_router(sentiment.router)
app.include_router(jodi_routes.router)
app.include_router(thermal_routes.router)
app.include_router(portwatch_routes.router)
app.include_router(signals_routes.router)
app.include_router(settings_routes.router)
app.include_router(briefing_routes.router)
app.include_router(waitlist_routes.router)
app.include_router(auth_routes.router)
app.include_router(webhooks_routes.router)
app.include_router(voyages.router)
