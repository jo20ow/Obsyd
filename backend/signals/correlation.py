"""
Chokepoint-Brent correlation analysis.

Computes Pearson correlation between daily tanker traffic at each key
chokepoint and Brent crude oil price, including time-lagged correlations
to detect whether chokepoint disruptions lead price moves.
"""

import math
from datetime import datetime, timedelta, timezone

from backend.collectors.portwatch_store import (
    _init_db,
    CHOKEPOINTS,
    query_chokepoint_averages,
)
from backend.database import SessionLocal
from backend.models.prices import FREDSeries

KEY_CHOKEPOINTS = {
    "chokepoint6": "Strait of Hormuz",
    "chokepoint1": "Suez Canal",
    "chokepoint5": "Malacca Strait",
    "chokepoint2": "Panama Canal",
    "chokepoint7": "Cape of Good Hope",
}

MAX_LAG = 7


def _pearson(xs: list[float], ys: list[float]) -> float:
    """Pearson correlation coefficient. Returns 0.0 on degenerate input."""
    n = len(xs)
    if n < 10:
        return 0.0
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if dx == 0 or dy == 0:
        return 0.0
    return num / (dx * dy)


IMPACT_THRESHOLD = 30.0  # percent drop to count as disruption event


def _compute_lagged_correlations(tanker_vals, common_dates, tanker_map,
                                  brent_map, all_brent_dates, brent_date_idx):
    """Compute level and delta correlations at lag 0..MAX_LAG."""
    brent_vals = [brent_map[d] for d in common_dates]

    # --- Level correlations ---
    corr_level_0 = _pearson(tanker_vals, brent_vals)
    level_lags = {0: round(corr_level_0, 3)}
    best_lag = 0
    best_lag_r = abs(corr_level_0)

    for lag in range(1, MAX_LAG + 1):
        t_lag, b_lag = [], []
        for d in common_dates:
            idx = brent_date_idx.get(d)
            if idx is None:
                continue
            future_idx = idx + lag
            if future_idx >= len(all_brent_dates):
                continue
            t_lag.append(tanker_map[d][0])
            b_lag.append(brent_map[all_brent_dates[future_idx]])
        r = _pearson(t_lag, b_lag)
        level_lags[lag] = round(r, 3)
        if abs(r) > best_lag_r:
            best_lag_r = abs(r)
            best_lag = lag

    # --- Delta (change) correlations: Δtanker vs ΔBrent ---
    delta_tanker = [tanker_vals[i] - tanker_vals[i - 1] for i in range(1, len(tanker_vals))]
    delta_brent = [brent_vals[i] - brent_vals[i - 1] for i in range(1, len(brent_vals))]
    delta_dates = common_dates[1:]

    corr_delta_0 = _pearson(delta_tanker, delta_brent)
    delta_lags = {0: round(corr_delta_0, 3)}
    best_delta_lag = 0
    best_delta_r = abs(corr_delta_0)

    for lag in range(1, MAX_LAG + 1):
        dt_lag, db_lag = [], []
        for i, d in enumerate(delta_dates):
            idx = brent_date_idx.get(d)
            if idx is None or idx < 1:
                continue
            future_idx = idx + lag
            if future_idx >= len(all_brent_dates):
                continue
            prev_idx = future_idx - 1
            if prev_idx < 0:
                continue
            dt_lag.append(delta_tanker[i])
            db_lag.append(brent_map[all_brent_dates[future_idx]] -
                          brent_map[all_brent_dates[prev_idx]])
        r = _pearson(dt_lag, db_lag)
        delta_lags[lag] = round(r, 3)
        if abs(r) > best_delta_r:
            best_delta_r = abs(r)
            best_delta_lag = lag

    return {
        "level": {
            "corr_0": round(corr_level_0, 3),
            "lags": level_lags,
            "best_lag": best_lag,
            "best_lag_r": round(best_lag_r, 3),
        },
        "delta": {
            "corr_0": round(corr_delta_0, 3),
            "lags": delta_lags,
            "best_lag": best_delta_lag,
            "best_lag_r": round(best_delta_r, 3),
        },
    }


def _detect_current_event(rows, tanker_vals, common_dates, brent_map,
                           avg_total):
    """Detect ongoing disruption event (>30% anomaly streak).

    Returns None or dict with event_start, brent_at_start, brent_current,
    brent_change_pct, anomaly_pct, duration_days.
    """
    if not rows or avg_total == 0:
        return None

    # Walk backwards from the latest date to find consecutive >30% anomaly days
    all_dates = [r[0] for r in rows]
    all_totals = {r[0]: r[2] for r in rows}  # date -> n_total

    event_start = None
    for d in reversed(all_dates):
        n = all_totals[d]
        anom = (n - avg_total) / avg_total * 100
        if abs(anom) >= IMPACT_THRESHOLD:
            event_start = d
        else:
            break

    if not event_start:
        return None

    latest_date = all_dates[-1]
    latest_anom = (all_totals[latest_date] - avg_total) / avg_total * 100

    # Find Brent on the day before or on the event start
    sorted_brent = sorted(brent_map.keys())
    brent_at_start = None
    for bd in reversed(sorted_brent):
        if bd <= event_start:
            brent_at_start = brent_map[bd]
            break

    brent_current = None
    for bd in reversed(sorted_brent):
        if bd in brent_map:
            brent_current = brent_map[bd]
            break

    if brent_at_start is None or brent_current is None or brent_at_start == 0:
        return None

    start_dt = datetime.strptime(event_start, "%Y-%m-%d")
    latest_dt = datetime.strptime(latest_date, "%Y-%m-%d")
    duration = (latest_dt - start_dt).days + 1

    return {
        "event_start": event_start,
        "event_latest": latest_date,
        "duration_days": duration,
        "anomaly_pct": round(latest_anom, 1),
        "brent_at_start": round(brent_at_start, 2),
        "brent_current": round(brent_current, 2),
        "brent_change_pct": round(
            (brent_current - brent_at_start) / brent_at_start * 100, 1
        ),
    }


