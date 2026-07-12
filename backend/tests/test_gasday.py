"""The gas day (06:00–06:00 local) — the calendar every gas source keeps, and
the one the power-burn leg of the balance did NOT keep until 2026-07-12."""
from __future__ import annotations

from datetime import datetime, timezone

from backend.gas.gasday import gas_day


def _utc(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def test_winter_boundary_is_0500_utc():
    """CET = UTC+1 → the gas day flips at 05:00 UTC in winter."""
    assert gas_day(_utc("2026-01-15T04:59")) == "2026-01-14"
    assert gas_day(_utc("2026-01-15T05:00")) == "2026-01-15"
    assert gas_day(_utc("2026-01-15T23:00")) == "2026-01-15"


def test_summer_boundary_is_0400_utc():
    """CEST = UTC+2 → the same 06:00 LOCAL boundary lands at 04:00 UTC."""
    assert gas_day(_utc("2026-07-15T03:59")) == "2026-07-14"
    assert gas_day(_utc("2026-07-15T04:00")) == "2026-07-15"


def test_the_hours_that_used_to_be_misfiled():
    """These are exactly the hours the UTC-day bucketing put on the wrong day:
    the small hours of a UTC date belong to the PREVIOUS gas day."""
    for utc_hour, expected in [("00:30", "2026-07-14"), ("02:00", "2026-07-14"),
                               ("05:00", "2026-07-15"), ("12:00", "2026-07-15")]:
        assert gas_day(_utc(f"2026-07-15T{utc_hour}")) == expected, utc_hour


def test_naive_timestamps_are_read_as_utc():
    assert gas_day(datetime(2026, 7, 15, 3, 0)) == "2026-07-14"


def test_dst_switch_day_keeps_the_local_boundary():
    """Spring-forward 2026-03-29: the clocks jump at 01:00 UTC, so by the time
    it is 06:00 LOCAL it is only 04:00 UTC — the boundary follows the local
    hour, not a fixed offset, which is why gas day 2026-03-28 is 23h long.
    A naive UTC-offset implementation would misfile that hour."""
    assert gas_day(_utc("2026-03-29T03:00")) == "2026-03-28"  # 05:00 CEST — still gas day D-1
    assert gas_day(_utc("2026-03-29T04:00")) == "2026-03-29"  # 06:00 CEST — the flip
    # Autumn fall-back 2026-10-25: the mirror case, a 25h gas day.
    assert gas_day(_utc("2026-10-24T05:00")) == "2026-10-24"  # 07:00 CEST
    assert gas_day(_utc("2026-10-25T04:59")) == "2026-10-24"  # 05:59 CET — still D-1
    assert gas_day(_utc("2026-10-25T05:00")) == "2026-10-25"  # 06:00 CET — the flip
