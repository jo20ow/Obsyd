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
from datetime import UTC, datetime, timedelta
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
MIN_FREE_GB = 2.0     # a full disk on this VPS killed dockerd, Caddy, and both sites for 2.5 days


def _wal_mb() -> float:
    from backend.config import settings

    url = settings.database_url
    if not url.startswith("sqlite"):
        return 0.0
    wal = Path(url.split("///")[-1] + "-wal")
    return wal.stat().st_size / 1e6 if wal.exists() else 0.0


#: power_hourly is 28.5 MILLION rows, indexed on (series_id, zone_id, ts_utc). Every query here
#: resolves the ids FIRST and filters on a ts RANGE — never `strftime(...)` or a join on
#: series_dim.key over the whole table. The first cut of this script did exactly that (a DISTINCT
#: strftime to list a zone's months) and SQLite spilled a multi-gigabyte temp B-tree for it: the
#: VPS went from 92% to 95% full while the script was merely READING. Same trap as the 36s → 1.6s
#: lesson on the border layer, with a disk-alarm attached.


def _series_ids(db) -> tuple[int | None, dict[int, str]]:
    """(load series id, {gen series id: psrType}) — resolved once, used in every range scan."""
    rows = db.execute(text("SELECT id, key FROM series_dim WHERE key = 'load.actual' OR key LIKE 'gen.%'")).all()
    load_id = next((i for i, k in rows if k == "load.actual"), None)
    gen_ids = {i: k.split(".", 1)[1] for i, k in rows if k.startswith("gen.")}
    return load_id, gen_ids


def _zone_id(db, zone: str) -> int | None:
    return db.execute(text("SELECT id FROM zone_dim WHERE key = :z"), {"z": zone}).scalar()


def _hours_by_day(db, zid: int, load_id: int | None, gen_ids: dict[int, str],
                  start_ts: int, end_ts: int) -> tuple[dict, dict]:
    """{day: {hour: mw}} for load, {day: {psr: {hour: mw}}} for generation — one indexed range scan."""
    ids = ([load_id] if load_id is not None else []) + list(gen_ids)
    if not ids:
        return {}, {}
    rows = db.execute(text(f"""
        SELECT series_id, ts_utc, value
          FROM power_hourly
         WHERE zone_id = :zid
           AND series_id IN ({','.join(str(i) for i in ids)})
           AND ts_utc >= :a AND ts_utc < :b
    """), {"zid": zid, "a": start_ts, "b": end_ts}).all()

    load: dict[str, dict[int, float]] = defaultdict(dict)
    gen: dict[str, dict[str, dict[int, float]]] = defaultdict(lambda: defaultdict(dict))
    for sid, ts, value in rows:
        t = datetime.fromtimestamp(ts, UTC)
        day = t.strftime("%Y-%m-%d")
        if sid == load_id:
            load[day][t.hour] = value
        else:
            gen[day][gen_ids[sid]][t.hour] = value
    return load, gen


def _month_windows(db, zid: int) -> list[tuple[str, int, int]]:
    """[(YYYY-MM, start_ts, end_ts)] spanning the zone's record — from MIN/MAX, not a table scan."""
    row = db.execute(text(
        "SELECT MIN(ts_utc), MAX(ts_utc) FROM power_hourly WHERE zone_id = :zid"
    ), {"zid": zid}).one()
    if row[0] is None:
        return []
    first = datetime.fromtimestamp(row[0], UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    last = datetime.fromtimestamp(row[1], UTC)

    windows: list[tuple[str, int, int]] = []
    cur = first
    while cur <= last:
        nxt = (cur.replace(day=28) + timedelta(days=4)).replace(day=1)
        windows.append((cur.strftime("%Y-%m"), int(cur.timestamp()), int(nxt.timestamp())))
        cur = nxt
    return windows


def _free_gb() -> float:
    import shutil

    return shutil.disk_usage("/").free / 1e9


def rederive(zones: list[str], *, dry_run: bool = False) -> None:
    from backend.migrations import run_migrations

    run_migrations()   # idempotent; the hour columns are what this script fills
    db = SessionLocal()
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    worst: list[tuple[float, str, str, float, float]] = []
    n_days = n_deleted = n_months = 0

    load_id, gen_ids = _series_ids(db)

    try:
        for zone in zones:
            zid = _zone_id(db, zone)
            if zid is None:
                continue
            for month, start_ts, end_ts in _month_windows(db, zid):
                # This VPS has been taken down by a full disk before, and the previous incident
                # started with a maintenance script exactly like this one.
                if _free_gb() < MIN_FREE_GB:
                    logger.error("STOP: only %.1f GB free — refusing to write further", _free_gb())
                    if not dry_run:
                        db.execute(text("PRAGMA wal_checkpoint(TRUNCATE)"))
                        db.commit()
                    return

                load_by_day, gen_by_day = _hours_by_day(db, zid, load_id, gen_ids, start_ts, end_ts)
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
                    logger.info("… %s %s · %d days · WAL %.0f MB · %.1f GB free",
                                zone, month, n_days, _wal_mb(), _free_gb())

        if not dry_run:
            db.execute(text("PRAGMA wal_checkpoint(TRUNCATE)"))
            db.commit()
    finally:
        db.close()

    worst.sort(reverse=True)
    logger.info("%s: %d days re-derived, %d unfinished days dropped · %.1f GB free",
                "DRY RUN" if dry_run else "done", n_days, n_deleted, _free_gb())
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
