"""In-process rate limiter for the magic-link endpoint (email-bomb / Resend-quota guard)."""
from __future__ import annotations

from backend.auth.ratelimit import allow, client_ip, magic_link_rules, reset_limits


def test_per_email_minute_cooldown():
    reset_limits()
    assert allow(magic_link_rules("a@b.c", "1.1.1.1"), now=100.0) is True
    assert allow(magic_link_rules("a@b.c", "1.1.1.1"), now=130.0) is False  # 2nd within 60s
    assert allow(magic_link_rules("a@b.c", "1.1.1.1"), now=200.0) is True   # >60s later


def test_per_email_hourly_cap():
    reset_limits()
    for i in range(5):  # 5 sends spaced >60s → all allowed
        assert allow(magic_link_rules("x@y.z", "1.1.1.1"), now=100.0 + i * 61) is True
    assert allow(magic_link_rules("x@y.z", "1.1.1.1"), now=100.0 + 5 * 61) is False  # 6th within hour


def test_per_ip_cap_across_emails():
    reset_limits()
    for i in range(10):  # 10 distinct emails from one IP, spaced to clear the per-email minute rule
        assert allow(magic_link_rules(f"u{i}@y.z", "9.9.9.9"), now=100.0 + i * 61) is True
    assert allow(magic_link_rules("u10@y.z", "9.9.9.9"), now=100.0 + 10 * 61) is False  # IP cap hit


def test_blocked_call_does_not_consume_budget():
    reset_limits()
    assert allow(magic_link_rules("a@b.c", "1.1.1.1"), now=100.0) is True
    assert allow(magic_link_rules("a@b.c", "1.1.1.1"), now=110.0) is False  # blocked, not recorded
    # After the minute passes, exactly one fresh send is allowed (the blocked one didn't count).
    assert allow(magic_link_rules("a@b.c", "1.1.1.1"), now=170.0) is True


def test_client_ip_prefers_forwarded_header():
    class Req:
        headers = {"x-forwarded-for": "203.0.113.9, 10.0.0.1"}
        client = None
    assert client_ip(Req()) == "203.0.113.9"


def test_client_ip_falls_back_to_peer():
    class Peer:
        host = "198.51.100.7"

    class Req:
        headers = {}
        client = Peer()
    assert client_ip(Req()) == "198.51.100.7"
