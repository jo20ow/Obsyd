"""Backfill GB (Elexon/BMRS) day by day — MID price, demand, fuel mix, system
prices, plus the derived residual/daily rows (backend/power/elexon.py).

Kept OUT of power_backfill.py on purpose: that script is the ENTSO-E family
(every source keys on an EIC), and folding a different API's pacing and error
shapes into its loop is how collectors get entangled. Same reasoning that keeps
units_gen out of ALL_SOURCES.

Elexon publishes no hard rate limit; a polite fixed pause keeps a full-history
run at four requests per day-of-data, sequential. History depth: Insights
serves most datasets back to ~2015 — probe with a --start 2019 run first (the
uniform-2019 doctrine, OPS.md §4) and extend if the answers are non-empty.

Usage (on the VPS, as the obsyd user):
    python -m backend.scripts.elexon_backfill --start 2019-01-01 --dry-run
    python -m backend.scripts.elexon_backfill --start 2019-01-01
"""
from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import UTC, date, datetime, timedelta

from backend.database import SessionLocal
from backend.power.elexon import ingest_elexon

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("elexon_backfill")

PAUSE_S = 0.3  # between day-batches; ~4 requests each


async def run(start: str, end: str | None, *, overwrite: bool, dry_run: bool) -> None:
    d = date.fromisoformat(start)
    stop = date.fromisoformat(end) if end else datetime.now(UTC).date() + timedelta(days=1)
    db = SessionLocal()
    total_hours = 0
    try:
        while d < stop:
            # Month-sized batches keep the log readable and the commits frequent.
            batch = []
            month = d.month
            while d < stop and d.month == month:
                batch.append(d.isoformat())
                d += timedelta(days=1)
            if dry_run:
                logger.info("would ingest %s … %s (%d days)", batch[0], batch[-1], len(batch))
                continue
            result = await ingest_elexon(db, batch, overwrite=overwrite)
            total_hours += result["hours"]
            logger.info("%s: %s", batch[0][:7], result)
            await asyncio.sleep(PAUSE_S)
    finally:
        db.close()
    logger.info("elexon backfill done: %d hours", total_hours)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", default="2019-01-01")
    ap.add_argument("--end", default=None, help="exclusive; default tomorrow")
    ap.add_argument("--overwrite", action="store_true",
                    help="refetch past the raw cache (stale-frontier repair)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    asyncio.run(run(args.start, args.end, overwrite=args.overwrite, dry_run=args.dry_run))


if __name__ == "__main__":
    main()
