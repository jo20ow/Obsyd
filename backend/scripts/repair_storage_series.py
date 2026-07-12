"""Repair the storage series corrupted by the pre-2026-07-12 A75 parser.

    python -m backend.scripts.repair_storage_series --start 2021-01-01
    python -m backend.scripts.repair_storage_series --dry-run

ENTSO-E publishes storage technologies TWICE in one A75 document — generation
(inBiddingZone) and consumption (outBiddingZone). The parser keyed only on
psrType, so the two were averaged into one number that describes neither, and
the pumping half was counted as generation (inflating both the mix and the
generation total the coverage guard divides by load).

The parser is fixed; this repairs the HISTORY it wrote. A full grid re-parse
would do it too, but it rewrites every series for every zone-month (~30M row
upserts) — far too heavy for a VPS whose disk headroom is measured in single
gigabytes. This touches ONLY the storage rows: ~1M hourly points and ~43k daily
rows across the 22 zones that have pumped storage.

Cache-only by design: every A75 document is already on disk (raw_cache), so this
makes ZERO API calls. Zone-months whose document is missing are counted and
reported rather than fetched — they simply keep their old (wrong) storage rows
until someone backfills them, which is honest and visible.

The WAL is checkpointed every CHECKPOINT_EVERY zone-months: the API process
holds long-lived readers, so without this the write-ahead log grows unbounded
through a long batch job — which is exactly how a 2026-07-07-class disk incident
starts.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime

from sqlalchemy import text

from backend.database import SessionLocal
from backend.gas import raw_cache
from backend.models.energy import PowerGenMix
from backend.power.entsoe_grid import (
    PSR_LABELS,
    base_psr,
    is_consumption_key,
    parse_generation_by_type,
    parse_generation_hourly,
)
from backend.power.hourly_store import upsert_day_hours
from backend.power.zones import POWER_ZONES

logger = logging.getLogger("repair_storage_series")

CACHE_SOURCE = "entsoe_genmix"
CHECKPOINT_EVERY = 50  # zone-months


def _months(start: date, end: date) -> list[date]:
    out, cur = [], start.replace(day=1)
    while cur <= end:
        out.append(cur)
        cur = date(cur.year + 1, 1, 1) if cur.month == 12 else date(cur.year, cur.month + 1, 1)
    return out


def storage_psrs(day_map: dict[str, dict[str, float]]) -> set[str]:
    """PSR codes that appear as CONSUMPTION anywhere in the document.

    Only these were corrupted: a psrType published in one direction only was
    always parsed correctly. Returned as base codes (B10, not B10_CONS).
    """
    return {
        base_psr(key)
        for by_psr in day_map.values()
        for key in by_psr
        if is_consumption_key(key)
    }


def repair_zone_month(db, zone: str, eic: str, month: date, *, dry_run: bool = False) -> dict:
    """Rewrite the storage rows for one zone-month from its cached A75 document."""
    payload = raw_cache.read_cached(CACHE_SOURCE, f"{eic}_{month:%Y-%m}", month)
    if payload is None:
        return {"cached": False}
    xml = payload.get("xml", "")
    if not xml:
        return {"cached": False}

    daily = parse_generation_by_type(xml)
    codes = storage_psrs(daily)
    if not codes:
        return {"cached": True, "storage": False}
    if dry_run:
        return {"cached": True, "storage": True, "codes": sorted(codes), "days": len(daily)}

    hourly = parse_generation_hourly(xml)
    hourly_written = 0
    for code in codes:
        for prefix, key in (("gen", code), ("consumption", f"{code}_CONS")):
            day_hours = {
                day: by_psr[key]
                for day, by_psr in hourly.items()
                if key in by_psr
            }
            if day_hours:
                hourly_written += upsert_day_hours(
                    db, f"{prefix}.{code}", zone, day_hours, unit="MW"
                )

    # Daily mix: the generation leg only (pumping is load, not generation).
    daily_written = 0
    for day, by_psr in daily.items():
        for code in codes:
            if code not in by_psr:
                continue
            label = PSR_LABELS.get(code, code)
            row = (
                db.query(PowerGenMix)
                .filter(PowerGenMix.date == day, PowerGenMix.zone == zone,
                        PowerGenMix.psr_type == label)
                .first()
            )
            if row is None:
                db.add(PowerGenMix(date=day, zone=zone, psr_type=label, gen_mw=by_psr[code]))
            else:
                row.gen_mw = by_psr[code]
            daily_written += 1
    db.commit()
    return {
        "cached": True, "storage": True,
        "hourly": hourly_written, "daily": daily_written, "codes": sorted(codes),
    }


def run(db, start: date, end: date, *, dry_run: bool = False) -> dict:
    months = _months(start, end)
    total = {"zone_months": 0, "missing_cache": 0, "no_storage": 0,
             "repaired": 0, "hourly": 0, "daily": 0}
    processed = 0
    for zone, cfg in POWER_ZONES.items():
        for month in months:
            total["zone_months"] += 1
            res = repair_zone_month(db, zone, cfg["eic"], month, dry_run=dry_run)
            if not res.get("cached"):
                total["missing_cache"] += 1
                continue
            if not res.get("storage"):
                total["no_storage"] += 1
                continue
            total["repaired"] += 1
            total["hourly"] += res.get("hourly", 0)
            total["daily"] += res.get("daily", 0)
            processed += 1
            if not dry_run and processed % CHECKPOINT_EVERY == 0:
                # Bound the WAL: the API holds long-lived readers, so a long batch
                # job would otherwise grow it without limit (disk-incident class).
                db.execute(text("PRAGMA wal_checkpoint(TRUNCATE)"))
                logger.info("repair: %s %s — %d zone-months repaired (WAL checkpointed)",
                            zone, month.strftime("%Y-%m"), total["repaired"])
    return total


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser(description="Repair pumped-storage series written by the old A75 parser")
    p.add_argument("--start", default="2015-01-01")
    p.add_argument("--end", default=date.today().isoformat())
    p.add_argument("--dry-run", action="store_true", help="report what would be repaired")
    args = p.parse_args(argv[1:])

    start = datetime.strptime(args.start, "%Y-%m-%d").date()
    end = datetime.strptime(args.end, "%Y-%m-%d").date()

    db = SessionLocal()
    try:
        result = run(db, start, end, dry_run=args.dry_run)
    finally:
        db.close()
    logger.info("repair_storage_series complete: %s", result)
    if result["missing_cache"]:
        logger.warning(
            "%d zone-months had no cached A75 document — their storage rows keep the "
            "old (wrong) values until those months are backfilled.",
            result["missing_cache"],
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
