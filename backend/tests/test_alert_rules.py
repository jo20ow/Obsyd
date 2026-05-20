"""
Tests for user-defined alert rules:
  - rule-template evaluators (chokepoint anomaly, floating storage, crack spread)
  - REST CRUD with tier limits (trial cap, paid unlimited)
  - alert-runner cooldown + triggering + email-sent flag
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

import backend.database as _db_module
from backend.auth.jwt import create_token
from backend.main import app
from backend.models.alert_rules import AlertRule, UserAlertEvent
from backend.models.pro_features import CrackSpreadHistory
from backend.models.subscription import Subscription
from backend.models.vessels import FloatingStorageEvent, GeofenceEvent
from backend.notifications import alert_runner
from backend.signals import user_alert_rules


NOW = datetime(2026, 5, 20, 12, 0, 0)


# ---------- shared fixtures ----------


@pytest.fixture
def client(db_session):
    return TestClient(app)


@pytest.fixture
def session_factory(db_session):
    return _db_module.SessionLocal


def _pro_cookie(email: str) -> dict[str, str]:
    return {"obsyd_token": create_token(email, subscription_status="pro")}


def _make_pro(db_session, email: str, *, trial: bool = False):
    """Insert a Subscription that grants Pro access."""
    if trial:
        db_session.add(
            Subscription(
                email=email,
                status="trialing",
                plan="pro",
                trial_ends_at=NOW + timedelta(days=10),
            )
        )
    else:
        db_session.add(
            Subscription(
                email=email,
                status="active",
                plan="pro",
                lemon_squeezy_id=f"ls-{email}",
            )
        )
    db_session.commit()


# ---------- evaluator unit tests ----------


def _seed_geofence(db_session, zone: str, days_back: int, count: int):
    date_str = (NOW - timedelta(days=days_back)).strftime("%Y-%m-%d")
    db_session.add(
        GeofenceEvent(zone=zone, date=date_str, tanker_count=count)
    )


def test_chokepoint_anomaly_triggers_above(db_session):
    for d in range(1, 31):
        _seed_geofence(db_session, "hormuz", d, 10)
    _seed_geofence(db_session, "hormuz", 0, 30)  # +200% over avg 10
    db_session.commit()

    res = user_alert_rules.evaluate_chokepoint_anomaly(
        db_session,
        {"zone": "hormuz", "threshold_pct": 15, "direction": "above"},
        now=NOW,
    )
    assert res is not None
    assert "HORMUZ" in res.title
    assert res.payload["zone"] == "hormuz"
    assert res.payload["today_count"] == 30
    assert res.payload["deviation_pct"] > 100


def test_chokepoint_anomaly_direction_filter_blocks_below(db_session):
    # Today is HIGHER but rule asks for direction=below — must not trigger.
    for d in range(1, 31):
        _seed_geofence(db_session, "suez", d, 8)
    _seed_geofence(db_session, "suez", 0, 30)
    db_session.commit()
    res = user_alert_rules.evaluate_chokepoint_anomaly(
        db_session,
        {"zone": "suez", "threshold_pct": 15, "direction": "below"},
        now=NOW,
    )
    assert res is None


def test_chokepoint_anomaly_returns_none_without_baseline(db_session):
    _seed_geofence(db_session, "hormuz", 0, 999)
    db_session.commit()
    res = user_alert_rules.evaluate_chokepoint_anomaly(
        db_session,
        {"zone": "hormuz", "threshold_pct": 15},
        now=NOW,
    )
    assert res is None  # no baseline rows


def test_floating_storage_surge_triggers(db_session):
    for i in range(5):
        db_session.add(
            FloatingStorageEvent(
                mmsi=str(100000 + i),
                ship_name=f"TANKER {i}",
                zone="hormuz",
                first_seen=NOW - timedelta(days=8),
                last_seen=NOW - timedelta(hours=2),
                status="active",
            )
        )
    db_session.commit()
    res = user_alert_rules.evaluate_floating_storage_surge(
        db_session,
        {"zone": "hormuz", "min_vessels": 3, "window_days": 7},
        now=NOW,
    )
    assert res is not None
    assert res.payload["count"] == 5


def test_floating_storage_surge_no_trigger_below_threshold(db_session):
    db_session.add(
        FloatingStorageEvent(
            mmsi="111", zone="hormuz",
            first_seen=NOW - timedelta(days=3),
            last_seen=NOW,
            status="active",
        )
    )
    db_session.commit()
    res = user_alert_rules.evaluate_floating_storage_surge(
        db_session,
        {"zone": "hormuz", "min_vessels": 5},
        now=NOW,
    )
    assert res is None


def test_crack_spread_breach_triggers_above(db_session):
    db_session.add(
        CrackSpreadHistory(
            date="2026-05-20",
            wti_price=80.0,
            rbob_price=2.5,
            ho_price=2.7,
            gasoline_crack=15.0,
            heating_oil_crack=18.0,
            three_two_one_crack=28.50,
        )
    )
    db_session.commit()
    res = user_alert_rules.evaluate_crack_spread_breach(
        db_session,
        {"direction": "above", "threshold_usd": 25.0},
        now=NOW,
    )
    assert res is not None
    assert "28.5" in res.title
    assert res.payload["spread_321"] == 28.5


def test_crack_spread_breach_no_trigger_within_band(db_session):
    db_session.add(
        CrackSpreadHistory(
            date="2026-05-20",
            wti_price=80, rbob_price=2.5, ho_price=2.7,
            gasoline_crack=15, heating_oil_crack=18,
            three_two_one_crack=20.0,
        )
    )
    db_session.commit()
    res = user_alert_rules.evaluate_crack_spread_breach(
        db_session,
        {"direction": "above", "threshold_usd": 25.0},
        now=NOW,
    )
    assert res is None


# ---------- REST CRUD ----------


def test_create_rule_requires_pro(client):
    resp = client.post(
        "/api/alerts/rules",
        json={"rule_type": "chokepoint_anomaly", "params": {"zone": "hormuz"}},
    )
    assert resp.status_code == 401


def test_create_rule_validates_params(client, db_session):
    _make_pro(db_session, "alice@example.com")
    resp = client.post(
        "/api/alerts/rules",
        json={"rule_type": "chokepoint_anomaly", "params": {"zone": "atlantis"}},
        cookies=_pro_cookie("alice@example.com"),
    )
    assert resp.status_code == 422


def test_create_rule_persists_and_lists(client, db_session):
    _make_pro(db_session, "alice@example.com")
    create = client.post(
        "/api/alerts/rules",
        json={
            "rule_type": "chokepoint_anomaly",
            "name": "Hormuz spike",
            "params": {"zone": "hormuz", "threshold_pct": 20, "direction": "above"},
        },
        cookies=_pro_cookie("alice@example.com"),
    )
    assert create.status_code == 200, create.text

    listed = client.get("/api/alerts/rules", cookies=_pro_cookie("alice@example.com"))
    assert listed.status_code == 200
    body = listed.json()
    assert body["tier"] == "paid"
    assert len(body["rules"]) == 1
    assert body["rules"][0]["name"] == "Hormuz spike"


def test_trial_user_capped_at_three_active_rules(client, db_session):
    _make_pro(db_session, "trial@example.com", trial=True)
    for i in range(3):
        resp = client.post(
            "/api/alerts/rules",
            json={
                "rule_type": "chokepoint_anomaly",
                "params": {"zone": "hormuz", "threshold_pct": 10 + i},
            },
            cookies=_pro_cookie("trial@example.com"),
        )
        assert resp.status_code == 200, f"rule #{i} failed: {resp.text}"

    # 4th must be rejected with 403
    resp = client.post(
        "/api/alerts/rules",
        json={
            "rule_type": "chokepoint_anomaly",
            "params": {"zone": "hormuz", "threshold_pct": 50},
        },
        cookies=_pro_cookie("trial@example.com"),
    )
    assert resp.status_code == 403
    assert "Trial" in resp.json()["detail"]


def test_paid_user_has_no_rule_cap(client, db_session):
    _make_pro(db_session, "alice@example.com")
    for i in range(5):
        resp = client.post(
            "/api/alerts/rules",
            json={
                "rule_type": "chokepoint_anomaly",
                "params": {"zone": "hormuz", "threshold_pct": 10 + i},
            },
            cookies=_pro_cookie("alice@example.com"),
        )
        assert resp.status_code == 200


def test_delete_rule(client, db_session):
    _make_pro(db_session, "alice@example.com")
    created = client.post(
        "/api/alerts/rules",
        json={"rule_type": "chokepoint_anomaly", "params": {"zone": "hormuz"}},
        cookies=_pro_cookie("alice@example.com"),
    ).json()
    rule_id = created["id"]
    resp = client.delete(
        f"/api/alerts/rules/{rule_id}",
        cookies=_pro_cookie("alice@example.com"),
    )
    assert resp.status_code == 200
    # Can't delete twice
    resp2 = client.delete(
        f"/api/alerts/rules/{rule_id}",
        cookies=_pro_cookie("alice@example.com"),
    )
    assert resp2.status_code == 404


def test_user_cannot_delete_someone_elses_rule(client, db_session):
    _make_pro(db_session, "alice@example.com")
    _make_pro(db_session, "bob@example.com")
    created = client.post(
        "/api/alerts/rules",
        json={"rule_type": "chokepoint_anomaly", "params": {"zone": "hormuz"}},
        cookies=_pro_cookie("alice@example.com"),
    ).json()
    resp = client.delete(
        f"/api/alerts/rules/{created['id']}",
        cookies=_pro_cookie("bob@example.com"),
    )
    assert resp.status_code == 404


def test_templates_endpoint_public(client):
    resp = client.get("/api/alerts/templates")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"chokepoint_anomaly", "floating_storage_surge", "crack_spread_breach"}
    assert body["chokepoint_anomaly"]["params_schema"]["zone"]["type"] == "enum"


# ---------- runner / cooldown ----------


def test_runner_triggers_and_sets_cooldown(db_session, session_factory):
    # Seed a Hormuz spike + a matching rule
    for d in range(1, 31):
        _seed_geofence(db_session, "hormuz", d, 10)
    _seed_geofence(db_session, "hormuz", 0, 30)
    db_session.commit()

    rule = AlertRule(
        email="alice@example.com",
        rule_type="chokepoint_anomaly",
        params=json.dumps({"zone": "hormuz", "threshold_pct": 15, "direction": "above"}),
        is_active=True,
    )
    db_session.add(rule)
    db_session.commit()

    counters = alert_runner.process_alert_rules(
        db_factory=session_factory, now=NOW, send_email=False
    )
    assert counters["triggered"] == 1
    assert counters["evaluated"] == 1

    events = db_session.query(UserAlertEvent).all()
    assert len(events) == 1
    assert "HORMUZ" in events[0].title

    db_session.refresh(rule)
    assert rule.cooldown_until is not None
    assert rule.cooldown_until > NOW


def test_runner_respects_cooldown_skip(db_session, session_factory):
    for d in range(1, 31):
        _seed_geofence(db_session, "hormuz", d, 10)
    _seed_geofence(db_session, "hormuz", 0, 30)
    db_session.commit()

    rule = AlertRule(
        email="alice@example.com",
        rule_type="chokepoint_anomaly",
        params=json.dumps({"zone": "hormuz", "threshold_pct": 15}),
        is_active=True,
        cooldown_until=NOW + timedelta(hours=2),
    )
    db_session.add(rule)
    db_session.commit()

    counters = alert_runner.process_alert_rules(
        db_factory=session_factory, now=NOW, send_email=False
    )
    assert counters["triggered"] == 0
    assert counters["skipped_cooldown"] == 1
    assert db_session.query(UserAlertEvent).count() == 0


def test_runner_skips_inactive_rules(db_session, session_factory):
    for d in range(1, 31):
        _seed_geofence(db_session, "hormuz", d, 10)
    _seed_geofence(db_session, "hormuz", 0, 30)
    db_session.commit()

    db_session.add(
        AlertRule(
            email="alice@example.com",
            rule_type="chokepoint_anomaly",
            params=json.dumps({"zone": "hormuz", "threshold_pct": 15}),
            is_active=False,
        )
    )
    db_session.commit()

    counters = alert_runner.process_alert_rules(
        db_factory=session_factory, now=NOW, send_email=False
    )
    assert counters["evaluated"] == 0
    assert counters["triggered"] == 0


# ---------- inbox ----------


def test_notifications_inbox_returns_user_events_only(client, db_session):
    _make_pro(db_session, "alice@example.com")
    _make_pro(db_session, "bob@example.com")

    db_session.add(AlertRule(email="alice@example.com", rule_type="chokepoint_anomaly"))
    db_session.add(AlertRule(email="bob@example.com", rule_type="chokepoint_anomaly"))
    db_session.commit()
    rules = db_session.query(AlertRule).all()

    for rule in rules:
        db_session.add(
            UserAlertEvent(
                rule_id=rule.id,
                email=rule.email,
                title=f"event for {rule.email}",
                detail="",
            )
        )
    db_session.commit()

    resp = client.get("/api/alerts/notifications", cookies=_pro_cookie("alice@example.com"))
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["events"]) == 1
    assert body["events"][0]["title"] == "event for alice@example.com"
    assert body["unseen"] == 1


def test_mark_notification_seen(client, db_session):
    _make_pro(db_session, "alice@example.com")
    rule = AlertRule(email="alice@example.com", rule_type="chokepoint_anomaly")
    db_session.add(rule)
    db_session.commit()
    db_session.refresh(rule)
    evt = UserAlertEvent(rule_id=rule.id, email="alice@example.com", title="t")
    db_session.add(evt)
    db_session.commit()
    db_session.refresh(evt)

    resp = client.post(
        f"/api/alerts/notifications/{evt.id}/seen",
        cookies=_pro_cookie("alice@example.com"),
    )
    assert resp.status_code == 200
    db_session.refresh(evt)
    assert evt.seen_at is not None
