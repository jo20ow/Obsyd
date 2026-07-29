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

Border-level sources (zone-independent, run AFTER the zone loop): "flows" (Energy-Charts
/cbpf), "scheduled" (A09), "netpos" (A25) and "ntc" (A61 day-ahead NTC — NTC-allocated
borders only, both directions per pair; the flow-based Core region and the Nordics publish
none, so a full-history run stays cheap).

"balancing" (activated balancing energy, A83/A84) ALSO runs after the zone loop, but it is
per-zone: ingest_balancing fetches whole control-area MONTHS (one A84 + one A83 request per
zone-month, raw-cached), so it gets its own zone×month sweep with its own counter instead of
piggybacking on the per-day sibling sources above. It stays in ALL_SOURCES because that
volume — 2 requests per zone-month — is the same order as the siblings' 1, unlike the two
opt-outs below. Pre-availability years (2019–2020 answer empty for many zones) and zones
without a balancing publication return ENTSO-E's two documented "genuinely nothing here"
400 phrases, which the collector caches as emptiness (entsoe_balancing.py — ONLY those
phrases; a 401/429/5xx raises and is never cached), so a deep run pays for each empty
zone-month exactly once.

"units_gen" (A73 per-unit generation) also runs after the zone loop — it iterates its own
A73_ZONES config (currently DE_LU = 4 German control areas), not the enabled zones. It is
deliberately NOT in ALL_SOURCES: run it EXPLICITLY (`--sources units_gen --start 2025-01-01`)
and never bundle it into an unfiltered full-history run — 4 CTAs × ~5 chunks/month adds up
fast against the shared ENTSO-E token (the lesson every deep multi-source run here has
re-taught). Recommended --start 2025-01-01; deeper history is possible but each extra year
is another ~240 requests for a per-plant drill-down whose product value is recent.

"capacity" (procured balancing-capacity prices, A15, DE-LU LFC block only) is likewise
deliberately NOT in ALL_SOURCES — it is BY FAR the heaviest source per calendar day: 3
processTypes/day, each offset-paginated in steps of 100 TimeSeries (~69 pages observed for
a single busy aFRR day → ~200+ requests/day, i.e. thousands of times a sibling source's
volume), all against the shared ENTSO-E token. Run it EXPLICITLY, LAST and ALONE
(`--sources capacity --start 2024-01-01`), NEVER bundled with other sources. The German
daily-tender history barely predates ~2018/2019 and the product value is recent —
--start 2024-01-01 is the recommended floor; every extra year is ~70k more requests.

FIRST DEPLOY of the balancing/capacity/ntc/units_gen collectors: right after the
service restart that ships them, run `--sources balancing`, `--sources ntc`,
`--sources units_gen --start 2025-01-01` and — last and alone, per the warning above —
`--sources capacity --start 2024-01-01` once each. Not a launch
blocker if skipped — the 09:00 UTC collector watchdog will email a stale alert
(balancing_energy/capacity_prices/ntc_dayahead; unit_generation can flag once because
the 09:40 UTC job has not yet had its first run) until the daily jobs fill the series
in on their own (self-heals within 24h) — but the backfill closes the gap immediately
instead of waiting out one noisy watchdog cycle.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import date, datetime, timedelta

from backend.database import SessionLocal
from backend.observability import install_log_redaction
from backend.power.energy_charts_flows import ingest_cbpf
from backend.power.entsoe_balancing import ingest_balancing
from backend.power.entsoe_grid import ingest_grid, ingest_load_forecast
from backend.power.entsoe_imbalance import ingest_imbalance
from backend.power.entsoe_prices import ingest_day_ahead
from backend.power.zones import POWER_ZONES

logger = logging.getLogger("power_backfill")

BACKFILL_START = date(2015, 1, 1)  # ENTSO-E Transparency era; override with --start
# "flows" is zone-independent (one /cbpf sweep covers every border) and runs once
# per month after the zone loop. Moderate history is the point (--start 2024-01-01
# per roadmap Block 2.4) — deep flow history adds little over the daily means.
# "capacity" and "units_gen" are deliberately NOT here (see module docstring): both are
# explicit-opt-in sources whose request volume per default run is far beyond the siblings'
# (~200+ paginated requests per DAY for A15 vs. 1–2 per zone-MONTH for everything below).
ALL_SOURCES = ("price", "grid", "forecast", "imbalance", "balancing", "flows", "scheduled", "netpos", "ntc")
# Small pause between zone-months to stay under ENTSO-E's ~400 req/min token limit.
THROTTLE_SECONDS = 1.0


