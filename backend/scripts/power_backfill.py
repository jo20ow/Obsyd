"""One-command idempotent backfill for the European power desk (deep history).

    python -m backend.scripts.power_backfill                          # 2015-01-01 → today, all enabled zones
    python -m backend.scripts.power_backfill --start 2020-01-01 --zones DE_LU,FR
    python -m backend.scripts.power_backfill --sources price,grid     # skip forecasts
    python -m backend.scripts.power_backfill --dry-run                # print the zone×month plan only
    python -m backend.scripts.power_backfill --overwrite              # re-fetch cached months

Loops enabled zones × months, calling ingest_day_ahead / ingest_grid /
ingest_load_forecast (which now also populate power_hourly). Every write is an
upsert and every raw payload is disk-cached (raw_cache), so a crashed run resumes
from cache for free. Meant to run in the ingest process (never the API worker) —
a mass backfill is a throttled, multi-day marathon against ENTSO-E's rate limit.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import date, datetime, timedelta

from backend.database import SessionLocal
from backend.power.energy_charts_flows import ingest_cbpf
from backend.power.entsoe_grid import ingest_grid, ingest_load_forecast
from backend.power.entsoe_imbalance import ingest_imbalance
from backend.power.entsoe_prices import ingest_day_ahead
from backend.power.zones import POWER_ZONES

logger = logging.getLogger("power_backfill")

BACKFILL_START = date(2015, 1, 1)  # ENTSO-E Transparency era; override with --start
# "flows" is zone-independent (one /cbpf sweep covers every border) and runs once
# per month after the zone loop. Moderate history is the point (--start 2024-01-01
# per roadmap Block 2.4) — deep flow history adds little over the daily means.
ALL_SOURCES = ("price", "grid", "forecast", "imbalance", "flows", "scheduled")
# Small pause between zone-months to stay under ENTSO-E's ~400 req/min token limit.
THROTTLE_SECONDS = 1.0


async def _with_retry(coro_factory, label: str, attempts: int = 4, base: float = 2.0):
    """Retry a network step with exponential backoff. Cached months are skipped on
    retry (raw_cache), so this resumes cheaply rather than re-fetching everything."""
    import httpx

    for i in range(attempts):
        try:
            return await coro_factory()
        except (httpx.HTTPError, OSError) as exc:
            if i == attempts - 1:
                logger.error("%s failed after %d attempts: %s", label, attempts, exc)
                raise
            wait = base * (4**i)
            logger.warning("%s error (%s); retrying in %.0fs", label, exc, wait)
            await asyncio.sleep(wait)


def _month_windows(start: date, end: date) -> list[tuple[date, date]]:
    windows, cur = [], start.replace(day=1)
    while cur <= end:
        nxt = date(cur.year + 1, 1, 1) if cur.month == 12 else date(cur.year, cur.month + 1, 1)
        windows.append((max(start, cur), min(end, date.fromordinal(nxt.toordinal() - 1))))
        cur = nxt
    return windows


def _daterange(a: date, b: date) -> list[str]:
    return [(a + timedelta(days=i)).isoformat() for i in range((b - a).days + 1)]


def _resolve_zones(raw: str | None) -> list[str]:
    """Validate a --zones list against the enabled POWER_ZONES; empty → all enabled."""
    if not raw:
        return list(POWER_ZONES.keys())
    keys = [k.strip() for k in raw.split(",") if k.strip()]
    return [k for k in keys if k in POWER_ZONES]


async def run_backfill(
    db,
    start: date,
    end: date,
    zones: list[str],
    sources: set[str],
    overwrite: bool,
    *,
    dry_run: bool = False,
    throttle: float = THROTTLE_SECONDS,
) -> dict:
    windows = _month_windows(start, end)
    plan = len(zones) * len(windows)
    logger.info(
        "power_backfill: %d zone-months (%d zones × %d months), sources=%s%s",
        plan, len(zones), len(windows), sorted(sources), " [DRY RUN]" if dry_run else "",
    )
    done = 0
    # A flows-only run must not walk the zone×month loop — it would do nothing
    # but sleep through the throttle (37 zones × months × 1 s on prod).
    zone_sources = sources & {"price", "grid", "forecast", "imbalance"}
    for zone in zones if zone_sources else []:
        cfg = POWER_ZONES[zone]
        eic = cfg["eic"]
        for m_start, m_end in windows:
            days = _daterange(m_start, m_end)
            tag = f"{zone} {m_start:%Y-%m}"
            if dry_run:
                done += 1
                continue
            if "price" in sources:
                await _with_retry(
                    lambda d=days: ingest_day_ahead(db, d, eic=eic, symbol=cfg["price_symbol"], zone=zone, overwrite=overwrite),
                    f"price {tag}",
                )
            if "grid" in sources:
                await _with_retry(lambda d=days: ingest_grid(db, d, eic=eic, zone=zone, overwrite=overwrite), f"grid {tag}")
            if "forecast" in sources:
                await _with_retry(lambda d=days: ingest_load_forecast(db, d, eic=eic, zone=zone, overwrite=overwrite), f"forecast {tag}")
            if "imbalance" in sources:
                await _with_retry(lambda d=days: ingest_imbalance(db, d, zone=zone, overwrite=overwrite), f"imbalance {tag}")
            done += 1
            logger.info("power_backfill: %s done (%d/%d)", tag, done, plan)
            if throttle:
                await asyncio.sleep(throttle)

    # Cross-border flows are zone-independent — one month-chunked, raw-cached
    # /cbpf sweep per month covers every enabled border (daily + hourly grain).
    flow_months = 0
    if "flows" in sources:
        for m_start, m_end in windows:
            if dry_run:
                flow_months += 1
                continue
            days = _daterange(m_start, m_end)
            await _with_retry(
                lambda d=days: ingest_cbpf(db, d, use_cache=True),
                f"flows {m_start:%Y-%m}",
            )
            flow_months += 1
            logger.info("power_backfill: flows %s done (%d/%d)", f"{m_start:%Y-%m}", flow_months, len(windows))

    # Scheduled exchanges iterate BORDERS, not zones — so, like flows, they belong after the
    # zone loop. Putting them inside it would walk 37 zones sleeping through the throttle to
    # do the same 63 borders 37 times over.
    sched_months = 0
    if "scheduled" in sources:
        from backend.power.entsoe_exchange import ingest_scheduled_exchanges

        for m_start, _m_end in windows:
            if dry_run:
                sched_months += 1
                continue
            await _with_retry(
                lambda m=m_start: ingest_scheduled_exchanges(db, [m], overwrite=overwrite),
                f"scheduled {m_start:%Y-%m}",
            )
            sched_months += 1
            logger.info("power_backfill: scheduled %s done (%d/%d)",
                        f"{m_start:%Y-%m}", sched_months, len(windows))

    return {"zone_months": done, "zones": zones, "months": len(windows),
            "flow_months": flow_months, "scheduled_months": sched_months,
            "dry_run": dry_run}


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser(description="European power desk deep backfill")
    p.add_argument("--start", default=BACKFILL_START.isoformat())
    p.add_argument("--end", default=date.today().isoformat())
    p.add_argument("--zones", default=None, help="comma list of zone keys (default: all enabled)")
    p.add_argument("--sources", default=",".join(ALL_SOURCES), help="comma list: price,grid,forecast")
    p.add_argument("--overwrite", action="store_true", help="re-fetch cached months")
    p.add_argument("--dry-run", action="store_true", help="print the plan without fetching")
    p.add_argument("--throttle", type=float, default=THROTTLE_SECONDS, help="seconds between zone-months")
    args = p.parse_args(argv[1:])

    start = datetime.strptime(args.start, "%Y-%m-%d").date()
    end = datetime.strptime(args.end, "%Y-%m-%d").date()
    zones = _resolve_zones(args.zones)
    sources = {s.strip() for s in args.sources.split(",") if s.strip()}
    if not zones:
        logger.error("no valid zones resolved from %r (enabled: %s)", args.zones, list(POWER_ZONES))
        return 2

    db = SessionLocal()
    try:
        result = asyncio.run(
            run_backfill(db, start, end, zones, sources, args.overwrite,
                         dry_run=args.dry_run, throttle=args.throttle)
        )
    finally:
        db.close()
    logger.info("power_backfill complete: %s → %s, %s", start, end, result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
