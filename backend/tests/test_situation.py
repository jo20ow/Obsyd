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
    out = combine_domains(_dom("CALM"), _dom("ELEVATED"), _dom("STRESSED"))
    assert out["overall"] == "STRESSED"
    assert out["available"] is True
    assert set(out["domains"]) == {"oil", "gas", "power"}


def test_combine_excludes_unavailable_domains_from_overall():
    # a STRESSED but unavailable oil domain must not drive the overall state
    out = combine_domains(_dom("STRESSED", available=False), _dom("CALM"), _dom("CALM"))
    assert out["overall"] == "CALM"


def test_combine_all_unavailable():
    out = combine_domains(*(_dom("CALM", available=False) for _ in range(3)))
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
    assert ctx["brent_median_7d_pct"] == 4.0
    assert ctx["brent_median_30d_pct"] == 5.0


def test_chokepoint_price_context_none_without_events(monkeypatch):
    from backend.situation import physical

    monkeypatch.setattr(
        "backend.signals.historical_lookup.find_anomalies",
        lambda *a, **k: {"anomalies": []},
    )
    assert physical.chokepoint_price_context("hormuz") is None


def test_situation_endpoint_envelope(db_session):
    body = TestClient(app).get("/api/situation").json()
    assert "overall" in body and "domains" in body
    assert set(body["domains"]) == {"oil", "gas", "power"}
    for d in body["domains"].values():
        assert "state" in d and "available" in d and "headline" in d
