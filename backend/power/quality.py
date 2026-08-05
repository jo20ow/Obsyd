"""Nightly data-quality aggregates: completeness + rule-based anomaly flags.

The desk republishes ENTSO-E's numbers; the Honest Record owes the reader a
statement of what those numbers looked like — hours missing, solar generation
at night, a load feed flatlining at zero. This engine computes that statement
per (zone, series, UTC day) from the canonical hourly store and persists it
(`quality_daily`) — records.py's doctrine: recomputation replaces the row, no
incremental state to corrupt, and a day the data no longer supports is
retracted rather than left to rot (forecast_score.py did the same).

Posture B, said plainly: every flag DESCRIBES the published data. "Solar > 50 MW
at 23:00 UTC" is a statement about the feed, not about the market, and none of
it predicts anything.

Completeness counts the series' native intervals: 24 for hourly series, 96 for
`.qh` quarter-hour series (`hours_expected`). A day with zero points still gets
a row (hours_present=0) ONLY while the series shows activity in the surrounding
30 days — otherwise the zone simply doesn't carry that series, and a row would
be noise, not information.

Rules are small pure functions over the day's hour-points, wired per series by
QUALITY_RULES. Conservatism is the design constraint throughout — a false
positive here costs the product exactly the trust it exists to build:

* pv_at_night (gen.B16): the night window is 22:00–01:00 UTC (NIGHT_HOURS_UTC).
  Chosen from the map, not from taste: the latest sunset among enabled zones is
  the west Irish coast at midsummer (~21:30 UTC), the earliest sunrise among
  zones with a MATERIAL PV fleet is Finland at midsummer (~00:54 UTC), so the
  window is dark wherever solar could plausibly move the number. At and above
  the Arctic Circle (SE1/SE2, NO3/NO4, northern FI — midnight sun / no
  astronomical night) no UTC window is dark year-round — those fleets are at
  most tens of MW, which is what the MW floor is for.
* zero_run (load.actual): six consecutive exact-0.0 hours. Zero LOAD is
  physically implausible anywhere in Europe; zero or negative PRICES are real
  market outcomes, so this rule must never be wired onto a price series.
* step_jump (load.actual, price.dayahead): |Δ| beyond 8× the trailing-30-day
  IQR of hourly deltas, with an absolute floor per series so a thin series
  doesn't flag on its own noise. Real scarcity spikes clear neither bar the
  way an ingest glitch does — and when they do, the flag still only SAYS "this
  hour moved 8× more than this series' own month", which is true.
* gen_below_load_exports (zone-level, series_key "_zone"): see the rule's
  docstring — gated on the A75 coverage guard AND on flow data being present.
"""

from __future__ import annotations

import json
import math
from bisect import bisect_left
from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy.orm import Session

from backend.models.energy import QualityDaily, SeriesDim
from backend.power.coverage import coverage_min_ratio
from backend.power.hourly_store import day_hour_ts, iter_border_points, read_hourly

_DAY_S = 24 * 3600
_HOUR_S = 3600

#: Series the completeness pass covers. Config-only: adding a series here gives
#: it a daily hours_present/hours_expected row (and any rules wired below).
QUALITY_SERIES: tuple[str, ...] = (
    "load.actual",
    "price.dayahead",
    "price.dayahead.qh",
    "gen.B16",
    "gen.B18",
    "gen.B19",
)

#: A day with no points only earns a row while the series has ANY point within
#: this many days on either side — beyond that the zone doesn't carry the series.
ACTIVITY_WINDOW_DAYS = 30

