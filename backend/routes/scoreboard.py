"""Honest-Record forecast scoreboard read API (/api/v1/scoreboard/*) — slice B2.

Public, versioned endpoints over `forecast_score_daily` (the B1 engine in
backend/power/forecast_score.py): per-(zone, series, UTC-day) error metrics for
ENTSO-E's published day-ahead forecasts vs the published actuals.

Posture B, said plainly: every number here GRADES a forecast ENTSO-E published.
OBSYD makes no forecast. The two naive baselines (persistence = actual 24 h ago,
seasonal = actual 168 h ago) are built from published actuals alone — the
"no model at all" yardsticks a fair grade needs, not models of ours.

SIGN CONVENTION (declared on the wire as `bias_convention` wherever a bias is
served): bias = mean(forecast − actual) — positive means the published forecast
leaned HIGH. The OLDER /api/power/forecast-error endpoint reports the OPPOSITE
sign (bias_mw = mean(actual − forecast)) and stays unchanged for its readers.

AGGREGATION over daily rows (documented once here, shared by /summary and
/monthly): day-weighted by `n_hours`, which makes the recombined numbers exact
per-hour statistics of the window, not means-of-means —

* mae  = Σ(mae_d·n_d) / Σn_d            (the exact window per-hour MAE)
* bias = Σ(bias_d·n_d) / Σn_d           (the exact window per-hour bias)
* rmse = sqrt(Σ(rmse_d²·n_d) / Σn_d)    (exact: rmse_d² recovers the day's Σe²)
* mape = Σ(mape_d·n_d) / Σn_d over days where mape exists (approximate: the
  day's MAPE subset may be slightly smaller than n_hours — the engine drops
  hours whose |actual| is under its division floor and does not store that
  sub-count; load only by design)
* skill_x = 1 − Σ(mae_d·n_d) / Σ(mae_x_d·n_d), summed ONLY over days whose
  baseline MAE exists — a day with a NULL baseline drops out of the skill
  RATIO only, never out of the headline mae. skill > 0 = the published
  forecast beat the naive baseline; NULL when no day carries the baseline.

Conventions (matching backend/routes/quality.py, the freshest v1 precedent):
* every handler is `def`, never `async def` — sync-DB routes must run in
  Starlette's threadpool (repo rule, PR #116);
* the shared v1 per-IP budget applies (`_rate_limit` from routes/api_v1);
* /ranking is zone-independent and the heaviest read here → computed once per
  ~15 min per window (api_guard.cached_value) behind heavy_query_guard, with
  the freshness triple stamped per REQUEST (a warm cache must not freeze
  age_days); /profile scans the hourly store on-read → heavy-guarded too;
* every response carries `as_of`/`age_days`/`stale`; the stale window is the
  `forecast_scoreboard` freshness spec's (collectors/freshness.py::SPECS),
  looked up by key so retuning the spec retunes these endpoints;
* unknown zone/series → HTTP 400 listing the valid values; window/days ranges
  via Query(ge=, le=) → 422; a valid-but-empty combination is HTTP 200 with
  `available: false`, never a 404;
* timestamps on the wire are ISO 8601 UTC; hour-of-day buckets are UTC.
"""
from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.api_guard import cached_value, heavy_query_guard
from backend.collectors.freshness import SPECS, freshness_meta
from backend.database import get_db
from backend.models.energy import ForecastScoreDaily, InstalledCapacity
from backend.power.forecast_score import FORECAST_PAIRS, score_hours
from backend.power.hourly_store import read_hourly
from backend.power.zones import POWER_ZONES
from backend.routes.api_v1 import _rate_limit  # same v1 per-IP budget — scoreboard IS v1 traffic

router = APIRouter(prefix="/api/v1/scoreboard", tags=["v1"])

#: /summary trailing windows (UTC days) — short / season / year.
SUMMARY_WINDOW_DAYS = (30, 90, 365)

