"""Tests for scorecard computation, persistence, and the validation API."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pytest
from fastapi.testclient import TestClient

from backend.analytics.validation import scorecards
from backend.main import app
from backend.models.analytics import DisruptionScoreHistory
from backend.models.energy import EnergyPrice, PowerGrid, SparkSpreadHistory
from backend.models.gas import GasBalance
from backend.models.metals import CopperSupply
from backend.models.prices import FREDSeries
from backend.models.validation import SignalScorecard


@pytest.fixture
def client(db_session):
    return TestClient(app)


def _seed(db, n_days=80, seed=4):
    """Plant disruption history where the composite predicts the 7d-forward
    Brent move, plus a matching FRED Brent series."""
    rng = np.random.default_rng(seed)
    start = date(2026, 1, 1)
    composite = rng.uniform(0, 100, size=n_days)
    price = [80.0]
    for i in range(1, n_days + 12):
        drift = 0.02 * (composite[i - 7] - 50) if 0 <= i - 7 < n_days else 0.0
        price.append(price[-1] + drift + rng.normal(scale=0.3))
    for i in range(n_days):
        d = (start + timedelta(days=i)).strftime("%Y-%m-%d")
        # two rows per day to exercise the one-obs-per-day dedup
        for _ in range(2):
            db.add(
                DisruptionScoreHistory(
                    date=d,
                    composite_score=float(composite[i]),
                    hormuz_component=0,
                    cape_component=0,
                    storage_component=0,
                    crack_component=0,
                    backwardation_component=0,
                    sentiment_component=0,
                )
            )
    for i in range(n_days + 12):
        d = (start + timedelta(days=i)).strftime("%Y-%m-%d")
        db.add(FREDSeries(series_id="DCOILBRENTEU", date=d, value=float(price[i])))
    db.commit()


def test_load_signal_series_dedupes_to_one_per_day(db_session):
    _seed(db_session, n_days=30)
    dates, values = scorecards.load_signal_series(db_session, "DisruptionScoreHistory", "composite_score")
    assert len(dates) == 30  # 60 rows -> 30 distinct days
    assert len(values) == 30


def test_recompute_writes_cards_for_every_signal_and_horizon(db_session):
    _seed(db_session)
    cards = scorecards.recompute_scorecards(db_session, as_of="2026-06-11")
    # disruption_score has data; the other two signals have no history -> skipped
    ds_cards = [c for c in cards if c["signal"] == "disruption_score"]
    assert {c["horizon_days"] for c in ds_cards} == set(scorecards.HORIZONS)
    rows = db_session.query(SignalScorecard).filter(SignalScorecard.signal == "disruption_score").all()
    assert len(rows) == len(scorecards.HORIZONS)


def test_recompute_is_idempotent_per_as_of(db_session):
    _seed(db_session)
    scorecards.recompute_scorecards(db_session, as_of="2026-06-11")
    scorecards.recompute_scorecards(db_session, as_of="2026-06-11")  # upsert, not duplicate
    rows = db_session.query(SignalScorecard).filter(SignalScorecard.as_of == "2026-06-11").all()
    assert len(rows) == len(scorecards.HORIZONS)  # only disruption_score had data


def test_predictive_signal_shows_positive_ic(db_session):
    _seed(db_session)
    cards = scorecards.recompute_scorecards(db_session, as_of="2026-06-11")
    card_7d = next(c for c in cards if c["signal"] == "disruption_score" and c["horizon_days"] == 7)
    # Planted predictive signal: positive IC, HAC-significant, high hit rate.
    assert card_7d["ic"] is not None and card_7d["ic"] > 0.15
    assert card_7d["t_stat"] > 2.0
    assert card_7d["hit_rate"] > 0.7
    assert card_7d["confident"] == 1  # n >= 30


def test_two_sided_p_matches_known_values():
    # |z|=1.96 -> p ~ 0.05; z=0 -> p=1
    assert abs(scorecards._two_sided_p(1.959964) - 0.05) < 1e-3
    assert abs(scorecards._two_sided_p(0.0) - 1.0) < 1e-9


def test_scorecards_api_returns_latest(client, db_session):
    _seed(db_session)
    scorecards.recompute_scorecards(db_session, as_of="2026-06-11")
    resp = client.get("/api/validation/scorecards")
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is True
    assert body["as_of"] == "2026-06-11"
    assert "disruption_score" in body["signals"]
    assert len(body["signals"]["disruption_score"]) == len(scorecards.HORIZONS)


def test_scorecards_api_empty_is_graceful(client, db_session):
    resp = client.get("/api/validation/scorecards")
    assert resp.status_code == 200
    assert resp.json()["available"] is False


def test_disruption_weights_api_is_public(client, db_session):
    # Read-only validation data is free/public now (no Pro gate).
    resp = client.get("/api/validation/disruption-weights")
    assert resp.status_code == 200


def _seed_gas(db, n_days=80, seed=7):
    """Plant a gas residual z_score that predicts the 7d-forward TTF move, plus a
    matching EnergyPrice TTF series. GasBalance has no created_at + date is PK."""
    rng = np.random.default_rng(seed)
    start = date(2026, 1, 1)
    z = rng.uniform(-2, 2, size=n_days)
    price = [40.0]
    for i in range(1, n_days + 12):
        drift = 0.5 * z[i - 7] if 0 <= i - 7 < n_days else 0.0
        price.append(price[-1] + drift + rng.normal(scale=0.3))
    for i in range(n_days):
        d = (start + timedelta(days=i)).strftime("%Y-%m-%d")
        db.add(GasBalance(date=d, z_score=float(z[i]), residual=float(z[i]), residual_7d=float(z[i])))
    for i in range(n_days + 12):
        d = (start + timedelta(days=i)).strftime("%Y-%m-%d")
        db.add(EnergyPrice(date=d, symbol="TTF", close=float(price[i])))
    db.commit()


def test_gas_residual_loads_without_created_at(db_session):
    # GasBalance lacks created_at and date is the PK (one row/day) — the loader
    # must read it directly rather than ordering by created_at.
    _seed_gas(db_session, n_days=40)
    dates, values = scorecards.load_signal_series(db_session, "GasBalance", "z_score")
    assert len(dates) == 40 and len(values) == 40


def test_gas_residual_scores_against_ttf_not_brent(db_session):
    # Seed gas + TTF but NO FRED Brent. gas_residual must still score (uses the
    # per-target TTF map); the planted z->TTF relationship is significant.
    _seed_gas(db_session)
    cards = scorecards.recompute_scorecards(db_session, as_of="2026-06-11")
    gas = [c for c in cards if c["signal"] == "gas_residual"]
    assert {c["horizon_days"] for c in gas} == set(scorecards.HORIZONS)
    card_7d = next(c for c in gas if c["horizon_days"] == 7)
    assert card_7d["n"] >= 30 and card_7d["confident"] == 1  # scored despite no Brent
    # Planted z->TTF relationship is positive → IC has the right sign.
    assert card_7d["ic"] is not None and card_7d["ic"] > 0


# ── Energy signals: power_residual + spark_spread ────────────────────────────


def _seed_power(db, n_days=80, seed=13):
    """Plant PowerGrid rows with residual_mw that predicts the 7d-forward
    POWER_DE move, plus a matching EnergyPrice POWER_DE series.

    PowerGrid has one row per (date, zone), so the loader reads it directly.

    A positive planted relationship (high residual → gas/coal plants must run
    → tighter supply → higher power price next week) is used to assert IC sign.
    """
    rng = np.random.default_rng(seed)
    start = date(2026, 1, 1)
    residual = rng.uniform(20000, 50000, size=n_days)  # MW range realistic for DE-LU
    price = [80.0]
    for i in range(1, n_days + 12):
        # High residual load → more thermal generation needed → upward price pressure
        drift = 0.0002 * (residual[i - 7] - 35000) if 0 <= i - 7 < n_days else 0.0
        price.append(price[-1] + drift + rng.normal(scale=0.5))
    for i in range(n_days):
        d = (start + timedelta(days=i)).strftime("%Y-%m-%d")
        db.add(
            PowerGrid(
                date=d,
                zone="DE_LU",
                load_mw=float(residual[i]) + 10000.0,  # load > residual
                wind_mw=5000.0,
                solar_mw=5000.0,
                residual_mw=float(residual[i]),
            )
        )
    for i in range(n_days + 12):
        d = (start + timedelta(days=i)).strftime("%Y-%m-%d")
        db.add(EnergyPrice(date=d, symbol="POWER_DE", close=float(price[i])))
    db.commit()


def _seed_spark(db, n_days=80, seed=17):
    """Plant SparkSpreadHistory rows where spark_spread predicts the 7d-forward
    POWER_DE move, plus a matching EnergyPrice POWER_DE series.

    Note on circularity: spark_spread = power − gas × heat_rate, so it is a
    *linear function* of POWER_DE on the signal date. The IC measures whether
    today's level predicts the *forward return* (7d-ahead log price change), not
    whether high spark_spread correlates with a high absolute price — so there
    is a meaningful (though partially circular) signal here. Tests simply assert
    the mechanical plumbing (n≥30, IC not None), not a specific sign.
    """
    rng = np.random.default_rng(seed)
    start = date(2026, 1, 1)
    spark = rng.uniform(-10, 30, size=n_days)  # EUR/MWh realistic range
    price = [80.0]
    for i in range(1, n_days + 12):
        drift = 0.05 * spark[i - 7] if 0 <= i - 7 < n_days else 0.0
        price.append(price[-1] + drift + rng.normal(scale=0.5))
    for i in range(n_days):
        d = (start + timedelta(days=i)).strftime("%Y-%m-%d")
        db.add(
            SparkSpreadHistory(
                date=d,
                power_price=float(price[i]),
                gas_price=float(price[i]) - float(spark[i]) * 2.0,
                heat_rate=2.0,
                spark_spread=float(spark[i]),
            )
        )
    for i in range(n_days + 12):
        d = (start + timedelta(days=i)).strftime("%Y-%m-%d")
        db.add(EnergyPrice(date=d, symbol="POWER_DE", close=float(price[i])))
    db.commit()


def test_resolve_model_finds_energy_classes(db_session):
    """_resolve_model must return the right classes for energy-vertical tables."""
    assert scorecards._resolve_model("PowerGrid") is PowerGrid
    assert scorecards._resolve_model("SparkSpreadHistory") is SparkSpreadHistory


def test_power_residual_scores_against_power_price(db_session):
    """power_residual is scored against POWER_DE (not Brent, not TTF)."""
    _seed_power(db_session)
    cards = scorecards.recompute_scorecards(db_session, as_of="2026-06-11")
    pr = [c for c in cards if c["signal"] == "power_residual"]
    assert {c["horizon_days"] for c in pr} == set(scorecards.HORIZONS)
    card_7d = next(c for c in pr if c["horizon_days"] == 7)
    assert card_7d["n"] >= 30 and card_7d["confident"] == 1
    # Planted positive residual→power relationship: IC should be positive.
    assert card_7d["ic"] is not None and card_7d["ic"] > 0


def test_spark_spread_scores_against_power_price(db_session):
    """spark_spread card exists with n≥30 and a non-None IC when seeded."""
    _seed_spark(db_session)
    cards = scorecards.recompute_scorecards(db_session, as_of="2026-06-11")
    ss = [c for c in cards if c["signal"] == "spark_spread"]
    assert {c["horizon_days"] for c in ss} == set(scorecards.HORIZONS)
    card_7d = next(c for c in ss if c["horizon_days"] == 7)
    assert card_7d["n"] >= 30 and card_7d["confident"] == 1
    assert card_7d["ic"] is not None


def test_existing_brent_ttf_signals_not_broken(db_session):
    """Regression: adding energy targets must not break Brent or TTF signals."""
    _seed(db_session)
    _seed_gas(db_session)
    cards = scorecards.recompute_scorecards(db_session, as_of="2026-06-11")
    signals = {c["signal"] for c in cards}
    assert "disruption_score" in signals
    assert "gas_residual" in signals
    # Both scored at all horizons
    assert {c["horizon_days"] for c in cards if c["signal"] == "disruption_score"} == set(scorecards.HORIZONS)
    assert {c["horizon_days"] for c in cards if c["signal"] == "gas_residual"} == set(scorecards.HORIZONS)


def test_copper_target_reads_energy_price(db_session):
    """A4.1: the 'copper' scorecard target resolves to the EnergyPrice COPPER series."""
    db_session.add(EnergyPrice(date="2026-06-01", symbol="COPPER", close=4.5))
    db_session.add(EnergyPrice(date="2026-06-02", symbol="COPPER", close=4.6))
    db_session.commit()
    pm = scorecards._load_target_map(db_session, "copper")
    assert pm == {"2026-06-01": 4.5, "2026-06-02": 4.6}


def test_copper_stocks_signal_resolves_and_scores(db_session):
    """A4.3: copper_stocks (CopperSupply) resolves via metals module + scores vs copper price."""
    assert scorecards._resolve_model("CopperSupply") is CopperSupply
    # 36 monthly stock obs + a daily copper price series covering them + the 30d horizon tail
    months = [f"2023-{mm:02d}-01" for mm in range(1, 13)] + [f"2024-{mm:02d}-01" for mm in range(1, 13)] + [f"2025-{mm:02d}-01" for mm in range(1, 13)]
    for i, mdate in enumerate(months):
        db_session.add(CopperSupply(date=mdate, us_refined_stocks=100000.0 + i * 1000))
    # daily copper price across the whole span + 30d horizon tail
    d = date(2023, 1, 1)
    px = 4.0
    while d <= date(2026, 1, 31):
        db_session.add(EnergyPrice(date=d.isoformat(), symbol="COPPER", close=px))
        px += 0.001
        d += timedelta(days=1)
    db_session.commit()

    cards = scorecards.recompute_scorecards(db_session, as_of="2026-06-11")
    cs = [c for c in cards if c["signal"] == "copper_stocks"]
    assert {c["horizon_days"] for c in cs} == set(scorecards.HORIZONS)
    assert any(c["n"] > 0 for c in cs)  # scored against the COPPER price target
