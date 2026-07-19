"""Backfill: rebuild the residual.actual hourly series from load + wind + solar in
the canonical hourly store — DB-only, no ENTSO-E refetch.

Before the fix, build_hourly_forecast nulled a residual hour whenever wind OR
solar was absent, dropping every NIGHT hour in zones that omit solar (ES/IT/PT/
GR). Those dropped high-residual night hours made the residual.actual daily mean
disagree with PowerGrid.residual_mw (÷24) — ES 12303 vs 12822. This recomputes
every hour with the fixed rule (absent leg = 0; residual None only when NEITHER
leg is present) and upserts the now-complete series.

Usage (on the VPS, as the obsyd user):
    python -m backend.scripts.rebuild_residual_actual --dry-run
    python -m backend.scripts.rebuild_residual_actual
"""
from __future__ import annotations

import argparse
import logging
from collections import defaultdict
from datetime import UTC, datetime

from backend.database import SessionLocal
from backend.power.entsoe_grid import (
    PSR_SOLAR,
    PSR_WIND_OFFSHORE,
    PSR_WIND_ONSHORE,
    build_hourly_forecast,
)
from backend.power.hourly_store import read_hourly, upsert_day_hours
from backend.power.zones import POWER_ZONES

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("rebuild_residual_actual")

# The legs build_hourly_forecast reads (its keys are the raw psr codes).
_GEN_SERIES = {PSR_WIND_OFFSHORE, PSR_WIND_ONSHORE, PSR_SOLAR}


def _by_day_hour(rows: list[tuple[int, float]]) -> dict[str, dict[int, float]]:
    out: dict[str, dict[int, float]] = defaultdict(dict)
    for ts, v in rows:
        dt = datetime.fromtimestamp(ts, UTC)
        out[dt.strftime("%Y-%m-%d")][dt.hour] = v
    return out


def rebuild(*, dry_run: bool) -> dict:
    db = SessionLocal()
    zones_done = 0
    hours_written = 0
    try:
        for zone in POWER_ZONES:
            load_by_day = _by_day_hour(read_hourly(db, "load.actual", zone))
            if not load_by_day:
                continue
            gen_by_day: dict[str, dict[str, dict[int, float]]] = defaultdict(dict)
            for psr in _GEN_SERIES:
                for day, hours in _by_day_hour(read_hourly(db, f"gen.{psr}", zone)).items():
                    gen_by_day[day][psr] = hours

            resid_by_day: dict[str, dict[int, float]] = {}
            for day, load_hours in load_by_day.items():
                rr = {
                    p["hour"]: p["residual_mw"]
                    for p in build_hourly_forecast(load_hours, gen_by_day.get(day, {}))
                    if p["residual_mw"] is not None
                }
                if rr:
                    resid_by_day[day] = rr
            n = sum(len(v) for v in resid_by_day.values())
            if resid_by_day and not dry_run:
                upsert_day_hours(db, "residual.actual", zone, resid_by_day, unit="MW")
            hours_written += n
            zones_done += 1
            logger.info("  %s: %d residual hours across %d days", zone, n, len(resid_by_day))
    finally:
        db.close()
    result = {"zones": zones_done, "residual_hours": hours_written, "dry_run": dry_run}
    logger.info("rebuild_residual_actual: %s", result)
    return result


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="Compute without writing")
    args = ap.parse_args()
    rebuild(dry_run=args.dry_run)
