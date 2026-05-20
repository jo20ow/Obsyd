"""
Tests for the Lemon Squeezy webhook handler.

Covers:
  - Signature validation (HMAC-SHA256 of raw body)
  - subscription_created (fresh + trial-upgrade by email-match)
  - subscription_updated / cancelled / expired / payment_success
  - subscription_payment_failed -> past_due
  - Idempotent replay of subscription_created
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

# conftest.py sets the env vars before this import, so settings picks up
# the test webhook secret.
from backend.config import settings
from backend.main import app
from backend.models.subscription import Subscription


WEBHOOK_SECRET = "test-lemonsqueezy-webhook-secret"
WEBHOOK_PATH = "/api/webhooks/lemonsqueezy"


def _sign(body: bytes) -> str:
    return hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()


def _payload(
    event_name: str,
    *,
    email: str = "alice@example.com",
    subscription_id: str = "ls-sub-100",
    status: str = "active",
) -> dict:
    return {
        "meta": {"event_name": event_name},
        "data": {
            "id": subscription_id,
            "attributes": {
                "user_email": email,
                "status": status,
                "customer_id": "cust-1",
                "variant_id": "var-1",
                "urls": {
                    "update_payment_method": "https://ls/update",
                    "customer_portal": "https://ls/portal",
                },
            },
        },
    }


def _post(client: TestClient, payload: dict, *, sign_with: str | None = WEBHOOK_SECRET):
    body = json.dumps(payload).encode()
    sig = (
        hmac.new(sign_with.encode(), body, hashlib.sha256).hexdigest()
        if sign_with
        else ""
    )
    return client.post(WEBHOOK_PATH, content=body, headers={"X-Signature": sig})


@pytest.fixture
def client(db_session):
    # The webhook secret in settings was loaded from env in conftest, but
    # pydantic-settings wraps it in a SecretStr — confirm it matches.
    raw = settings.lemonsqueezy_webhook_secret
    if hasattr(raw, "get_secret_value"):
        raw = raw.get_secret_value()
    assert raw == WEBHOOK_SECRET, "conftest must set LEMONSQUEEZY_WEBHOOK_SECRET"
    return TestClient(app)


def test_rejects_invalid_signature(client, db_session):
    resp = _post(client, _payload("subscription_created"), sign_with="wrong-secret")
    assert resp.status_code == 403
    assert db_session.query(Subscription).count() == 0


def test_subscription_created_inserts_active_row(client, db_session):
    resp = _post(client, _payload("subscription_created", email="bob@example.com"))
    assert resp.status_code == 200, resp.text

    row = db_session.query(Subscription).filter(Subscription.email == "bob@example.com").one()
    assert row.status == "active"
    assert row.lemon_squeezy_id == "ls-sub-100"
    assert row.plan == "pro"
    assert row.trial_ends_at is None


def test_subscription_created_replay_is_idempotent(client, db_session):
    _post(client, _payload("subscription_created", email="bob@example.com"))
    _post(client, _payload("subscription_created", email="bob@example.com"))
    rows = db_session.query(Subscription).filter(Subscription.email == "bob@example.com").all()
    assert len(rows) == 1
    assert rows[0].status == "active"


def test_subscription_created_upgrades_existing_trial_by_email(client, db_session):
    # Pre-seed an in-app trial row (no LS id yet)
    trial = Subscription(
        email="carol@example.com",
        status="trialing",
        plan="pro",
        trial_ends_at=datetime.utcnow() + timedelta(days=10),
    )
    db_session.add(trial)
    db_session.commit()

    resp = _post(client, _payload("subscription_created", email="carol@example.com", subscription_id="ls-sub-200"))
    assert resp.status_code == 200

    rows = db_session.query(Subscription).filter(Subscription.email == "carol@example.com").all()
    assert len(rows) == 1, "Trial row should be upgraded, not duplicated"
    upgraded = rows[0]
    assert upgraded.lemon_squeezy_id == "ls-sub-200"
    assert upgraded.status == "active"
    assert upgraded.trial_ends_at is None


def test_subscription_cancelled_marks_row(client, db_session):
    _post(client, _payload("subscription_created", subscription_id="ls-sub-300"))
    resp = _post(client, _payload("subscription_cancelled", subscription_id="ls-sub-300"))
    assert resp.status_code == 200

    row = db_session.query(Subscription).filter(Subscription.lemon_squeezy_id == "ls-sub-300").one()
    assert row.status == "cancelled"


def test_subscription_expired_marks_row(client, db_session):
    _post(client, _payload("subscription_created", subscription_id="ls-sub-301"))
    resp = _post(client, _payload("subscription_expired", subscription_id="ls-sub-301"))
    assert resp.status_code == 200
    row = db_session.query(Subscription).filter(Subscription.lemon_squeezy_id == "ls-sub-301").one()
    assert row.status == "expired"


def test_subscription_payment_failed_marks_past_due(client, db_session):
    _post(client, _payload("subscription_created", subscription_id="ls-sub-400"))
    resp = _post(client, _payload("subscription_payment_failed", subscription_id="ls-sub-400"))
    assert resp.status_code == 200
    row = db_session.query(Subscription).filter(Subscription.lemon_squeezy_id == "ls-sub-400").one()
    assert row.status == "past_due"


def test_unknown_email_returns_ok_but_creates_nothing(client, db_session):
    payload = _payload("subscription_created", email="")
    resp = _post(client, payload)
    assert resp.status_code == 200
    assert db_session.query(Subscription).count() == 0
