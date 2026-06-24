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

from dataclasses import dataclass


@dataclass
class DetectorResult:
    """One anomaly emitted by a detector. Maps 1:1 onto an Alert row."""

    rule: str          # stable, globally-unique key per detector (dedup key with zone)
    zone: str          # geographic / market scope, "" if global
    vertical: str      # "oil" | "gas" | "power" | "metals" | "sentiment"
    severity: str      # "info" | "warning" | "critical"
    title: str
    detail: str = ""


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