# ── revision maturity (read side of the A1 ledger) ────────────────────────────
#: A restatement observed more than this long AFTER the hour it restates changed
#: SETTLED data; anything earlier is the normal provisional fill-in window
#: (ENTSO-E routinely re-publishes actuals for a day or two). The write path
#: stores everything beyond epsilon (REVISION_FLOOR/REVISION_REL_TOL, next to it
#: in backend/power/hourly_store.py); maturity is a READ-time judgement —
#: PowerRevision's docstring defers it here. Shared by /api/v1/quality/revisions
#: (routes/quality.py re-exports this name) and the radar's
#: quality_major_restatement detector; the engine is its canonical home so
#: neither reader has to import the other. 48 h is deliberately generous — a
#: fill-in miscounted as a restatement would overstate the source's churn, and
#: conservatism is this record's design constraint.
REVISION_MATURITY_S = 48 * 3600

# ── pv_at_night ───────────────────────────────────────────────────────────────
#: 22:00–01:00 UTC — dark in every enabled zone whose PV fleet is material
#: (rationale in the module docstring). Hour 0 is the day's pre-dawn end of the
#: window, 22–23 its evening start; a per-day rule checks all three.
NIGHT_HOURS_UTC = (0, 22, 23)
PV_NIGHT_FLOOR_MW = 50.0
PV_NIGHT_DAY_MAX_FRACTION = 0.01

# ── zero_run ──────────────────────────────────────────────────────────────────
ZERO_RUN_MIN_HOURS = 6

# ── step_jump ─────────────────────────────────────────────────────────────────
STEP_JUMP_IQR_MULT = 8.0
STEP_LOOKBACK_DAYS = 30
#: Absolute floors under the IQR threshold: 500 MW for load, 100 EUR for price.
#: Presence here doubles as "this series gets the step_jump rule's delta pass".
#: Hourly series only — the delta pass assumes 3600 s spacing.
STEP_JUMP_FLOORS: dict[str, float] = {
    "load.actual": 500.0,
    "price.dayahead": 100.0,
}

# ── gen_below_load_exports ────────────────────────────────────────────────────
#: The energy balance (generation = load + net exports) may be short by this
#: fraction before it flags — provisional data and losses live inside it.
GEN_DEFICIT_TOLERANCE = 0.10
#: Reserved series_key for zone-level flags (no single series owns them).
ZONE_SERIES_KEY = "_zone"


# ── Rules: pure functions over one day's hour-points ─────────────────────────
# Signature: rule(day_points, day_start, ctx) -> flag dict | None, where
# day_points = {epoch_ts: value} for the day, day_start = epoch of 00:00 UTC,
# ctx = the per-series context the engine precomputed for the whole range.


def rule_pv_at_night(day_points: dict[int, float], day_start: int, ctx: dict) -> dict | None:
    """Solar generation above max(50 MW, 1% of the day's own max) inside the
    conservative night window — a timezone-shifted or garbage feed signature."""
    if not day_points:
        return None
    threshold = max(PV_NIGHT_FLOOR_MW, PV_NIGHT_DAY_MAX_FRACTION * max(day_points.values()))
    bad = []
    for h in NIGHT_HOURS_UTC:
        t = day_start + h * _HOUR_S
        v = day_points.get(t)
        if v is not None and v > threshold:
            bad.append((t, v))
    if not bad:
        return None
    return {
        "rule": "pv_at_night",
        "hours": sorted(t for t, _ in bad),
        "detail": {"max_mw": round(max(v for _, v in bad), 1),
                   "threshold_mw": round(threshold, 1)},
    }


def rule_zero_run(day_points: dict[int, float], day_start: int, ctx: dict) -> dict | None:
    """≥6 consecutive exact-0.0 hours. Zero load is implausible; a MISSING hour
    is a completeness fact, not a zero, so it breaks the run rather than joining
    it. Never wire this onto prices — zero/negative prices are real."""
    flagged: list[int] = []
    longest = 0
    run: list[int] = []
    for h in range(24):
        t = day_start + h * _HOUR_S
        if day_points.get(t) == 0.0:
            run.append(t)
            continue
        if len(run) >= ZERO_RUN_MIN_HOURS:
            flagged.extend(run)
            longest = max(longest, len(run))
        run = []
    if len(run) >= ZERO_RUN_MIN_HOURS:
        flagged.extend(run)
        longest = max(longest, len(run))
    if not flagged:
        return None
    return {"rule": "zero_run", "hours": flagged, "detail": {"longest_run_hours": longest}}


