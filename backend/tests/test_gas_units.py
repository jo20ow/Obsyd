"""Unit tests for the gas conversion layer. Pure known-value assertions."""

from __future__ import annotations

import math

import pytest

from backend.gas import units


def test_kwh_per_day_to_gwh_per_day():
    assert units.kwh_per_day_to_gwh_per_day(1_000_000) == 1.0
    assert units.kwh_per_day_to_gwh_per_day(2_500_000_000) == 2500.0
    assert units.kwh_per_day_to_gwh_per_day(0) == 0.0
    assert units.kwh_per_day_to_gwh_per_day(-3_000_000) == -3.0  # net flows can be negative


def test_twh_gwh_roundtrip():
    assert units.twh_to_gwh(1.0) == 1000.0
    assert units.twh_to_gwh(0.0) == 0.0
    assert units.gwh_to_twh(1000.0) == 1.0
    for x in (0.5, 12.34, 487.6243):
        assert math.isclose(units.gwh_to_twh(units.twh_to_gwh(x)), x, rel_tol=1e-12)


def test_passthrough_validates_but_preserves():
    assert units.gwh_per_day_passthrough(3588.96) == 3588.96
    assert units.gwh_per_day_passthrough(-190.8) == -190.8


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_conversions_reject_non_finite(bad):
    for fn in (units.kwh_per_day_to_gwh_per_day, units.twh_to_gwh, units.gwh_to_twh, units.gwh_per_day_passthrough):
        with pytest.raises(ValueError):
            fn(bad)


def test_conversions_reject_non_numeric():
    with pytest.raises(ValueError):
        units.twh_to_gwh("12")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        units.kwh_per_day_to_gwh_per_day(True)  # bool is not a measurement


def test_coerce_float_numbers_and_strings():
    assert units.coerce_float("3588.96") == 3588.96
    assert units.coerce_float("  -190.8 ") == -190.8
    assert units.coerce_float(5) == 5.0
    assert units.coerce_float(5.5) == 5.5
    assert units.coerce_float("0") == 0.0


def test_coerce_float_gie_null_markers_are_none():
    for marker in ("", "-", "  ", "n/a", "NA", "null", "None"):
        assert units.coerce_float(marker) is None
    assert units.coerce_float(None) is None


def test_coerce_float_rejects_garbage_and_bool():
    with pytest.raises(ValueError):
        units.coerce_float("abc")
    with pytest.raises(ValueError):
        units.coerce_float(True)
    with pytest.raises(ValueError):
        units.coerce_float(float("nan"))
