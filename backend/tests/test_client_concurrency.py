"""Per-client concurrency cap for /api/* (2026-11-11: one zone-hopping browser held
46 requests in flight, exhausted the 15-connection DB pool and 500'd everyone)."""
from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi import FastAPI

from backend.client_concurrency import PerClientConcurrencyMiddleware


def _app(limit=2, max_waiting=10, delay=0.05):
    app = FastAPI()
    state = {"inflight": 0, "peak": 0, "served": 0}

    @app.get("/api/slow")
    async def slow():
        state["inflight"] += 1
        state["peak"] = max(state["peak"], state["inflight"])
        await asyncio.sleep(delay)
        state["inflight"] -= 1
        state["served"] += 1
        return {"ok": True}

    @app.get("/page")
    async def page():
        state["inflight"] += 1
        state["peak"] = max(state["peak"], state["inflight"])
        await asyncio.sleep(delay)
        state["inflight"] -= 1
        return {"ok": True}

    wrapped = PerClientConcurrencyMiddleware(app, limit=limit, max_waiting=max_waiting)
    return wrapped, state


def _client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


def _ip(ip):
    return {"X-Forwarded-For": ip}


@pytest.mark.anyio
async def test_one_client_is_capped_but_every_request_is_served():
    app, state = _app(limit=2)
    async with _client(app) as c:
        rs = await asyncio.gather(*[c.get("/api/slow", headers=_ip("1.1.1.1")) for _ in range(8)])
    assert all(r.status_code == 200 for r in rs)
    assert state["peak"] == 2
    assert state["served"] == 8


@pytest.mark.anyio
async def test_other_clients_are_not_blocked_by_a_greedy_one():
    app, state = _app(limit=2)
    async with _client(app) as c:
        rs = await asyncio.gather(
            *[c.get("/api/slow", headers=_ip("1.1.1.1")) for _ in range(4)],
            *[c.get("/api/slow", headers=_ip("2.2.2.2")) for _ in range(2)],
        )
    assert all(r.status_code == 200 for r in rs)
    assert state["peak"] == 4  # 2 per client, side by side


@pytest.mark.anyio
async def test_queue_overflow_is_rejected_fast_with_429():
    app, state = _app(limit=1, max_waiting=2)
    async with _client(app) as c:
        rs = await asyncio.gather(*[c.get("/api/slow", headers=_ip("1.1.1.1")) for _ in range(6)])
    codes = sorted(r.status_code for r in rs)
    assert codes.count(200) == 3  # 1 running + 2 waiting
    assert codes.count(429) == 3
    assert all("retry-after" in r.headers for r in rs if r.status_code == 429)


@pytest.mark.anyio
async def test_non_api_paths_are_untouched():
    app, state = _app(limit=1)
    async with _client(app) as c:
        await asyncio.gather(*[c.get("/page", headers=_ip("1.1.1.1")) for _ in range(4)])
    assert state["peak"] == 4


@pytest.mark.anyio
async def test_request_abandoned_while_queued_never_reaches_the_app():
    reached = []

    async def inner(scope, receive, send):
        reached.append(scope["path"])
        await asyncio.sleep(0.05)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    mw = PerClientConcurrencyMiddleware(inner, limit=1, max_waiting=10)

    def scope(path):
        return {"type": "http", "path": path, "headers": [(b"x-forwarded-for", b"9.9.9.9")],
                "client": ("127.0.0.1", 1)}

    async def receive_ok():
        await asyncio.sleep(10)  # stays connected
        return {"type": "http.disconnect"}

    gone = asyncio.Event()
    first = [True]

    async def receive_abandoning():
        if first[0]:
            first[0] = False
            return {"type": "http.request", "body": b"", "more_body": False}
        await gone.wait()
        return {"type": "http.disconnect"}

    sent = []

    async def send(msg):
        sent.append(msg)

    async def first_receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    calls = {"n": 0}

    async def receive_first_ok():
        calls["n"] += 1
        if calls["n"] == 1:
            return await first_receive()
        return await receive_ok()

    holder = asyncio.create_task(mw(scope("/api/a"), receive_first_ok, send))
    await asyncio.sleep(0.01)  # /api/a now holds the only slot
    waiter = asyncio.create_task(mw(scope("/api/b"), receive_abandoning, send))
    await asyncio.sleep(0.01)
    gone.set()  # client of /api/b disconnects while queued
    await asyncio.wait_for(asyncio.gather(holder, waiter), 2)
    assert reached == ["/api/a"]


@pytest.fixture
def anyio_backend():
    return "asyncio"
