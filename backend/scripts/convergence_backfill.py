"""Backfill the per-border convergence series (backend/power/convergence.py)
over full price history — DB-only, no upstream refetch: the bands are pure
arithmetic on price.dayahead rows already in the store.

Walks year chunks from the earliest stored day-ahead hour (or --from) to
tomorrow. Idempotent (upsert; the records doctrine: a re-run restates from
current prices).

Usage (on the VPS, as the obsyd user):
    python -m backend.scripts.convergence_backfill --dry-run
    python -m backend.scripts.convergence_backfill
    python -m backend.scripts.convergence_backfill --from 2024-01-01
"""
from __future__ import annotations

import argparse
import logging
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func

from backend.database import SessionLocal
from backend.models.energy import PowerHourly, SeriesDim
from backend.power.convergence import store_convergence

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("convergence_backfill")


def _earliest_price_day(db) -> date | None:
    sid = db.query(SeriesDim.id).filter(SeriesDim.key == "price.dayahead").scalar()
    if sid is None:
        return None
    ts = db.query(func.min(PowerHourly.ts_utc)).filter(PowerHourly.series_id == sid).scalar()
    return datetime.fromtimestamp(ts, tz=timezone.utc).date() if ts else None


def run(*, start: date | None, dry_run: bool) -> None:
    db = SessionLocal()
    total = 0
    try:
        first = start or _earliest_price_day(db)
        if first is None:
            logger.info("no price.dayahead rows — nothing to do")
            return
        end = datetime.now(timezone.utc).date() + timedelta(days=1)
        chunk_start = first
        while chunk_start <= end:
            chunk_end = min(date(chunk_start.year, 12, 31), end)
            if dry_run:
                logger.info("would compute %s → %s", chunk_start, chunk_end)
            else:
                n = store_convergence(db, chunk_start, chunk_end)
                total += n
                logger.info("%s → %s: %d points", chunk_start, chunk_end, n)
            chunk_start = date(chunk_start.year + 1, 1, 1)
    finally:
        db.close()
    logger.info("convergence backfill done: %d points", total)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--from", dest="start", type=date.fromisoformat, default=None,
                    help="first day (default: earliest stored price.dayahead hour)")
    args = ap.parse_args()
    run(start=args.start, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
