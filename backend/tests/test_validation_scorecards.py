"""Tests for scorecard computation, persistence, and the validation API."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pytest
from fastapi.testclient import TestClient

from backend.analytics.validation import scorecards
from backend.main import app
from backend.models.analytics import DisruptionScoreHistory
from backend.models.energy import EnergyPrice
from backend.models.gas import GasBalance
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


def test_disruption_weights_api_requires_pro(client, db_session):
    resp = client.get("/api/validation/disruption-weights")
    assert resp.status_code == 401


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
