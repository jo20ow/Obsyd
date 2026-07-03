"""Spark spread collector tests — no network, no ENTSO-E token required.

Seeds EnergyPrice rows directly into the in-memory DB, runs the collector's
compute+upsert logic, and verifies:
  - spark_spread == power − gas / efficiency  (formula correctness)
  - inner-join semantics (missing-price dates are skipped)
  - idempotent upsert (second run doesn't duplicate rows)
  - heat_rate respects settings.gas_ccgt_efficiency
  - co2_price and clean_spark_spread are always None (deferred)
"""

from __future__ import annotations

import pytest

from backend.models.energy import EnergyPrice, SparkSpreadHistory

# ─── helpers ─────────────────────────────────────────────────────────────────


def _seed(db, symbol: str, rows: list[tuple[str, float]]) -> None:
    """Insert EnergyPrice rows for the given symbol."""
    for date_str, close in rows:
        db.add(EnergyPrice(date=date_str, symbol=symbol, close=close))
    db.commit()


def _run_compute(db):
    """Run the sync inner compute function directly (avoids async overhead)."""
    from backend.collectors.spark_spreads import _compute_and_upsert

    return _compute_and_upsert(db)


# ─── tests ────────────────────────────────────────────────────────────────────


def test_spark_formula_default_efficiency(db_session, monkeypatch):
    """spark_spread = power − gas × (1/0.50) = power − 2 × gas."""
    from backend.config import settings as _settings

    monkeypatch.setattr(_settings, "gas_ccgt_efficiency", 0.50)

    power, gas = 80.0, 30.0
    _seed(db_session, "POWER_DE", [("2025-01-10", power)])
    _seed(db_session, "TTF", [("2025-01-10", gas)])

    result = _run_compute(db_session)
    assert result["computed"] == 1
    assert result["written"] == 1

    row = db_session.query(SparkSpreadHistory).filter_by(date="2025-01-10").first()
    assert row is not None
    expected = round(power - gas * (1.0 / 0.50), 4)
    assert row.spark_spread == pytest.approx(expected)
    assert row.power_price == power
    assert row.gas_price == gas
    assert row.heat_rate == pytest.approx(2.0)


def test_spark_formula_custom_efficiency(db_session, monkeypatch):
    """heat_rate = 1 / 0.60 ≈ 1.667 with custom efficiency."""
    from backend.config import settings as _settings

    monkeypatch.setattr(_settings, "gas_ccgt_efficiency", 0.60)

    power, gas = 100.0, 40.0
    _seed(db_session, "POWER_DE", [("2025-02-01", power)])
    _seed(db_session, "TTF", [("2025-02-01", gas)])

    _run_compute(db_session)

    row = db_session.query(SparkSpreadHistory).filter_by(date="2025-02-01").first()
    assert row is not None
    expected_heat_rate = 1.0 / 0.60
    expected_spark = round(power - gas * expected_heat_rate, 4)
    assert row.spark_spread == pytest.approx(expected_spark, rel=1e-5)
    assert row.heat_rate == pytest.approx(expected_heat_rate, rel=1e-5)


def test_inner_join_skips_missing_dates(db_session, monkeypatch):
    """Dates with only one price (no matching counterpart) are skipped."""
    from backend.config import settings as _settings

    monkeypatch.setattr(_settings, "gas_ccgt_efficiency", 0.50)

    _seed(db_session, "POWER_DE", [
        ("2025-03-01", 90.0),
        ("2025-03-03", 95.0),  # no TTF on 03-03
    ])
    _seed(db_session, "TTF", [
        ("2025-03-01", 35.0),
        ("2025-03-02", 36.0),  # no POWER_DE on 03-02
    ])

    result = _run_compute(db_session)
    assert result["computed"] == 1  # only 2025-03-01 aligns

    rows = db_session.query(SparkSpreadHistory).all()
    assert len(rows) == 1
    assert rows[0].date == "2025-03-01"


def test_idempotent_upsert_no_duplicate(db_session, monkeypatch):
    """Running compute twice yields exactly 1 row, not 2."""
    from backend.config import settings as _settings

    monkeypatch.setattr(_settings, "gas_ccgt_efficiency", 0.50)

    _seed(db_session, "POWER_DE", [("2025-04-05", 70.0)])
    _seed(db_session, "TTF", [("2025-04-05", 25.0)])

    _run_compute(db_session)
    _run_compute(db_session)

    assert db_session.query(SparkSpreadHistory).count() == 1


