"""
Historical Anomaly Lookup — finds past chokepoint disruptions
and correlates with oil price movements.

Uses: portwatch.db/chokepoint_daily (since 2019) + obsyd.db/fred_series (DCOILBRENTEU)
"""

import logging
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from backend.database import SessionLocal
from backend.models.prices import FREDSeries

logger = logging.getLogger(__name__)

PORTWATCH_DB = Path(__file__).parent.parent.parent / "data" / "portwatch.db"

# Map short names to portwatch portnames
CHOKEPOINT_NAMES = {
    "hormuz": "Strait of Hormuz",
    "suez": "Suez Canal",
    "malacca": "Malacca Strait",
    "panama": "Panama Canal",
    "cape": "Cape of Good Hope",
    "bab_el_mandeb": "Bab el-Mandeb Strait",
    "gibraltar": "Gibraltar Strait",
    "bosporus": "Bosporus Strait",
    "dover": "Dover Strait",
}


def _get_chokepoint_series(portname: str) -> list[dict]:
    """Load full daily series for a chokepoint from portwatch.db."""
    if not PORTWATCH_DB.exists():
        return []
    conn = sqlite3.connect(str(PORTWATCH_DB))
    rows = conn.execute(
        "SELECT date, n_total, n_tanker FROM chokepoint_daily "
        "WHERE portname = ? ORDER BY date",
        (portname,),
    ).fetchall()
    conn.close()
    return [{"date": r[0], "n_total": r[1], "n_tanker": r[2]} for r in rows]


def _get_brent_prices() -> dict[str, float]:
    """Load all Brent prices from fred_series as {date: price}."""
    db = SessionLocal()
    try:
        rows = db.query(FREDSeries).filter(
            FREDSeries.series_id == "DCOILBRENTEU"
        ).all()
        return {r.date: r.value for r in rows}
    finally:
        db.close()


def _get_disruptions_context() -> dict[str, list[str]]:
    """Load disruptions from portwatch.db, indexed by approximate date range."""
    if not PORTWATCH_DB.exists():
        return {}
    conn = sqlite3.connect(str(PORTWATCH_DB))
    rows = conn.execute(
        "SELECT event_name, event_type, start_date, end_date FROM disruptions"
    ).fetchall()
    conn.close()

    context = {}
    for name, etype, start, end in rows:
        if start:
            context.setdefault(start[:10], []).append(f"{name} ({etype})")
    return context


def find_anomalies(
    chokepoint: str,
    threshold_pct: float = 40.0,
    window: int = 30,
) -> dict:
    """
    Find historical periods where chokepoint traffic dropped significantly.

    Returns current status + list of historical anomalies with price context.
    """
    portname = CHOKEPOINT_NAMES.get(chokepoint.lower())
    if not portname:
        # Try direct match
        portname = chokepoint

    series = _get_chokepoint_series(portname)
    if len(series) < window + 1:
        return {"chokepoint": chokepoint, "data_points": len(series), "anomalies": []}

    brent = _get_brent_prices()
    disruptions = _get_disruptions_context()

    # Compute rolling average and find drops
    anomalies = []
    current_event = None

    for i in range(window, len(series)):
        avg = sum(s["n_total"] for s in series[i - window:i]) / window
        if avg == 0:
            continue

        today_val = series[i]["n_total"]
        drop_pct = ((today_val - avg) / avg) * 100

        if drop_pct < -threshold_pct:
            if current_event is None:
                current_event = {
                    "start_date": series[i]["date"],
                    "start_value": today_val,
                    "avg_30d": round(avg, 1),
                    "min_value": today_val,
                    "max_drop_pct": round(drop_pct, 1),
                }
            else:
                if today_val < current_event["min_value"]:
                    current_event["min_value"] = today_val
                    current_event["max_drop_pct"] = round(drop_pct, 1)
        else:
            if current_event is not None:
                current_event["end_date"] = series[i - 1]["date"]
                _enrich_event(current_event, brent, disruptions)
                anomalies.append(current_event)
                current_event = None

    # Handle ongoing event
    if current_event is not None:
        current_event["end_date"] = series[-1]["date"]
        current_event["ongoing"] = True
        _enrich_event(current_event, brent, disruptions)
        anomalies.append(current_event)

    # Current status
    latest_avg = sum(s["n_total"] for s in series[-window:]) / window
    latest_val = series[-1]["n_total"]
    current_drop = ((latest_val - latest_avg) / latest_avg) * 100 if latest_avg else 0

    return {
        "chokepoint": portname,
        "data_points": len(series),
        "date_range": f"{series[0]['date']} to {series[-1]['date']}",
        "current": {
            "date": series[-1]["date"],
            "n_total": latest_val,
            "avg_30d": round(latest_avg, 1),
            "drop_pct": round(current_drop, 1),
        },
        "anomaly_count": len(anomalies),
        "anomalies": anomalies[-20:],  # last 20
    }


def _enrich_event(event: dict, brent: dict, disruptions: dict):
    """Add duration, Brent price context, and disruption info to an event."""
    start = event["start_date"]
    end = event["end_date"]

    # Duration
    try:
        d1 = datetime.strptime(start, "%Y-%m-%d")
        d2 = datetime.strptime(end, "%Y-%m-%d")
        event["duration_days"] = (d2 - d1).days + 1
    except ValueError:
        event["duration_days"] = 0

    # Brent price context
    brent_before = _find_nearest_price(brent, start, direction=-1)
    brent_after_7d = _find_nearest_price(brent, end, direction=1, offset_days=7)
    brent_after_30d = _find_nearest_price(brent, end, direction=1, offset_days=30)

    if brent_before is not None:
        event["brent_at_start"] = brent_before
    if brent_before and brent_after_7d:
        event["brent_after_7d"] = brent_after_7d
        event["brent_change_7d_pct"] = round(
            (brent_after_7d - brent_before) / brent_before * 100, 1
        )
    if brent_before and brent_after_30d:
        event["brent_after_30d"] = brent_after_30d
        event["brent_change_30d_pct"] = round(
            (brent_after_30d - brent_before) / brent_before * 100, 1
        )

    # Disruption context (check ±7 days around start)
    try:
        d_start = datetime.strptime(start, "%Y-%m-%d")
        for offset in range(-7, 8):
            check = (d_start + timedelta(days=offset)).strftime("%Y-%m-%d")
            if check in disruptions:
                event["disruption_context"] = disruptions[check]
                break
    except ValueError:
        pass


def _find_nearest_price(
    prices: dict[str, float], date_str: str, direction: int = 1, offset_days: int = 0
) -> float | None:
    """Find nearest available Brent price near a target date."""
    try:
        target = datetime.strptime(date_str, "%Y-%m-%d") + timedelta(days=offset_days)
    except ValueError:
        return None

    for i in range(10):  # search ±10 days
        check = (target + timedelta(days=i * direction)).strftime("%Y-%m-%d")
        if check in prices:
            return prices[check]
    return None
