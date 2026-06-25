"""
Tests for the per-user watchlist (Personal Supply-Watch keystone):
  - /catalog is public and lists materials + zones
  - CRUD is Pro-gated (anon 401, authed-non-pro 403)
  - add is idempotent on (email, kind, key); invalid keys 422; delete works
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


def _pro_cookie(email: str) -> dict[str, str]:
    return {"obsyd_token": create_token(email, subscription_status="pro")}


def _make_pro(db_session, email: str):
    db_session.add(
        Subscription(email=email, status="active", plan="pro", lemon_squeezy_id=f"ls-{email}")
    )
    db_session.commit()


def test_catalog_is_public(client):
    r = client.get("/api/watchlist/catalog")
    assert r.status_code == 200
    data = r.json()
    materials = {m["key"] for m in data["material"]}
    assert {"cobalt", "rare_earths", "copper"} <= materials
    zones = {z["key"] for z in data["zone"]}
    assert "hormuz" in zones  # chokepoint geofence
    assert "DE_LU" in zones  # power bidding zone


def test_crud_requires_pro(client, db_session):
    # anonymous → 401
    assert client.get("/api/watchlist").status_code == 401
    # authenticated but no Pro subscription in DB → require_pro re-checks DB → 403
    ck = _pro_cookie("free@obsyd.dev")
    assert client.get("/api/watchlist", cookies=ck).status_code == 403


def test_add_list_idempotent_delete(client, db_session):
    email = "watcher@obsyd.dev"
    _make_pro(db_session, email)
    ck = _pro_cookie(email)

    r = client.post("/api/watchlist", json={"kind": "material", "key": "cobalt"}, cookies=ck)
    assert r.status_code == 200
    item = r.json()
    assert item["label"] == "Cobalt"
    item_id = item["id"]

    # duplicate add is a no-op → same row
    r2 = client.post("/api/watchlist", json={"kind": "material", "key": "cobalt"}, cookies=ck)
    assert r2.status_code == 200
    assert r2.json()["id"] == item_id

    r3 = client.get("/api/watchlist", cookies=ck)
    assert r3.status_code == 200
    assert len(r3.json()["items"]) == 1

    r4 = client.delete(f"/api/watchlist/{item_id}", cookies=ck)
    assert r4.status_code == 200
    assert client.get("/api/watchlist", cookies=ck).json()["items"] == []


def test_invalid_key_rejected(client, db_session):
    email = "watcher2@obsyd.dev"
    _make_pro(db_session, email)
    ck = _pro_cookie(email)
    assert (
        client.post(
            "/api/watchlist", json={"kind": "material", "key": "unobtanium"}, cookies=ck
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/watchlist", json={"kind": "bogus", "key": "hormuz"}, cookies=ck
        ).status_code
        == 422
    )


def test_watchlist_is_per_user(client, db_session):
    _make_pro(db_session, "a@obsyd.dev")
    _make_pro(db_session, "b@obsyd.dev")
    client.post(
        "/api/watchlist", json={"kind": "zone", "key": "hormuz"}, cookies=_pro_cookie("a@obsyd.dev")
    )
    # b sees nothing a saved
    rb = client.get("/api/watchlist", cookies=_pro_cookie("b@obsyd.dev"))
    assert rb.json()["items"] == []
