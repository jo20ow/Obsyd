"""A backfill measured in hours must not be killed by an hourly cron job.

The A09 backfill died after writing 432,774 points with `sqlite3.OperationalError: database is
locked`. It had run for 40 minutes. SQLite takes exactly one writer, the app writes on its own
schedule (the hourly outage snapshot, the daily ingests), and the 30 s busy_timeout is a hope,
not a guarantee.

`_with_retry` existed and caught network errors. A lock is exactly the kind of transient failure
a retry exists for — it just was not in the set.
"""
from __future__ import annotations

import asyncio

import pytest
from sqlalchemy.exc import OperationalError

from backend.scripts.gas_backfill import _with_retry as gas_retry
from backend.scripts.power_backfill import _with_retry as power_retry


@pytest.mark.parametrize("retry", [power_retry, gas_retry], ids=["power", "gas"])
def test_a_database_lock_is_retried_not_fatal(retry, monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    calls = {"n": 0}

    async def _flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise OperationalError("INSERT …", {}, Exception("database is locked"))
        return "written"

    assert asyncio.run(retry(_flaky, "test")) == "written"
    assert calls["n"] == 2, "the lock was survived, not swallowed"


@pytest.mark.parametrize("retry", [power_retry, gas_retry], ids=["power", "gas"])
def test_a_lock_that_never_clears_still_fails_loudly(retry, monkeypatch):
    """Retrying forever would hide a real problem — a held lock that never releases is a bug in
    something else, and the backfill must say so rather than spin."""
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)

    async def _always_locked():
        raise OperationalError("INSERT …", {}, Exception("database is locked"))

    with pytest.raises(OperationalError):
        asyncio.run(retry(_always_locked, "test"))


async def _no_sleep(_seconds):
    return None
