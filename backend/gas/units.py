"""Strict unit-conversion layer. Canonical internal unit = GWh/day.

Boundary sources and their native units:
  - ENTSOG physical flows: kWh/d   -> GWh/d  (the row carries its own `unit`)
  - AGSI gasInStorage:      TWh     -> GWh    (stock)
  - AGSI injection/withdrawal, ALSI sendOut: already GWh/d

Pure functions, no I/O. Conversions raise ValueError on non-finite/non-numeric
input rather than silently returning 0 (the model must never invent a number).
`coerce_float` is the one lenient parser, because GIE returns numbers as
strings and uses "-" / "" for "no value" (a real missing, not an error).
"""

from __future__ import annotations

import math

KWH_PER_GWH = 1_000_000  # 1 GWh = 1e6 kWh
GWH_PER_TWH = 1_000      # 1 TWh = 1e3 GWh

# GIE markers that mean "no value for this day" (missing, not invalid).
_GIE_NULLS = {"", "-", "n/a", "na", "null", "none"}


def _require_finite(x: float, what: str) -> float:
    if not isinstance(x, (int, float)) or isinstance(x, bool):
        raise ValueError(f"{what}: expected a number, got {type(x).__name__}")
    xf = float(x)
    if not math.isfinite(xf):
        raise ValueError(f"{what}: non-finite value {x!r}")
    return xf


def kwh_per_day_to_gwh_per_day(kwh_d: float) -> float:
    """ENTSOG physical flow kWh/d -> GWh/d."""
    return _require_finite(kwh_d, "kwh_per_day") / KWH_PER_GWH


def twh_to_gwh(twh: float) -> float:
    """AGSI gasInStorage TWh -> GWh."""
    return _require_finite(twh, "twh") * GWH_PER_TWH


def gwh_to_twh(gwh: float) -> float:
    """GWh -> TWh (presentation of stored stock)."""
    return _require_finite(gwh, "gwh") / GWH_PER_TWH


def gwh_per_day_passthrough(gwh_d: float) -> float:
    """Already GWh/d (AGSI injection/withdrawal, ALSI sendOut). Identity, but
    validated so a NaN/inf can never slip through as if it were real."""
    return _require_finite(gwh_d, "gwh_per_day")


def coerce_float(raw: object) -> float | None:
    """Parse a GIE/ENTSOG scalar to float, or None for a recognized empty
    marker. Raises ValueError only for genuinely un-parseable input.

    Accepts numbers and numeric strings (incl. thousands separators absent —
    GIE uses plain decimals like "3588.96"). Returns None for "", "-", etc.
    """
    if raw is None:
        return None
    if isinstance(raw, bool):
        raise ValueError(f"coerce_float: bool is not a measurement: {raw!r}")
    if isinstance(raw, (int, float)):
        f = float(raw)
        if not math.isfinite(f):
            raise ValueError(f"coerce_float: non-finite {raw!r}")
        return f
    if isinstance(raw, str):
        s = raw.strip()
        if s.lower() in _GIE_NULLS:
            return None
        try:
            f = float(s)
        except ValueError as exc:
            raise ValueError(f"coerce_float: not a number: {raw!r}") from exc
        if not math.isfinite(f):
            raise ValueError(f"coerce_float: non-finite {raw!r}")
        return f
    raise ValueError(f"coerce_float: unsupported type {type(raw).__name__}")
