"""
Tests for the per-user watchlist (Personal Supply-Watch keystone):
  - /catalog is public and lists materials + zones
  - CRUD is LOGIN-gated (anon 401, any logged-in user 200 — free product)
  - add is idempotent on (email, kind, key); invalid keys 422; delete works
  - items are per-user (no shared guest)
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.auth.jwt import create_token
from backend.main import app


@pytest.fixture
def client(db_session):
    return TestClient(app)


def _login_cookie(email: str) -> dict[str, str]:
    """A valid session cookie for a logged-in user (no Pro needed)."""
    return {"obsyd_token": create_token(email)}


def test_catalog_is_public(client):
    r = client.get("/api/watchlist/catalog")
    assert r.status_code == 200
    data = r.json()
    materials = {m["key"] for m in data["material"]}
    assert {"cobalt", "rare_earths", "copper"} <= materials
    zones = {z["key"] for z in data["zone"]}
    assert "hormuz" in zones  # chokepoint geofence
    assert "DE_LU" in zones  # power bidding zone


def test_crud_requires_login(client, db_session):
    # anonymous → 401 (login required)
    assert client.get("/api/watchlist").status_code == 401
    # any logged-in user (no Pro needed — the product is free, login-gated) → 200
    ck = _login_cookie("free@obsyd.dev")
    r = client.get("/api/watchlist", cookies=ck)
    assert r.status_code == 200
    assert r.json()["items"] == []


def test_add_list_idempotent_delete(client, db_session):
    email = "watcher@obsyd.dev"
    ck = _login_cookie(email)

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
    ck = _login_cookie(email)
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
    # Two different logged-in users must NOT share a watchlist (the shared-guest bug).
    client.post(
        "/api/watchlist", json={"kind": "zone", "key": "hormuz"}, cookies=_login_cookie("a@obsyd.dev")
    )
    rb = client.get("/api/watchlist", cookies=_login_cookie("b@obsyd.dev"))
    assert rb.json()["items"] == []
