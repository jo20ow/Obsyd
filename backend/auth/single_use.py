"""Single-use guard for magic-link tokens (in-process, single-worker).

Magic links are short-lived JWTs; without single-use a leaked or captured link
(email logs, proxies, shoulder-surf) is replayable within its 15-min window. We
record consumed ``jti`` values in-process — consistent with backend/auth/ratelimit.py
and correct for the single uvicorn worker — and prune them once past expiry. A
process restart clears the set, which is acceptable given the <=15-min token life.
"""
from __future__ import annotations

import threading
import time

_consumed: dict[str, float] = {}  # jti -> token expiry (unix seconds)
_lock = threading.Lock()


def _prune(now: float) -> None:
    for jti, exp in list(_consumed.items()):
        if exp <= now:
            del _consumed[jti]


def consume(jti: str, exp: float, *, now: float | None = None) -> bool:
    """Atomically mark ``jti`` consumed. Returns True on first use, False if the
    id was already consumed (i.e. a replay). Expired ids are pruned first, so a
    jti becomes free again only after its own token would have expired anyway."""
    t = now if now is not None else time.time()
    with _lock:
        _prune(t)
        if jti in _consumed:
            return False
        _consumed[jti] = exp
        return True