#: /ranking accepts exactly the summary windows — an enum, not a range, so an
#: unsupported value is a 400 listing the valid ones (house style for
#: enumerated params; free ranges get Query(ge=, le=) → 422 instead).
RANKING_WINDOW_DAYS = (30, 90, 365)

#: /ranking is one all-zones × all-series scan — computed once per window and
#: served from the keyed TTL cache for 15 min (quality-summary precedent; the
#: nightly scorer is the only writer, so even 15 min is generous).
RANKING_TTL_S = 900.0

#: A68 capacity labels backing the capacity-normalized MAE (installed_capacity
#: stores the READABLE labels from PSR_LABELS — B18/B19/B16 arrive translated).
WIND_CAPACITY_LABELS = ("Wind Onshore", "Wind Offshore")
SOLAR_CAPACITY_LABELS = ("Solar",)

#: The wire statement of the bias sign — consumer-facing, attached to every
#: response that serves a bias (summary, monthly, profile).
BIAS_CONVENTION = (
    "bias = mean(forecast - actual) in MW: positive means ENTSO-E's published "
    "forecast leaned HIGH. NOTE: the older /api/power/forecast-error endpoint "
    "reports the OPPOSITE sign (bias_mw = mean(actual - forecast))."
)

#: Stale window for every scoreboard endpoint — the forecast_scoreboard
#: freshness spec's own max age, looked up by key so spec and API never drift.
_SCOREBOARD_MAX_AGE_DAYS = int(
    next(s for s in SPECS if s.key == "forecast_scoreboard").max_age.total_seconds() // 86400
)

_DAY_S = 86400
_SERIES_KEYS = tuple(FORECAST_PAIRS)  # load, residual, wind, solar — engine order


# ─── shared helpers ───────────────────────────────────────────────────────────


def _require_zone(zone: str) -> None:
    if zone not in POWER_ZONES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown zone {zone!r}. Valid zones: {', '.join(POWER_ZONES)}.",
        )


def _require_series(series: str) -> None:
    if series not in FORECAST_PAIRS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown scoreboard series {series!r}. Valid: {', '.join(_SERIES_KEYS)}.",
        )


def _aggregate(rows: list[ForecastScoreDaily]) -> dict | None:
    """Day-weighted aggregate over daily score rows (module docstring math).
    Raw, unrounded floats — rounding happens once at the response edge. None
    when no usable row (a row without mae/n_hours is defensive-skipped; the
    engine never writes one)."""
    n_total = 0
    days = 0
    mae_sum = rmse2_sum = bias_sum = 0.0
    mape_sum = 0.0
    mape_n = 0
    sp_mae = sp_base = 0.0  # persistence-skill numerator/denominator sums
    ss_mae = ss_base = 0.0  # seasonal-skill sums
    for r in rows:
        if r.mae is None or not r.n_hours or r.n_hours <= 0:
            continue
        n = r.n_hours
        days += 1
        n_total += n
        mae_sum += r.mae * n
        bias_sum += (r.bias or 0.0) * n
        rmse2_sum += (r.rmse or 0.0) ** 2 * n
        if r.mape is not None:
            mape_sum += r.mape * n
            mape_n += n
        if r.mae_persistence is not None:
            sp_mae += r.mae * n
            sp_base += r.mae_persistence * n
        if r.mae_seasonal is not None:
            ss_mae += r.mae * n
            ss_base += r.mae_seasonal * n
    if days == 0:
        return None
    return {
        "days": days,
        "n_hours": n_total,
        "mae": mae_sum / n_total,
        "rmse": math.sqrt(rmse2_sum / n_total),
        "bias": bias_sum / n_total,
        "mape": (mape_sum / mape_n) if mape_n else None,
        "skill_persistence": (1.0 - sp_mae / sp_base) if sp_base > 0 else None,
        "skill_seasonal": (1.0 - ss_mae / ss_base) if ss_base > 0 else None,
    }