def rule_step_jump(day_points: dict[int, float], day_start: int, ctx: dict) -> dict | None:
    """|Δ hour-to-hour| > max(8 × trailing-30-day IQR of this series' own hourly
    deltas, an absolute floor). The IQR calibrates "8× more than this series'
    normal month"; the floor keeps a flat series from flagging its own noise.
    A delta belongs to the day containing its LATER hour (the midnight-crossing
    pair counts toward the new day)."""
    deltas: dict[int, float] = ctx["deltas"]
    ts_sorted: list[int] = ctx["delta_ts"]
    lo = bisect_left(ts_sorted, day_start - STEP_LOOKBACK_DAYS * _DAY_S)
    mid = bisect_left(ts_sorted, day_start)
    hi = bisect_left(ts_sorted, day_start + _DAY_S)
    threshold = max(
        STEP_JUMP_IQR_MULT * _iqr([deltas[t] for t in ts_sorted[lo:mid]]),
        ctx["step_floor"],
    )
    jumps = [t for t in ts_sorted[mid:hi] if abs(deltas[t]) > threshold]
    if not jumps:
        return None
    return {
        "rule": "step_jump",
        "hours": jumps,
        "detail": {"max_abs_delta": round(max(abs(deltas[t]) for t in jumps), 1),
                   "threshold": round(threshold, 1)},
    }


#: series_key → the rules that run on its day-points. Config-only wiring.
QUALITY_RULES: dict[str, tuple] = {
    "gen.B16": (rule_pv_at_night,),
    "load.actual": (rule_zero_run, rule_step_jump),
    "price.dayahead": (rule_step_jump,),
}


def rule_gen_below_load_exports(
    zone: str,
    gen_mwh: float | None,
    load_mwh: float | None,
    net_export_mwh: float,
    has_flows: bool,
) -> dict | None:
    """Daily total generation short of load + net exports by more than 10%.

    Energy bookkeeping: generation ≈ load + net exports. When the published
    generation covers less than 90% of that, either the mix feed dropped fuels
    for part of the day or something upstream is broken — worth a flag, but
    ONLY where the comparison is honest, so two gates come first:

    * A75 under-coverage exemption: coverage.py documents zones whose reported
      generation is STRUCTURALLY a fraction of load (SE4 0.36, NO1 0.60,
      IE_SEM 0.72 … measured 2026-07-13). This rule reuses that guard's exact
      threshold (`coverage_min_ratio`, default 0.6 with per-zone overrides): a
      day whose generation covers less than that fraction of load is exempt —
      the shortfall is the known structural gap (or a genuine net importer,
      which the guard admittedly cannot tell apart; same fail-safe false
      negative coverage.py already accepts), not news.
    * Flow-data gate: without the day's border flows an unmeasured IMPORT is
      indistinguishable from a generation deficit — a healthy importer would
      flag every day. No flow points, no flag.

    `net_export_mwh` uses iter_border_points' sign (positive = `zone` exports),
    summed over the day. Zone-level: recorded under series_key "_zone".

    Known bias, stated: consumption.* series (pumped-storage absorption) are
    ignored, which inflates generation relative to load + exports and therefore
    only SUPPRESSES flags — errs in this module's chosen safe direction."""
    if load_mwh is None or load_mwh <= 0 or gen_mwh is None or not has_flows:
        return None
    if gen_mwh < coverage_min_ratio(zone) * load_mwh:
        return None  # structurally under-covered (A75) — exempt, coverage.py doctrine
    supplied = load_mwh + net_export_mwh
    if supplied <= 0:
        return None
    if gen_mwh >= (1.0 - GEN_DEFICIT_TOLERANCE) * supplied:
        return None
    return {
        "rule": "gen_below_load_exports",
        "hours": [],
        "detail": {
            "gen_mwh": round(gen_mwh, 1),
            "load_mwh": round(load_mwh, 1),
            "net_export_mwh": round(net_export_mwh, 1),
            "deficit_pct": round(100.0 * (1.0 - gen_mwh / supplied), 1),
        },
    }


