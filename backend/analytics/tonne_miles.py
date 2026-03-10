"""
Tonne-Miles Index — measures transport capacity consumed by current routing patterns.

When tankers reroute from Suez to Cape, they travel ~5,300nm further.
This binds tanker capacity and tightens the freight market.
The index normalizes raw tonne-miles against a 30-day baseline (100 = average).

Scheduled: every 6 hours.
"""

import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.database import SessionLocal
from backend.models.analytics import TonneMilesHistory
from backend.models.vessels import VesselRegistry

logger = logging.getLogger(__name__)

PORTWATCH_DB = Path(__file__).parent.parent.parent / "data" / "portwatch.db"

# Reference distances (one-way, nautical miles)
ROUTE_DISTANCES = {
    "hormuz_suez": 6300,  # PG → Rotterdam via Suez
    "hormuz_cape": 11600,  # PG → Rotterdam via Cape
    "malacca": 3500,  # PG → Singapore via Malacca
    "houston": 5100,  # Houston → Rotterdam
    "panama": 10500,  # Houston → Singapore via Panama
}

# PortWatch chokepoint IDs
CHOKEPOINT_IDS = {
    "hormuz": "chokepoint6",
    "suez": "chokepoint1",
    "malacca": "chokepoint5",
    "panama": "chokepoint2",
    "cape": "chokepoint7",
}

# Average DWT by ship class (metric tonnes)
CLASS_DWT = {
    "VLCC": 300000,
    "Suezmax": 160000,
    "Aframax": 110000,
    "Panamax": 75000,
    "Product": 50000,
    "LNG": 80000,
    "Unknown": 80000,
}


def _get_class_distribution(db) -> dict[str, float]:
    """Get tanker class distribution from VesselRegistry as fractions."""
    regs = db.query(VesselRegistry.ship_class).filter(VesselRegistry.ship_class.isnot(None)).all()
    if not regs:
        return {"Unknown": 1.0}

    counts: dict[str, int] = {}
    for (cls,) in regs:
        key = cls if cls in CLASS_DWT else "Unknown"
        counts[key] = counts.get(key, 0) + 1

    total = sum(counts.values())
    return {k: v / total for k, v in counts.items()}


def _find_latest_good_date(conn) -> str | None:
    """Find latest date with meaningful PortWatch data (n_tanker >= 10)."""
    row = conn.execute(
        "SELECT MAX(date) FROM chokepoint_daily WHERE n_tanker >= 10",
    ).fetchone()
    return row[0] if row and row[0] else None


def _get_portwatch_tankers(days: int = 7) -> dict[str, float]:
    """Get average daily tanker count per chokepoint from PortWatch.

    Uses latest meaningful data to avoid PortWatch publication lag.
    """
    if not PORTWATCH_DB.exists():
        return {}

    conn = sqlite3.connect(str(PORTWATCH_DB))

    result = {}
    try:
        end_date = _find_latest_good_date(conn)
        if not end_date:
            return {}

        cutoff = (datetime.strptime(end_date, "%Y-%m-%d") - timedelta(days=days - 1)).strftime("%Y-%m-%d")

        for name, portid in CHOKEPOINT_IDS.items():
            rows = conn.execute(
                "SELECT AVG(n_tanker) FROM chokepoint_daily WHERE portid = ? AND date >= ? AND date <= ?",
                (portid, cutoff, end_date),
            ).fetchone()
            if rows and rows[0]:
                result[name] = rows[0]
    finally:
        conn.close()

    return result


