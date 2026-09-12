"""Backfill: derive the co2.intensity.* hourly series from the gen.* mix already
in the canonical hourly store — DB-only, no ENTSO-E refetch (the mix is the
source; methodology + factor table in backend/power/co2.py).

Idempotent by construction (upsert + full-window recompute): re-running any
window simply rebuilds it from the current mix, which is also how upstream
restatements older than the scheduler's 10-day window get absorbed — re-run
the affected span.

Usage (on the VPS, as the obsyd user):
    python -m backend.scripts.co2_backfill --dry-run
    python -m backend.scripts.co2_backfill --start 2015-01-01
    python -m backend.scripts.co2_backfill --start 2024-01-01 --zones DE_LU,FR
"""
from __future__ import annotations

import argparse
import logging
from datetime import UTC, datetime, timedelta

from backend.database import SessionLocal
from backend.power.co2 import compute_and_store_range
from backend.power.zones import POWER_ZONES

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("co2_backfill")


def _ts(day: str) -> int:
    return int(datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=UTC).timestamp())


def run(start: str, end: str | None, zones: list[str], *, dry_run: bool) -> int:
    start_ts = _ts(start)
    # End is exclusive; default = tomorrow, so today's already-published hours land too.
    end_ts = _ts(end) if end else int(
        (datetime.now(UTC) + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    )
    total = 0
    db = SessionLocal()
    try:
        for zone in zones:
            if dry_run:
                logger.info("%s: would recompute %s → %s", zone, start, end or "tomorrow")
                continue
            written = compute_and_store_range(db, zone, start_ts, end_ts)
            total += written
            logger.info("%s: %d hours written", zone, written)
    finally:
        db.close()
    logger.info("co2 backfill done: %d hours across %d zones", total, len(zones))
    return total


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", default="2015-01-01", help="first day (UTC), default 2015-01-01")
    ap.add_argument("--end", default=None, help="exclusive end day (UTC), default: tomorrow")
    ap.add_argument("--zones", default=None, help="comma list, default: all enabled zones")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    zones = [z.strip() for z in args.zones.split(",")] if args.zones else list(POWER_ZONES)
    unknown = [z for z in zones if z not in POWER_ZONES]
    if unknown:
        raise SystemExit(f"unknown zones: {unknown} (enabled: {POWER_ZONES})")
    run(args.start, args.end, zones, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
