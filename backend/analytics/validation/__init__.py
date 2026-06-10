"""Signal validation framework.

Measures whether Obsyd's signals carry a verifiable forward-return
relationship, and prunes signal components that add nothing — the same
rigor applied to the disabled ValueKick lineup layer.

See docs/signal-validation.md for the full design.

Layering:
  metrics.py  — pure, numpy-only statistics (no DB, no network). Unit-tested.
  prices.py   — FRED price access + forward-return alignment.
  weights.py  — disruption-score weight backtest (pure core + thin DB adapter).
"""
