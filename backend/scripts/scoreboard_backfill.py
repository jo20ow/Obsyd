"""One-time backfill of forecast_score_daily from the canonical hourly store. No API calls.

Scores ENTSO-E's published day-ahead forecasts against the published actuals,
per (zone, series, day), using the SAME engine the nightly job runs
(backend/power/forecast_score.py) — backfill and nightly can never disagree.
Posture B: this grades the TSOs' own forecasts; OBSYD forecasts nothing.

Each zone is one pass: 9 indexed range scans over power_hourly (4 forecast +
5 actual series, resolved by dim id) scored day by day, one commit per zone —
no strftime table scans, no per-day queries (the rederive_daily lesson). Zones
with no data in the range write nothing; the engine skips quietly, so the same
command works on a 3-zone dev DB and the 37-zone prod DB. Idempotent: a rerun
replaces the rows.

    python -m backend.scripts.scoreboard_backfill --start 2024-01-01
    python -m backend.scripts.scoreboard_backfill --start 2024-01-01 --end 2026-07-31 --zones DE_LU FR
"""

from __future__ import annotations

import argparse
import logging
from datetime import datetime, timedelta, timezone

from backend.database import SessionLocal
from backend.power.forecast_score import compute_and_store_range
from backend.power.zones import POWER_ZONES

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger("scoreboard_backfill")


def backfill(start: str, end: str, zones: list[str]) -> None:
    db = SessionLocal()
    written = removed = 0
    try:
        for zone in zones:
            try:
                result = compute_and_store_range(db, zone, start, end)
            except Exception as exc:
                db.rollback()
                logger.error("%s FAILED: %s", zone, exc)
                continue
            written += result["written"]
            removed += result["removed"]
            logger.info("%s: %d rows written, %d retracted", zone, result["written"], result["removed"])
    finally:
        db.close()
    logger.info("done: %d rows written, %d retracted over %d zones (%s..%s)",
                written, removed, len(zones), start, end)


if __name__ == "__main__":
    # Default end = yesterday UTC: a day that is not over is not a day (rederive_daily's rule),
    # and the nightly job owns the trailing window from here on anyway.
    yesterday = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", required=True, help="first UTC day, YYYY-MM-DD")
    ap.add_argument("--end", default=yesterday, help="last UTC day inclusive (default: yesterday UTC)")
    ap.add_argument("--zones", nargs="*", default=list(POWER_ZONES))
    args = ap.parse_args()
    backfill(args.start, args.end, args.zones)
