"""Tests for disruption-score components.

Focus: the backwardation component, which silently returned 0.0 for its entire
history because it read _fetch_structure() through get_market_structure()'s
"curves" wrapper. The validation backtest flagged it as a flat (zero-variance)
signal; these tests pin the corrected shape so it can't regress.
"""

from __future__ import annotations

from backend.analytics import disruption_score


def _patch_structure(monkeypatch, payload):
    # _backwardation_component imports _fetch_structure inside the function,
    # so patching the source-module attribute is what takes effect.
    monkeypatch.setattr(
        "backend.signals.market_structure._fetch_structure",
        lambda: payload,
    )


def test_backwardation_nonzero_for_unwrapped_curve_shape(monkeypatch):
    # Regression: _fetch_structure returns the curve dict DIRECTLY. The old
    # code read data["curves"]["BRENT"] and always got 0.0 — this would fail it.
    _patch_structure(monkeypatch, {"WTI": {"spread_pct": -1.88}, "BRENT": {"spread_pct": -1.69}})
    assert disruption_score._backwardation_component() > 0


def test_backwardation_value_for_known_spread(monkeypatch):
    # spread_pct -1.5 -> abs 1.5 in (1, 2] -> 20 + (1.5 - 1) * 40 = 40.0
    _patch_structure(monkeypatch, {"BRENT": {"spread_pct": -1.5}})
    assert abs(disruption_score._backwardation_component() - 40.0) < 1e-6


def test_backwardation_contango_is_zero(monkeypatch):
    # Positive spread = contango = no disruption signal.
    _patch_structure(monkeypatch, {"BRENT": {"spread_pct": 1.2}})
    assert disruption_score._backwardation_component() == 0.0


def test_backwardation_deep_caps_at_100(monkeypatch):
    _patch_structure(monkeypatch, {"BRENT": {"spread_pct": -9.0}})
    assert disruption_score._backwardation_component() == 100.0


def test_backwardation_missing_brent_is_zero(monkeypatch):
    _patch_structure(monkeypatch, {"WTI": {"spread_pct": -1.0}})
    assert disruption_score._backwardation_component() == 0.0


def test_backwardation_empty_is_zero(monkeypatch):
    _patch_structure(monkeypatch, {})
    assert disruption_score._backwardation_component() == 0.0