def _rounded_metrics(agg: dict) -> dict:
    """The shared metric block, rounded for the wire: MW to 0.1, mape to 0.01
    (it is a %), skill to 0.001 (a ratio). `mape` is present for every series
    and honestly null outside load — a stable schema beats a shape-shifting
    one. Skips the count keys — callers name those themselves."""
    r = lambda v, nd: round(v, nd) if v is not None else None  # noqa: E731
    return {
        "n_hours": agg["n_hours"],
        "mae": r(agg["mae"], 1),
        "rmse": r(agg["rmse"], 1),
        "bias": r(agg["bias"], 1),
        "mape": r(agg["mape"], 2),
        "skill_persistence": r(agg["skill_persistence"], 3),
        "skill_seasonal": r(agg["skill_seasonal"], 3),
    }


# ─── /summary ─────────────────────────────────────────────────────────────────


@router.get("/summary")
def scoreboard_summary(
    zone: str = Query(..., description="Bidding zone key, e.g. DE_LU"),
    db: Session = Depends(get_db),
    _rl: None = Depends(_rate_limit),
):
    """One zone's forecast report card: per series (load/residual/wind/solar)
    the trailing 30/90/365-day aggregates — mae/rmse/bias (+mape for load) and
    skill vs both naive baselines, day-weighted by n_hours (module docstring).
    Descriptive: grades ENTSO-E's published forecasts, makes none.

    Cheap by construction (≤365 days × 4 series of indexed daily rows for ONE
    zone) — rate-limited only, no heavy slot."""
    _require_zone(zone)
    today = datetime.now(UTC).date()
    cuts = {w: (today - timedelta(days=w)).isoformat() for w in SUMMARY_WINDOW_DAYS}
    longest = cuts[max(SUMMARY_WINDOW_DAYS)]

    rows = (
        db.query(ForecastScoreDaily)
        .filter(ForecastScoreDaily.zone == zone, ForecastScoreDaily.date >= longest)
        .all()
    )
    per_series: dict[str, dict[int, list[ForecastScoreDaily]]] = {}
    for r in rows:
        if r.series not in FORECAST_PAIRS:
            continue  # config drift — not part of the pair table
        buckets = per_series.setdefault(r.series, {w: [] for w in SUMMARY_WINDOW_DAYS})
        for w in SUMMARY_WINDOW_DAYS:
            if r.date >= cuts[w]:
                buckets[w].append(r)

    series_out = []
    for name in _SERIES_KEYS:  # engine order — deterministic
        buckets = per_series.get(name)
        if buckets is None:
            continue  # zone doesn't carry this pair — omit (quality precedent)
        windows = {}
        for w in SUMMARY_WINDOW_DAYS:
            agg = _aggregate(buckets[w])
            windows[f"{w}d"] = (
                {"days_covered": agg["days"], **_rounded_metrics(agg)} if agg else None
            )
        series_out.append({"series": name, "windows": windows})

    # as_of = the zone's newest scored day on record (indexed point lookup) —
    # deliberately NOT bounded by the 365d scan, so an anciently-stale zone
    # still reports WHEN it was last scored instead of a blank.
    latest = (
        db.query(func.max(ForecastScoreDaily.date))
        .filter(ForecastScoreDaily.zone == zone)
        .scalar()
    )
    return {
        "available": bool(series_out),
        "zone": zone,
        "series": series_out,
        "series_keys": list(_SERIES_KEYS),
        "windows_days": list(SUMMARY_WINDOW_DAYS),
        "bias_convention": BIAS_CONVENTION,
        "note": (
            "Grades ENTSO-E's own published D-1 forecasts against its published "
            "actuals — OBSYD forecasts nothing. Aggregates are day-weighted by "
            "n_hours (exact per-hour window means; rmse recombined quadratically). "
            "skill_x = 1 - mae/mae_baseline vs persistence (actual 24h ago) and "
            "seasonal (actual 168h ago); days without the baseline drop out of the "
            "skill ratio only. mape is load-only by design (wind/solar hit honest "
            "zeros, residual crosses zero)."
        ),
        **freshness_meta(latest, today, _SCOREBOARD_MAX_AGE_DAYS),
    }