def hours_expected(series_key: str) -> int:
    """Native intervals per UTC day: 96 for quarter-hour series, 24 otherwise."""
    return 96 if series_key.endswith(".qh") else 24


def compute_and_store_quality(db: Session, zone: str, day: str) -> dict:
    """Recompute + upsert one zone's quality rows for one UTC day ("YYYY-MM-DD").
    Idempotent; retracts rows the data no longer supports. Returns
    {"written": n, "removed": m}."""
    return compute_and_store_range(db, zone, day, day)


def compute_and_store_range(db: Session, zone: str, start_day: str, end_day: str) -> dict:
    """Recompute + upsert quality rows for `zone` over [start_day, end_day] incl.

    The scheduler's trailing-window recompute and the one-time backfill both
    land here, so they can never disagree. Each series is read ONCE over the
    whole range plus the ±30-day activity/lookback margin and bucketed per day
    in one pass — O(points), not O(days × points) (forecast_score.py's shape).
    Commits once at the end.
    """
    days = _day_list(start_day, end_day)
    start_ts = day_hour_ts(start_day, 0)
    end_ts = day_hour_ts(end_day, 0) + _DAY_S
    # One read per series covers both the activity check and the step-jump
    # lookback — max() so retuning either constant can't silently starve the other.
    margin = max(ACTIVITY_WINDOW_DAYS, STEP_LOOKBACK_DAYS) * _DAY_S
    activity_margin = ACTIVITY_WINDOW_DAYS * _DAY_S

    written = removed = 0
    buckets_by_series: dict[str, dict[int, dict[int, float]]] = {}

    for key in QUALITY_SERIES:
        # One extra hour below the margin: the delta AT the lookback window's
        # first hour needs its predecessor, or the first day of every run would
        # see 719 trailing deltas where a later run sees 720 — and backfill and
        # nightly must never disagree.
        points = read_hourly(db, key, zone, start_ts - margin - _HOUR_S, end_ts + margin)
        ts_all = [t for t, _ in points]  # read_hourly returns time-ordered rows
        buckets: dict[int, dict[int, float]] = defaultdict(dict)
        for t, v in points:
            i = (t - start_ts) // _DAY_S
            if 0 <= i < len(days):
                buckets[i][t] = v
        buckets_by_series[key] = buckets

        ctx: dict = {}
        if key in STEP_JUMP_FLOORS:
            values = dict(points)
            deltas = {t: v - values[t - _HOUR_S] for t, v in values.items() if (t - _HOUR_S) in values}
            ctx = {"deltas": deltas, "delta_ts": sorted(deltas), "step_floor": STEP_JUMP_FLOORS[key]}

        expected = hours_expected(key)
        for i, day in enumerate(days):
            day_start = start_ts + i * _DAY_S
            day_points = buckets.get(i, {})
            if not day_points and not _has_activity(
                ts_all, day_start - activity_margin, day_start + _DAY_S + activity_margin
            ):
                metrics = None  # zone doesn't carry this series here — no row, no noise
            else:
                flags = [f for rule in QUALITY_RULES.get(key, ())
                         if (f := rule(day_points, day_start, ctx)) is not None]
                metrics = {"hours_present": len(day_points), "hours_expected": expected, "flags": flags}
            w, r = _upsert(db, zone, key, day, metrics)
            written += w
            removed += r

    w, r = _zone_level_pass(db, zone, days, start_ts, end_ts, buckets_by_series)
    written += w
    removed += r

    db.commit()
    return {"written": written, "removed": removed}


