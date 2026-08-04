"""Score ENTSO-E's published day-ahead forecasts against the published actuals.

The desk's forecast-error endpoint answers "how wrong was the TSO forecast this
week" on the fly; the scoreboard needs the same answer per (zone, series, day),
kept, so a reader can see whether a forecast has been getting better or worse.
This engine computes those daily aggregates from the canonical hourly store and
persists them (`forecast_score_daily`) — records.py's doctrine: recomputation
replaces the row, no incremental state to corrupt, and a day the data no longer
supports is retracted rather than left to rot.

Posture B, said plainly: every number here GRADES a forecast ENTSO-E published.
OBSYD makes no forecast. The two naive baselines (persistence = actual(t−24h),
seasonal = actual(t−168h)) are built from published actuals alone — they are the
"no model at all" yardsticks a fair grade needs, not models of ours. Only their
MAEs are stored; skill (1 − mae/mae_baseline) is derived at read time, so the
stored floats stay unrounded (rounding here would compound into that ratio).

n semantics: `n_hours` counts the hours where BOTH forecast and actual exist.
MAPE and the baseline MAEs are means over their own (possibly smaller) subsets
of those hours — MAPE drops hours whose |actual| is under the division floor,
a baseline MAE drops hours whose lagged actual is missing — and each is NULL
when its subset is empty. `n_hours` never shrinks for either.
"""

from __future__ import annotations

import math
from datetime import date, timedelta

from sqlalchemy.orm import Session

from backend.models.energy import ForecastScoreDaily
from backend.power.hourly_store import day_hour_ts, read_hourly

#: forecast series → the series whose SUM is the realised counterpart.
#: There is no wind.actual/solar.actual — realised wind/solar live in the
#: generation mix (B18+B19 / B16), same derivation the residual ingest uses.
#: THE canonical pair table: the /api/power/forecast-error route imports it
#: from here, so route and scoreboard can never grade different forecasts.
FORECAST_PAIRS: dict[str, tuple[str, list[str]]] = {
    "load": ("load.forecast", ["load.actual"]),
    "residual": ("residual.forecast", ["residual.actual"]),
    "wind": ("wind.forecast", ["gen.B18", "gen.B19"]),
    "solar": ("solar.forecast", ["gen.B16"]),
}

#: Only load gets a MAPE. Wind/solar actuals hit honest zeros every calm night,
#: and residual crosses zero by design — a percentage against those is noise.
MAPE_SERIES = frozenset({"load"})

#: MAPE division guard: hours whose |actual| reads below this are excluded from
#: MAPE (they stay in MAE/RMSE/bias and n_hours). 100 MW is records.py's
#: LOAD_MIN_PLAUSIBLE — no European zone's real load is that low, so such an
#: hour is an ingest artifact, and a percentage against it would describe the
#: glitch, not the forecast.
MAPE_ACTUAL_FLOOR_MW = 100.0

_DAY_S = 24 * 3600
PERSISTENCE_LAG_S = 24 * 3600     # yesterday, same hour
SEASONAL_LAG_S = 168 * 3600       # last week, same hour


def score_hours(
    forecast: dict[int, float],
    actual: dict[int, float],
    hours: list[int],
    *,
    with_mape: bool = False,
) -> dict | None:
    """Pure: error metrics over `hours` (each present in both dicts). None if empty.

    bias = mean(forecast − actual): positive = the published forecast leaned
    HIGH. (The forecast-error ROUTE reports the opposite sign; the model
    docstring carries the warning.) `actual` may span more than `hours` — the
    baselines reach back through it for the lagged values.
    """
    if not hours:
        return None
    errors = [forecast[t] - actual[t] for t in hours]
    n = len(errors)
    metrics = {
        "n_hours": n,
        "mae": sum(abs(e) for e in errors) / n,
        "rmse": math.sqrt(sum(e * e for e in errors) / n),
        "bias": sum(errors) / n,
        "mape": None,
        "mae_persistence": _baseline_mae(actual, hours, PERSISTENCE_LAG_S),
        "mae_seasonal": _baseline_mae(actual, hours, SEASONAL_LAG_S),
    }
    if with_mape:
        apes = [
            abs(forecast[t] - actual[t]) / abs(actual[t])
            for t in hours
            if abs(actual[t]) >= MAPE_ACTUAL_FLOOR_MW
        ]
        if apes:
            metrics["mape"] = 100.0 * sum(apes) / len(apes)
    return metrics


