"""Unified physical-energy situation: molecules (oil) + gas + electrons (power)."""
from __future__ import annotations

from fastapi.testclient import TestClient

from backend.main import app
from backend.situation.physical import _state_from_severity, _worst, combine_domains


def _dom(state, available=True):
    return {"available": available, "state": state, "headline": "h", "as_of": None}


def test_state_from_severity_maps_to_states():
    assert _state_from_severity("critical") == "STRESSED"
    assert _state_from_severity("warning") == "ELEVATED"
    assert _state_from_severity("info") == "CALM"
    assert _state_from_severity(None) == "CALM"


def test_worst_picks_highest_rank():
    assert _worst(["CALM", "STRESSED", "ELEVATED"]) == "STRESSED"
    assert _worst(["CALM", "ELEVATED"]) == "ELEVATED"
    assert _worst([]) == "CALM"


def test_combine_overall_is_worst_of_available():
    # Refocus 2026-07-03: the desk fuses gas + power only (electrons + their fuel).
    out = combine_domains(_dom("ELEVATED"), _dom("STRESSED"))
    assert out["overall"] == "STRESSED"
    assert out["available"] is True
    assert set(out["domains"]) == {"gas", "power"}


def test_combine_excludes_unavailable_domains_from_overall():
    # a STRESSED but unavailable gas domain must not drive the overall state
    out = combine_domains(_dom("STRESSED", available=False), _dom("CALM"))
    assert out["overall"] == "CALM"


def test_combine_all_unavailable():
    out = combine_domains(_dom("CALM", available=False), _dom("CALM", available=False))
    assert out["available"] is False
    assert out["overall"] == "CALM"


def test_chokepoint_price_context_summarizes_forward_brent(monkeypatch):
    from backend.situation import physical

    def fake_find(chokepoint, **kw):
        return {"anomalies": [
            {"brent_change_7d_pct": 2.0, "brent_change_30d_pct": 5.0},
            {"brent_change_7d_pct": 4.0, "brent_change_30d_pct": 9.0},
            {"brent_change_7d_pct": 6.0, "brent_change_30d_pct": 1.0},
        ]}

    monkeypatch.setattr("backend.signals.historical_lookup.find_anomalies", fake_find)
    ctx = physical.chokepoint_price_context("hormuz")
    assert ctx["n"] == 3
    assert ctx["median_7d_pct"] == 4.0
    assert ctx["median_30d_pct"] == 5.0


def test_chokepoint_price_context_none_without_events(monkeypatch):
    from backend.situation import physical

    monkeypatch.setattr(
        "backend.signals.historical_lookup.find_anomalies",
        lambda *a, **k: {"anomalies": []},
    )
    assert physical.chokepoint_price_context("hormuz") is None


def test_gas_balance_price_context_from_seeded_db(db_session):
    from backend.models.energy import EnergyPrice
    from backend.models.gas import GasBalance
    from backend.situation import physical

    for d, c in [("2025-01-01", 30.0), ("2025-01-08", 33.0), ("2025-01-31", 27.0)]:
        db_session.add(EnergyPrice(date=d, symbol="TTF", close=c))
    db_session.add(GasBalance(date="2024-12-31", flag=None))
    db_session.add(GasBalance(date="2025-01-01", flag="SIGNAL:supply↑"))  # onset
    db_session.commit()

    ctx = physical.gas_balance_price_context(db_session)
    assert ctx["n"] == 1
    assert ctx["median_7d_pct"] == 10.0   # 30 → 33
    assert ctx["median_30d_pct"] == -10.0  # 30 → 27


def test_gas_balance_price_context_none_without_ttf(db_session):
    from backend.situation import physical

    assert physical.gas_balance_price_context(db_session) is None  # empty DB → no TTF


def test_alerts_feed_attaches_gas_context(db_session):
    from datetime import datetime

    from backend.models.alerts import Alert
    from backend.models.energy import EnergyPrice
    from backend.models.gas import GasBalance

    for d, c in [("2025-01-01", 30.0), ("2025-01-08", 33.0), ("2025-01-31", 27.0)]:
        db_session.add(EnergyPrice(date=d, symbol="TTF", close=c))
    db_session.add(GasBalance(date="2024-12-31", flag=None))
    db_session.add(GasBalance(date="2025-01-01", flag="SIGNAL:supply↑"))
    db_session.add(Alert(
        rule="gas_balance", zone="EU", vertical="gas", severity="critical",
        title="EU gas balance signal", detail="...", created_at=datetime.utcnow(),
    ))
    db_session.commit()

    items = TestClient(app).get("/api/alerts").json()
    gas = [a for a in items if a["rule"] == "gas_balance"]
    assert gas and gas[0].get("context")
    assert gas[0]["context"]["price_label"] == "TTF"
    assert gas[0]["context"]["n"] == 1


def test_power_forward_residual_from_seeded_forecast(db_session):
    from backend.models.energy import PowerLoadForecast
    from backend.situation import physical

    db_session.add(PowerLoadForecast(
        date="2026-07-03", zone="DE_LU", forecast_mw=54000.0,
        wind_forecast_mw=29000.0, solar_forecast_mw=24000.0,
    ))
    db_session.commit()

    fwd = physical._power_forward(db_session, "DE_LU")
    assert fwd["date"] == "2026-07-03"
    assert fwd["residual_mw"] == 1000.0  # 54000 - 29000 - 24000


def test_power_forward_none_without_wind_solar(db_session):
    from backend.models.energy import PowerLoadForecast
    from backend.situation import physical

    db_session.add(PowerLoadForecast(date="2026-07-03", zone="DE_LU", forecast_mw=54000.0))
    db_session.commit()
    assert physical._power_forward(db_session, "DE_LU") is None


def test_situation_endpoint_envelope(db_session):
    body = TestClient(app).get("/api/situation").json()
    assert "overall" in body and "domains" in body
    assert set(body["domains"]) == {"gas", "power"}
    for d in body["domains"].values():
        assert "state" in d and "available" in d and "headline" in d
