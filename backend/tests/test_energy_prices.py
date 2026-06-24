"""Energy price collector — exercise the upsert path with yfinance mocked."""

from __future__ import annotations

import pandas as pd

from backend.collectors import energy_prices
from backend.models.energy import EnergyPrice


def _frame(rows):
    """rows: list[(date_str, close)] -> a yfinance-style DataFrame."""
    idx = pd.to_datetime([d for d, _ in rows])
    return pd.DataFrame({"Close": [c for _, c in rows]}, index=idx)


def test_store_symbol_inserts_then_upserts_idempotently(db_session, monkeypatch):
    monkeypatch.setattr(
        energy_prices.yf,
        "download",
        lambda *a, **k: _frame([("2024-01-02", 30.0), ("2024-01-03", 31.5)]),
    )

    # First run: both dates inserted.
    n1 = energy_prices._store_symbol(db_session, "TTF", "TTF=F")
    db_session.commit()
    assert n1 == 2
    assert db_session.query(EnergyPrice).filter_by(symbol="TTF").count() == 2

    # Second run: same dates -> no new rows (update in place), still 2.
    n2 = energy_prices._store_symbol(db_session, "TTF", "TTF=F")
    db_session.commit()
    assert n2 == 0
    assert db_session.query(EnergyPrice).filter_by(symbol="TTF").count() == 2
    row = db_session.query(EnergyPrice).filter_by(date="2024-01-02", symbol="TTF").first()
    assert row.close == 30.0


def test_store_symbol_handles_empty(db_session, monkeypatch):
    monkeypatch.setattr(energy_prices.yf, "download", lambda *a, **k: pd.DataFrame())
    assert energy_prices._store_symbol(db_session, "TTF", "TTF=F") == 0
    assert db_session.query(EnergyPrice).count() == 0
