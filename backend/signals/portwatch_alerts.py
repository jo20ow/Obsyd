"""
PortWatch chokepoint anomaly alerts.

Compares latest daily values against the same 30-day window from the previous
year (YoY seasonality correction). Falls back to a simple 30-day average when
no prior-year data exists. Chokepoints that are seasonally low (<5 vessels/day
in the prior-year window) are suppressed.

Triggers alerts when anomaly exceeds +/-30%. Cross-references active disruptions
to escalate to critical.
"""

from datetime import datetime, timedelta

from backend.collectors.portwatch_store import (
    _init_db,
    query_active_disruptions,
)

ANOMALY_THRESHOLD = 30.0  # percent
SEASONAL_LOW_THRESHOLD = 5  # vessels/day — suppress alerts below this


def _get_yoy_baseline(conn, portid: str, latest_date: str) -> dict | None:
    """Get avg for the same 30-day window one year ago."""
    dt = datetime.strptime(latest_date, "%Y-%m-%d")
    yoy_end = (dt - timedelta(days=365)).strftime("%Y-%m-%d")
    yoy_start = (dt - timedelta(days=365 + 30)).strftime("%Y-%m-%d")

    row = conn.execute(
        """
        SELECT AVG(n_total), AVG(n_tanker), COUNT(*)
        FROM chokepoint_daily
        WHERE portid = ? AND date >= ? AND date <= ?
    """,
        (portid, yoy_start, yoy_end),
    ).fetchone()

    if not row or not row[2] or row[2] < 10:
        return None

    return {"avg_total": row[0], "avg_tanker": row[1], "n_days": row[2]}


def _get_30d_baseline(conn, portid: str, latest_date: str) -> dict | None:
    """Fallback: simple 30-day average."""
    cutoff = (datetime.strptime(latest_date, "%Y-%m-%d") - timedelta(days=30)).strftime("%Y-%m-%d")

    row = conn.execute(
        """
        SELECT AVG(n_total), AVG(n_tanker), COUNT(*)
        FROM chokepoint_daily
        WHERE portid = ? AND date >= ? AND date < ?
    """,
        (portid, cutoff, latest_date),
    ).fetchone()

    if not row or not row[2]:
        return None

    return {"avg_total": row[0], "avg_tanker": row[1], "n_days": row[2]}


def check_chokepoint_anomalies(db_path=None) -> list[dict]:
    """Check all chokepoints for anomalies vs year-over-year baseline.

    Returns a list of alert dicts for chokepoints where
    |anomaly| > ANOMALY_THRESHOLD.
    """
    conn = _init_db(db_path)

    latest_date = conn.execute("SELECT MAX(date) FROM chokepoint_daily").fetchone()[0]
    if not latest_date:
        conn.close()
        return []

    latest_rows = conn.execute(
        """
        SELECT portid, portname, n_total, n_tanker, capacity
        FROM chokepoint_daily
        WHERE date = ?
    """,
        (latest_date,),
    ).fetchall()

    disruptions = query_active_disruptions(db_path)

    alerts = []
    for row in latest_rows:
        portid, portname, n_total, n_tanker, capacity = row

        # Try YoY baseline first, fall back to 30-day
        yoy = _get_yoy_baseline(conn, portid, latest_date)
        baseline = yoy
        baseline_type = "yoy"

        if not baseline:
            baseline = _get_30d_baseline(conn, portid, latest_date)
            baseline_type = "30d"

        if not baseline or baseline["avg_total"] == 0:
            continue

        # Suppress seasonal lows: if prior-year window was <5 vessels/day
        if baseline_type == "yoy" and baseline["avg_total"] < SEASONAL_LOW_THRESHOLD:
            continue
        if baseline_type == "30d":
            # Check prior-year window to detect seasonal lows even without full baseline
            yoy_check = _get_yoy_baseline(conn, portid, latest_date)
            if yoy_check and yoy_check["avg_total"] < SEASONAL_LOW_THRESHOLD:
                continue

        anomaly_pct = (n_total - baseline["avg_total"]) / baseline["avg_total"] * 100
        anomaly_tanker_pct = (
            (n_tanker - baseline["avg_tanker"]) / baseline["avg_tanker"] * 100 if baseline["avg_tanker"] else 0
        )

        if abs(anomaly_pct) < ANOMALY_THRESHOLD:
            continue

        direction = "surge" if anomaly_pct > 0 else "drop"

        # Match active disruptions to this chokepoint
        matched_disruption = None
        name_lower = portname.lower()
        for d in disruptions:
            dn = d["event_name"].lower()
            for fragment in name_lower.replace("strait of ", "").replace(" strait", "").replace(" canal", "").split():
                if len(fragment) > 3 and fragment in dn:
                    matched_disruption = d["event_name"]
                    break
            if matched_disruption:
                break

        alert_level = "critical" if matched_disruption else "warning"

        alerts.append(
            {
                "chokepoint": portname,
                "portid": portid,
                "date": latest_date,
                "n_total": n_total,
                "baseline_avg": round(baseline["avg_total"], 1),
                "baseline_type": baseline_type,
                "anomaly_pct": round(anomaly_pct, 1),
                "n_tanker": n_tanker,
                "anomaly_tanker_pct": round(anomaly_tanker_pct, 1),
                "direction": direction,
                "alert_level": alert_level,
                "disruption_name": matched_disruption,
            }
        )

    conn.close()

    alerts.sort(key=lambda a: abs(a["anomaly_pct"]), reverse=True)
    return alerts
