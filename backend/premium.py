"""Premium preview gate — the first brick of the paid tier.

Owner decision 2026-09-13: the analytics products (convergence bands, TB
spreads, the capture €0-floor variant) are PREMIUM, tested hidden first —
not offered on the free tier. The free tier keeps everything that was free
before the analytics phase; nothing public before 2026-09-13 is walled back
(the July doctrine: the free tier stays generous, paid is the production/
analytics layer).

Mechanism: the existing subscription machinery. `is_pro()` over the newest
Subscription row — today only the owner's comp subscription
(backend/scripts/grant_pro.py) passes, which makes "hidden testing" and
"premium gating" the same code path: when Lemon Squeezy billing reactivates,
paying subscribers pass the same check, and the Phase-2 API keys will plug
in beside the session cookie.

The gate is enforced where the data leaves the system:
  * /api/v1/series, /snapshot — 403 on a premium series without pro
  * /api/v1/series/catalog     — premium series absent from the listing
  * /api/power/convergence     — requires pro outright
  * /api/power/capture         — the floor0 variant fields are omitted
Panels hide themselves on 401/403 instead of rendering a teaser — hidden
means hidden, not advertised-but-locked. (The CODE is public — AGPL — and so
are the finding docs; what is gated is the served data, not the method.)
"""
from __future__ import annotations

#: Exact premium series keys …
PREMIUM_SERIES: frozenset[str] = frozenset({"spread.tb1", "spread.tb2", "spread.tb4"})
#: … prefixes (conv.* = the whole per-border convergence family) …
PREMIUM_PREFIXES: tuple[str, ...] = ("conv.",)
#: … and suffixes (capture.<PSR>.price_floor0).
PREMIUM_SUFFIXES: tuple[str, ...] = (".price_floor0",)

PREMIUM_DETAIL = (
    "This series is part of the premium preview and not on the free tier. "
    "The methodology is public (AGPL); the served data is gated."
)


def is_premium_series(key: str) -> bool:
    return (
        key in PREMIUM_SERIES
        or key.startswith(PREMIUM_PREFIXES)
        or key.endswith(PREMIUM_SUFFIXES)
    )
