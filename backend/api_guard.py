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
- `cached_coverage`: the catalog's coverage window changes only when the hourly
  ingest writes (hourly), so a short TTL cache turns a 28s cold scan into one
  scan per hour. A lock collapses a cold-start thundering herd to one scan.
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
_coverage: dict[str, object] = {"value": None, "expires": 0.0}
_coverage_lock = threading.Lock()


def cached_coverage(compute: Callable[[], object], *, now: float | None = None) -> object:
    """Return the cached coverage window, recomputing at most once per TTL.

    `compute` is the expensive min/max scan; it runs under a lock so a cold cache
    hit by N threads does the scan once, not N times.
    """
    t = time.monotonic() if now is None else now
    if _coverage["value"] is not None and t < _coverage["expires"]:
        return _coverage["value"]
    with _coverage_lock:
        # Re-check inside the lock: another thread may have filled it while we waited.
        if _coverage["value"] is not None and t < _coverage["expires"]:
            return _coverage["value"]
        _coverage["value"] = compute()
        _coverage["expires"] = t + _COVERAGE_TTL
        return _coverage["value"]


def _reset_coverage_cache() -> None:
    """Test hook."""
    _coverage["value"] = None
    _coverage["expires"] = 0.0