# ─── /ranking ─────────────────────────────────────────────────────────────────


def _latest_capacity(db: Session) -> dict[tuple[str, str], float]:
    """(zone, psr label) → A68 capacity_mw at that zone+label's own latest year.
    One small scan of the annual reference table (37 zones × 3 labels × a few
    years), reduced in Python — never a per-zone point query."""
    labels = WIND_CAPACITY_LABELS + SOLAR_CAPACITY_LABELS
    best: dict[tuple[str, str], tuple[int, float]] = {}
    for zone, psr, year, cap in (
        db.query(
            InstalledCapacity.zone,
            InstalledCapacity.psr_type,
            InstalledCapacity.year,
            InstalledCapacity.capacity_mw,
        )
        .filter(InstalledCapacity.psr_type.in_(labels))
        .all()
    ):
        k = (zone, psr)
        if k not in best or year > best[k][0]:
            best[k] = (year, cap)
    return {k: cap for k, (_, cap) in best.items()}


def _ranking_payload(db: Session, window: int) -> dict:
    """The full enabled-zones ranking for one window — one daily-table scan plus
    one capacity scan, grouped in Python. Cached per window via cached_value;
    freshness stamps ride each REQUEST, outside the cache."""
    today = datetime.now(UTC).date()
    cut = (today - timedelta(days=window)).isoformat()
    rows = db.query(ForecastScoreDaily).filter(ForecastScoreDaily.date >= cut).all()

    cells: dict[tuple[str, str], list[ForecastScoreDaily]] = {}
    for r in rows:
        if r.zone in POWER_ZONES and r.series in FORECAST_PAIRS:
            cells.setdefault((r.zone, r.series), []).append(r)

    caps = _latest_capacity(db)
    metric_of = {"load": "mape", "wind": "nmae_pct", "solar": "nmae_pct", "residual": "mae"}
    series_out: dict[str, dict] = {}
    any_ranked = False

    for name in _SERIES_KEYS:
        entries: list[dict] = []
        for zone in POWER_ZONES:  # registry order in, metric order out
            agg = _aggregate(cells.get((zone, name), []))
            if agg is None:
                continue  # zone never scored for this pair — nothing to rank
            entry = {
                "zone": zone,
                "days_covered": agg["days"],
                "n_hours": agg["n_hours"],
                "mae": round(agg["mae"], 1),
            }
            if name == "load":
                entry["mape"] = round(agg["mape"], 2) if agg["mape"] is not None else None
                entry["_metric"] = agg["mape"]
            elif name in ("wind", "solar"):
                labels = WIND_CAPACITY_LABELS if name == "wind" else SOLAR_CAPACITY_LABELS
                cap = sum(caps.get((zone, lb), 0.0) for lb in labels)
                if cap > 0:
                    entry["capacity_mw"] = round(cap, 1)
                    entry["nmae_pct"] = round(100.0 * agg["mae"] / cap, 2)
                    entry["_metric"] = agg["mae"] / cap
                else:
                    # LISTED, never silently hidden — the absolute MAE is still
                    # honest, only the cross-zone normalization is impossible.
                    entry["capacity_mw"] = None
                    entry["nmae_pct"] = None
                    entry["_metric"] = None
                    entry["signposted"] = (
                        f"no A68 capacity data for {name} in this zone - "
                        "nMAE not computable, absolute mae shown unranked"
                    )
            else:  # residual — plain MW, caveated below
                entry["_metric"] = agg["mae"]
            entries.append(entry)

        ranked = sorted((e for e in entries if e["_metric"] is not None),
                        key=lambda e: e["_metric"])
        unranked = sorted((e for e in entries if e["_metric"] is None),
                          key=lambda e: e["zone"])
        for i, e in enumerate(ranked, start=1):
            e["rank"] = i
        for e in unranked:
            e["rank"] = None
        for e in entries:
            e.pop("_metric")
        any_ranked = any_ranked or bool(entries)
        block = {"metric": metric_of[name], "ranking": ranked + unranked}
        if name == "residual":
            block["caveat"] = (
                "mae is absolute MW — zones of different size are not comparable "
                "on it; the ordering is orientation, not a fair cross-zone grade"
            )
        series_out[name] = block

    latest = db.query(func.max(ForecastScoreDaily.date)).scalar()
    return {
        "available": any_ranked,
        "window_days": window,
        "series": series_out,
        "as_of": latest,
        "note": (
            "Grades ENTSO-E's own published D-1 forecasts — OBSYD forecasts nothing. "
            "Comparable metrics per series: load by MAPE (%); wind/solar by nMAE "
            "(100 x window MAE / A68 installed capacity of the matching technology, "
            "each zone+type at its own latest A68 year; wind = onshore + offshore); "
            "residual by absolute MAE (see caveat). Lower = better; rank 1 = the "
            "forecast the actuals stayed closest to. Aggregation is day-weighted "
            "by n_hours, as on /scoreboard/summary."
        ),
    }