async def _with_retry(coro_factory, label: str, attempts: int = 4, base: float = 2.0):
    """Retry a step with exponential backoff. Cached months are skipped on retry (raw_cache),
    so this resumes cheaply rather than re-fetching everything.

    OperationalError is in the catch set because "database is locked" is exactly the kind of
    transient failure a retry exists for — and it is not hypothetical: the A09 backfill died on
    one after writing 432,774 points, because the app was writing at the same time (SQLite takes
    one writer, and the 30 s busy_timeout is not a guarantee). Without it, a backfill measured in
    hours can be killed by an hourly cron job.
    """
    import httpx
    from sqlalchemy.exc import OperationalError

    for i in range(attempts):
        try:
            return await coro_factory()
        except (httpx.HTTPError, OSError, OperationalError) as exc:
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

    # Activated balancing energy (A83/A84) is per-zone but runs AFTER the main zone loop:
    # ingest_balancing fetches whole control-area MONTHS (one A84 + one A83 request per
    # zone-month, raw-cached), so it gets its own zone×month sweep and counter instead of
    # piggybacking on the per-day sibling sources above — and a balancing-only run does not
    # drag the price/grid/forecast/imbalance machinery through the throttle for nothing.
    # Pre-availability years (2019–2020 answer empty for many zones) are absorbed by the
    # collector: ENTSO-E's two documented "genuinely nothing here" 400 phrases are cached as
    # emptiness, ONLY those — so each empty zone-month costs one request, once. A 401/429/5xx
    # is never cached, but note it is also never retried HERE: ingest_balancing swallows
    # httpx.HTTPError internally (log-and-skip per month, the sibling per-step-isolation
    # convention), so the _with_retry wrapper below effectively guards the SQLite-lock/
    # OSError cases only; an HTTP-failed month simply stays uncached and is re-fetched by
    # the next run. See the module docstring for why this — at 2 requests per zone-month —
    # stays in ALL_SOURCES while capacity/units_gen do not.
    balancing_months = 0
    if "balancing" in sources:
        balancing_plan = len(zones) * len(windows)
        for zone in zones:
            for m_start, m_end in windows:
                if dry_run:
                    balancing_months += 1
                    continue
                days = _daterange(m_start, m_end)
                await _with_retry(
                    lambda d=days, z=zone: ingest_balancing(db, d, zone=z, overwrite=overwrite),
                    f"balancing {zone} {m_start:%Y-%m}",
                )
                balancing_months += 1
                logger.info("power_backfill: balancing %s %s done (%d/%d)",
                            zone, f"{m_start:%Y-%m}", balancing_months, balancing_plan)
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

    # A25 walks its own zone list (34 of 37) in WEEKLY windows — a one-month A25 request did
    # not return inside 90 s. Like flows and scheduled, it belongs outside the zone loop.
    netpos_months = 0
    if "netpos" in sources:
        from backend.power.entsoe_exchange import ingest_net_positions

        for m_start, m_end in windows:
            if dry_run:
                netpos_months += 1
                continue
            weeks = _weeks_in(m_start, m_end)
            await _with_retry(
                lambda w=weeks: ingest_net_positions(db, w, overwrite=overwrite),
                f"netpos {m_start:%Y-%m}",
            )
            netpos_months += 1
            logger.info("power_backfill: netpos %s done (%d/%d)",
                        f"{m_start:%Y-%m}", netpos_months, len(windows))

    # Day-ahead NTC (A61) iterates the NTC_BORDERS register — border-level like "scheduled"
    # above, so it belongs after the zone loop for the same reason. 23 borders × 2
    # directions per month; non-publishing months answer a clean ACK that is cached, so a
    # re-run costs nothing.
    ntc_months = 0
    if "ntc" in sources:
        from backend.power.entsoe_ntc import ingest_ntc

        for m_start, _m_end in windows:
            if dry_run:
                ntc_months += 1
                continue
            await _with_retry(
                lambda m=m_start: ingest_ntc(db, [m], overwrite=overwrite),
                f"ntc {m_start:%Y-%m}",
            )
            ntc_months += 1
            logger.info("power_backfill: ntc %s done (%d/%d)",
                        f"{m_start:%Y-%m}", ntc_months, len(windows))

    # Balancing-capacity prices (FCR/aFRR/mFRR, A15) are DE_LU-only (AREA_DOMAIN in
    # backend/power/entsoe_reserves.py — the DE-LU LFC block, the one domain that answers)
    # and zone-independent — one sweep per month covers the whole German market.
    # ⚠ NOT in ALL_SOURCES, and that must never change casually: each day fetches 3
    # processTypes, each individually offset-paginated in steps of 100 TimeSeries (~69 pages
    # observed for one busy aFRR day → ~200+ requests/day against the shared ENTSO-E token —
    # thousands of times a sibling source's per-day volume). Run it EXPLICITLY, LAST and
    # ALONE, never bundled with other sources, and with a tight --start (recommended
    # `--sources capacity --start 2024-01-01`; the daily-tender history barely predates
    # ~2018/2019 anyway). The month-window calls below are only the retry/log granularity:
    # the ingest itself fetches DAY by DAY per processType with a per-day raw_cache entry,
    # so the actual ENTSO-E windows are single days and a crashed month resumes from cache.
    capacity_months = 0
    if "capacity" in sources:
        from backend.power.entsoe_reserves import ingest_capacity_prices

        for m_start, m_end in windows:
            if dry_run:
                capacity_months += 1
                continue
            days = _daterange(m_start, m_end)
            await _with_retry(
                lambda d=days: ingest_capacity_prices(db, d, overwrite=overwrite),
                f"capacity {m_start:%Y-%m}",
            )
            capacity_months += 1
            logger.info("power_backfill: capacity %s done (%d/%d)",
                        f"{m_start:%Y-%m}", capacity_months, len(windows))

    # Per-unit generation (A73) iterates its own A73_ZONES config, not the enabled
    # zones — so it belongs after the zone loop like the border-level sources. Not
    # in ALL_SOURCES (see module docstring): explicit opt-in only, month windows
    # split into the probe-proven 7-day chunks by the ingest itself.
    units_gen_months = 0
    if "units_gen" in sources:
        from backend.power.entsoe_unit_generation import ingest_unit_generation_window

        for m_start, m_end in windows:
            if dry_run:
                units_gen_months += 1
                continue
            await _with_retry(
                lambda s=m_start, e=m_end: ingest_unit_generation_window(
                    db, s, e + timedelta(days=1), overwrite=overwrite),
                f"units_gen {m_start:%Y-%m}",
            )
            units_gen_months += 1
            logger.info("power_backfill: units_gen %s done (%d/%d)",
                        f"{m_start:%Y-%m}", units_gen_months, len(windows))

    return {"zone_months": done, "zones": zones, "months": len(windows),
            "balancing_months": balancing_months,
            "flow_months": flow_months, "scheduled_months": sched_months,
            "netpos_months": netpos_months, "ntc_months": ntc_months,
            "capacity_months": capacity_months,
            "units_gen_months": units_gen_months, "dry_run": dry_run}


def _weeks_in(m_start, m_end) -> list:
    """Monday-anchored weeks covering a month window."""
    from datetime import timedelta

    weeks, w = [], m_start - timedelta(days=m_start.weekday())
    while w <= m_end:
        weeks.append(w)
        w += timedelta(days=7)
    return weeks


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    install_log_redaction()  # ENTSO-E puts its key in the query string; httpx logs the URL
    p = argparse.ArgumentParser(description="European power desk deep backfill")
    p.add_argument("--start", default=BACKFILL_START.isoformat())
    p.add_argument("--end", default=date.today().isoformat())
    p.add_argument("--zones", default=None, help="comma list of zone keys (default: all enabled)")
    p.add_argument("--sources", default=",".join(ALL_SOURCES),
                   help="comma list (default: every standard source; 'capacity' and "
                        "'units_gen' are explicit opt-ins — see module docstring)")
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
