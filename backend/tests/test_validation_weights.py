"""Unit tests for the disruption-score weight backtest core.

Planted data: one predictive component + five noise components. The backtest
must rank the predictive one highest and flag noise components as drop
candidates — the ValueKick "does this layer help?" test.
"""

from __future__ import annotations

import numpy as np

from backend.analytics.validation import weights
from backend.analytics.validation.prices import forward_log_returns


def _planted(n=400, seed=11):
    """Component 0 is predictive of the forward return; 1..5 are noise."""
    rng = np.random.default_rng(seed)
    k = 6
    comps = rng.uniform(0, 100, size=(n, k))
    forward = 0.001 * (comps[:, 0] - 50) + rng.normal(scale=0.005, size=n)
    base_weights = np.array([0.25, 0.20, 0.10, 0.15, 0.15, 0.15])
    return comps, forward, base_weights


def test_predictive_component_has_highest_ic():
    comps, forward, _ = _planted()
    ics = weights.component_ics(comps, forward)
    assert np.argmax(ics) == 0
    assert ics[0] > 0.5
    for j in range(1, 6):
        assert abs(ics[j]) < 0.2


def test_ic_proportional_weights_concentrate_on_signal():
    comps, forward, _ = _planted()
    w = weights.ic_proportional_weights(comps, forward)
    assert abs(w.sum() - 1.0) < 1e-9
    assert w[0] > 0.5  # most weight on the predictive component


def test_ic_fitted_beats_equal_weights_out_of_sample():
    comps, forward, base = _planted()
    res = weights.run_backtest(comps, forward, base_weights=base, horizon_days=7)
    oos = res["oos_ic"]
    assert oos["ic_proportional_oos"] >= oos["equal"]


def test_ablation_flags_a_noise_component_and_keeps_signal():
    comps, forward, base = _planted()
    res = weights.run_backtest(comps, forward, base_weights=base, horizon_days=7)
    rows = {c["name"]: c for c in res["components"]}
    names = res["component_names"]
    signal_name = names[0]
    # The predictive component must never be a drop candidate.
    assert rows[signal_name]["verdict"] == "keep"
    # At least one of the five noise components should be flagged for pruning.
    noise_verdicts = [rows[names[j]]["verdict"] for j in range(1, 6)]
    assert "drop?" in noise_verdicts


def test_small_sample_is_not_confident():
    comps, forward, base = _planted(n=20)
    res = weights.run_backtest(comps, forward, base_weights=base, horizon_days=7)
    assert res["confident"] is False


def test_nan_returns_rows_are_dropped():
    comps, forward, base = _planted(n=100)
    forward[:10] = np.nan  # immature horizon rows
    res = weights.run_backtest(comps, forward, base_weights=base, horizon_days=7)
    assert res["n"] == 90


def test_forward_log_returns_no_lookahead():
    # Price series only reaches 2026-01-10; a 7d return from 2026-01-08 would
    # need 2026-01-15, which doesn't exist yet -> must be nan, not fabricated.
    price_map = {f"2026-01-{d:02d}": 80.0 + d for d in range(1, 11)}
    dates = ["2026-01-02", "2026-01-08"]
    out = forward_log_returns(price_map, dates, horizon_days=7)
    assert np.isfinite(out[0])  # 01-02 + 7d = 01-09, available
    assert np.isnan(out[1])     # 01-08 + 7d = 01-15, not available
