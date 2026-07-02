"""Magic-link tokens are single-use — a captured/leaked link can't be replayed."""
from __future__ import annotations

from fastapi.testclient import TestClient

from backend.auth import single_use
from backend.auth.jwt import create_magic_token
from backend.main import app


def test_consume_first_use_true_then_false():
    single_use._consumed.clear()
    assert single_use.consume("abc", exp=9_999_999_999) is True
    assert single_use.consume("abc", exp=9_999_999_999) is False  # replay rejected


def test_consume_prunes_expired_jti():
    single_use._consumed.clear()
    assert single_use.consume("old", exp=100, now=50) is True
    # once past its expiry the jti is pruned, so the id is free again
    assert single_use.consume("old", exp=100, now=200) is True


def test_magic_link_verify_is_single_use(db_session):
    single_use._consumed.clear()
    client = TestClient(app)
    token = create_magic_token("user@example.com")

    r1 = client.get(f"/api/auth/verify?token={token}")
    assert r1.status_code == 200
    assert "auth=success" in r1.text
    assert "obsyd_token" in r1.headers.get("set-cookie", "")

    r2 = client.get(f"/api/auth/verify?token={token}")
    assert "auth=success" not in r2.text  # second use rejected
    assert "obsyd_token" not in r2.headers.get("set-cookie", "")
