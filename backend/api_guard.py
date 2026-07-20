"""Availability guards for the public v1 data API.

The v1 read endpoints run as sync handlers in Starlette's threadpool (one uvicorn
worker, ~40 threads). A handful of the endpoints scan the 28M-row power_hourly
store, and one — /series/catalog — did a full min/max scan on every unthrottled
call. Under a launch spike (or a curl loop) enough concurrent heavy scans occupy
every threadpool thread and the whole app stalls for everyone.

Two cheap defences, no external dependency:

- `heavy_query_guard`: a bounded semaphore so at most HEAVY_QUERY_SLOTS heavy
  scans run at once; the overflow gets an immediate 503 instead of queueing and
  starving the light endpoints (health, situation, meta). It does NOT slow a
  legitimate single bulk pull — it only caps how many run simultaneously.
- `cached_value`: a keyed TTL cache — the catalog's coverage window (and, since
  P1, its per-(series,zone) coverage table) changes only when the hourly ingest
  writes (hourly), so caching either for an hour turns a full-table scan into
  one scan per hour instead of one per request. A lock collapses a cold-start
  thundering herd to one scan per key. `cached_coverage` is the original global
  window, kept as a thin wrapper over `cached_value` for existing callers.
"""
from __future__ import annotations

import os
import threading
import time
from typing import Callable

from fastapi import HTTPException

# From ~40 threadpool threads, allow this many concurrent heavy scans; the rest
# stay free for the cheap endpoints. Env-tunable without a code change.
HEAVY_QUERY_SLOTS = int(os.environ.get("OBSYD_HEAVY_QUERY_SLOTS", "8"))

_heavy_sem = threading.BoundedSemaphore(HEAVY_QUERY_SLOTS)


def heavy_query_guard():
    """FastAPI dependency: hold one heavy-query slot for the request's duration.

    Non-blocking acquire — if every slot is taken, fail fast with 503 rather than
    block this threadpool thread waiting (which would make the exhaustion worse).
    """
    if not _heavy_sem.acquire(blocking=False):
        raise HTTPException(
            status_code=503,
            detail="The data API is busy (too many concurrent large queries). Retry shortly.",
        )
    try:
        yield
    finally:
        _heavy_sem.release()


_COVERAGE_TTL = 3600.0
_cache: dict[str, dict[str, object]] = {}
_cache_lock = threading.Lock()


def cached_value(key: str, compute: Callable[[], object], *, ttl: float = _COVERAGE_TTL,
                  now: float | None = None) -> object:
    """Return the cached value for `key`, recomputing at most once per `ttl` seconds.

    One shared keyed cache rather than one slot per caller: every entry (the global
    coverage window, the per-(series,zone) coverage table, …) needs the identical
    "recompute at most once per TTL, one lock so a cold thundering herd does the
    expensive `compute` once, not N times" semantics, so it lives here once instead
    of copy-pasted per cache.
    """
    t = time.monotonic() if now is None else now
    slot = _cache.get(key)
    if slot is not None and slot["value"] is not None and t < slot["expires"]:
        return slot["value"]
    with _cache_lock:
        # Re-check inside the lock: another thread may have filled it while we waited.
        slot = _cache.get(key)
        if slot is not None and slot["value"] is not None and t < slot["expires"]:
            return slot["value"]
        value = compute()
        _cache[key] = {"value": value, "expires": t + ttl}
        return value


def cached_coverage(compute: Callable[[], object], *, now: float | None = None) -> object:
    """The catalog's global coverage window, cached under its own key.

    Kept as a thin wrapper so existing callers/tests (which pass no `ttl`) are
    unaffected by the move to a keyed cache.
    """
    return cached_value("coverage", compute, ttl=_COVERAGE_TTL, now=now)


def _reset_coverage_cache() -> None:
    """Test hook — clears the WHOLE keyed cache, not just 'coverage'. Every cache
    entry is process-global and would otherwise leak its value across tests."""
    _cache.clear()
