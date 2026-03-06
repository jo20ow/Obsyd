"""
Tonnage Proxy — Cape/Suez Rerouting Index.

When Suez is disrupted, tankers divert around the Cape of Good Hope.
This signal detects rerouting by computing:

  rerouting_ratio = cape_tankers / (suez_tankers + cape_tankers)

Normal baseline: ~15-25% Cape share (some routes naturally use Cape)
Disruption signal: >35% Cape share = significant rerouting

Uses PortWatch historical data from portwatch.db.
"""

import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

STORE_PATH = Path(__file__).parent.parent.parent / "data" / "portwatch.db"

SUEZ_PORTID = "chokepoint1"
CAPE_PORTID = "chokepoint7"

# Thresholds for the rerouting index
REROUTING_ELEVATED = 0.30   # >30% Cape share = elevated
REROUTING_HIGH = 0.40       # >40% Cape share = high rerouting


def _get_conn() -> sqlite3.Connection:
    return sqlite3.connect(str(STORE_PATH))


def compute_rerouting_index(days: int = 365) -> dict:
    """Compute Cape/Suez rerouting index over a time period.

    Returns daily ratios + current state + historical context.
    """
    conn = _get_conn()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")

    try:
        # Fetch Suez and Cape tanker counts
        suez_rows = conn.execute(
            "SELECT date, n_tanker, n_total FROM chokepoint_daily "
            "WHERE portid = ? AND date >= ? ORDER BY date ASC",
            (SUEZ_PORTID, cutoff),
        ).fetchall()

        cape_rows = conn.execute(
            "SELECT date, n_tanker, n_total FROM chokepoint_daily "
            "WHERE portid = ? AND date >= ? ORDER BY date ASC",
            (CAPE_PORTID, cutoff),
        ).fetchall()
    finally:
        conn.close()

    # Index by date
    suez_by_date = {r[0]: {"tanker": r[1], "total": r[2]} for r in suez_rows}
    cape_by_date = {r[0]: {"tanker": r[1], "total": r[2]} for r in cape_rows}

    # Compute daily ratios for dates where both have data
    all_dates = sorted(set(suez_by_date.keys()) & set(cape_by_date.keys()))
    daily = []
    for d in all_dates:
        s = suez_by_date[d]
        c = cape_by_date[d]
        combined = s["tanker"] + c["tanker"]
        ratio = c["tanker"] / combined if combined > 0 else 0
        daily.append({
            "date": d,
            "suez_tanker": s["tanker"],
            "cape_tanker": c["tanker"],
            "ratio": round(ratio, 4),
        })

    if not daily:
        return {"available": False, "reason": "no overlapping data"}

    # Current state (last 7 days average)
    recent = daily[-7:] if len(daily) >= 7 else daily
    current_ratio = sum(d["ratio"] for d in recent) / len(recent)

    # 30-day rolling average for baseline
    baseline_window = daily[-30:] if len(daily) >= 30 else daily
    baseline_ratio = sum(d["ratio"] for d in baseline_window) / len(baseline_window)

    # Historical 365-day average (for context)
    full_avg = sum(d["ratio"] for d in daily) / len(daily)

    # Detect rerouting state
    if current_ratio >= REROUTING_HIGH:
        state = "high_rerouting"
        severity = "warning"
    elif current_ratio >= REROUTING_ELEVATED:
        state = "elevated"
        severity = "info"
    else:
        state = "normal"
        severity = None

    # Find historical rerouting events (sustained >35% for 5+ days)
    events = _detect_rerouting_events(daily)

    # Anomaly vs baseline
    anomaly_pct = ((current_ratio - full_avg) / full_avg * 100) if full_avg > 0 else 0

    return {
        "available": True,
        "current": {
            "ratio": round(current_ratio, 4),
            "ratio_pct": round(current_ratio * 100, 1),
            "baseline_30d": round(baseline_ratio, 4),
            "baseline_365d": round(full_avg, 4),
            "anomaly_pct": round(anomaly_pct, 1),
            "state": state,
            "severity": severity,
            "last_date": daily[-1]["date"],
            "suez_tanker_7d_avg": round(sum(d["suez_tanker"] for d in recent) / len(recent), 1),
            "cape_tanker_7d_avg": round(sum(d["cape_tanker"] for d in recent) / len(recent), 1),
        },
        "history": daily[-min(days, 365):],  # Cap at 365 points for chart
        "events": events,
        "data_points": len(daily),
    }


def _detect_rerouting_events(daily: list[dict], threshold: float = 0.35, min_days: int = 5) -> list[dict]:
    """Find periods where Cape share exceeded threshold for sustained periods."""
    events = []
    in_event = False
    start_idx = 0

    for i, d in enumerate(daily):
        if d["ratio"] >= threshold:
            if not in_event:
                in_event = True
                start_idx = i
        else:
            if in_event:
                duration = i - start_idx
                if duration >= min_days:
                    event_days = daily[start_idx:i]
                    peak = max(event_days, key=lambda x: x["ratio"])
                    events.append({
                        "start_date": daily[start_idx]["date"],
                        "end_date": daily[i - 1]["date"],
                        "duration_days": duration,
                        "peak_ratio": round(peak["ratio"], 4),
                        "peak_date": peak["date"],
                        "avg_ratio": round(sum(x["ratio"] for x in event_days) / len(event_days), 4),
                    })
                in_event = False

    # Handle event still ongoing at end of data
    if in_event:
        duration = len(daily) - start_idx
        if duration >= min_days:
            event_days = daily[start_idx:]
            peak = max(event_days, key=lambda x: x["ratio"])
            events.append({
                "start_date": daily[start_idx]["date"],
                "end_date": daily[-1]["date"],
                "duration_days": duration,
                "peak_ratio": round(peak["ratio"], 4),
                "peak_date": peak["date"],
                "avg_ratio": round(sum(x["ratio"] for x in event_days) / len(event_days), 4),
                "ongoing": True,
            })

    return events
