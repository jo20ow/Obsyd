"""
Tests for the hardened /health endpoints and the require_pro DB-recheck.

Covers:
  - /health returns 200 with a reachable DB
  - /api/health/collectors reports stale collectors as down (not just "ever wrote")
  - require_pro no longer trusts a stale "pro" JWT claim (refund/downgrade case)
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend.auth.dependencies import require_pro
from backend.auth.jwt import create_token
from backend.main import app
from backend.models.subscription import Subscription
from backend.models.vessels import VesselPosition


@pytest.fixture
def client(db_session):
    return TestClient(app)


def _position(mmsi: str, ts: datetime) -> VesselPosition:
    return VesselPosition(
        mmsi=mmsi,
        ship_type=80,
        latitude=26.5,
        longitude=56.5,
        sog=12.0,
        cog=90.0,
        zone="hormuz",
        timestamp=ts,
    )


# ─── /health ─────────────────────────────────────────────────────────────────


def test_health_ok_with_db(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


# ─── /api/health/collectors staleness ───────────────────────────────────────


def test_collectors_empty_db_reports_all_down(client):
    body = client.get("/api/health/collectors").json()
    assert body["eia"] is False
    assert body["fred"] is False
    assert body["ais"] is False
    assert body["gdelt"] is False
    assert body["last_seen"]["ais"] is None


def test_collectors_stale_ais_reports_down(client, db_session):
    # Data exists, but older than the 2h AIS freshness window
    db_session.add(_position("111111111", datetime.utcnow() - timedelta(days=2)))
    db_session.commit()
    body = client.get("/api/health/collectors").json()
    assert body["ais"] is False
    assert body["last_seen"]["ais"] is not None


def test_collectors_fresh_ais_reports_up(client, db_session):
    db_session.add(_position("222222222", datetime.utcnow()))
    db_session.commit()
    body = client.get("/api/health/collectors").json()
    assert body["ais"] is True


# ─── require_pro DB-recheck (refund/downgrade case) ─────────────────────────


def test_pro_jwt_claim_without_subscription_is_rejected(db_session):
    """A token minted with sub_status=pro must NOT grant access once the
    subscription is gone from the DB (refund before token expiry)."""
    token = create_token("refunded@example.com", subscription_status="pro")
    with pytest.raises(HTTPException) as exc:
        require_pro(None, token)
    assert exc.value.status_code == 403


def test_pro_jwt_claim_with_expired_subscription_is_rejected(db_session):
    db_session.add(
        Subscription(
            email="expired@example.com",
            status="expired",
            plan="pro",
            lemon_squeezy_id="ls-expired",
        )
    )
    db_session.commit()
    token = create_token("expired@example.com", subscription_status="pro")
    with pytest.raises(HTTPException) as exc:
        require_pro(None, token)
    assert exc.value.status_code == 403


def test_pro_jwt_claim_with_active_subscription_is_allowed(db_session):
    db_session.add(
        Subscription(
            email="active@example.com",
            status="active",
            plan="pro",
            lemon_squeezy_id="ls-active",
        )
    )
    db_session.commit()
    token = create_token("active@example.com", subscription_status="pro")
    user = require_pro(None, token)
    assert user["email"] == "active@example.com"
