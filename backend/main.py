"""
OBSYD - Open-Source Energy Market Intelligence
FastAPI application entry point.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.database import init_db, SessionLocal
from backend.collectors.scheduler import start_scheduler, stop_scheduler
from backend.collectors.aisstream import start_aisstream, stop_aisstream
from backend.collectors.aishub import start_aishub, stop_aishub
from backend.collectors.portwatch import collect_portwatch
from backend.collectors.noaa import collect_noaa_alerts
from backend.collectors.gdelt import collect_gdelt_volume, collect_gdelt_sentiment
from backend.collectors.jodi import collect_jodi
from backend.routes import health, prices, vessels, alerts, ports, weather, sentiment
from backend.routes import jodi as jodi_routes

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    # TODO: Remove this one-time cleanup after first restart
    from backend.models.alerts import Alert
    _db = SessionLocal()
    deleted = _db.query(Alert).delete()
    _db.commit()
    _db.close()
    logging.getLogger(__name__).info(f"Startup cleanup: deleted {deleted} old alerts")
    start_scheduler()
    start_aisstream()
    start_aishub()
    # Fetch PortWatch data on startup (runs in background, non-blocking)
    import asyncio
    asyncio.create_task(collect_portwatch())
    asyncio.create_task(collect_noaa_alerts())
    asyncio.create_task(collect_gdelt_volume())
    asyncio.create_task(collect_gdelt_sentiment())
    asyncio.create_task(collect_jodi())
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
