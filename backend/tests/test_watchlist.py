"""
Tests for the per-user watchlist (Personal Watch keystone). Refocus 2026-07-03:
the catalog is the electricity desk's universe — power bidding zones + the
day-ahead power price and TTF gas price. Materials / chokepoint zones / crypto
moved to the sibling project.
  - /catalog is public and lists zones + symbols
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
    zones = {z["key"] for z in data["zone"]}
    assert {"DE_LU", "FR", "NL"} <= zones  # power bidding zones
    symbols = {s["key"] for s in data["symbol"]}
    assert {"TTF", "POWER_DE"} <= symbols
    assert "material" not in data and "crypto" not in data  # moved to the sibling


def test_crud_requires_login(client, db_session):
    assert client.get("/api/watchlist").status_code == 401
    ck = _login_cookie("free@obsyd.dev")
    r = client.get("/api/watchlist", cookies=ck)
    assert r.status_code == 200
    assert r.json()["items"] == []


def test_add_list_idempotent_delete(client, db_session):
    email = "watcher@obsyd.dev"
    ck = _login_cookie(email)

    r = client.post("/api/watchlist", json={"kind": "zone", "key": "DE_LU"}, cookies=ck)
    assert r.status_code == 200
    item = r.json()
    assert item["label"] == "DE-LU"
    item_id = item["id"]

    # duplicate add is a no-op → same row
    r2 = client.post("/api/watchlist", json={"kind": "zone", "key": "DE_LU"}, cookies=ck)
    assert r2.status_code == 200
    assert r2.json()["id"] == item_id

    r3 = client.get("/api/watchlist", cookies=ck)
    assert len(r3.json()["items"]) == 1

    r4 = client.delete(f"/api/watchlist/{item_id}", cookies=ck)
    assert r4.status_code == 200
    assert client.get("/api/watchlist", cookies=ck).json()["items"] == []


def test_invalid_key_rejected(client, db_session):
    ck = _login_cookie("watcher2@obsyd.dev")
    assert client.post("/api/watchlist", json={"kind": "symbol", "key": "unobtanium"}, cookies=ck).status_code == 422
    assert client.post("/api/watchlist", json={"kind": "bogus", "key": "DE_LU"}, cookies=ck).status_code == 422
    # kinds that moved to the sibling are no longer valid here
    assert client.post("/api/watchlist", json={"kind": "material", "key": "cobalt"}, cookies=ck).status_code == 422


def test_catalog_includes_symbols(client):
    data = client.get("/api/watchlist/catalog").json()
    assert "symbol" in data
    keys = {s["key"] for s in data["symbol"]}
    assert {"TTF", "POWER_DE"} <= keys


def test_add_symbol_item(client, db_session):
    ck = _login_cookie("sym@obsyd.dev")
    r = client.post("/api/watchlist", json={"kind": "symbol", "key": "TTF"}, cookies=ck)
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "symbol" and body["key"] == "TTF" and body["label"]
    assert client.post("/api/watchlist", json={"kind": "symbol", "key": "NOPE"}, cookies=ck).status_code == 422


def test_watch_block_renders_symbol_item(db_session):
    from backend.models.watchlist import WatchlistItem
    from backend.notifications.daily_email import _build_watch_block

    db_session.add(WatchlistItem(email="s@obsyd.dev", kind="symbol", key="TTF", label="Dutch TTF Gas (TTF)"))
    db_session.commit()
    html = _build_watch_block(db_session, "s@obsyd.dev")
    assert "TTF" in html


def test_watchlist_is_per_user(client, db_session):
    client.post(
        "/api/watchlist", json={"kind": "zone", "key": "DE_LU"}, cookies=_login_cookie("a@obsyd.dev")
    )
    rb = client.get("/api/watchlist", cookies=_login_cookie("b@obsyd.dev"))
    assert rb.json()["items"] == []
