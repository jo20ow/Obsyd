"""
Unit tests for backend/signals/correlation.py — the pure-function core
of the auditable signal pitch.

We test the three internal helpers directly (no DB, no fixtures): the
Pearson computation, the lag scanner, and the current-event detector.
`compute_correlations()` itself is the DB-heavy top-level orchestrator
and is exercised indirectly via these building blocks.
"""

from __future__ import annotations

import math

import pytest

from backend.signals.correlation import (
    IMPACT_THRESHOLD,
    _compute_lagged_correlations,
    _detect_current_event,
    _pearson,
)


# ----------------------------- _pearson -----------------------------


def test_pearson_perfect_positive():
    xs = list(range(20))
    ys = [x * 2 + 3 for x in xs]
    assert _pearson(xs, ys) == pytest.approx(1.0, abs=1e-9)


def test_pearson_perfect_negative():
    xs = list(range(20))
    ys = [-x for x in xs]
    assert _pearson(xs, ys) == pytest.approx(-1.0, abs=1e-9)


def test_pearson_no_correlation_roughly_zero():
    # Sine vs cosine over a few periods — orthogonal in expectation.
    n = 200
    xs = [math.sin(i * 0.1) for i in range(n)]
    ys = [math.cos(i * 0.1) for i in range(n)]
    r = _pearson(xs, ys)
    assert abs(r) < 0.2, f"expected near-zero correlation, got {r}"


def test_pearson_returns_zero_on_too_short():
    # Guard: n < 10 returns 0.0 by design (low signal).
    assert _pearson([1, 2, 3], [4, 5, 6]) == 0.0


def test_pearson_returns_zero_on_constant_input():
    # Zero variance in xs or ys is a degenerate case.
    xs = [5.0] * 20
    ys = list(range(20))
    assert _pearson(xs, ys) == 0.0
    assert _pearson(ys, xs) == 0.0


# --------------------- _compute_lagged_correlations ---------------------


def _build_lagged_inputs(tanker_vals: list[float], lag_shift: int = 0):
    """Build the 6-arg fixture for _compute_lagged_correlations.

    `lag_shift`: if > 0, Brent is the tanker series shifted forward by
    `lag_shift` days (so the correlator should pick lag == lag_shift as
    best for the level series).
    """
    n = len(tanker_vals)
    common_dates = [f"2026-01-{i + 1:02d}" if i < 31 else f"2026-02-{i - 30:02d}" for i in range(n)]
    tanker_map = {d: (v, v) for d, v in zip(common_dates, tanker_vals)}

    # Brent dates extend beyond common dates by MAX_LAG to leave room.
    extra = 10
    brent_only_dates = [
        f"2026-03-{i + 1:02d}" for i in range(extra)
    ]
    all_brent_dates = common_dates + brent_only_dates

    # If lag_shift > 0, "future" Brent at index i+lag = tanker[i].
    brent_map = {}
    for i, d in enumerate(all_brent_dates):
        src = i - lag_shift
        if 0 <= src < n:
            brent_map[d] = float(tanker_vals[src])
        else:
            brent_map[d] = float(tanker_vals[0])  # neutral filler
    brent_date_idx = {d: i for i, d in enumerate(all_brent_dates)}
    return tanker_vals, common_dates, tanker_map, brent_map, all_brent_dates, brent_date_idx


def test_compute_lagged_correlations_returns_structured_dict():
    vals = list(range(40))
    args = _build_lagged_inputs(vals, lag_shift=0)
    out = _compute_lagged_correlations(*args)
    assert set(out) == {"level", "delta"}
    for branch in ("level", "delta"):
        assert "corr_0" in out[branch]
        assert "lags" in out[branch]
        assert 0 in out[branch]["lags"]
        assert "best_lag" in out[branch]
        assert "best_lag_r" in out[branch]


def test_compute_lagged_correlations_detects_known_lag():
    # Brent leads tanker by 3 days -> best_lag for the level series == 3.
    vals = list(range(40))
    args = _build_lagged_inputs(vals, lag_shift=3)
    out = _compute_lagged_correlations(*args)
    # Correlation at the planted lag should be very strong.
    assert out["level"]["lags"][3] == pytest.approx(1.0, abs=1e-6)
    assert out["level"]["best_lag"] == 3


# ----------------------- _detect_current_event -----------------------


def _row(date_str: str, n_total: int):
    """Mimic the (date, n_tanker, n_total) shape `_detect_current_event` reads."""
    return (date_str, n_total, n_total)


def test_detect_event_returns_none_for_normal_traffic():
    rows = [_row(f"2026-05-{i:02d}", 100) for i in range(1, 11)]
    brent_map = {f"2026-05-{i:02d}": 80.0 for i in range(1, 11)}
    result = _detect_current_event(rows, [100] * 10, [r[0] for r in rows], brent_map, avg_total=100)
    assert result is None


def test_detect_event_triggers_when_anomaly_above_threshold():
    # Last 3 days are at 50% of baseline (anomaly = -50%, exceeds the 30%
    # IMPACT_THRESHOLD as |%|).
    normal_days = [_row(f"2026-05-{i:02d}", 100) for i in range(1, 8)]
    spike_days = [_row(f"2026-05-{i:02d}", 50) for i in range(8, 11)]
    rows = normal_days + spike_days
    common_dates = [r[0] for r in rows]
    brent_map = {d: 80.0 for d in common_dates}
    # Brent moves on the spike days
    brent_map["2026-05-10"] = 88.0

    result = _detect_current_event(rows, [r[1] for r in rows], common_dates, brent_map, avg_total=100)
    assert result is not None
    assert result["event_start"] == "2026-05-08"
    assert result["duration_days"] == 3
    assert result["anomaly_pct"] == pytest.approx(-50.0, abs=0.5)
    assert result["brent_at_start"] == 80.0
    assert result["brent_current"] == 88.0
    assert result["brent_change_pct"] == pytest.approx(10.0, abs=0.1)


def test_detect_event_returns_none_when_avg_total_zero():
    rows = [_row("2026-05-01", 100)]
    assert _detect_current_event(rows, [100], ["2026-05-01"], {"2026-05-01": 80}, avg_total=0) is None


def test_impact_threshold_is_30_percent():
    # Sanity: the module constant matches the documented 30% threshold.
    assert IMPACT_THRESHOLD == 30.0
