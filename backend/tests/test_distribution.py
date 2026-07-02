"""Distribution surfaces (Weg B): RSS radar feed, free daily-brief opt-in, power block in the brief."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.auth.dependencies import require_auth
from backend.main import app
from backend.models.alerts import Alert
from backend.models.pro_features import EmailSubscriber
from backend.notifications.daily_email import _build_power_block


@pytest.fixture
def client(db_session):
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


# ─── RSS radar feed ────────────────────────────────────────────────────────────


def test_alerts_rss_feed_serves_valid_rss(client, db_session):
    db_session.add(Alert(rule="dunkelflaute", zone="NL", vertical="power",
                         severity="warning", title="NL Dunkelflaute", detail="wind+solar low"))
    db_session.commit()
    resp = client.get("/api/alerts/rss")
    assert resp.status_code == 200
    assert "application/rss+xml" in resp.headers["content-type"]
    body = resp.text
    assert body.startswith("<?xml")
    assert "<rss" in body and "<channel>" in body and "<item>" in body
    assert "OBSYD Anomaly Radar" in body
    assert "NL Dunkelflaute" in body


def test_alerts_rss_escapes_special_chars(client, db_session):
    db_session.add(Alert(rule="x", zone="z", vertical="power", severity="info",
                         title="A & B < C", detail="d>e"))
    db_session.commit()
    body = client.get("/api/alerts/rss").text
    assert "A &amp; B &lt; C" in body       # escaped, not raw
    assert "A & B < C" not in body


def test_alerts_rss_filters_by_vertical(client, db_session):
    db_session.add(Alert(rule="a", zone="", vertical="power", severity="info", title="POWER ITEM", detail=""))
    db_session.add(Alert(rule="b", zone="", vertical="oil", severity="info", title="OIL ITEM", detail=""))
    db_session.commit()
    body = client.get("/api/alerts/rss?vertical=power").text
    assert "POWER ITEM" in body and "OIL ITEM" not in body


# ─── Free daily-brief opt-in ─────────────────────────────────────────────────


def test_subscribe_creates_free_subscriber(client, db_session):
    app.dependency_overrides[require_auth] = lambda: {"email": "free@user.com"}
    resp = client.post("/api/email/subscribe")
    assert resp.status_code == 200
    assert resp.json()["subscribed"] == "free@user.com"
    sub = db_session.query(EmailSubscriber).filter(EmailSubscriber.email == "free@user.com").first()
    assert sub is not None and sub.active is True and sub.tier == "free"


def test_subscribe_reactivates_unsubscribed(client, db_session):
    db_session.add(EmailSubscriber(email="back@user.com", tier="free",
                                   unsubscribe_token="tok", active=False))
    db_session.commit()
    app.dependency_overrides[require_auth] = lambda: {"email": "back@user.com"}
    client.post("/api/email/subscribe")
    sub = db_session.query(EmailSubscriber).filter(EmailSubscriber.email == "back@user.com").first()
    assert sub.active is True


def test_subscription_status_reflects_state(client, db_session):
    app.dependency_overrides[require_auth] = lambda: {"email": "who@user.com"}
    assert client.get("/api/email/subscription").json()["subscribed"] is False
    client.post("/api/email/subscribe")
    assert client.get("/api/email/subscription").json()["subscribed"] is True


# ─── Power block in the brief ────────────────────────────────────────────────


def test_power_block_renders_state_and_metrics():
    sit = {
        "available": True, "zone": "DE_LU", "zone_label": "DE-LU", "state": "ELEVATED",
        "headline": "DE-LU · day-ahead €120 · residual 45 GW",
        "flags": [{"key": "negative_prices", "severity": "warning", "label": "3h negative prices"}],
        "stale": False, "as_of": "2026-07-02", "age_days": 0,
    }
    html = _build_power_block([sit])
    assert "European Power Desk" in html
    assert "DE-LU" in html and "ELEVATED" in html
    assert "day-ahead €120" in html
    assert "3h negative prices" in html


def test_power_block_empty_when_no_data():
    assert _build_power_block(None) == ""
    assert _build_power_block([{"available": False}]) == ""