def _baseline_mae(actual: dict[int, float], hours: list[int], lag_s: int) -> float | None:
    """MAE of the naive "actual, `lag_s` ago" baseline, over the scored hours
    whose lagged actual exists. None when none does — a baseline scored on no
    hours is not a small number, it is no number."""
    diffs = [abs(actual[t] - actual[t - lag_s]) for t in hours if (t - lag_s) in actual]
    return sum(diffs) / len(diffs) if diffs else None


def compute_and_store_scores(db: Session, zone: str, day: str) -> dict:
    """Recompute + upsert one zone's scores for one UTC day ("YYYY-MM-DD").
    Idempotent; a zone/day with no overlapping data writes nothing (and retracts
    a stale row if one exists). Returns {"written": n, "removed": m}."""
    return compute_and_store_range(db, zone, day, day)


def compute_and_store_range(db: Session, zone: str, start_day: str, end_day: str) -> dict:
    """Recompute + upsert scores for `zone` over [start_day, end_day] inclusive.

    The scheduler's trailing-window recompute and the one-time backfill both
    land here, so they can never disagree. Each series is read ONCE over the
    whole range (plus the seasonal lookback for actuals) and scored day by day
    — a multi-year backfill is 9 indexed range scans per zone, not 9 per day.
    Commits once at the end.
    """
    days = _day_list(start_day, end_day)
    start_ts = day_hour_ts(start_day, 0)
    end_ts = day_hour_ts(end_day, 0) + _DAY_S

    written = removed = 0
    for series, (fc_key, actual_keys) in FORECAST_PAIRS.items():
        forecast = dict(read_hourly(db, fc_key, zone, start_ts, end_ts))
        actual: dict[int, float] = {}
        for key in actual_keys:
            for t, v in read_hourly(db, key, zone, start_ts - SEASONAL_LAG_S, end_ts):
                actual[t] = actual.get(t, 0.0) + v

        # Bucket the common hours per day in ONE pass over the forecast points —
        # a per-day scan of the whole range's dict would be O(days × points),
        # which a multi-year backfill turns into minutes for no reason.
        buckets: list[list[int]] = [[] for _ in days]
        for t in forecast:
            i = (t - start_ts) // _DAY_S
            if 0 <= i < len(days) and t in actual:
                buckets[i].append(t)

        for day, hours in zip(days, buckets):
            metrics = score_hours(forecast, actual, sorted(hours), with_mape=series in MAPE_SERIES)
            w, r = _upsert(db, zone, series, day, metrics)
            written += w
            removed += r

    db.commit()
    return {"written": written, "removed": removed}


def _upsert(db: Session, zone: str, series: str, day: str, metrics: dict | None) -> tuple[int, int]:
    """One row per (zone, series, day), replaced in place. metrics=None RETRACTS:
    a day a later revision of the data no longer supports disappears, rather
    than sitting in the scoreboard forever (episodes doctrine)."""
    row = (
        db.query(ForecastScoreDaily)
        .filter_by(zone=zone, series=series, date=day)
        .one_or_none()
    )
    if metrics is None:
        if row is not None:
            db.delete(row)
            return 0, 1
        return 0, 0
    if row is None:
        row = ForecastScoreDaily(zone=zone, series=series, date=day)
        db.add(row)
    row.n_hours = metrics["n_hours"]
    row.mae = metrics["mae"]
    row.rmse = metrics["rmse"]
    row.bias = metrics["bias"]
    row.mape = metrics["mape"]
    row.mae_persistence = metrics["mae_persistence"]
    row.mae_seasonal = metrics["mae_seasonal"]
    return 1, 0


def _day_list(start_day: str, end_day: str) -> list[str]:
    a, b = date.fromisoformat(start_day), date.fromisoformat(end_day)
    return [(a + timedelta(days=i)).isoformat() for i in range((b - a).days + 1)]