def compute_correlations(days: int = 365, db_path=None) -> list[dict]:
    """Compute tanker-Brent correlations for all key chokepoints.

    Returns a list of dicts with:
      - chokepoint, portid
      - correlation (level, lag 0)
      - delta_correlation (day-over-day changes, lag 0)
      - best_lag_days, best_lag_correlation
      - lag_correlations, delta_lag_correlations
      - avg_price_impact_pct (avg Brent % change 7d after >30% traffic drop)
      - current_event (if anomaly currently >30%)
    """
    conn = _init_db(db_path)

    # Load Brent prices from obsyd.db/fred_series
    db = SessionLocal()
    try:
        brent_rows = (
            db.query(FREDSeries.date, FREDSeries.value)
            .filter(FREDSeries.series_id == "DCOILBRENTEU")
            .order_by(FREDSeries.date.asc())
            .all()
        )
        brent_map = {r.date: r.value for r in brent_rows}
    finally:
        db.close()

    if not brent_map:
        conn.close()
        return []

    all_brent_dates = sorted(brent_map.keys())
    brent_date_idx = {d: i for i, d in enumerate(all_brent_dates)}

    # Current anomaly data from 30d averages
    avgs = query_chokepoint_averages(days=30, db_path=db_path)
    avg_map = {a["portid"]: a for a in avgs}

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")

    results = []
    for portid, portname in KEY_CHOKEPOINTS.items():
        # Get daily tanker data
        rows = conn.execute("""
            SELECT date, n_tanker, n_total FROM chokepoint_daily
            WHERE portid = ? AND date >= ?
            ORDER BY date ASC
        """, (portid, cutoff)).fetchall()

        tanker_map = {r[0]: (r[1], r[2]) for r in rows}

        # Align dates: only dates where both tanker data and Brent exist
        common_dates = sorted(set(tanker_map.keys()) & set(brent_map.keys()))
        if len(common_dates) < 30:
            continue

        tanker_vals = [tanker_map[d][0] for d in common_dates]

        # Compute level + delta correlations with lags
        corr = _compute_lagged_correlations(
            tanker_vals, common_dates, tanker_map,
            brent_map, all_brent_dates, brent_date_idx,
        )

        # Compute avg price impact: when tanker traffic drops >30% vs 30d rolling avg,
        # what is the average Brent % change over the next 7 days?
        brent_vals = [brent_map[d] for d in common_dates]
        impacts = []
        window = 30
        if len(common_dates) > window + 7:
            for i in range(window, len(common_dates) - 7):
                window_vals = tanker_vals[i - window:i]
                avg_30 = sum(window_vals) / len(window_vals)
                if avg_30 == 0:
                    continue
                pct_change = (tanker_vals[i] - avg_30) / avg_30 * 100

                if pct_change < -IMPACT_THRESHOLD:
                    brent_now = brent_vals[i]
                    if brent_now == 0:
                        continue
                    future_d = common_dates[i + 7] if i + 7 < len(common_dates) else None
                    if future_d and future_d in brent_map:
                        brent_future = brent_map[future_d]
                        brent_pct = (brent_future - brent_now) / brent_now * 100
                        impacts.append(brent_pct)

        avg_impact = round(sum(impacts) / len(impacts), 1) if impacts else 0.0

        # Get current anomaly for this chokepoint
        avg_data = avg_map.get(portid, {})
        avg_total = avg_data.get("avg_total", 0)
        current_n = 0
        current_anomaly = 0.0
        if rows:
            latest = rows[-1]
            current_n = latest[2]  # n_total
            if avg_total:
                current_anomaly = round((current_n - avg_total) / avg_total * 100, 1)

        # Detect current event if anomaly > 30%
        current_event = _detect_current_event(
            rows, tanker_vals, common_dates, brent_map, avg_total
        )

        results.append({
            "chokepoint": portname,
            "portid": portid,
            "correlation": corr["level"]["corr_0"],
            "delta_correlation": corr["delta"]["corr_0"],
            "best_lag_days": corr["level"]["best_lag"],
            "best_lag_correlation": corr["level"]["best_lag_r"],
            "lag_correlations": corr["level"]["lags"],
            "delta_lag_correlations": corr["delta"]["lags"],
            "best_delta_lag_days": corr["delta"]["best_lag"],
            "best_delta_lag_correlation": corr["delta"]["best_lag_r"],
            "avg_price_impact_pct": avg_impact,
            "n_impact_events": len(impacts),
            "current_anomaly_pct": current_anomaly,
            "current_event": current_event,
            "data_points": len(common_dates),
        })

    conn.close()

    # Sort by absolute delta correlation (the real signal)
    results.sort(key=lambda x: abs(x["delta_correlation"]), reverse=True)
    return results
