"""One-command idempotent backfill for the EU gas balance data layer.

    python -m backend.scripts.gas_backfill                       # 2023-01-01 → today, all sources
    python -m backend.scripts.gas_backfill --start 2025-01-01 --sources entsog
    python -m backend.scripts.gas_backfill --overwrite           # re-fetch (provisional → confirmed)

Order: sync point registry → ENTSOG flows → AGSI storage → ALSI LNG, batched
monthly with retry/backoff. Every write is an upsert and every raw payload is
disk-cached, so a crashed run resumes from cache for free.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import date, datetime

from backend.database import SessionLocal
from backend.gas.demand import compute_demand_model
from backend.gas.entsoe import ingest_power_burn
from backend.gas.entsog import ingest_flows, sync_points
from backend.gas.gie import daterange, ingest_lng, ingest_storage
from backend.gas.weather import ingest_weather

logger = logging.getLogger("gas_backfill")

BACKFILL_START = date(2023, 1, 1)
# entsoe skips gracefully if no token is configured, so it's safe in the default
# set. "demand" runs once after the loop (it calibrates over the whole period).
ALL_SOURCES = ("entsog", "agsi", "alsi", "entsoe", "weather", "demand")


async def _with_retry(coro_factory, label: str, attempts: int = 3, base: float = 1.0):
    """Retry a network step with exponential backoff (cached days are skipped
    on retry, so this resumes cheaply rather than re-fetching everything)."""
    import httpx

    for i in range(attempts):
        try:
            return await coro_factory()
        except (httpx.HTTPError, OSError) as exc:
            if i == attempts - 1:
                logger.error("%s failed after %d attempts: %s", label, attempts, exc)
                raise
            wait = base * (4**i)
            logger.warning("%s error (%s); retrying in %.0fs", label, exc, wait)
            await asyncio.sleep(wait)


def _month_windows(start: date, end: date) -> list[tuple[date, date]]:
    windows, cur = [], start
    while cur <= end:
        if cur.month == 12:
            nxt = date(cur.year + 1, 1, 1)
        else:
            nxt = date(cur.year, cur.month + 1, 1)
        windows.append((cur, min(end, date.fromordinal(nxt.toordinal() - 1))))
        cur = nxt
    return windows


async def run_backfill(db, start: date, end: date, sources: set[str], overwrite: bool) -> None:
    if "entsog" in sources:
        await _with_retry(lambda: sync_points(db, overwrite=overwrite), "sync_points")

    for m_start, m_end in _month_windows(start, end):
        days = daterange(m_start, m_end)
        tag = f"{m_start:%Y-%m}"
        if "entsog" in sources:
            await _with_retry(lambda d=days: ingest_flows(db, d, reference=end, overwrite=overwrite), f"entsog {tag}")
        if "agsi" in sources:
            await _with_retry(lambda d=days: ingest_storage(db, d, overwrite=overwrite), f"agsi {tag}")
        if "alsi" in sources:
            await _with_retry(lambda d=days: ingest_lng(db, d, overwrite=overwrite), f"alsi {tag}")
        if "entsoe" in sources:
            await _with_retry(lambda d=days: ingest_power_burn(db, d, overwrite=overwrite), f"entsoe {tag}")
        if "weather" in sources:
            await _with_retry(lambda d=days: ingest_weather(db, d, overwrite=overwrite), f"weather {tag}")
        logger.info("backfill: %s done", tag)

    # Demand model calibrates over the whole period, so it runs once at the end.
    if "demand" in sources:
        result = await compute_demand_model(db)
        logger.info("backfill: demand model %s", result)


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser(description="EU gas balance backfill")
    p.add_argument("--start", default=BACKFILL_START.isoformat())
    p.add_argument("--end", default=date.today().isoformat())
    p.add_argument("--sources", default=",".join(ALL_SOURCES), help="comma list: entsog,agsi,alsi,entsoe,weather,demand")
    p.add_argument("--overwrite", action="store_true", help="re-fetch cached days (provisional→confirmed)")
    args = p.parse_args(argv[1:])

    start = datetime.strptime(args.start, "%Y-%m-%d").date()
    end = datetime.strptime(args.end, "%Y-%m-%d").date()
    sources = {s.strip() for s in args.sources.split(",") if s.strip()}

    db = SessionLocal()
    try:
        asyncio.run(run_backfill(db, start, end, sources, args.overwrite))
    finally:
        db.close()
    logger.info("backfill complete: %s → %s, sources=%s", start, end, sorted(sources))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
