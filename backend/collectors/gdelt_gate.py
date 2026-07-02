"""Shared rate-limit gate for ALL GDELT DOC 2.0 traffic.

GDELT hard-limits to ~1 request / 5s per IP (429 otherwise), and punishes bursts
with a sticky ban. The app has several independent GDELT callers (the sentiment
collector in ``collectors/gdelt.py`` and the cross-asset news reader in
``news/gdelt_news.py``). Throttling each caller on its own is not enough — from
GDELT's view they share one IP budget, so their calls interleave and blow the limit.

Everything that talks to GDELT must go through :func:`gdelt_get`, which serializes
all callers through a single lock and enforces one minimum interval between any two
requests.
"""
from __future__ import annotations

import asyncio
import time

import httpx

_lock = asyncio.Lock()
_last_call = 0.0
# GDELT's documented limit is 1 req / 5s, but bursts trigger a sticky IP ban that
# outlasts the burst, so we keep a conservative margin above the documented rate.
MIN_INTERVAL = 10.0  # seconds between ANY two GDELT calls


async def gdelt_get(
    client: httpx.AsyncClient, url: str, params: dict, timeout: float = 20
) -> httpx.Response:
    """GET ``url`` behind the shared GDELT gate: serialize all callers and space
    requests by at least ``MIN_INTERVAL`` so the shared IP never trips a 429."""
    global _last_call
    async with _lock:
        wait = MIN_INTERVAL - (time.monotonic() - _last_call)
        if wait > 0:
            await asyncio.sleep(wait)
        try:
            return await client.get(url, params=params, timeout=timeout)
        finally:
            _last_call = time.monotonic()
