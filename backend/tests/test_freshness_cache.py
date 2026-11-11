"""The freshness evaluation behind /api/health/collectors and /api/v1/status is
cached briefly: it runs ~80 MAX() probes (p50 2.8 s on prod) and every open desk
tab polled it, which queued the cheap endpoints (/api/v1/meta took 4–10 s and the
heartbeat timed out, 2026-11-10/11)."""
from __future__ import annotations

from fastapi.testclient import TestClient

from backend.collectors import freshness
from backend.main import app


def test_cached_freshness_computes_once_within_ttl(db_session, monkeypatch):
    calls = []
    real = freshness.evaluate_freshness

    def counting(db, **kw):
        calls.append(1)
        return real(db, **kw)

    monkeypatch.setattr(freshness, "evaluate_freshness", counting)
    a = freshness.cached_freshness(db_session)
    b = freshness.cached_freshness(db_session)
    assert a == b
    assert len(calls) == 1


def test_cached_freshness_recomputes_after_ttl(db_session, monkeypatch):
    calls = []
    monkeypatch.setattr(freshness, "evaluate_freshness", lambda db, **kw: calls.append(1) or {})
    freshness.cached_freshness(db_session, now=0.0)
    freshness.cached_freshness(db_session, now=freshness.FRESHNESS_CACHE_TTL - 1)
    assert len(calls) == 1
    freshness.cached_freshness(db_session, now=freshness.FRESHNESS_CACHE_TTL + 1)
    assert len(calls) == 2


def test_both_routes_share_one_computation(db_session, monkeypatch):
    calls = []
    real = freshness.evaluate_freshness

    def counting(db, **kw):
        calls.append(1)
        return real(db, **kw)

    monkeypatch.setattr(freshness, "evaluate_freshness", counting)
    client = TestClient(app)
    assert client.get("/api/health/collectors").status_code == 200
    assert client.get("/api/v1/status").status_code == 200
    assert client.get("/api/health/collectors").status_code == 200
    assert len(calls) == 1
