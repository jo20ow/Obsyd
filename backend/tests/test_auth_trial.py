"""
Tests for the in-app trial flow (POST /api/auth/start-trial).

Covers:
  - unauthenticated -> 401
  - first-time authed user -> trial created, cookie reissued with sub_status=pro
  - second start-trial attempt -> 409
  - already-Pro user -> 200 with status=already_pro, no second row
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from backend.auth.jwt import create_token
from backend.main import app
from backend.models.subscription import Subscription
from backend.notifications import trial_drip


@pytest.fixture(autouse=True)
def _mute_welcome_email(monkeypatch):
    """Trial-start sends a welcome email; we mute Resend in every test."""
    monkeypatch.setattr(trial_drip, "send_welcome_now", lambda email: True)


@pytest.fixture
def client(db_session):
    return TestClient(app)


def _login_cookie(email: str, sub_status: str = "free") -> dict[str, str]:
    return {"obsyd_token": create_token(email, subscription_status=sub_status)}


def test_start_trial_unauthenticated_is_rejected(client):
    resp = client.post("/api/auth/start-trial")
    assert resp.status_code == 401


def test_start_trial_creates_trialing_subscription(client, db_session):
    resp = client.post("/api/auth/start-trial", cookies=_login_cookie("dave@example.com"))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "trial_started"
    assert body["tier"] == "pro"
    assert body["days_remaining"] == 14

    row = db_session.query(Subscription).filter(Subscription.email == "dave@example.com").one()
    assert row.status == "trialing"
    assert row.lemon_squeezy_id is None
    assert row.trial_ends_at is not None
    # End should be roughly 14 days from now
    delta = row.trial_ends_at - datetime.utcnow()
    assert timedelta(days=13, hours=23) < delta < timedelta(days=14, minutes=1)


def test_start_trial_while_trial_active_returns_already_pro(client, db_session):
    """During an active trial, hitting the endpoint again is a no-op (200), not 409."""
    client.post("/api/auth/start-trial", cookies=_login_cookie("eve@example.com"))
    resp = client.post("/api/auth/start-trial", cookies=_login_cookie("eve@example.com"))
    assert resp.status_code == 200
    assert resp.json()["status"] == "already_pro"
    assert db_session.query(Subscription).filter(Subscription.email == "eve@example.com").count() == 1


def test_start_trial_after_trial_expired_is_rejected(client, db_session):
    """Once the trial expired (or any past sub exists), no fresh trial is grantable."""
    # Seed an expired trial row directly
    db_session.add(
        Subscription(
            email="eve2@example.com",
            status="trialing",
            plan="pro",
            trial_ends_at=datetime.utcnow() - timedelta(days=1),
        )
    )
    db_session.commit()
    resp = client.post("/api/auth/start-trial", cookies=_login_cookie("eve2@example.com"))
    assert resp.status_code == 409
    assert db_session.query(Subscription).filter(Subscription.email == "eve2@example.com").count() == 1


def test_start_trial_for_active_pro_returns_already_pro(client, db_session):
    # Pre-seed a paid Pro sub
    db_session.add(
        Subscription(
            email="frank@example.com",
            status="active",
            plan="pro",
            lemon_squeezy_id="ls-sub-frank",
        )
    )
    db_session.commit()

    resp = client.post("/api/auth/start-trial", cookies=_login_cookie("frank@example.com"))
    assert resp.status_code == 200
    assert resp.json()["status"] == "already_pro"
    assert db_session.query(Subscription).filter(Subscription.email == "frank@example.com").count() == 1


def test_me_reflects_trial_status(client, db_session):
    client.post("/api/auth/start-trial", cookies=_login_cookie("grace@example.com"))
    resp = client.get("/api/auth/me", cookies=_login_cookie("grace@example.com"))
    assert resp.status_code == 200
    data = resp.json()
    assert data["tier"] == "pro"
    assert data["trial_ends_at"] is not None
    assert data["trial_eligible"] is False
    # Checkout url is None once Pro
    assert data["checkout_url"] is None


def test_me_anon_is_trial_eligible_false_but_has_checkout(client, db_session):
    resp = client.get("/api/auth/me")
    assert resp.status_code == 200
    data = resp.json()
    assert data["authenticated"] is False
    assert data["tier"] == "free"
    # checkout_url is exposed even to anonymous visitors so the PricingModal works.
    assert data["checkout_url"]
    # Anonymous → no email prefill (they pick one at checkout).
    assert "checkout[email]=" not in data["checkout_url"]


def test_me_authed_free_checkout_prefills_account_email(client, db_session):
    resp = client.get("/api/auth/me", cookies=_login_cookie("dora@example.com"))
    assert resp.status_code == 200
    data = resp.json()
    assert data["tier"] == "free"
    # Signed-in free users get the LS checkout prefilled with their account email,
    # so the subscription_created webhook attaches Pro to the right account.
    assert "checkout[email]=dora%40example.com" in data["checkout_url"]