def test_upsert_updates_on_price_revision(db_session, monkeypatch):
    """If the underlying price changes (provisional→confirmed), the spread is revised."""
    from backend.config import settings as _settings

    monkeypatch.setattr(_settings, "gas_ccgt_efficiency", 0.50)

    _seed(db_session, "POWER_DE", [("2025-05-10", 80.0)])
    _seed(db_session, "TTF", [("2025-05-10", 30.0)])

    _run_compute(db_session)

    # Simulate price revision
    power_row = (
        db_session.query(EnergyPrice)
        .filter_by(date="2025-05-10", symbol="POWER_DE")
        .first()
    )
    power_row.close = 85.0
    db_session.commit()

    result2 = _run_compute(db_session)
    assert result2["written"] == 1  # updated

    row = db_session.query(SparkSpreadHistory).filter_by(date="2025-05-10").first()
    assert row.power_price == 85.0
    expected = round(85.0 - 30.0 * 2.0, 4)
    assert row.spark_spread == pytest.approx(expected)


def test_co2_columns_are_null(db_session, monkeypatch):
    """co2_price and clean_spark_spread are always None (deferred)."""
    from backend.config import settings as _settings

    monkeypatch.setattr(_settings, "gas_ccgt_efficiency", 0.50)

    _seed(db_session, "POWER_DE", [("2025-06-01", 75.0)])
    _seed(db_session, "TTF", [("2025-06-01", 28.0)])

    _run_compute(db_session)

    row = db_session.query(SparkSpreadHistory).filter_by(date="2025-06-01").first()
    assert row.co2_price is None
    assert row.clean_spark_spread is None


def test_no_data_returns_zero_counts(db_session):
    """With no EnergyPrice rows, compute returns computed=0, written=0."""
    result = _run_compute(db_session)
    assert result == {"computed": 0, "written": 0}


def test_spark_route_computes_per_zone(db_session, monkeypatch):
    """/api/power/spark-spread computes live per zone from EnergyPrice(POWER_<zone>) × TTF."""
    from datetime import date, timedelta

    from fastapi.testclient import TestClient

    from backend.config import settings as _settings
    from backend.database import get_db
    from backend.main import app

    monkeypatch.setattr(_settings, "gas_ccgt_efficiency", 0.50)
    d = (date.today() - timedelta(days=5)).isoformat()
    _seed(db_session, "POWER_FR", [(d, 90.0)])
    _seed(db_session, "TTF", [(d, 30.0)])

    app.dependency_overrides[get_db] = lambda: db_session
    try:
        body = TestClient(app).get("/api/power/spark-spread?zone=FR&days=120").json()
    finally:
        app.dependency_overrides.clear()

    assert body["available"] is True
    assert body["zone"] == "FR"
    assert set(body["zones"]) == {"DE_LU", "FR", "NL"}
    assert body["data"][0]["spark_spread"] == 90.0 - 30.0 * 2.0  # 30.0, heat_rate=2.0
    assert body["latest"]["gas_price"] == 30.0


def test_spark_route_unavailable_without_overlap(db_session):
    from fastapi.testclient import TestClient

    from backend.database import get_db
    from backend.main import app

    app.dependency_overrides[get_db] = lambda: db_session
    try:
        body = TestClient(app).get("/api/power/spark-spread?zone=NL&days=120").json()
    finally:
        app.dependency_overrides.clear()
    assert body["available"] is False
    assert body["zone"] == "NL"


def test_multiple_dates(db_session, monkeypatch):
    """Multiple aligned dates all get their own row."""
    from backend.config import settings as _settings

    monkeypatch.setattr(_settings, "gas_ccgt_efficiency", 0.50)

    dates = ["2025-07-01", "2025-07-02", "2025-07-03"]
    power_prices = [80.0, 85.0, 90.0]
    gas_prices = [30.0, 32.0, 31.0]

    _seed(db_session, "POWER_DE", list(zip(dates, power_prices)))
    _seed(db_session, "TTF", list(zip(dates, gas_prices)))

    result = _run_compute(db_session)
    assert result["computed"] == 3
    assert result["written"] == 3

    rows = {r.date: r for r in db_session.query(SparkSpreadHistory).all()}
    for date_str, p, g in zip(dates, power_prices, gas_prices):
        assert date_str in rows
        expected = round(p - g * 2.0, 4)
        assert rows[date_str].spark_spread == pytest.approx(expected)
