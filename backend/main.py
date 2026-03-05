"""
OBSYD - Open-Source Energy Market Intelligence
FastAPI application entry point.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.database import init_db
from backend.collectors.scheduler import start_scheduler, stop_scheduler
from backend.routes import health, prices, vessels, alerts

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    start_scheduler()
    yield
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
