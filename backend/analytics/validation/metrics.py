"""Pure statistical metrics for signal validation.

numpy-only, no DB and no network — every function takes arrays in and
returns scalars/dicts out, so they are trivially unit-testable against
synthetic series with a known answer.

Two evaluation modes (see docs/signal-validation.md):
  - continuous signals -> rank IC + HAC-robust t-stat
  - event / threshold signals -> event study (lift vs baseline + hit rate)
"""

from __future__ import annotations

import numpy as np

__all__ = [
    "rankdata",
    "pearson",
    "spearman_ic",
    "newey_west_tstat",
    "event_study",
]


def rankdata(a: np.ndarray) -> np.ndarray:
    """Average-tie ranks (1-based), matching scipy.stats.rankdata('average').

    Implemented in numpy so the project keeps a single math dependency.
    """
    a = np.asarray(a, dtype=float)
    sorter = np.argsort(a, kind="mergesort")
    inv = np.empty(len(a), dtype=np.intp)
    inv[sorter] = np.arange(len(a), dtype=np.intp)
    a_sorted = a[sorter]
    obs = np.r_[True, a_sorted[1:] != a_sorted[:-1]]
    dense = obs.cumsum()[inv]
    # `count[i]` = number of values strictly less than the i-th dense group.
    count = np.r_[np.nonzero(obs)[0], len(a)]
    # Mid-rank for ties: average of the first and last position in the group.
    return 0.5 * (count[dense] + count[dense - 1] + 1)


def pearson(x: np.ndarray, y: np.ndarray) -> float:
    """Pearson correlation. Returns nan on degenerate input."""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    xc = x - x.mean()
    yc = y - y.mean()
    dx = np.sqrt((xc * xc).sum())
    dy = np.sqrt((yc * yc).sum())
    if dx == 0 or dy == 0:
        return float("nan")
    return float((xc * yc).sum() / (dx * dy))


def _finite_pair(x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    mask = np.isfinite(x) & np.isfinite(y)
    return x[mask], y[mask]


def spearman_ic(signal: np.ndarray, forward_return: np.ndarray) -> float:
    """Rank information coefficient: Spearman correlation between the signal
    level and the forward return. nan if fewer than 3 aligned observations."""
    x, y = _finite_pair(signal, forward_return)
    if len(x) < 3:
        return float("nan")
    return pearson(rankdata(x), rankdata(y))


def newey_west_tstat(x: np.ndarray, y: np.ndarray, lag: int) -> float:
    """HAC (Newey-West) t-stat that corr(x, y) != 0.

    With overlapping forward-return windows (e.g. 7d returns sampled daily),
    observations are autocorrelated and the naive t-stat overstates
    significance. We form the standardized cross-product g_t = z(x)_t * z(y)_t
    (whose mean is the Pearson r) and estimate the variance of its mean with a
    Bartlett-kernel HAC estimator using `lag` lags. Set lag = horizon_days - 1.

    Returns nan when there are too few points to estimate `lag` covariances.
    """
    x, y = _finite_pair(x, y)
    n = len(x)
    lag = max(0, int(lag))
    if n < max(3, lag + 2):
        return float("nan")
    sx = x.std(ddof=0)
    sy = y.std(ddof=0)
    if sx == 0 or sy == 0:
        return float("nan")
    zx = (x - x.mean()) / sx
    zy = (y - y.mean()) / sy
    g = zx * zy
    gbar = g.mean()  # == Pearson r
    gc = g - gbar
    var = float((gc @ gc) / n)  # gamma_0
    for lcov in range(1, lag + 1):
        weight = 1.0 - lcov / (lag + 1)
        gamma = float((gc[lcov:] @ gc[:-lcov]) / n)
        var += 2.0 * weight * gamma
    if var <= 0:
        return float("nan")
    se = np.sqrt(var / n)
    if se == 0:
        return float("nan")
    return float(gbar / se)


def event_study(
    signal: np.ndarray,
    forward_return: np.ndarray,
    threshold: float,
    *,
    direction: str = "above",
) -> dict:
    """Compare forward returns on days the signal crosses a threshold vs the
    unconditional baseline.

    direction="above": event = signal >= threshold, "hit" = forward return > 0.
    direction="below": event = signal <= threshold, "hit" = forward return < 0.

    Returns counts, mean event vs baseline return, lift (event - baseline) and
    directional hit rate. All-nan-safe; returns n_event=0 when nothing fires.
    """
    if direction not in ("above", "below"):
        raise ValueError("direction must be 'above' or 'below'")
    sig, ret = _finite_pair(signal, forward_return)
    n = len(ret)
    base = {
        "n_total": int(n),
        "n_event": 0,
        "mean_event": float("nan"),
        "mean_base": float(ret.mean()) if n else float("nan"),
        "lift": float("nan"),
        "hit_rate": float("nan"),
    }
    if n == 0:
        return base
    event = sig >= threshold if direction == "above" else sig <= threshold
    n_event = int(event.sum())
    if n_event == 0:
        return base
    ev_ret = ret[event]
    mean_event = float(ev_ret.mean())
    mean_base = float(ret.mean())
    hits = ev_ret > 0 if direction == "above" else ev_ret < 0
    return {
        "n_total": int(n),
        "n_event": n_event,
        "mean_event": mean_event,
        "mean_base": mean_base,
        "lift": mean_event - mean_base,
        "hit_rate": float(hits.mean()),
    }
