"""
PortWatch API endpoints.

Uses the standalone portwatch_store for data access —
fetches from IMF PortWatch ArcGIS API and caches in local SQLite.
"""

from fastapi import APIRouter, Path, Query

from backend.collectors.portwatch_store import (
    fetch_chokepoint_data,
    fetch_disruptions,
    store_chokepoint_data,
    store_disruptions,
    query_chokepoint_averages,
    query_active_disruptions,
    CHOKEPOINTS,
)

router = APIRouter(prefix="/api/portwatch", tags=["portwatch"])

# Reverse map: portid -> name
CP_NAMES = {v.lower().replace(" ", "-"): k for k, v in CHOKEPOINTS.items()}
# Also allow short names
CP_ALIASES = {
    "hormuz": "chokepoint6",
    "suez": "chokepoint1",
    "malacca": "chokepoint5",
    "panama": "chokepoint2",
    "cape": "chokepoint7",
    "strait-of-hormuz": "chokepoint6",
    "suez-canal": "chokepoint1",
    "malacca-strait": "chokepoint5",
    "panama-canal": "chokepoint2",
    "cape-of-good-hope": "chokepoint7",
}


def _resolve_chokepoint(name: str) -> str | None:
    """Resolve a chokepoint name/alias to a portid."""
    name_lower = name.lower().replace(" ", "-")
    if name_lower in CP_ALIASES:
        return CP_ALIASES[name_lower]
    if name_lower in CP_NAMES:
        return CP_NAMES[name_lower]
    # Try direct portid
    if name_lower in CHOKEPOINTS:
        return name_lower
    return None


def _ensure_data(days: int = 30):
    """Fetch and store chokepoint data if not already cached."""
    data = fetch_chokepoint_data(days=days)
    store_chokepoint_data(data)
    return data


@router.get("/chokepoints")
async def get_chokepoints():
    """Current daily values for all chokepoints."""
    data = _ensure_data(days=7)

    # Group by portid, take most recent date per chokepoint
    latest = {}
    for row in data:
        pid = row["portid"]
        if pid not in latest or row["date"] > latest[pid]["date"]:
            latest[pid] = row

    return {
        "source": "IMF PortWatch",
        "chokepoints": list(latest.values()),
    }


@router.get("/chokepoints/{name}/history")
async def get_chokepoint_history(
    name: str = Path(description="Chokepoint name (e.g. 'hormuz', 'suez', 'malacca', 'panama', 'cape')"),
    days: int = Query(365, ge=1, le=730),
):
    """Time series for a single chokepoint."""
    portid = _resolve_chokepoint(name)
    if not portid:
        return {"error": f"Unknown chokepoint: {name}", "valid": list(CP_ALIASES.keys())}

    data = fetch_chokepoint_data(days=days)
    store_chokepoint_data(data)

    history = [r for r in data if r["portid"] == portid]
    history.sort(key=lambda x: x["date"])

    return {
        "source": "IMF PortWatch",
        "chokepoint": CHOKEPOINTS.get(portid, name),
        "portid": portid,
        "days": len(history),
        "history": history,
    }


@router.get("/disruptions")
async def get_disruptions():
    """Active disruption events."""
    data = fetch_disruptions(days=365)
    store_disruptions(data)
    active = query_active_disruptions()

    return {
        "source": "IMF PortWatch",
        "total_recent": len(data),
        "active": len(active),
        "disruptions": active,
    }


@router.get("/summary")
async def get_summary():
    """Dashboard overview: current values + anomaly vs 30-day average."""
    data = _ensure_data(days=35)

    # Latest day per chokepoint
    latest = {}
    for row in data:
        pid = row["portid"]
        if pid not in latest or row["date"] > latest[pid]["date"]:
            latest[pid] = row

    # 30-day averages from stored data
    store_chokepoint_data(data)
    averages = query_chokepoint_averages(days=30)
    avg_map = {a["portid"]: a for a in averages}

    # Active disruptions
    dis_data = fetch_disruptions(days=365)
    store_disruptions(dis_data)
    active_disruptions = query_active_disruptions()

    summary = []
    for pid, cur in latest.items():
        avg = avg_map.get(pid, {})
        avg_total = avg.get("avg_total", 0)
        avg_tanker = avg.get("avg_tanker", 0)

        anomaly_total = round((cur["n_total"] - avg_total) / avg_total * 100, 1) if avg_total else 0.0
        anomaly_tanker = round((cur["n_tanker"] - avg_tanker) / avg_tanker * 100, 1) if avg_tanker else 0.0

        summary.append({
            "portid": pid,
            "name": cur["portname"],
            "date": cur["date"],
            "n_total": cur["n_total"],
            "n_tanker": cur["n_tanker"],
            "capacity": cur["capacity"],
            "avg_total_30d": avg_total,
            "avg_tanker_30d": avg_tanker,
            "anomaly_total_pct": anomaly_total,
            "anomaly_tanker_pct": anomaly_tanker,
        })

    summary.sort(key=lambda x: abs(x["anomaly_total_pct"]), reverse=True)

    return {
        "source": "IMF PortWatch",
        "chokepoints": summary,
        "active_disruptions": len(active_disruptions),
        "disruptions": active_disruptions,
    }
