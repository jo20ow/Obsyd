"""Recording what is offline, because ENTSO-E will not.

A77 is a notice board, not an archive. These tests pin the two decisions that make the
recording trustworthy: it never speaks for an hour it did not see, and it inherits the
revision semantics that keep the outage feed from fabricating gigawatts.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.models.energy import PowerOutage
from backend.power.hourly_store import read_hourly
from backend.power.outage_history import (
    SERIES_FORCED,
    SERIES_OFFLINE,
    offline_mw_at,
    snapshot_outages,
)

NOW = datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%MZ")


def _outage(db, mrid, *, zone="DE_LU", revision=1, status="active", bt="A54",
            nominal=1_000.0, available=0.0, start=None, end=None):
    row = PowerOutage(
        mrid=mrid, revision=revision, doc_type="A77", zone=zone, status=status,
        business_type=bt, nominal_mw=nominal, available_mw=available,
        start_utc=_iso(start or NOW - timedelta(days=1)),
        end_utc=_iso(end or NOW + timedelta(days=1)),
    )
    db.add(row)
    return row


# ─── the instant ──────────────────────────────────────────────────────────────


def test_offline_is_nominal_minus_what_is_still_available():
    """A unit derated to 300 MW of its 1000 is 700 MW offline, not 1000."""
    class R:
        status, business_type, nominal_mw, available_mw = "active", "A54", 1_000.0, 300.0
        start_utc, end_utc = "2026-07-01T00:00Z", "2026-07-30T00:00Z"

    total, forced = offline_mw_at([R()], "2026-07-13T12:00Z")
    assert total == forced == 700.0


def test_planned_and_forced_are_counted_apart():
    """Planned maintenance is priced in months ahead; a forced trip is not. The desk
    leads with the forced number, so it cannot be buried in the total."""
    def row(bt):
        class R:
            status, business_type, nominal_mw, available_mw = "active", bt, 500.0, 0.0
            start_utc, end_utc = "2026-07-01T00:00Z", "2026-07-30T00:00Z"
        return R()

    total, forced = offline_mw_at([row("A54"), row("A53")], "2026-07-13T12:00Z")
    assert total == 1_000.0
    assert forced == 500.0, "the A53 maintenance must not inflate the forced headline"


def test_an_outage_outside_the_hour_is_not_offline_in_it():
    class R:
        status, business_type, nominal_mw, available_mw = "active", "A54", 900.0, 0.0
        start_utc, end_utc = "2026-07-20T00:00Z", "2026-07-25T00:00Z"   # next week

    assert offline_mw_at([R()], "2026-07-13T12:00Z") == (0.0, 0.0)


def test_a_withdrawn_event_is_not_offline():
    class R:
        status, business_type, nominal_mw, available_mw = "withdrawn", "A54", 900.0, 0.0
        start_utc, end_utc = "2026-07-01T00:00Z", "2026-07-30T00:00Z"

    assert offline_mw_at([R()], "2026-07-13T12:00Z") == (0.0, 0.0)


# ─── the snapshot ─────────────────────────────────────────────────────────────


def test_the_snapshot_records_this_hour(db_session):
    _outage(db_session, "M1", nominal=2_000.0)          # forced, running
    _outage(db_session, "M2", bt="A53", nominal=500.0)  # planned, running
    db_session.commit()

    out = snapshot_outages(db_session, now=NOW)
    assert out["hour"] == "2026-07-13T12:00Z"

    ts = int(NOW.timestamp())
    offline = dict(read_hourly(db_session, SERIES_OFFLINE, "DE_LU"))
    forced = dict(read_hourly(db_session, SERIES_FORCED, "DE_LU"))
    assert offline[ts] == 2_500.0
    assert forced[ts] == 2_000.0


def test_only_the_latest_revision_of_an_event_counts(db_session):
    """The outage feed's central trap: an event is republished as a new revision, and a
    withdrawn latest revision must HIDE it. Rank first, filter second — the other order
    lets an older active revision win and fabricates gigawatts."""
    _outage(db_session, "M1", revision=1, status="active", nominal=3_000.0)
    _outage(db_session, "M1", revision=2, status="withdrawn", nominal=3_000.0)
    db_session.commit()

    snapshot_outages(db_session, now=NOW)
    forced = dict(read_hourly(db_session, SERIES_FORCED, "DE_LU"))
    assert forced[int(NOW.timestamp())] == 0.0, "the withdrawal must erase the event"


def test_a_quiet_zone_records_a_zero_not_a_gap(db_session):
    """"Nothing was offline" is a fact. A series that only exists in the hours something
    broke has no floor to measure the next break against."""
    _outage(db_session, "M1", start=NOW + timedelta(days=5), end=NOW + timedelta(days=6))
    db_session.commit()

    snapshot_outages(db_session, now=NOW)
    assert dict(read_hourly(db_session, SERIES_OFFLINE, "DE_LU"))[int(NOW.timestamp())] == 0.0


def test_rerunning_the_same_hour_does_not_double_count(db_session):
    _outage(db_session, "M1", nominal=1_000.0)
    db_session.commit()

    snapshot_outages(db_session, now=NOW)
    snapshot_outages(db_session, now=NOW)

    points = read_hourly(db_session, SERIES_FORCED, "DE_LU")
    assert len(points) == 1 and points[0][1] == 1_000.0


def test_the_recorder_never_speaks_for_an_hour_it_did_not_see(db_session):
    """The tempting shortcut is to backfill the last 24h on every run. It is wrong in one
    direction: an outage that ended six hours ago has already left the notice board, so
    those hours get recomputed WITHOUT it and the history quietly undercounts exactly the
    events worth recording. Each run writes ONE hour: the one it can speak for."""
    _outage(db_session, "M1", nominal=1_000.0)
    db_session.commit()

    snapshot_outages(db_session, now=NOW)

    written = [ts for ts, _ in read_hourly(db_session, SERIES_OFFLINE, "DE_LU")]
    assert written == [int(NOW.timestamp())], "one hour, not a backfilled window"


def test_the_snapshot_is_monitored(db_session):
    """It is the one series that cannot be backfilled — a day of silence is a day of
    history destroyed, so it must be the tightest freshness window on the desk."""
    from backend.collectors.freshness import SPECS

    spec = next(s for s in SPECS if s.key == "outage_snapshot")
    assert spec.hourly_series == SERIES_OFFLINE
    assert spec.max_age <= timedelta(days=1)
    assert spec.max_age <= min(
        s.max_age for s in SPECS if s.hourly_series and s.key != "outage_snapshot"
    )


def test_the_job_is_scheduled_hourly():
    import inspect

    from backend.collectors import scheduler

    src = inspect.getsource(scheduler)
    assert 'id="outage_snapshot_hourly"' in src
    assert "_run_outage_snapshot, CronTrigger(minute=45)" in src, "hourly, not every 6h"


@pytest.mark.parametrize("bt,expected", [("A54", 800.0), ("A53", 0.0)])
def test_the_forced_series_is_the_a54_subset(db_session, bt, expected):
    _outage(db_session, "M1", bt=bt, nominal=800.0)
    db_session.commit()
    snapshot_outages(db_session, now=NOW)
    assert dict(read_hourly(db_session, SERIES_FORCED, "DE_LU"))[int(NOW.timestamp())] == expected
