from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.geofences.zones import LNG_TERMINALS, NO_AIS_COVERAGE, STS_HOTSPOTS, ZONES
from backend.models.vessels import FloatingStorageEvent, GeofenceEvent, GlobalVesselPosition, VesselPosition
from backend.signals.sts_detection import get_sts_summary
from backend.signals.vessel_weight import classify_vessel, compute_weighted_count

router = APIRouter(prefix="/api/vessels", tags=["vessels"])


@router.get("/positions")
async def get_vessel_positions(
    zone: str = Query(None, description="Filter by geofence zone name"),
    limit: int = Query(500, ge=1, le=2000),
    db: Session = Depends(get_db),
):
    """Get the latest position per vessel (MMSI) within geofences."""
    # Subquery: latest timestamp per MMSI
    latest = (
        db.query(VesselPosition.mmsi, func.max(VesselPosition.id).label("max_id"))
        .group_by(VesselPosition.mmsi)
        .subquery()
    )

    query = (
        db.query(VesselPosition)
        .join(latest, VesselPosition.id == latest.c.max_id)
        .order_by(VesselPosition.timestamp.desc())
    )

    if zone:
        query = query.filter(VesselPosition.zone == zone)

    rows = query.limit(limit).all()
    return [
        {
            "mmsi": r.mmsi,
            "ship_name": r.ship_name,
            "ship_type": r.ship_type,
            "lat": r.latitude,
            "lon": r.longitude,
            "sog": r.sog,
            "cog": r.cog,
            "zone": r.zone,
            "timestamp": r.timestamp.isoformat(),
        }
        for r in rows
    ]


@router.get("/global")
async def get_global_vessels(
    limit: int = Query(5000, ge=1, le=10000),
    db: Session = Depends(get_db),
):
    """Get all vessels from AISHub global snapshot (not just tankers/zones)."""
    rows = db.query(GlobalVesselPosition).limit(limit).all()
    return [
        {
            "mmsi": r.mmsi,
            "ship_name": r.ship_name,
            "ship_type": r.ship_type,
            "lat": r.latitude,
            "lon": r.longitude,
            "sog": r.sog,
            "cog": r.cog,
            "is_tanker": bool(r.is_tanker),
            "zone": r.zone,
        }
        for r in rows
    ]


@router.get("/geofence-events")
async def get_geofence_events(
    zone: str = Query(None, description="Filter by geofence zone name"),
    limit: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
):
    """Get aggregated geofence events (daily tanker counts, dwell times)."""
    query = db.query(GeofenceEvent).order_by(GeofenceEvent.date.desc())
    if zone:
        query = query.filter(GeofenceEvent.zone == zone)
    rows = query.limit(limit).all()
    return [
        {
            "zone": r.zone,
            "date": r.date,
            "tanker_count": r.tanker_count,
            "avg_dwell_hours": r.avg_dwell_hours,
            "slow_movers": r.slow_movers,
        }
        for r in rows
    ]


@router.get("/zones")
async def list_zones():
    """List all configured geofence zones, including STS hotspots."""
    main = [
        {
            "name": z["name"],
            "display_name": z["display_name"],
            "bounds": z["bounds"],
            "description": z["description"],
            "no_ais_coverage": z["name"] in NO_AIS_COVERAGE,
            "is_sts": False,
        }
        for z in ZONES
    ]
    sts = [
        {
            "name": z["name"],
            "display_name": z["display_name"],
            "bounds": z["bounds"],
            "description": z["description"],
            "no_ais_coverage": False,
            "is_sts": True,
        }
        for z in STS_HOTSPOTS
    ]
    lng = [
        {
            "name": z["name"],
            "display_name": z["display_name"],
            "bounds": z["bounds"],
            "description": z["description"],
            "no_ais_coverage": False,
            "is_sts": False,
            "is_lng_terminal": True,
            "terminal_type": z.get("terminal_type", "export"),
        }
        for z in LNG_TERMINALS
    ]
    return main + sts + lng


