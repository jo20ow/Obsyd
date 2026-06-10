"""Disruption-score weight backtest.

The flagship validation: the disruption score combines 6 components with
fixed weights (disruption_score.WEIGHTS). This module measures whether those
weights — and each component — actually relate to forward Brent returns, the
same "does this layer improve the score?" test applied to ValueKick.

Design: a pure, DB-free core (testable with planted data) plus a thin adapter
that loads DisruptionScoreHistory + FRED. Methodology guardrails:
  - one observation per day (last row; dedupes the 2-hourly cadence)
  - forward returns never use look-ahead (see prices.forward_log_returns)
  - weights are evaluated OUT-OF-SAMPLE: IC-proportional weights are fit on a
    training prefix and scored on a held-out suffix; in-sample fit is never
    reported
  - HAC t-stats (overlap = horizon-1) so overlapping windows don't inflate
    significance
  - n < MIN_CONFIDENT_N is never called "confident"
"""

from __future__ import annotations

import numpy as np

from backend.analytics.validation import metrics

# Component order is fixed and shared with the DB adapter below.
COMPONENT_NAMES = ("hormuz", "cape", "storage", "crack", "backwardation", "sentiment")

# Below this sample size we refuse to make any predictive claim.
MIN_CONFIDENT_N = 30

# An ablation must improve OOS IC by at least this much to flag a "drop".
DROP_IMPROVEMENT_EPS = 0.02


def _normalize(weights: np.ndarray) -> np.ndarray:
    total = float(np.abs(weights).sum())
    if total == 0:
        return weights
    return weights / total


def composite_ic(components: np.ndarray, weights: np.ndarray, forward_return: np.ndarray) -> float:
    """Rank IC of the weighted composite vs forward return."""
    composite = components @ weights
    return metrics.spearman_ic(composite, forward_return)


def component_ics(components: np.ndarray, forward_return: np.ndarray) -> list[float]:
    """Marginal rank IC of each component column vs forward return."""
    return [metrics.spearman_ic(components[:, j], forward_return) for j in range(components.shape[1])]


def ic_proportional_weights(components: np.ndarray, forward_return: np.ndarray) -> np.ndarray:
    """Weights proportional to each component's IC (clipped at 0 so a component
    that points the wrong way gets no weight rather than negative weight)."""
    ics = np.array(component_ics(components, forward_return), dtype=float)
    ics = np.nan_to_num(ics, nan=0.0)
    ics = np.clip(ics, 0.0, None)
    if ics.sum() == 0:
        return np.full(components.shape[1], 1.0 / components.shape[1])
    return _normalize(ics)


