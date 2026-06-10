"""Unit tests for the pure validation metrics.

Synthetic series with a known answer — no DB, no network. These gate the
math, so a refactor that breaks the statistics fails loudly.
"""

from __future__ import annotations

import numpy as np

from backend.analytics.validation import metrics


def test_rankdata_matches_average_ties():
    # values: 10, 10, 20, 30 -> ranks 1.5, 1.5, 3, 4
    r = metrics.rankdata(np.array([10.0, 10.0, 20.0, 30.0]))
    assert list(r) == [1.5, 1.5, 3.0, 4.0]


def test_spearman_perfect_monotonic_is_one():
    x = np.arange(50.0)
    y = x ** 2  # strictly increasing but nonlinear -> Spearman == 1
    assert metrics.spearman_ic(x, y) == 1.0


def test_spearman_perfect_inverse_is_minus_one():
    x = np.arange(50.0)
    y = -x
    assert metrics.spearman_ic(x, y) == -1.0


def test_spearman_pure_noise_is_near_zero():
    rng = np.random.default_rng(42)
    x = rng.normal(size=2000)
    y = rng.normal(size=2000)
    assert abs(metrics.spearman_ic(x, y)) < 0.1


def test_spearman_too_few_points_is_nan():
    assert np.isnan(metrics.spearman_ic([1.0], [2.0]))


def test_newey_west_large_t_for_strong_relationship():
    rng = np.random.default_rng(0)
    x = rng.normal(size=500)
    y = x + rng.normal(scale=0.1, size=500)  # near-perfect
    t = metrics.newey_west_tstat(x, y, lag=0)
    assert t > 10


def test_newey_west_small_t_for_noise():
    rng = np.random.default_rng(1)
    x = rng.normal(size=500)
    y = rng.normal(size=500)
    t = metrics.newey_west_tstat(x, y, lag=6)
    assert abs(t) < 2.5  # no real relationship -> not significant


def test_newey_west_overlap_shrinks_t_vs_naive():
    # Overlapping-window case: a slow signal and overlapping forward returns
    # both inherit persistence from a common AR(1) factor, so the cross-product
    # g_t = z(x)*z(y) is autocorrelated. The HAC t-stat (lag>0) must then be
    # smaller in magnitude than the naive lag-0 one, which ignores the overlap.
    rng = np.random.default_rng(7)
    n = 800
    factor = np.zeros(n)
    for i in range(1, n):
        factor[i] = 0.9 * factor[i - 1] + rng.normal()
    x = factor + rng.normal(scale=0.3, size=n)
    y = factor + rng.normal(scale=0.3, size=n)
    t_naive = abs(metrics.newey_west_tstat(x, y, lag=0))
    t_hac = abs(metrics.newey_west_tstat(x, y, lag=20))
    assert t_hac < t_naive


def test_event_study_planted_lift_and_hit_rate():
    # When signal is high, forward return is positive; otherwise random small.
    rng = np.random.default_rng(3)
    n = 400
    signal = rng.uniform(0, 100, size=n)
    forward = rng.normal(scale=0.01, size=n)
    high = signal >= 80
    forward[high] += 0.05  # planted positive drift after high signal
    res = metrics.event_study(signal, forward, threshold=80, direction="above")
    assert res["n_event"] == int(high.sum())
    assert res["lift"] > 0.02
    assert res["hit_rate"] > 0.8
    assert res["n_total"] == n


def test_event_study_no_events_is_safe():
    res = metrics.event_study(
        np.array([1.0, 2.0, 3.0]), np.array([0.1, -0.1, 0.0]), threshold=100, direction="above"
    )
    assert res["n_event"] == 0
    assert np.isnan(res["mean_event"])
    assert not np.isnan(res["mean_base"])


def test_event_study_below_direction_counts_negative_hits():
    signal = np.array([10.0, 5.0, 1.0, 2.0])
    forward = np.array([0.0, -0.02, -0.03, 0.01])
    res = metrics.event_study(signal, forward, threshold=5, direction="below")
    # events: signal <= 5 -> indices 1,2,3; hits = forward < 0 -> indices 1,2
    assert res["n_event"] == 3
    assert abs(res["hit_rate"] - 2 / 3) < 1e-9