@router.get("/weighted")
async def get_weighted_vessels(
    zone: str = Query(..., description="Geofence zone name (e.g. 'hormuz', 'suez')"),
    db: Session = Depends(get_db),
):
    """Weighted tanker count for a zone using ship-class heuristics.

    Computes on-the-fly from current vessel_positions — no schema changes.
    Weight factors: VLCC=3x, Suezmax=2x, Aframax=1x, Product/Tanker=0.5x.
    """
    # Latest position per MMSI in the given zone
    latest = (
        db.query(VesselPosition.mmsi, func.max(VesselPosition.id).label("max_id"))
        .filter(VesselPosition.zone == zone)
        .group_by(VesselPosition.mmsi)
        .subquery()
    )

    rows = (
        db.query(VesselPosition)
        .join(latest, VesselPosition.id == latest.c.max_id)
        .order_by(VesselPosition.timestamp.desc())
        .all()
    )

    # Build vessel list with classification
    vessels = []
    for r in rows:
        cls_name, weight = classify_vessel(r.ship_name, r.ship_type)
        vessels.append(
            {
                "mmsi": r.mmsi,
                "ship_name": r.ship_name,
                "ship_type": r.ship_type,
                "class": cls_name,
                "weight": weight,
                "lat": r.latitude,
                "lon": r.longitude,
                "sog": r.sog,
                "zone": r.zone,
                "timestamp": r.timestamp.isoformat(),
            }
        )

    # Compute weighted totals from raw vessel data
    summary = compute_weighted_count([{"ship_name": v["ship_name"], "ship_type": v["ship_type"]} for v in vessels])

    return {
        "zone": zone,
        "raw_count": summary["raw_count"],
        "weighted_count": summary["weighted_count"],
        "by_class": summary["by_class"],
        "vessels": vessels,
    }


@router.get("/sts")
async def get_sts_intelligence(db: Session = Depends(get_db)):
    """STS transfer detection + dark activity tracking.

    Returns:
    - sts_candidates: tankers anchored in known STS hotspots (SOG < 1 kn)
    - dark_vessels: tankers with no AIS signal for >48h
    - proximity_pairs: two tankers within 500m of each other in STS zones
    - hotspots: STS hotspot zone definitions
    """
    return get_sts_summary(db)


@router.get("/floating-storage")
async def get_floating_storage(
    db: Session = Depends(get_db),
):
    """Tankers stationary for 7+ days — potential floating storage.

    Returns active events + recently resolved (last 30 days).
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    rows = (
        db.query(FloatingStorageEvent)
        .filter((FloatingStorageEvent.status == "active") | (FloatingStorageEvent.last_seen >= cutoff))
        .order_by(FloatingStorageEvent.status.asc(), FloatingStorageEvent.duration_days.desc())
        .all()
    )

    events = []
    for r in rows:
        cls_name, _ = classify_vessel(r.ship_name, r.ship_type)
        events.append(
            {
                "mmsi": r.mmsi,
                "ship_name": r.ship_name,
                "ship_type": r.ship_type,
                "ship_class": cls_name,
                "zone": r.zone,
                "lat": r.latitude,
                "lon": r.longitude,
                "first_seen": r.first_seen.isoformat() if r.first_seen else None,
                "last_seen": r.last_seen.isoformat() if r.last_seen else None,
                "duration_days": r.duration_days,
                "avg_sog": r.avg_sog,
                "status": r.status,
            }
        )

    active_count = sum(1 for e in events if e["status"] == "active")
    return {"active_count": active_count, "events": events}


@router.get("/zone-history")
async def get_zone_history(
    zone: str = Query(None, description="Zone name (e.g. 'hormuz'). Omit for all zones."),
    days: int = Query(90, ge=7, le=365),
    db: Session = Depends(get_db),
):
    """Historical tanker counts per zone per day from GeofenceEvent data."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    query = db.query(GeofenceEvent).filter(GeofenceEvent.date >= cutoff).order_by(GeofenceEvent.date.asc())
    if zone:
        query = query.filter(GeofenceEvent.zone == zone)

    rows = query.all()

    # Group by zone
    series = {}
    for r in rows:
        if r.zone not in series:
            series[r.zone] = []
        series[r.zone].append(
            {
                "date": r.date,
                "tanker_count": r.tanker_count,
                "slow_movers": r.slow_movers,
                "avg_dwell_hours": r.avg_dwell_hours,
            }
        )

    return {"days": days, "zones": series}
