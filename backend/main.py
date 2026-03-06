"""
OBSYD - Open-Source Energy Market Intelligence
FastAPI application entry point.
"""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.database import init_db
from backend.collectors.scheduler import start_scheduler, stop_scheduler
from backend.collectors.aisstream import start_aisstream, stop_aisstream
from backend.collectors.aishub import start_aishub, stop_aishub
from backend.collectors.portwatch import collect_portwatch
from backend.collectors.gdelt import collect_gdelt_volume, collect_gdelt_sentiment
from backend.collectors.geofence_aggregator import backfill_geofence_events
from backend.signals.sentiment_scorer import compute_sentiment_score
from backend.collectors.jodi import collect_jodi
from backend.routes import health, prices, vessels, alerts, ports, weather, sentiment
from backend.routes import jodi as jodi_routes
from backend.routes import thermal as thermal_routes
from backend.routes import portwatch as portwatch_routes
from backend.routes import signals as signals_routes
from backend.routes import settings as settings_routes

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
    logger.info("Startup: GDELT + Sentiment started (background)")

    await asyncio.sleep(3)
    asyncio.create_task(backfill_geofence_events())
    logger.info("Startup: Geofence backfill started (background)")

    # FIRMS and NOAA disabled — 0 data returned since deployment
    # Re-enable when investigated and confirmed working
    logger.info("Startup: FIRMS and NOAA collectors DISABLED (0 data since deployment)")

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
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
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
