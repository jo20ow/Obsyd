"""API keys + metering (Phase 2): mint/list/revoke under the email identity,
key-authenticated premium access resolving the OWNER'S subscription per
request (never a stored tier), and the aggregate metering flush — counters in
memory on the hot path, SQLite rows only via the scheduler flush."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from backend import metering
from backend.auth.api_keys import create_key, hash_key
from backend.models.api_key import ApiKey, ApiUsageDaily
from backend.models.subscription import Subscription
from backend.power.hourly_store import upsert_hourly


@pytest.fixture(autouse=True)
def _clean():
    metering._reset()
    yield
    metering._reset()
    from backend.main import app

    app.dependency_overrides.clear()


def _client(db, *, login: str | None = None) -> TestClient:
    from backend.auth.dependencies import require_auth
    from backend.database import get_db
    from backend.main import app

    app.dependency_overrides[get_db] = lambda: db
    if login:
        app.dependency_overrides[require_auth] = lambda: {"email": login}
    return TestClient(app, raise_server_exceptions=True)


def _mint(db, email="owner@test", *, pro=True) -> str:
    if pro:
        db.add(Subscription(email=email, status="active", plan="pro"))
        db.commit()
    _, raw = create_key(db, email, "test key")
    return raw


# ─── issuance / management ───────────────────────────────────────────────────


def test_mint_list_revoke_roundtrip(db_session):
    c = _client(db_session, login="a@b.c")
    made = c.post("/api/v1/keys", json={"label": "desk"}).json()
    assert made["key"].startswith("obsyd_") and made["prefix"] == made["key"][:14]
    # Only the hash is stored — the raw key is nowhere in the DB.
    row = db_session.query(ApiKey).one()
    assert row.key_hash == hash_key(made["key"]) and made["key"] not in (row.key_hash, row.prefix)

    listed = c.get("/api/v1/keys").json()
    assert [k["id"] for k in listed["keys"]] == [made["id"]]
    assert "key" not in listed["keys"][0]  # never retrievable again

    assert c.delete(f"/api/v1/keys/{made['id']}").json() == {"revoked": made["id"]}
    assert c.get("/api/v1/keys").json()["keys"][0]["revoked_at"] is not None


def test_active_key_cap(db_session):
    c = _client(db_session, login="a@b.c")
    for _ in range(5):
        assert c.post("/api/v1/keys", json={}).status_code == 200
    assert c.post("/api/v1/keys", json={}).status_code == 409


def test_management_requires_login(db_session):
    c = _client(db_session)
    assert c.post("/api/v1/keys", json={}).status_code == 401
    assert c.get("/api/v1/keys").status_code == 401


# ─── key-authenticated premium access ────────────────────────────────────────


def _seed_premium(db):
    ts = int(datetime(2026, 9, 1, tzinfo=timezone.utc).timestamp())
    upsert_hourly(db, "spread.tb2", "DE_LU", [(ts, 225.0)], unit="EUR/MW-day")


def test_pro_key_opens_the_premium_gate(db_session):
    _seed_premium(db_session)
    raw = _mint(db_session, pro=True)
    c = _client(db_session)
    url = "/api/v1/series?series=spread.tb2&zone=DE_LU&start=2026-08-01"
    assert c.get(url).status_code == 403  # anonymous stays out
    body = c.get(url, headers={"Authorization": f"Bearer {raw}"}).json()
    assert body["available"] is True and body["data"][0]["value"] == 225.0
    # X-Api-Key works identically.
    assert c.get(url, headers={"X-Api-Key": raw}).json()["available"] is True
    # /api/power/convergence accepts the key path too (401→ok shape).
    assert c.get("/api/power/convergence",
                 headers={"X-Api-Key": raw}).json()["available"] is False


def test_free_key_and_revoked_key_stay_out(db_session):
    _seed_premium(db_session)
    free_raw = _mint(db_session, "free@t.co", pro=False)
    c = _client(db_session)
    url = "/api/v1/series?series=spread.tb2&zone=DE_LU"
    assert c.get(url, headers={"X-Api-Key": free_raw}).status_code == 403

    pro_raw = _mint(db_session, "pro@t.co", pro=True)
    key_row = db_session.query(ApiKey).filter(ApiKey.email == "pro@t.co").one()
    key_row.revoked_at = datetime.utcnow()
    db_session.commit()
    # Revoked = the key no longer authenticates at all (403 like anonymous).
    assert c.get(url, headers={"X-Api-Key": pro_raw}).status_code == 403
    assert c.get(url, headers={"X-Api-Key": "obsyd_nonsense"}).status_code == 403


# ─── metering ────────────────────────────────────────────────────────────────


def test_keyed_requests_are_metered_once_with_points(db_session):
    _seed_premium(db_session)
    raw = _mint(db_session, pro=True)
    key_id = db_session.query(ApiKey.id).scalar()
    c = _client(db_session)
    c.get("/api/v1/series?series=spread.tb2&zone=DE_LU&start=2026-08-01",
          headers={"X-Api-Key": raw})
    c.get("/api/v1/meta", headers={"X-Api-Key": raw})
    requests, points = metering.usage_today(key_id)
    # Two requests, one of which served exactly one row. The chokepoint +
    # request.state cache guarantee no double count despite three resolution
    # sites (rate limit, optional_pro, points).
    assert (requests, points) == (2, 1)


def test_flush_upserts_and_stamps_last_used(db_session):
    raw = _mint(db_session, pro=True)
    key_id = db_session.query(ApiKey.id).scalar()
    metering.record(key_id, requests=3, points=100)
    assert metering.flush(db_session) == 1
    row = db_session.query(ApiUsageDaily).one()
    assert (row.requests, row.points) == (3, 100)
    # A second interval ADDS to the same day row.
    metering.record(key_id, requests=2, points=50)
    metering.flush(db_session)
    row = db_session.query(ApiUsageDaily).one()
    assert (row.requests, row.points) == (5, 150)
    assert db_session.query(ApiKey).one().last_used_at is not None
    # Nothing pending → no-op.
    assert metering.flush(db_session) == 0
    _ = raw


def test_usage_endpoint_merges_ledger_and_memory(db_session):
    raw = _mint(db_session, "a@b.c", pro=False)
    key_id = db_session.query(ApiKey.id).scalar()
    metering.record(key_id, requests=4, points=10)
    metering.flush(db_session)
    metering.record(key_id, requests=1, points=5)  # un-flushed tail
    c = _client(db_session, login="a@b.c")
    body = c.get("/api/v1/keys/usage").json()
    today = [r for r in body["usage"] if r["key_id"] == key_id][0]
    assert (today["requests"], today["points"]) == (5, 15)
    _ = raw
