"""Build the full bulk Parquet archive (backend/power/archive.py) — the
initial build, or a forced full rebuild after a deep-history backfill
restated old years (the nightly job only refreshes current + previous year).

Usage (on the VPS, as the obsyd user):
    python -m backend.scripts.archive_backfill --dry-run
    python -m backend.scripts.archive_backfill
    python -m backend.scripts.archive_backfill --force
"""
from __future__ import annotations

import argparse
import logging

from backend.database import SessionLocal
from backend.power.archive import ARCHIVE_ROOT, build_archive

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("archive_backfill")


def run(*, force: bool, dry_run: bool) -> None:
    db = SessionLocal()
    try:
        if dry_run:
            from sqlalchemy import func

            from backend.models.energy import PowerHourly, SeriesDim
            n_series = db.query(SeriesDim).count()
            oldest = db.query(func.min(PowerHourly.ts_utc)).scalar()
            logger.info("would build up to %d series × years since %s into %s (force=%s)",
                        n_series, oldest, ARCHIVE_ROOT, force)
            return
        out = build_archive(db, force=force)
        logger.info("archive backfill done: %s", out)
    finally:
        db.close()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--force", action="store_true",
                    help="rebuild every file, not just the hot window / missing ones")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    run(force=args.force, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
