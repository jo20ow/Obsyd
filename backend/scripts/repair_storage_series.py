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
from backend.models.energy import PowerGenMix, PowerGrid
from backend.observability import install_log_redaction
from backend.power.entsoe_grid import (
    PSR_LABELS,
    PSR_SOLAR,
    PSR_WIND_OFFSHORE,
    PSR_WIND_ONSHORE,
    base_psr,
    is_consumption_key,
    parse_generation_by_type,
    parse_generation_hourly,
)
from backend.power.hourly_store import day_hour_ts, read_hourly, upsert_day_hours
from backend.power.zones import POWER_ZONES

#: Codes whose corruption propagated BEYOND the mix: wind and solar feed
#: PowerGrid.wind_mw / solar_mw and hence residual load — and therefore the
#: renewable share, the Dunkelflaute flag and every residual z-score. Verified in
#: prod: IE-SEM carries 38,374 hourly wind-CONSUMPTION points (2021-2025), so its
#: wind was averaged with a real consumption series for four straight years.
RENEWABLE_CODES = {PSR_SOLAR, PSR_WIND_OFFSHORE, PSR_WIND_ONSHORE}

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
    # Wind/solar corruption propagated into residual load — repair that too, or
    # the Dunkelflaute detector keeps reading a zone that never existed.
    grid_days = 0
    if codes & RENEWABLE_CODES:
        grid_days = _repair_residual(db, zone, daily, hourly)

    db.commit()
    return {
        "cached": True, "storage": True,
        "hourly": hourly_written, "daily": daily_written,
        "grid_days": grid_days, "codes": sorted(codes),
    }


def _repair_residual(db, zone: str, daily: dict, hourly: dict) -> int:
    """Recompute wind/solar/residual from the corrected generation.

    Load is untouched by the parser bug (A65 has no consumption twin), so it is
    read back from the canonical store rather than re-parsed.
    """
    days = 0
    for day, by_psr in daily.items():
        row = (
            db.query(PowerGrid)
            .filter(PowerGrid.date == day, PowerGrid.zone == zone)
            .first()
        )
        if row is None:
            continue
        wind = by_psr.get(PSR_WIND_OFFSHORE, 0.0) + by_psr.get(PSR_WIND_ONSHORE, 0.0)
        solar = by_psr.get(PSR_SOLAR, 0.0)
        row.wind_mw = wind or None
        row.solar_mw = solar or None
        row.residual_mw = (
            round(row.load_mw - wind - solar, 2) if row.load_mw is not None else None
        )
        days += 1

    # Hourly residual = load − wind − solar, hour by hour.
    load_by_ts = dict(read_hourly(db, "load.actual", zone))
    resid: dict[str, dict[int, float]] = {}
    for day, by_psr in hourly.items():
        hours: dict[int, float] = {}
        for hour in range(24):
            ts = day_hour_ts(day, hour)
            load = load_by_ts.get(ts)
            if load is None:
                continue
            renew = sum(
                by_psr.get(code, {}).get(hour, 0.0)
                for code in (PSR_WIND_OFFSHORE, PSR_WIND_ONSHORE, PSR_SOLAR)
            )
            hours[hour] = round(load - renew, 2)
        if hours:
            resid[day] = hours
    if resid:
        upsert_day_hours(db, "residual.actual", zone, resid, unit="MW")
    return days


def run(db, start: date, end: date, *, dry_run: bool = False) -> dict:
    months = _months(start, end)
    total = {"zone_months": 0, "missing_cache": 0, "no_storage": 0,
             "repaired": 0, "hourly": 0, "daily": 0, "grid_days": 0}
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
            total["grid_days"] += res.get("grid_days", 0)
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
    install_log_redaction()  # ENTSO-E puts its key in the query string; httpx logs the URL
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
