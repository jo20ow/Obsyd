"""Shared contract + helpers for the cross-vertical anomaly radar.

A *detector* is a pure, side-effect-free function ``(db) -> list[DetectorResult]``
that reads the latest PERSISTED flag rows for one data vertical, decides what is
"abnormal vs history", and returns descriptive results. The registry runner
(``detectors/__init__.py``) is the only thing that writes to the DB, via the
existing ``_upsert_alert`` backbone.

Two hard rules for every detector:
  1. **DB reads only** — no recompute, no network. The runner is on a 5-minute
     cron, so detectors must be cheap and deterministic.
  2. **Descriptive, never predictive** — describe the physical state / deviation
     ("residual +3.1σ vs 90d, dominant mover supply↑"), never a price call.

The shared contract is the three severity levels + the descriptive tone, NOT a
single numeric transform: each vertical keeps its own validated thresholds.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import date as _date
from datetime import datetime, timezone


@dataclass
class DetectorResult:
    """One anomaly emitted by a detector. Maps 1:1 onto an Alert row."""

    rule: str          # stable, globally-unique key per detector (dedup key with zone)
    zone: str          # geographic / market scope, "" if global
    vertical: str      # "oil" | "gas" | "power" | "metals" | "sentiment"
    severity: str      # "info" | "warning" | "critical"
    title: str
    detail: str = ""
    as_of: str | None = None  # ISO date of the underlying data; runner uses it for staleness
    #: Per-result staleness override (days). The runner's per-vertical windows
    #: assume daily cadence — a weekly source (hydro A72, ~2 weeks publication
    #: lag) would be suppressed at "power": 3 without this.
    max_age_days: int | None = None


def is_stale(latest: str | _date | None, max_age_days: int, *, today: _date | None = None) -> bool:
    """True if `latest` is missing, unparseable, or older than `max_age_days` from today.

    Detectors read the newest persisted row and treat it as "now". If a collector
    stalls, that row goes days stale while the alert still looks fresh in the feed
    (each 5-min re-fire bumps ``created_at``, so retention never expires it). Gate
    every latest-row read on this so a frozen source goes quiet instead of asserting
    stale data as current. Missing/unparseable dates are treated as stale (fail safe:
    if we cannot prove the data is fresh, we do not emit).
    """
    if latest is None:
        return True
    if isinstance(latest, str):
        try:
            latest = _date.fromisoformat(latest[:10])
        except ValueError:
            return True
    if today is None:
        today = datetime.now(timezone.utc).date()
    return (today - latest).days > max_age_days


# Valid verticals — kept here so the API/frontend and tests share one source.
VERTICALS = ("oil", "gas", "power", "metals", "sentiment")


def severity_from_zscore(z: float) -> str:
    """|z|>=3 → critical, >=2 → warning, else info. Mirrors gas WATCH/SIGNAL bands."""
    az = abs(z)
    if az >= 3.0:
        return "critical"
    if az >= 2.0:
        return "warning"
    return "info"


def severity_from_count(n: int, warn_at: int, crit_at: int) -> str:
    """Count-based escalation: >=crit_at → critical, >=warn_at → warning, else info."""
    if n >= crit_at:
        return "critical"
    if n >= warn_at:
        return "warning"
    return "info"


def severity_from_enum(value: str | None, mapping: dict[str, str], default: str = "info") -> str:
    """Map a categorical state to a severity. Unknown/None → default."""
    if value is None:
        return default
    return mapping.get(value, default)


# Minimum history points before a trailing baseline is trustworthy.
MIN_BASELINE_N = 14


def trailing_zscore(current: float, history: list[float], *, min_n: int = MIN_BASELINE_N):
    """z-score of `current` against a trailing `history` — "abnormal vs THIS series' own past".

    This is the core of the descriptive radar: an anomaly is a statistical deviation from a
    series' own recent norm, not a flat threshold (which misfires on structurally-high series
    like a permanent anchorage). Returns (z, mean, std, n), or None if history is too short or
    has zero variance (→ no trustworthy baseline → no alert).
    """
    n = len(history)
    if n < min_n:
        return None
    mean = statistics.fmean(history)
    std = statistics.pstdev(history)
    if std == 0:
        return None
    return (current - mean) / std, mean, std, n