def _zone_level_pass(
    db: Session,
    zone: str,
    days: list[str],
    start_ts: int,
    end_ts: int,
    buckets_by_series: dict[str, dict[int, dict[int, float]]],
) -> tuple[int, int]:
    """gen_below_load_exports per day, under series_key "_zone". Reuses the
    load/gen buckets the completeness pass already read; only gen.* series
    outside QUALITY_SERIES cost an extra indexed range scan each."""
    gen_totals: dict[int, float] = defaultdict(float)
    gen_seen: set[int] = set()
    for (key,) in db.query(SeriesDim.key).filter(SeriesDim.key.like("gen.%")).all():
        if key in buckets_by_series:
            for i, day_points in buckets_by_series[key].items():
                if day_points:
                    gen_totals[i] += sum(day_points.values())
                    gen_seen.add(i)
        else:
            for t, v in read_hourly(db, key, zone, start_ts, end_ts):
                i = (t - start_ts) // _DAY_S
                gen_totals[i] += v
                gen_seen.add(i)

    flow_totals: dict[int, float] = defaultdict(float)
    flow_seen: set[int] = set()
    for _neighbor, t, v in iter_border_points(db, zone, start_ts, end_ts):
        i = (t - start_ts) // _DAY_S
        flow_totals[i] += v  # positive = zone exports (iter_border_points' contract)
        flow_seen.add(i)

    load_buckets = buckets_by_series.get("load.actual", {})
    written = removed = 0
    for i, day in enumerate(days):
        load_points = load_buckets.get(i, {})
        flag = rule_gen_below_load_exports(
            zone,
            gen_totals[i] if i in gen_seen else None,
            sum(load_points.values()) if load_points else None,
            flow_totals.get(i, 0.0),
            i in flow_seen,
        )
        metrics = (
            {"hours_present": 0, "hours_expected": 0, "flags": [flag]}
            if flag is not None
            else None  # _zone rows exist only on flagged days; None also retracts
        )
        w, r = _upsert(db, zone, ZONE_SERIES_KEY, day, metrics)
        written += w
        removed += r
    return written, removed


def _upsert(db: Session, zone: str, series_key: str, day: str, metrics: dict | None) -> tuple[int, int]:
    """One row per (zone, series_key, day), replaced in place. metrics=None
    RETRACTS: a row a later revision of the data no longer supports disappears,
    rather than sitting in the record forever (episodes doctrine)."""
    row = (
        db.query(QualityDaily)
        .filter_by(zone=zone, series_key=series_key, date=day)
        .one_or_none()
    )
    if metrics is None:
        if row is not None:
            db.delete(row)
            return 0, 1
        return 0, 0
    if row is None:
        row = QualityDaily(zone=zone, series_key=series_key, date=day)
        db.add(row)
    row.hours_present = metrics["hours_present"]
    row.hours_expected = metrics["hours_expected"]
    row.flags = json.dumps(metrics["flags"], separators=(",", ":"))
    return 1, 0


def _has_activity(ts_sorted: list[int], lo_ts: int, hi_ts: int) -> bool:
    """Any point in [lo_ts, hi_ts)? `ts_sorted` is time-ordered."""
    i = bisect_left(ts_sorted, lo_ts)
    return i < len(ts_sorted) and ts_sorted[i] < hi_ts


def _iqr(values: list[float]) -> float:
    """Interquartile range (linear-interpolated quartiles). 0.0 under 4 values —
    quartiles of a handful of deltas are noise, and the caller's absolute floor
    governs then anyway."""
    if len(values) < 4:
        return 0.0
    s = sorted(values)
    return _quantile(s, 0.75) - _quantile(s, 0.25)


def _quantile(sorted_values: list[float], q: float) -> float:
    pos = (len(sorted_values) - 1) * q
    lo = math.floor(pos)
    hi = math.ceil(pos)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (pos - lo)


def _day_list(start_day: str, end_day: str) -> list[str]:
    a, b = date.fromisoformat(start_day), date.fromisoformat(end_day)
    return [(a + timedelta(days=i)).isoformat() for i in range((b - a).days + 1)]
