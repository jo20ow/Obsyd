"""Per-client concurrency cap for /api/* — one visitor cannot starve everyone else.

Incident 2026-11-11 10:00: one browser hopping between zones held 46 API requests
in flight (most of them already abandoned client-side). The sync route handlers run
in Starlette's ~40-thread pool against a 15-connection DB pool, so that one client
exhausted both: `QueuePool limit ... reached` 500s for every visitor, and the cheap
endpoints (/api/v1/meta, the heartbeat probe) queued for seconds.

This pure-ASGI middleware gives every client IP at most `limit` concurrent /api/*
requests. The rest do NOT fail — they wait in the event loop, which costs no thread
and no DB connection, so a full desk load (~26 parallel panel fetches) still
completes, just in turn. Two refinements:

- A request whose client disconnects while it is still queued is dropped before it
  reaches the app (zone-hopping aborts most of its own requests; those used to be
  computed in full anyway).
- Past `max_waiting` queued requests per client, the overflow gets an immediate 429
  with Retry-After instead of an ever-growing queue.

A handler already running in a thread cannot be interrupted — the cap bounds how
many of those one client can have, which is what protects the others.
"""
from __future__ import annotations

import asyncio
import os

from backend.auth.ratelimit import client_ip

DEFAULT_LIMIT = int(os.environ.get("OBSYD_PER_CLIENT_CONCURRENCY", "8"))
DEFAULT_MAX_WAITING = int(os.environ.get("OBSYD_PER_CLIENT_MAX_WAITING", "100"))


class _Headers:
    """Just enough of a Request for `client_ip` (headers.get + client.host)."""

    def __init__(self, scope):
        self.headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        client = scope.get("client")
        self.client = type("C", (), {"host": client[0]})() if client else None


class _Slot:
    __slots__ = ("sem", "users", "waiting")

    def __init__(self, limit: int):
        self.sem = asyncio.Semaphore(limit)
        self.users = 0  # running + waiting; the slot is dropped when it reaches 0
        self.waiting = 0


class PerClientConcurrencyMiddleware:
    def __init__(self, app, limit: int = DEFAULT_LIMIT, max_waiting: int = DEFAULT_MAX_WAITING,
                 prefix: str = "/api/"):
        self.app = app
        self.limit = limit
        self.max_waiting = max_waiting
        self.prefix = prefix
        self._slots: dict[str, _Slot] = {}

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope.get("path", "").startswith(self.prefix):
            await self.app(scope, receive, send)
            return

        key = client_ip(_Headers(scope))
        slot = self._slots.get(key)
        if slot is None:
            slot = self._slots[key] = _Slot(self.limit)

        if slot.sem.locked() and slot.waiting >= self.max_waiting:
            await _reject(send)
            if slot.users == 0:
                self._slots.pop(key, None)
            return

        slot.users += 1
        try:
            if not slot.sem.locked():
                await slot.sem.acquire()  # free slot: no waiting, no disconnect watch
                app_receive = receive
            else:
                app_receive = await self._wait_or_drop(slot, receive)
                if app_receive is None:
                    return  # client left while queued — never reaches the app
            try:
                await self.app(scope, app_receive, send)
            finally:
                slot.sem.release()
        finally:
            slot.users -= 1
            if slot.users == 0:
                self._slots.pop(key, None)

    async def _wait_or_drop(self, slot: _Slot, receive):
        """Wait for a slot while watching for the client's disconnect.

        Returns the receive callable to hand the app (replaying the request body
        message already read), or None if the client disconnected first (the slot
        is then NOT held).
        """
        first = await receive()
        if first.get("type") == "http.disconnect":
            return None
        if first.get("more_body"):
            # Streaming body (not the desk's GETs): don't interleave reads — just wait.
            slot.waiting += 1
            try:
                await slot.sem.acquire()
            finally:
                slot.waiting -= 1
            return _replay(first, receive, None)

        watch = asyncio.ensure_future(receive())  # resolves on http.disconnect
        acquire = asyncio.ensure_future(slot.sem.acquire())
        slot.waiting += 1
        try:
            await asyncio.wait({watch, acquire}, return_when=asyncio.FIRST_COMPLETED)
        except BaseException:
            acquire.cancel()
            watch.cancel()
            raise
        finally:
            slot.waiting -= 1

        if watch.done() and watch.result().get("type") == "http.disconnect":
            if acquire.done() and not acquire.cancelled():
                slot.sem.release()
            else:
                acquire.cancel()
            return None
        if not acquire.done():  # watch saw something other than a disconnect
            await acquire
        return _replay(first, receive, watch)


def _replay(first, receive, watch):
    """A receive that yields the buffered first message, then the watch task's
    result (if any), then the real receive."""
    pending = [first]

    async def app_receive():
        if pending:
            return pending.pop(0)
        nonlocal watch
        if watch is not None:
            w, watch = watch, None
            return await w
        return await receive()

    return app_receive


async def _reject(send):
    await send({
        "type": "http.response.start",
        "status": 429,
        "headers": [(b"content-type", b"application/json"), (b"retry-after", b"2")],
    })
    await send({
        "type": "http.response.body",
        "body": b'{"detail":"Too many concurrent requests from this client - retry shortly."}',
    })
