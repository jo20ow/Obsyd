"""
OBSYD - Open-Source Energy Market Intelligence
FastAPI application entry point.
"""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.collectors.scheduler import start_scheduler, stop_scheduler
from backend.database import init_db
from backend.migrations import run_migrations
from backend.observability import TraceIDMiddleware, setup_logging
from backend.routes import alert_rules as alert_rules_routes
from backend.routes import alerts, api_v1, health, ports, prices, sentiment, vessels, voyages, weather
from backend.routes import analytics as analytics_routes
from backend.routes import atlas as atlas_routes
from backend.routes import auth as auth_routes
from backend.routes import briefing as briefing_routes
from backend.routes import crypto as crypto_routes
from backend.routes import econ as econ_routes
from backend.routes import email as email_routes
from backend.routes import embed as embed_routes
from backend.routes import filings as filings_routes
from backend.routes import gas as gas_routes
from backend.routes import jodi as jodi_routes
from backend.routes import metals as metals_routes
from backend.routes import news as news_routes
from backend.routes import portwatch as portwatch_routes
from backend.routes import power as power_routes
from backend.routes import rates as rates_routes
from backend.routes import settings as settings_routes
from backend.routes import signals as signals_routes
from backend.routes import situation as situation_routes
from backend.routes import thermal as thermal_routes
from backend.routes import validation as validation_routes
from backend.routes import waitlist as waitlist_routes
from backend.routes import watchlist as watchlist_routes
from backend.routes import webhooks as webhooks_routes

setup_logging()
logger = logging.getLogger(__name__)

# Strong references to fire-and-forget tasks (asyncio only keeps weak ones).
_background_tasks: set = set()


def _create_task_logged(coro, name: str) -> asyncio.Task:
    """Spawn a background task whose crash is logged instead of silently dropped."""
    task = asyncio.create_task(coro, name=name)
    _background_tasks.add(task)

    def _on_done(t: asyncio.Task) -> None:
        _background_tasks.discard(t)
        if t.cancelled():
            return
        exc = t.exception()
        if exc:
            logger.error("Background task %r failed: %s", name, exc, exc_info=exc)

    task.add_done_callback(_on_done)
    return task


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Phase 1: DB + Scheduler (no I/O, instant)
    init_db()
    run_migrations()

    # Reject insecure default secrets
    from backend.config import settings

    for name in ("secret_key", "jwt_secret"):
        val = getattr(settings, name)
        raw = val.get_secret_value() if hasattr(val, "get_secret_value") else val
        if "change-me" in raw:
            if settings.environment == "production":
                raise RuntimeError(
                    f"SECURITY: {name} uses the default value — refusing to start. "
                    "Set it in .env (e.g. `openssl rand -hex 32`)."
                )
            logger.warning("SECURITY: %s uses default value — set it in .env before production!", name)

    # Role gating (OBSYD_ROLE): only the ingest/all roles run the scheduler and act as
    # the DB writer. The api role serves requests only, so its workers can scale without
    # double-firing crons. Default "all" = the current single-process behavior.
    from backend.collectors.scheduler import scheduler_role_enabled

    ingest_enabled = scheduler_role_enabled(settings.obsyd_role)
    if ingest_enabled:
        start_scheduler()
        logger.info("Startup: DB initialized, scheduler started (role=%s)", settings.obsyd_role)
    else:
        logger.info("Startup: DB initialized, scheduler DISABLED (role=api)")

    # Phase 2: Power desk startup refresh (staggered, non-blocking) — ingest role only.
    # REFOCUS 2026-07-03 — Obsyd is the European electricity+gas desk. The non-power
    # verticals (AIS/oil, portwatch, gdelt/sentiment, jodi, crack/equities, analytics)
    # were split to the sibling project; their startup runs + AIS websockets are gone.
    # Pull day-ahead/grid/flows/forecasts to the published frontier on startup so a
    # restart doesn't wait for the 22:30 cron. Gas runs via its daily cron.
    if ingest_enabled:
        await asyncio.sleep(2)
        from backend.collectors.scheduler import _run_power_daily

        _create_task_logged(_run_power_daily(), "power_daily_startup")
        logger.info("Startup: power desk refresh started (background)")

    # Daily Briefing Email status
    if settings.resend_api_key:
        logger.info("Daily Briefing Email: enabled (RESEND_API_KEY configured)")
    else:
        logger.info("Daily Briefing Email: disabled (no RESEND_API_KEY)")

    logger.info("Startup complete")
    yield
    if ingest_enabled:
        stop_scheduler()


app = FastAPI(
    title="OBSYD — European electricity data API",
    description=(
        "The European electricity desk. Day-ahead prices, "
        "load & residual load, generation mix, cross-border flows, forecasts and the "
        "gas that fuels them, from the official record (ENTSO-E, Energy-Charts, GIE). "
        "Programmatic access: GET /api/v1/series (JSON/CSV). Free, descriptive, AGPL-3.0."
    ),
    version="0.3.0",
    lifespan=lifespan,
    # Serve interactive docs under /api/* so the existing reverse-proxy exposes them
    # publicly without a Caddy change (the proxy already forwards /api/*).
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
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

# Trace IDs: generated per request, exposed via X-Trace-Id response header
# and embedded into every log line via the contextvar in backend.observability.
app.add_middleware(TraceIDMiddleware)

# Refocus 2026-07-03: the PRODUCT is the European electricity+gas desk (only power/
# gas tabs in the frontend; non-power scheduler jobs + startups are off). The
# non-power routers stay registered but DORMANT — nothing in the product calls them
# and no fresh data is collected — until they are physically extracted to the
# sibling project (Phase 2), together with their code and tests.
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
app.include_router(alert_rules_routes.router)
app.include_router(email_routes.router)
app.include_router(voyages.router)
app.include_router(analytics_routes.router)
app.include_router(validation_routes.router)
app.include_router(crypto_routes.router)
app.include_router(rates_routes.router)
app.include_router(filings_routes.router)
app.include_router(econ_routes.router)
app.include_router(news_routes.router)
app.include_router(gas_routes.router)
app.include_router(metals_routes.router)
app.include_router(atlas_routes.router)
app.include_router(power_routes.router)
app.include_router(api_v1.router)  # public data API v1 (/api/v1/series, catalog, meta)
app.include_router(embed_routes.router)  # /api/v1/badge/*.svg — embeddable status badges
app.include_router(situation_routes.router)
app.include_router(watchlist_routes.router)
