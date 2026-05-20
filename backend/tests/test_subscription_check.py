"""
Tests for the central Pro-status helper. Pure-function — no DB needed.
"""

from datetime import datetime, timedelta

from backend.auth.subscription_check import is_pro
from backend.models.subscription import Subscription


NOW = datetime(2026, 5, 20, 12, 0, 0)


def _sub(**kwargs) -> Subscription:
    defaults = {"email": "u@example.com", "status": "active", "plan": "pro"}
    defaults.update(kwargs)
    return Subscription(**defaults)


def test_none_is_not_pro():
    assert is_pro(None) is False


def test_active_is_pro():
    assert is_pro(_sub(status="active"), now=NOW) is True


def test_past_due_is_pro_grace_period():
    # While LS retries the charge we keep the user on Pro to avoid noisy
    # downgrades on transient declines.
    assert is_pro(_sub(status="past_due"), now=NOW) is True


def test_cancelled_is_pro_until_period_end():
    # LS keeps `cancelled` for the remainder of the paid period; only the
    # subsequent `subscription_expired` event flips the row to expired.
    assert is_pro(_sub(status="cancelled"), now=NOW) is True


def test_expired_is_not_pro():
    assert is_pro(_sub(status="expired"), now=NOW) is False


def test_trialing_with_future_end_is_pro():
    sub = _sub(status="trialing", trial_ends_at=NOW + timedelta(days=5))
    assert is_pro(sub, now=NOW) is True


def test_trialing_with_past_end_is_not_pro():
    sub = _sub(status="trialing", trial_ends_at=NOW - timedelta(seconds=1))
    assert is_pro(sub, now=NOW) is False


def test_trialing_without_end_is_not_pro_defensive():
    # Defensive: a trialing row without an end date is invalid; treat as
    # not-Pro rather than granting infinite access.
    sub = _sub(status="trialing", trial_ends_at=None)
    assert is_pro(sub, now=NOW) is False


def test_unknown_status_is_not_pro():
    assert is_pro(_sub(status="weird"), now=NOW) is False
