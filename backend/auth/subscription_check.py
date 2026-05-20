"""
Pure-function helpers around Subscription state.

Centralised so that require_pro(), /me, the webhook, and the daily-email
worker all use the same Pro-status definition. Keep this module DB-free
and side-effect-free; pass Subscription objects in.
"""

from datetime import datetime

from backend.models.subscription import Subscription


# Status values that grant Pro access immediately (no expiry check needed).
PRO_STATUSES_UNCONDITIONAL: frozenset[str] = frozenset(
    {
        "active",  # paid LS subscription
        "past_due",  # LS payment failed, grace period
        "cancelled",  # user cancelled but still within paid period
    }
)


def is_pro(sub: Subscription | None, *, now: datetime | None = None) -> bool:
    """Does this Subscription currently grant Pro access?

    Args:
        sub: Subscription row or None.
        now: Override for "current time" (testing). Defaults to utcnow.

    Returns:
        True iff the Subscription is in an active Pro state.
    """
    if sub is None:
        return False

    if sub.status in PRO_STATUSES_UNCONDITIONAL:
        return True

    if sub.status == "trialing":
        if sub.trial_ends_at is None:
            # Defensive: trialing without an end is not a valid state. Treat
            # as not-Pro rather than infinite trial.
            return False
        now = now or datetime.utcnow()
        return sub.trial_ends_at > now

    return False
