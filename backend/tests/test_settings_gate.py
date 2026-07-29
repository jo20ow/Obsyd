"""GET /api/settings and /api/settings/credits are owner-only (require_pro).

They expose provider wiring and TwelveData credit counters — operational
internals, not desk data. POST /provider was already gated; the two GETs
must match (they were public until the 2026-07 readiness audit).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.auth.jwt import create_token
from backend.main import app
from backend.models.subscription import Subscription


@pytest.fixture
def client(db_session):
    return TestClient(app)


def test_get_settings_requires_auth(client):
    assert client.get("/api/settings").status_code == 401


def test_get_credits_requires_auth(client):
    assert client.get("/api/settings/credits").status_code == 401


def test_free_login_is_not_enough(client, db_session):
    """A free magic-link login has no subscription row → 403, same as the POST."""
    cookies = {"obsyd_token": create_token("free@example.com")}
    assert client.get("/api/settings", cookies=cookies).status_code == 403
    assert client.get("/api/settings/credits", cookies=cookies).status_code == 403


def test_owner_with_active_subscription_can_read(client, db_session):
    db_session.add(Subscription(
        email="owner@example.com",
        status="active",
        plan="pro",
        lemon_squeezy_id="ls-owner",
    ))
    db_session.commit()
    cookies = {"obsyd_token": create_token("owner@example.com", subscription_status="pro")}

    resp = client.get("/api/settings", cookies=cookies)
    assert resp.status_code == 200
    assert "primary" in resp.json() or isinstance(resp.json(), dict)

    resp = client.get("/api/settings/credits", cookies=cookies)
    assert resp.status_code == 200