def run_backtest(
    components: np.ndarray,
    forward_return: np.ndarray,
    *,
    base_weights: np.ndarray,
    component_names: tuple[str, ...] = COMPONENT_NAMES,
    horizon_days: int = 7,
    train_frac: float = 0.6,
) -> dict:
    """Evaluate weighting schemes out-of-sample and produce keep/drop verdicts.

    `components`: (n, k) matrix of component scores, chronologically ordered.
    `forward_return`: (n,) forward returns aligned to the same rows (may contain
        nan for rows whose horizon hasn't matured — those rows are dropped).
    """
    components = np.asarray(components, dtype=float)
    forward_return = np.asarray(forward_return, dtype=float)
    k = components.shape[1]

    # Drop rows without a matured forward return.
    row_mask = np.isfinite(forward_return) & np.all(np.isfinite(components), axis=1)
    comp = components[row_mask]
    ret = forward_return[row_mask]
    n = len(ret)

    base_weights = _normalize(np.asarray(base_weights, dtype=float))
    equal_weights = np.full(k, 1.0 / k)

    result: dict = {
        "horizon_days": horizon_days,
        "n": int(n),
        "confident": bool(n >= MIN_CONFIDENT_N),
        "component_names": list(component_names),
    }

    if n < 3:
        result["note"] = "insufficient data — need aligned signal/return rows"
        return result

    # Full-sample marginal ICs + HAC t-stats (descriptive, in-sample).
    full_ics = component_ics(comp, ret)
    hac_lag = max(0, horizon_days - 1)
    full_t = [
        metrics.newey_west_tstat(comp[:, j], ret, hac_lag) for j in range(k)
    ]

    # Out-of-sample split: fit IC-proportional weights on the training prefix,
    # score every scheme on the held-out suffix.
    split = max(2, int(n * train_frac))
    if split >= n - 1:
        # Too little data for a clean split — report descriptive only.
        result["note"] = "too few rows for an out-of-sample split; ICs are in-sample only"
        result["components"] = [
            {
                "name": component_names[j],
                "ic_in_sample": _round(full_ics[j]),
                "hac_t": _round(full_t[j]),
                "weight_current": _round(float(base_weights[j])),
            }
            for j in range(k)
        ]
        return result

    train_c, train_r = comp[:split], ret[:split]
    test_c, test_r = comp[split:], ret[split:]
    fitted = ic_proportional_weights(train_c, train_r)

    oos = {
        "current": composite_ic(test_c, base_weights, test_r),
        "equal": composite_ic(test_c, equal_weights, test_r),
        "ic_proportional_oos": composite_ic(test_c, fitted, test_r),
    }
    result["oos_ic"] = {key: _round(val) for key, val in oos.items()}
    result["oos_n"] = int(len(test_r))

    # Drop-one-out ablation on the held-out suffix, relative to current weights.
    full_oos_ic = oos["current"]
    comp_rows = []
    for j in range(k):
        w_drop = base_weights.copy()
        w_drop[j] = 0.0
        w_drop = _normalize(w_drop)
        ic_without = composite_ic(test_c, w_drop, test_r)
        improvement = _safe_sub(ic_without, full_oos_ic)
        verdict = "drop?" if (improvement is not None and improvement >= DROP_IMPROVEMENT_EPS) else "keep"
        comp_rows.append(
            {
                "name": component_names[j],
                "ic_in_sample": _round(full_ics[j]),
                "hac_t": _round(full_t[j]),
                "weight_current": _round(float(base_weights[j])),
                "weight_ic_fit": _round(float(fitted[j])),
                "oos_ic_without": _round(ic_without),
                "oos_ic_delta_if_dropped": _round(improvement),
                "verdict": verdict,
            }
        )
    result["components"] = comp_rows
    return result


def _round(x, nd: int = 4):
    if x is None:
        return None
    try:
        if np.isnan(x):
            return None
    except TypeError:
        return None
    return round(float(x), nd)


def _safe_sub(a: float, b: float):
    if a is None or b is None or np.isnan(a) or np.isnan(b):
        return None
    return a - b


# ─── DB adapter ──────────────────────────────────────────────────────────────


def load_disruption_components(db) -> tuple[list[str], np.ndarray]:
    """One observation per day from DisruptionScoreHistory (last row of each
    day), as (dates, (n, 6) matrix) in component order COMPONENT_NAMES."""
    from backend.models.analytics import DisruptionScoreHistory

    rows = (
        db.query(DisruptionScoreHistory)
        .order_by(
            DisruptionScoreHistory.date.asc(),
            DisruptionScoreHistory.created_at.asc(),
        )
        .all()
    )
    by_day: dict[str, DisruptionScoreHistory] = {}
    for r in rows:
        by_day[r.date] = r  # later row of the same day overwrites — keeps last
    dates = sorted(by_day)
    matrix = np.array(
        [
            [
                by_day[d].hormuz_component,
                by_day[d].cape_component,
                by_day[d].storage_component,
                by_day[d].crack_component,
                by_day[d].backwardation_component,
                by_day[d].sentiment_component,
            ]
            for d in dates
        ],
        dtype=float,
    )
    return dates, matrix


def backtest_disruption(db, horizon_days: int = 7) -> dict:
    """Load real data and run the backtest. Returns the run_backtest dict,
    or a {'n': 0, ...} stub when there isn't any history yet."""
    from backend.analytics.disruption_score import WEIGHTS
    from backend.analytics.validation.prices import BRENT_SERIES, forward_log_returns, load_price_map

    dates, components = load_disruption_components(db)
    if len(dates) == 0:
        return {"horizon_days": horizon_days, "n": 0, "confident": False, "note": "no disruption_score_history rows"}

    price_map = load_price_map(db, BRENT_SERIES)
    forward = forward_log_returns(price_map, dates, horizon_days)
    base_weights = np.array([WEIGHTS[name] for name in COMPONENT_NAMES], dtype=float)
    return run_backtest(
        components,
        forward,
        base_weights=base_weights,
        component_names=COMPONENT_NAMES,
        horizon_days=horizon_days,
    )
