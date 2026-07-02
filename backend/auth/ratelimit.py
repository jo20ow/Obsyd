"""In-process rate limiter for abuse-prone auth endpoints — no external dependency.

Production runs a single uvicorn worker (deploy/obsyd.service --workers 1) on one
asyncio event loop, so module-level state is authoritative and the critical section
below contains no ``await`` — under cooperative scheduling no other coroutine can
interleave it, so no lock is needed. Sliding-window counters per key, pruned on access.

Primary target: ``POST /api/auth/magic-link``, which sends a real Resend email to an
arbitrary address with no throttle — an email-bomb / quota-exhaustion / sender-reputation
vector. Limits are per-email (cooldown + hourly cap) and per-IP (hourly cap).
"""

from __future__ import annotations

import time
from collections import deque

# key -> monotonic timestamps of recorded hits (ascending)
_HITS: dict[str, deque[float]] = {}


def allow(rules: list[tuple[str, int, float]], *, now: float | None = None) -> bool:
    """Return True (and record a hit on every rule) iff ALL rules are under their limit.

    rules: list of (key, limit, window_seconds). A blocked call records nothing, so a
    rejected request never consumes budget against a stricter co-rule.
    """
    t = time.monotonic() if now is None else now
    for key, limit, window in rules:
        dq = _HITS.setdefault(key, deque())
        cutoff = t - window
        while dq and dq[0] <= cutoff:
            dq.popleft()
        if len(dq) >= limit:
            return False
    for key, _limit, _window in rules:
        _HITS[key].append(t)
    return True


def reset_limits() -> None:
    """Clear all counters (test isolation)."""
    _HITS.clear()


def client_ip(request) -> str:
    """Real client IP behind the Docker-Caddy proxy.

    uvicorn only ever sees the Docker gateway as request.client.host, so prefer the
    X-Forwarded-For / X-Real-IP header Caddy injects (uvicorn is UFW-firewalled to accept
    only Caddy, so the header is trustworthy). Falls back to the peer address.
    """
    xff = request.headers.get("x-forwarded-for", "") or request.headers.get("x-real-ip", "")
    if xff:
        return xff.split(",")[0].strip()
    client = getattr(request, "client", None)
    return getattr(client, "host", None) or "unknown"


def magic_link_rules(email: str, ip: str) -> list[tuple[str, int, float]]:
    """Rate-limit rules for a magic-link request: per-email cooldown + hourly cap, per-IP hourly cap."""
    # Distinct key per window — rules sharing a key would share one deque, and the
    # shorter window's prune would erase the longer window's history.
    return [
        (f"ml:email:1m:{email}", 1, 60.0),    # at most 1 login email per address per minute
        (f"ml:email:1h:{email}", 5, 3600.0),  # ...and at most 5 per hour
        (f"ml:ip:1h:{ip}", 10, 3600.0),       # at most 10 login emails per IP per hour
    ]
