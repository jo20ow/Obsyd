"""Unit tests for backend.api_guard's keyed TTL cache (cached_value)."""
from __future__ import annotations

import pytest

from backend.api_guard import _reset_coverage_cache, cached_value


@pytest.fixture(autouse=True)
def _isolate():
    _reset_coverage_cache()  # process-global; would leak values between tests
    yield
    _reset_coverage_cache()


def test_cached_value_keys_are_independent():
    """Two different cache keys must not share a slot or a recompute."""
    calls = {"a": 0, "b": 0}

    def compute_a():
        calls["a"] += 1
        return "A"

    def compute_b():
        calls["b"] += 1
        return "B"

    assert cached_value("a", compute_a) == "A"
    assert cached_value("b", compute_b) == "B"
    assert cached_value("a", compute_a) == "A"
    assert cached_value("b", compute_b) == "B"
    assert calls == {"a": 1, "b": 1}, "each key's compute must run exactly once"


def test_cached_value_expiry_honored_via_now_param():
    """Within the TTL the cached value is returned without recomputing; once
    `now` has advanced past `expires`, it recomputes."""
    calls = {"n": 0}

    def compute():
        calls["n"] += 1
        return calls["n"]

    assert cached_value("x", compute, ttl=10.0, now=0.0) == 1
    assert cached_value("x", compute, ttl=10.0, now=5.0) == 1  # still within TTL
    assert cached_value("x", compute, ttl=10.0, now=9.999) == 1  # just under TTL
    assert cached_value("x", compute, ttl=10.0, now=10.0) == 2  # TTL elapsed
    assert calls["n"] == 2
