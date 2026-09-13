"""Backfill the derived-statistics series (backend/power/derived_stats.py) over
full history — DB-only, no upstream refetch: both series mirror sources already
in the store (PowerPriceDaily; the capture engine over power_hourly).

Usage (on the VPS, as the obsyd user):
    python -m backend.scripts.derived_stats_backfill --dry-run
    python -m backend.scripts.derived_stats_backfill
"""
from __future__ import annotations

import argparse
import logging

from backend.database import SessionLocal
from backend.power.derived_stats import (
    store_capture,
    store_da_imbalance_spread,
    store_negative_hours,
    store_tb_spreads,
)
from backend.power.zones import POWER_ZONES

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("derived_stats_backfill")

#: Far past every zone's price history — compute_capture cuts to real months.
CAPTURE_MONTHS = 200


def run(*, dry_run: bool) -> None:
    db = SessionLocal()
    total_neg = total_cap = 0
    try:
        for zone in POWER_ZONES:
            if dry_run:
                logger.info("%s: would mirror negative hours (full) + capture (%d months)",
                            zone, CAPTURE_MONTHS)
                continue
            neg = store_negative_hours(db, zone)
            cap = store_capture(db, zone, months=CAPTURE_MONTHS)
            spr = store_da_imbalance_spread(db, zone)
            tb = store_tb_spreads(db, zone)
            total_neg += neg
            total_cap += cap + spr + tb
            logger.info("%s: %d negative-hour, %d capture, %d spread, %d TB points",
                        zone, neg, cap, spr, tb)
    finally:
        db.close()
    logger.info("derived stats backfill done: %d + %d points", total_neg, total_cap)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    run(dry_run=ap.parse_args().dry_run)


if __name__ == "__main__":
    main()
