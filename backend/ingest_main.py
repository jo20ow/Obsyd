"""Entrypoint for the OBSYD ingest role — the sole steady-state DB writer.

Runs the APScheduler collectors (and the startup power refresh) with NO HTTP server,
so the API process (obsyd.service, OBSYD_ROLE=api) can scale uvicorn workers without
double-firing crons. Used by deploy/obsyd-ingest.service. Same venv + same SQLite file
as the API; WAL gives many readers + this one writer.

Inert until the two-unit split is activated on the VPS (default OBSYD_ROLE=all keeps
the single-process behavior in obsyd.service). See backend/config.py::obsyd_role.
"""
from __future__ import annotations

import asyncio
import logging
import signal

from backend.collectors.scheduler import _run_power_daily, start_scheduler, stop_scheduler
from backend.database import init_db
from backend.migrations import run_migrations

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("obsyd.ingest")


async def main() -> None:
    init_db()
    run_migrations()
    start_scheduler()
    logger.info("ingest: DB ready, scheduler started (sole writer)")

    # Startup power refresh so a restart doesn't wait for the 22:30 cron.
    await asyncio.sleep(2)
    try:
        await _run_power_daily()
        logger.info("ingest: startup power refresh done")
    except Exception as exc:  # noqa: BLE001 — never let a startup fetch kill the writer
        logger.error("ingest: startup power refresh failed: %s", exc)

    # Run until SIGINT/SIGTERM (systemd stop).
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # pragma: no cover — non-Unix
            pass
    await stop.wait()
    stop_scheduler()
    logger.info("ingest: scheduler stopped, exiting")


if __name__ == "__main__":
    asyncio.run(main())