def _get_cape_share() -> float:
    """Get current Cape share from PortWatch data.

    Uses latest meaningful data to avoid PortWatch publication lag.
    """
    if not PORTWATCH_DB.exists():
        return 0.0

    conn = sqlite3.connect(str(PORTWATCH_DB))

    try:
        end_date = _find_latest_good_date(conn)
        if not end_date:
            return 0.0

        cutoff = (datetime.strptime(end_date, "%Y-%m-%d") - timedelta(days=6)).strftime("%Y-%m-%d")

        suez = conn.execute(
            "SELECT AVG(n_tanker) FROM chokepoint_daily WHERE portid = ? AND date >= ? AND date <= ?",
            (CHOKEPOINT_IDS["suez"], cutoff, end_date),
        ).fetchone()
        cape = conn.execute(
            "SELECT AVG(n_tanker) FROM chokepoint_daily WHERE portid = ? AND date >= ? AND date <= ?",
            (CHOKEPOINT_IDS["cape"], cutoff, end_date),
        ).fetchone()

        s = suez[0] or 0
        c = cape[0] or 0
        combined = s + c
        return c / combined if combined > 0 else 0.0
    finally:
        conn.close()


async def compute_tonne_miles():
    """Compute and persist Tonne-Miles Index. Scheduled every 6 hours."""
    db = SessionLocal()
    try:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        # 1. Get cape share
        cape_share = _get_cape_share()

        # 2. Get tanker counts per chokepoint (7d average)
        tanker_counts = _get_portwatch_tankers(days=7)
        if not tanker_counts:
            logger.warning("Tonne-miles: no PortWatch data available")
            return

        # 3. Get class distribution
        class_dist = _get_class_distribution(db)

        # 4. Calculate effective distances per chokepoint
        # Hormuz → Europe: weighted by cape_share
        hormuz_eff = (1 - cape_share) * ROUTE_DISTANCES["hormuz_suez"] + cape_share * ROUTE_DISTANCES["hormuz_cape"]
        distances = {
            "hormuz": hormuz_eff,
            "suez": ROUTE_DISTANCES["hormuz_suez"],
            "malacca": ROUTE_DISTANCES["malacca"],
            "panama": ROUTE_DISTANCES["panama"],
            "cape": ROUTE_DISTANCES["hormuz_cape"],
        }

        # 5. Calculate daily tonne-miles
        daily_tm = 0.0
        class_counts = {}
        for cp, avg_tankers in tanker_counts.items():
            dist = distances.get(cp, 5000)
            for cls, frac in class_dist.items():
                tankers_this_class = avg_tankers * frac
                dwt = CLASS_DWT.get(cls, 80000)
                daily_tm += tankers_this_class * dwt * dist
                class_counts[cls] = class_counts.get(cls, 0) + tankers_this_class

        avg_distance = daily_tm / max(sum(v * CLASS_DWT.get(k, 80000) for k, v in class_counts.items()), 1)

        # 6. Normalize to index (30d baseline = 100)
        recent = db.query(TonneMilesHistory).order_by(TonneMilesHistory.date.desc()).limit(30).all()
        if recent:
            baseline = sum(r.tonne_miles_raw for r in recent) / len(recent)
            index_val = (daily_tm / baseline * 100) if baseline > 0 else 100.0
        else:
            index_val = 100.0

        # 7. Upsert
        existing = db.query(TonneMilesHistory).filter(TonneMilesHistory.date == today).first()
        if existing:
            existing.tonne_miles_raw = daily_tm
            existing.tonne_miles_index = round(index_val, 1)
            existing.cape_share = round(cape_share, 4)
            existing.tanker_count_by_class = json.dumps({k: round(v, 1) for k, v in class_counts.items()})
            existing.avg_distance = round(avg_distance, 0)
        else:
            db.add(
                TonneMilesHistory(
                    date=today,
                    tonne_miles_raw=daily_tm,
                    tonne_miles_index=round(index_val, 1),
                    cape_share=round(cape_share, 4),
                    tanker_count_by_class=json.dumps({k: round(v, 1) for k, v in class_counts.items()}),
                    avg_distance=round(avg_distance, 0),
                )
            )

        db.commit()
        logger.info(
            "Tonne-miles: index=%.1f, raw=%.0f, cape_share=%.1f%%",
            index_val,
            daily_tm,
            cape_share * 100,
        )
    except Exception as e:
        logger.error("Tonne-miles computation failed: %s", e)
        db.rollback()
    finally:
        db.close()