@router.get("/ranking")
def scoreboard_ranking(
    window: int = Query(90, description=f"Trailing window in days ({', '.join(map(str, RANKING_WINDOW_DAYS))})"),
    db: Session = Depends(get_db),
    _rl: None = Depends(_rate_limit),
    _g: None = Depends(heavy_query_guard),
):
    """All enabled zones ranked per series by the comparable metric — which
    zone's published forecast the actuals stayed closest to. Zones without the
    A68 capacity a normalization needs are listed unranked with an explicit
    signpost, never hidden. Descriptive throughout.

    Zone-independent and the heaviest scoreboard read → one compute per window
    per ~15 min (cached_value) behind heavy_query_guard; freshness is stamped
    per request so a warm cache can't freeze age_days. The cached dict is never
    mutated — the response is rebuilt around it."""
    if window not in RANKING_WINDOW_DAYS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unsupported window {window}. Valid windows (days): "
                f"{', '.join(map(str, RANKING_WINDOW_DAYS))}."
            ),
        )
    data = cached_value(
        f"scoreboard_ranking_{window}",
        lambda: _ranking_payload(db, window),
        ttl=RANKING_TTL_S,
    )
    return {
        **data,
        **freshness_meta(data.get("as_of"), datetime.now(UTC).date(), _SCOREBOARD_MAX_AGE_DAYS),
    }


# ─── /monthly ─────────────────────────────────────────────────────────────────


@router.get("/monthly")
def scoreboard_monthly(
    zone: str = Query(..., description="Bidding zone key, e.g. DE_LU"),
    series: str = Query(..., description=f"Scoreboard series ({', '.join(_SERIES_KEYS)})"),
    db: Session = Depends(get_db),
    _rl: None = Depends(_rate_limit),
):
    """Calendar-month aggregates (UTC months) for ONE zone+series over the full
    scored history, oldest first — has the published forecast been getting
    better or worse? Same day-weighted aggregation and skill definitions as
    /summary (module docstring).

    Cheap by construction (one zone-indexed scan of a daily table; the output
    is months × one series) — rate-limited only, no heavy slot."""
    _require_zone(zone)
    _require_series(series)

    rows = (
        db.query(ForecastScoreDaily)
        .filter(ForecastScoreDaily.zone == zone, ForecastScoreDaily.series == series)
        .order_by(ForecastScoreDaily.date.asc())
        .all()
    )
    months: dict[str, list[ForecastScoreDaily]] = {}
    for r in rows:
        months.setdefault(r.date[:7], []).append(r)  # "YYYY-MM" — UTC calendar month

    data = []
    for month in sorted(months):
        agg = _aggregate(months[month])
        if agg is None:
            continue
        data.append({"month": month, "days": agg["days"], **_rounded_metrics(agg)})

    latest = rows[-1].date if rows else None
    return {
        "available": bool(data),
        "zone": zone,
        "series": series,
        "data": data,
        "bias_convention": BIAS_CONVENTION,
        "note": (
            "Grades ENTSO-E's own published D-1 forecasts — OBSYD forecasts "
            "nothing. Months are UTC calendar months, oldest first; aggregation "
            "is day-weighted by n_hours as on /scoreboard/summary; mape is "
            "load-only by design."
        ),
        **freshness_meta(latest, datetime.now(UTC).date(), _SCOREBOARD_MAX_AGE_DAYS),
    }


