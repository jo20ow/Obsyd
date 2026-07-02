"""Shared GDELT rate-limit gate: all callers must serialize through one interval."""
from __future__ import annotations

import asyncio
import time

from backend.collectors import gdelt_gate


class _FakeResp:
    def raise_for_status(self):
        pass

    def json(self):
        return {}


class _FakeClient:
    def __init__(self, log):
        self._log = log

    async def get(self, url, params=None, timeout=None):
        self._log.append(time.monotonic())
        return _FakeResp()


def test_gdelt_get_serializes_with_min_interval(monkeypatch):
    """Three concurrent callers (mimicking the sentiment collector + news reader
    hitting GDELT from the same IP) must be spaced >= MIN_INTERVAL apart."""
    monkeypatch.setattr(gdelt_gate, "MIN_INTERVAL", 0.1)
    gdelt_gate._last_call = 0.0
    calls: list[float] = []
    client = _FakeClient(calls)

    async def main():
        await asyncio.gather(
            gdelt_gate.gdelt_get(client, "https://gdelt", {"q": "a"}),
            gdelt_gate.gdelt_get(client, "https://gdelt", {"q": "b"}),
            gdelt_gate.gdelt_get(client, "https://gdelt", {"q": "c"}),
        )

    asyncio.run(main())

    assert len(calls) == 3
    gaps = [calls[i + 1] - calls[i] for i in range(len(calls) - 1)]
    assert all(g >= 0.1 * 0.9 for g in gaps), gaps  # ~MIN_INTERVAL spacing, small tolerance
