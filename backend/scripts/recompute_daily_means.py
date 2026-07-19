"""Backfill: re-derive PowerPriceDaily.mean_price (and EnergyPrice.close) from the
canonical price.dayahead hourly store — DB-only, no ENTSO-E refetch.

Before the coherence fix, the daily mean was averaged from a finest-resolution
snapshot (the QH slots) while the chart/v1-API/client average the accumulated
hourly series. On mixed-resolution days (ENTSO-E serving some hours as PT60M and
some as PT15M since the SDAC 15-min switch, 2025-10-01) the two diverged — the
table said FR €62 while the chart said €53. This recomputes every affected day's
mean straight from the hourly store, so all views agree.

Usage (on the VPS, as the obsyd user):
    python -m backend.scripts.recompute_daily_means --dry-run          # count only
    python -m backend.scripts.recompute_daily_means --since 2025-10-01 # write
"""
from __future__ import annotations

import argparse
import logging

from backend.database import SessionLocal
from backend.models.energy import EnergyPrice, PowerPriceDaily
from backend.power.entsoe_prices import _canonical_daily_mean
from backend.power.zones import ZONE_REGISTRY

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("recompute_daily_means")

# Divergence only exists on days that also have a 15-min series, i.e. since SDAC's
# quarter-hour switch. Default the floor there; older days were pure-hourly and match.
DEFAULT_SINCE = "2025-10-01"

_ZONE_SYMBOL = {z: cfg["price_symbol"] for z, cfg in ZONE_REGISTRY.items()}


def recompute(since: str, *, dry_run: bool) -> dict:
    db = SessionLocal()
    changed = 0
    checked = 0
    max_delta = 0.0
    try:
        rows = (
            db.query(PowerPriceDaily)
            .filter(PowerPriceDaily.date >= since)
            .order_by(PowerPriceDaily.zone, PowerPriceDaily.date)
            .all()
        )
        for row in rows:
            checked += 1
            mean = _canonical_daily_mean(db, row.zone, row.date)
            if mean is None:
                continue
            if row.mean_price is None or abs(row.mean_price - mean) > 0.005:
                max_delta = max(max_delta, abs((row.mean_price or 0) - mean))
                if not dry_run:
                    row.mean_price = mean
                    symbol = _ZONE_SYMBOL.get(row.zone)
                    if symbol:
                        ep = (
                            db.query(EnergyPrice)
                            .filter(EnergyPrice.date == row.date, EnergyPrice.symbol == symbol)
                            .first()
                        )
                        if ep is not None:
                            ep.close = mean
                changed += 1
        if not dry_run:
            db.commit()
    finally:
        db.close()
    result = {"checked": checked, "changed": changed, "max_delta": round(max_delta, 2), "dry_run": dry_run}
    logger.info("recompute_daily_means: %s", result)
    return result


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default=DEFAULT_SINCE, help="Earliest delivery day to recompute (YYYY-MM-DD)")
    ap.add_argument("--dry-run", action="store_true", help="Count what would change without writing")
    args = ap.parse_args()
    recompute(args.since, dry_run=args.dry_run)