# ─── /profile ─────────────────────────────────────────────────────────────────


@router.get("/profile")
def scoreboard_profile(
    zone: str = Query(..., description="Bidding zone key, e.g. DE_LU"),
    series: str = Query(..., description=f"Scoreboard series ({', '.join(_SERIES_KEYS)})"),
    window: int = Query(90, ge=1, le=365, description="Trailing window (UTC days)"),
    db: Session = Depends(get_db),
    _rl: None = Depends(_rate_limit),
    _g: None = Depends(heavy_query_guard),
):
    """Forecast error by hour-of-day (0–23 UTC) for ONE zone+series: per bucket
    the mean absolute error, mean bias and n over the trailing window — does the
    published forecast miss the morning ramp, the evening peak, the solar noon?
    Buckets are UTC hours: a zone's local-time pattern appears shifted by its
    offset. Descriptive: grades ENTSO-E's published forecast, makes none.

    The daily table stores no hourly residuals, so this is computed on-read from
    the canonical hourly store through the ENGINE's own pieces — FORECAST_PAIRS
    for the pair alignment (wind's actual = gen.B18+gen.B19 summed, exactly as
    the nightly scorer does) and score_hours per bucket, so route and scoreboard
    can never grade different series. Up to a year of hourly rows per side →
    heavy-guarded; the window cap is the 422-checked Query bound."""
    _require_zone(zone)
    _require_series(series)

    now = datetime.now(UTC)
    end_ts = int(now.timestamp())
    start_ts = end_ts - window * _DAY_S

    fc_key, actual_keys = FORECAST_PAIRS[series]
    forecast = dict(read_hourly(db, fc_key, zone, start_ts, end_ts))
    actual: dict[int, float] = {}
    for key in actual_keys:
        for t, v in read_hourly(db, key, zone, start_ts, end_ts):
            actual[t] = actual.get(t, 0.0) + v

    buckets: dict[int, list[int]] = {}
    for t in forecast:
        if t in actual:
            buckets.setdefault((t % _DAY_S) // 3600, []).append(t)

    hours_out = []
    newest = None
    if buckets:
        newest = max(t for ts in buckets.values() for t in ts)
        for h in range(24):
            m = score_hours(forecast, actual, sorted(buckets.get(h, [])))
            hours_out.append(
                {
                    "hour_utc": h,
                    "n": m["n_hours"] if m else 0,
                    "mae": round(m["mae"], 1) if m else None,
                    "bias": round(m["bias"], 1) if m else None,
                }
            )

    return {
        "available": bool(buckets),
        "zone": zone,
        "series": series,
        "window_days": window,
        "hours": hours_out,
        "bias_convention": BIAS_CONVENTION,
        "note": (
            "Grades ENTSO-E's own published D-1 forecast — OBSYD forecasts "
            "nothing. hour_utc buckets are UTC hours of day (0-23); local-time "
            "features (morning ramp, solar noon) appear shifted by the zone's "
            "offset. n = hours where both forecast and actual exist; empty "
            "buckets carry honest nulls."
        ),
        **freshness_meta(
            datetime.fromtimestamp(newest, UTC).isoformat() if newest is not None else None,
            now.date(),
            _SCOREBOARD_MAX_AGE_DAYS,
        ),
    }
