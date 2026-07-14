"""Re-derive power_grid + power_gen_mix from the canonical hourly store. No API calls.

WHY
---
The daily tables were built by a second parse of the ENTSO-E XML, with `sum(points)/len(points)`
as the daily mean. That stored the current day's morning hours as a day, made every zone that
omits solar at night (PT sends 18 points, not 24) a third sunnier than it is, and counted revised
overlapping points twice — so the daily table and the hourly store disagreed about the same day.
backend/power/daily.py now owns the rule; this script applies it to the history that was written
under the old one.

Verified before writing this: `power_hourly` covers the daily history exactly — same first day,
same day count, for all 37 zones — so nothing is lost by deriving the days from it.

WHAT IT DOES, per zone-month:
  * rebuilds every finished day's PowerGrid row (means, and the hour counts behind them)
  * rebuilds its PowerGenMix rows from the same hour maps
  * deletes rows for days that are NOT OVER — a running total is not a daily mean

OPS
---
The last full grid backfill drove this VPS's disk from 70% to 90% with a 439 MB WAL, because the
API process holds long-lived readers and SQLite therefore cannot checkpoint. This writes far less
(it only rewrites existing rows), but it checkpoints every CHECKPOINT_EVERY zone-months anyway and
prints the WAL size as it goes. Run it with an eye on `df -h`.

    python -m backend.scripts.rederive_daily              # every zone, whole history
    python -m backend.scripts.rederive_daily --zones PT   # one zone
    python -m backend.scripts.rederive_daily --dry-run    # report the deltas, write nothing
"""

from __future__ import annotations

import argparse
import logging
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import text

from backend.database import SessionLocal
from backend.models.energy import PowerGenMix, PowerGrid
from backend.power.daily import daily_from_hours, days_to_derive
from backend.power.entsoe_grid import PSR_LABELS
from backend.power.zones import POWER_ZONES

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger("rederive_daily")

CHECKPOINT_EVERY = 20  # zone-months


def _wal_mb() -> float:
    from backend.config import settings

    url = settings.database_url
    if not url.startswith("sqlite"):
        return 0.0
    wal = Path(url.split("///")[-1] + "-wal")
    return wal.stat().st_size / 1e6 if wal.exists() else 0.0


def _hours_by_day(db, zone: str, month: str) -> tuple[dict, dict]:
    """{day: {hour: mw}} for load, and {day: {psr: {hour: mw}}} for generation — one query."""
    rows = db.execute(text("""
        SELECT s.key,
               strftime('%Y-%m-%d', h.ts_utc, 'unixepoch') AS day,
               CAST(strftime('%H', h.ts_utc, 'unixepoch') AS INTEGER) AS hour,
               h.value
          FROM power_hourly h
          JOIN series_dim s ON s.id = h.series_id
          JOIN zone_dim  z ON z.id = h.zone_id
         WHERE z.key = :zone
           AND (s.key = 'load.actual' OR s.key LIKE 'gen.%')
           AND strftime('%Y-%m', h.ts_utc, 'unixepoch') = :month
    """), {"zone": zone, "month": month}).all()

    load: dict[str, dict[int, float]] = defaultdict(dict)
    gen: dict[str, dict[str, dict[int, float]]] = defaultdict(lambda: defaultdict(dict))
    for key, day, hour, value in rows:
        if key == "load.actual":
            load[day][hour] = value
        else:
            gen[day][key.split(".", 1)[1]][hour] = value   # gen.B16 → B16
    return load, gen


def _months(db, zone: str) -> list[str]:
    return [
        m for (m,) in db.execute(text("""
            SELECT DISTINCT strftime('%Y-%m', h.ts_utc, 'unixepoch') AS m
              FROM power_hourly h JOIN zone_dim z ON z.id = h.zone_id
             WHERE z.key = :zone ORDER BY m
        """), {"zone": zone}).all()
    ]


def rederive(zones: list[str], *, dry_run: bool = False) -> None:
    from backend.migrations import run_migrations

    run_migrations()   # idempotent; the hour columns are what this script fills
    db = SessionLocal()
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    worst: list[tuple[float, str, str, float, float]] = []
    n_days = n_deleted = n_months = 0

    try:
        for zone in zones:
            for month in _months(db, zone):
                load_by_day, gen_by_day = _hours_by_day(db, zone, month)
                days = set(load_by_day) | set(gen_by_day)

                # A day that is not over is not a day — and the old code wrote one.
                for unfinished in sorted(d for d in days if d >= today):
                    if not dry_run:
                        db.query(PowerGrid).filter_by(zone=zone, date=unfinished).delete()
                        db.query(PowerGenMix).filter_by(zone=zone, date=unfinished).delete()
                    n_deleted += 1

                for day in days_to_derive(days):
                    row = daily_from_hours(load_by_day.get(day, {}), gen_by_day.get(day, {}))
                    existing = db.query(PowerGrid).filter_by(zone=zone, date=day).first()

                    # Track the biggest corrections so the run can be judged, not just trusted.
                    if existing and existing.solar_mw and row["solar_mw"]:
                        drift = abs(existing.solar_mw - row["solar_mw"]) / existing.solar_mw
                        worst.append((drift, zone, day, existing.solar_mw, row["solar_mw"]))

                    if not dry_run:
                        target = existing or PowerGrid(date=day, zone=zone)
                        target.load_mw = row["load_mw"]
                        target.wind_mw = row["wind_mw"]
                        target.solar_mw = row["solar_mw"]
                        target.residual_mw = row["residual_mw"]
                        target.load_hours = row["load_hours"]
                        target.gen_hours = row["gen_hours"]
                        if existing is None:
                            db.add(target)

                        for code, mean_mw in row["mix"].items():
                            label = PSR_LABELS.get(code, code)
                            mix_row = (
                                db.query(PowerGenMix)
                                .filter_by(zone=zone, date=day, psr_type=label)
                                .first()
                            )
                            if mix_row:
                                mix_row.gen_mw = mean_mw
                            else:
                                db.add(PowerGenMix(date=day, zone=zone,
                                                   psr_type=label, gen_mw=mean_mw))
                    n_days += 1

                if not dry_run:
                    db.commit()
                n_months += 1
                if n_months % CHECKPOINT_EVERY == 0:
                    if not dry_run:
                        db.execute(text("PRAGMA wal_checkpoint(TRUNCATE)"))
                        db.commit()
                    logger.info("… %s %s · %d days · WAL %.0f MB", zone, month, n_days, _wal_mb())

        if not dry_run:
            db.execute(text("PRAGMA wal_checkpoint(TRUNCATE)"))
            db.commit()
    finally:
        db.close()

    worst.sort(reverse=True)
    logger.info("%s: %d days re-derived, %d unfinished days dropped",
                "DRY RUN" if dry_run else "done", n_days, n_deleted)
    logger.info("Biggest solar corrections (old → new daily mean):")
    seen: set[str] = set()
    for drift, zone, day, old, new in worst:
        if zone in seen:
            continue
        seen.add(zone)
        logger.info("   %-16s %s  %8.0f → %8.0f MW  (%.0f%%)", zone, day, old, new, drift * 100)
        if len(seen) >= 10:
            break


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--zones", nargs="*", default=list(POWER_ZONES))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    rederive(args.zones, dry_run=args.dry_run)
