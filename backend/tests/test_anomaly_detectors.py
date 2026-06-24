"""Tests for the cross-vertical anomaly radar (anonymous Alert backbone).

Covers the detector layer + run_all_detectors + the /api/alerts vertical
exposure/grouping. This is a different subsystem from test_alert_rules.py
(which covers the Pro user rule-builder) — keep them separate.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect

from backend.main import app
from backend.models.alerts import Alert
from backend.models.analytics import (
    DaysOfSupplyHistory,
    FreightProxyHistory,
    SupplyDemandBalance,
)
from backend.models.energy import PowerPriceDaily
from backend.models.gas import GasBalance
from backend.models.sentiment import SentimentScore
from backend.models.vessels import FloatingStorageEvent
from backend.signals.detectors import DETECTORS, run_all_detectors
from backend.signals.detectors.gas import detect_gas_balance
from backend.signals.detectors.oil import (
    detect_days_of_supply,
    detect_floating_storage,
    detect_freight_divergence,
    detect_supply_demand_divergence,
)
from backend.signals.detectors.power import detect_negative_prices
from backend.signals.detectors.sentiment import detect_sentiment_risk


@pytest.fixture
def client(db_session):
    return TestClient(app)


# ─── Gas ──────────────────────────────────────────────────────────────────────


def test_gas_balance_signal_is_critical(db_session):
    db_session.add(GasBalance(date="2026-06-24", residual_7d=-820, z_score=3.4, flag="SIGNAL:supply↑"))
    db_session.commit()
    results = detect_gas_balance(db_session)
    assert len(results) == 1
    r = results[0]
    assert r.vertical == "gas" and r.rule == "gas_balance" and r.severity == "critical"
    assert "signal" in r.title.lower()
    assert "supply↑" in r.detail


def test_gas_balance_ok_flag_suppressed(db_session):
    db_session.add(GasBalance(date="2026-06-24", residual_7d=-10, z_score=-0.2, flag="OK"))
    db_session.commit()
    assert detect_gas_balance(db_session) == []


def test_gas_balance_watch_is_warning(db_session):
    db_session.add(GasBalance(date="2026-06-24", residual_7d=400, z_score=2.3, flag="WATCH:demand↓"))
    db_session.commit()
    assert detect_gas_balance(db_session)[0].severity == "warning"


def test_gas_balance_uses_latest_day_not_stale_flag(db_session):
    # Old flagged day + a newer normal (unflagged) day → current state wins → suppress.
    db_session.add(GasBalance(date="2026-06-01", residual_7d=900, z_score=3.2, flag="SIGNAL:supply↑"))
    db_session.add(GasBalance(date="2026-06-24", residual_7d=-20, z_score=-0.17, flag=None))
    db_session.commit()
    assert detect_gas_balance(db_session) == []


# ─── Oil analytics ──────────────────────────────────────────────────────────


def test_days_of_supply_tight_warns(db_session):
    db_session.add(DaysOfSupplyHistory(date="2026-06-24", assessment="TIGHT", deviation=-4.5, commercial_days=21.0))
    db_session.commit()
    r = detect_days_of_supply(db_session)[0]
    assert r.vertical == "oil" and r.severity == "warning"


def test_days_of_supply_in_line_suppressed(db_session):
    db_session.add(DaysOfSupplyHistory(date="2026-06-24", assessment="IN_LINE", deviation=0.2, commercial_days=27.0))
    db_session.commit()
    assert detect_days_of_supply(db_session) == []


def test_supply_demand_divergence_warns(db_session):
    db_session.add(
        SupplyDemandBalance(date="2026-06-24", divergence_type="EIA_AIS_DIVERGENCE", divergence_detail="Forecast vs AIS gap.")
    )
    db_session.commit()
    r = detect_supply_demand_divergence(db_session)[0]
    assert r.severity == "warning" and "gap" in r.detail.lower()


def test_supply_demand_confirmed_suppressed(db_session):
    db_session.add(SupplyDemandBalance(date="2026-06-24", divergence_type="EIA_AIS_CONFIRMED", divergence_detail="Agree."))
    db_session.commit()
    assert detect_supply_demand_divergence(db_session) == []


def test_freight_divergence_info(db_session):
    db_session.add(FreightProxyHistory(date="2026-06-24", proxy_index=104.0, divergence_flag="FREIGHT_PROXY_DIVERGENCE"))
    db_session.commit()
    r = detect_freight_divergence(db_session)[0]
    assert r.severity == "info" and r.vertical == "oil"


def test_floating_storage_threshold(db_session):
    # 2 active in a zone → below threshold (suppress); add a 3rd → info.
    for i in range(2):
        db_session.add(_fs_event(f"20000000{i}", "hormuz"))
    db_session.commit()
    assert detect_floating_storage(db_session) == []

    db_session.add(_fs_event("200000099", "hormuz"))
    db_session.commit()
    r = detect_floating_storage(db_session)[0]
    assert r.severity == "info" and r.zone == "hormuz"


def _fs_event(mmsi: str, zone: str) -> FloatingStorageEvent:
    from datetime import datetime

    return FloatingStorageEvent(
        mmsi=mmsi, zone=zone, first_seen=datetime.utcnow(), last_seen=datetime.utcnow(),
        duration_days=9.0, status="active",
    )


# ─── Power ────────────────────────────────────────────────────────────────────


def test_negative_prices_warns(db_session):
    db_session.add(PowerPriceDaily(date="2026-06-24", zone="DE_LU", mean_price=20, min_price=-30, max_price=80, negative_hours=8))
    db_session.commit()
    r = detect_negative_prices(db_session)[0]
    assert r.vertical == "power" and r.severity == "warning" and r.zone == "DE_LU"


def test_negative_prices_zero_suppressed(db_session):
    db_session.add(PowerPriceDaily(date="2026-06-24", zone="DE_LU", mean_price=50, min_price=10, max_price=80, negative_hours=0))
    db_session.commit()
    assert detect_negative_prices(db_session) == []


# ─── Sentiment ──────────────────────────────────────────────────────────────


def test_sentiment_high_risk_warns(db_session):
    db_session.add(SentimentScore(date="2026-06-24", risk_score=9.0, risk_factors='["Strait of Hormuz tension"]'))
    db_session.commit()
    r = detect_sentiment_risk(db_session)[0]
    assert r.vertical == "sentiment" and r.severity == "warning"
    assert "Hormuz" in r.detail


def test_sentiment_low_risk_suppressed(db_session):
    db_session.add(SentimentScore(date="2026-06-24", risk_score=4.0, risk_factors="[]"))
    db_session.commit()
    assert detect_sentiment_risk(db_session) == []


# ─── Runner (loop) ────────────────────────────────────────────────────────────


def test_run_all_detectors_multi_vertical(db_session):
    db_session.add(GasBalance(date="2026-06-24", residual_7d=-800, z_score=3.5, flag="SIGNAL:supply↑"))
    db_session.add(SentimentScore(date="2026-06-24", risk_score=9.0, risk_factors="[]"))
    db_session.commit()

    n = run_all_detectors(db_session)
    assert n == 2
    verticals = {a.vertical for a in db_session.query(Alert).all()}
    assert verticals == {"gas", "sentiment"}


def test_run_all_detectors_isolates_failures(db_session, monkeypatch):
    """One detector raising must not suppress the others."""
    def boom(db):
        raise RuntimeError("detector exploded")

    monkeypatch.setattr("backend.signals.detectors.DETECTORS", [boom, detect_gas_balance])
    db_session.add(GasBalance(date="2026-06-24", residual_7d=-800, z_score=3.5, flag="SIGNAL:supply↑"))
    db_session.commit()

    n = run_all_detectors(db_session)  # must not raise
    assert n == 1
    assert db_session.query(Alert).filter(Alert.vertical == "gas").count() == 1


def test_detector_registry_has_seven(db_session):
    assert len(DETECTORS) == 7


# ─── API exposure + grouping ──────────────────────────────────────────────────


def test_api_exposes_vertical_and_filters(client, db_session):
    db_session.add(Alert(rule="gas_balance", zone="EU", vertical="gas", severity="critical", title="g", detail="d"))
    db_session.add(Alert(rule="negative_prices", zone="DE_LU", vertical="power", severity="warning", title="p", detail="d"))
    db_session.commit()

    body = client.get("/api/alerts?vertical=gas").json()
    assert len(body) == 1 and body[0]["vertical"] == "gas"


def test_api_group_by_vertical_severity_sorted(client, db_session):
    db_session.add(Alert(rule="freight_divergence", zone="t", vertical="oil", severity="info", title="i", detail=""))
    db_session.add(Alert(rule="cushing_drawdown", zone="c", vertical="oil", severity="critical", title="c", detail=""))
    db_session.add(Alert(rule="gas_balance", zone="EU", vertical="gas", severity="warning", title="g", detail=""))
    db_session.commit()

    body = client.get("/api/alerts?group_by_vertical=true").json()
    assert body["total"] == 3
    assert set(body["verticals"].keys()) == {"oil", "gas"}
    # oil group: critical before info
    oil_sev = [a["severity"] for a in body["verticals"]["oil"]]
    assert oil_sev == ["critical", "info"]


# ─── Migration ────────────────────────────────────────────────────────────────


def test_alerts_vertical_column_present(db_session):
    # The column is part of the schema the app actually binds to.
    cols = {c["name"] for c in inspect(db_session.get_bind()).get_columns("alerts")}
    assert "vertical" in cols


def test_alert_defaults_to_oil(db_session):
    db_session.add(Alert(rule="cushing_drawdown", zone="cushing", severity="critical", title="t", detail="d"))
    db_session.commit()
    row = db_session.query(Alert).filter(Alert.rule == "cushing_drawdown").first()
    assert row.vertical == "oil"
