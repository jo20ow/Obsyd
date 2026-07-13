"""Re-read three years of gas payloads we already own, and keep the 90% we threw away.

`gie._eu_row` kept one row out of a tree: the EU aggregate. The 23 countries under it —
including Ukraine, whose 77 TWh of storage is arguably the most trade-relevant number in the
whole file — were parsed, discarded, and forgotten, every day since 2023-01-01. The payloads
themselves were never lost: they are on disk in the raw cache, 164 MB of AGSI and 54 MB of
ALSI. So this is not a backfill. It is a re-read.

CACHE-ONLY BY DESIGN: ZERO API CALLS. A day whose payload is missing from the cache is
COUNTED and reported, never fetched. It keeps no country rows until someone backfills that
day — which is honest and visible, and infinitely preferable to a script that quietly hammers
GIE for three years of history it was told it already had.

    python -m backend.scripts.backfill_gie_countries --dry-run
    python -m backend.scripts.backfill_gie_countries --start 2023-01-01
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime, timedelta

from sqlalchemy import text

from backend.database import SessionLocal, init_db
from backend.gas import raw_cache
from backend.gas.gie import upsert_lng_countries, upsert_storage_countries
from backend.observability import install_log_redaction

logger = logging.getLogger(__name__)

#: Checkpoint the WAL every N days. This is load-bearing, not tidiness: the API process holds
#: long-lived readers, so SQLite cannot checkpoint on its own while a long batch job writes,
#: and an unbounded WAL is exactly how the 2026-07-07 disk incident began.
CHECKPOINT_EVERY = 50

SOURCES = (
    ("agsi", upsert_storage_countries),
    ("alsi", upsert_lng_countries),
)


def _days(start: date, end: date) -> list[date]:
    out, d = [], start
    while d <= end:
        out.append(d)
        d += timedelta(days=1)
    return out


def run(db, start: date, end: date, *, dry_run: bool = False) -> dict:
    total = {"days": 0, "missing_cache": 0, "storage_rows": 0, "lng_rows": 0}
    written_days = 0

    for day in _days(start, end):
        iso = day.isoformat()
        total["days"] += 1
        touched = False

        for source, upsert in SOURCES:
            payload = raw_cache.read_cached(source, f"{source}_{iso}", day)
            if payload is None:
                total["missing_cache"] += 1
                continue  # COUNTED, never fetched
            if dry_run:
                touched = True
                continue
            n = upsert(db, iso, payload)
            total["storage_rows" if source == "agsi" else "lng_rows"] += n
            touched = True

        if touched and not dry_run:
            db.commit()
            written_days += 1
            if written_days % CHECKPOINT_EVERY == 0:
                db.execute(text("PRAGMA wal_checkpoint(TRUNCATE)"))
                logger.info("gie countries: %s — %d days re-read (WAL checkpointed)",
                            iso, written_days)

    if not dry_run:
        db.commit()
        db.execute(text("PRAGMA wal_checkpoint(TRUNCATE)"))
    return total


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    install_log_redaction()  # ENTSO-E puts its key in the query string; httpx logs the URL
    p = argparse.ArgumentParser(description="Re-read cached AGSI/ALSI payloads into the country tables")
    p.add_argument("--start", default="2023-01-01")
    p.add_argument("--end", default=date.today().isoformat())
    p.add_argument("--dry-run", action="store_true", help="report coverage, write nothing")
    args = p.parse_args(argv[1:])

    start = datetime.strptime(args.start, "%Y-%m-%d").date()
    end = datetime.strptime(args.end, "%Y-%m-%d").date()

    # The country tables are new; a script run outside the app never saw the startup that
    # creates them. create_all is idempotent and touches nothing that already exists.
    init_db()

    db = SessionLocal()
    try:
        result = run(db, start, end, dry_run=args.dry_run)
    finally:
        db.close()

    logger.info("backfill_gie_countries complete: %s", result)
    if result["missing_cache"]:
        logger.warning(
            "%d source-days had no cached payload — those days have no country rows and were "
            "NOT fetched. Run the daily ingest for them if you want them.",
            result["missing_cache"],
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
