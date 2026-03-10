from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.auth.dependencies import require_pro
from backend.database import get_db
from backend.geofences.zones import LNG_TERMINALS, NO_AIS_COVERAGE, STS_HOTSPOTS, ZONES
from backend.models.vessels import (
    FloatingStorageEvent,
    GeofenceEvent,
    GlobalVesselPosition,
    VesselPosition,
    VesselRegistry,
)
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

    # Batch-fetch registry data for all MMSIs
    mmsi_list = [r.mmsi for r in rows]
    registry_map = {}
    if mmsi_list:
        regs = db.query(VesselRegistry).filter(VesselRegistry.mmsi.in_(mmsi_list)).all()
        registry_map = {reg.mmsi: reg for reg in regs}

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
            "ship_class": registry_map[r.mmsi].ship_class if r.mmsi in registry_map else None,
            "estimated_dwt": registry_map[r.mmsi].dwt if r.mmsi in registry_map else None,
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
async def get_sts_intelligence(
    user: dict = Depends(require_pro),
    db: Session = Depends(get_db),
):
    """STS transfer detection + dark activity tracking (Pro only).

    Returns:
    - sts_events: persisted STS candidate/proximity events
    - dark_vessels: tankers with no AIS signal for >48h
    - hotspots: STS hotspot zone definitions
    """
    from backend.models.pro_features import STSEvent

    # Read persisted events (active + last 7 days resolved)
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    events = (
        db.query(STSEvent)
        .filter((STSEvent.status == "active") | (STSEvent.last_seen >= cutoff))
        .order_by(STSEvent.last_seen.desc())
        .all()
    )

    from backend.signals.sts_detection import detect_dark_vessels

    dark = detect_dark_vessels(db)

    return {
        "sts_candidates": [
            {
                "mmsi": e.mmsi_1,
                "ship_name": e.ship_name_1,
                "class": e.ship_class_1,
                "lat": e.lat,
                "lon": e.lon,
                "sog": None,
                "zone": e.zone,
                "sts_hotspot": e.zone,
                "sts_display": e.zone.replace("sts_", "").replace("_", " ").title(),
                "timestamp": e.last_seen.isoformat() if e.last_seen else None,
                "age_hours": round(e.duration_hours or 0, 1),
                "event_type": e.event_type,
                "status": e.status,
                "mmsi_2": e.mmsi_2,
                "ship_name_2": e.ship_name_2,
                "distance_m": e.distance_m,
            }
            for e in events
        ],
        "sts_candidate_count": sum(1 for e in events if e.event_type == "candidate"),
        "dark_vessels": dark,
        "dark_vessel_count": len(dark),
        "proximity_pairs": [
            {
                "vessel_1": {"mmsi": e.mmsi_1, "ship_name": e.ship_name_1, "class": e.ship_class_1},
                "vessel_2": {"mmsi": e.mmsi_2, "ship_name": e.ship_name_2, "class": e.ship_class_2},
                "distance_km": round((e.distance_m or 0) / 1000, 3),
                "hotspot": e.zone,
            }
            for e in events
            if e.event_type == "proximity"
        ],
        "proximity_pair_count": sum(1 for e in events if e.event_type == "proximity"),
        "hotspots": [
            {"name": h["name"], "display_name": h["display_name"], "bounds": h["bounds"]} for h in STS_HOTSPOTS
        ],
    }


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

    # Batch-fetch registry for DWT
    fs_mmsis = [r.mmsi for r in rows]
    fs_reg_map = {}
    if fs_mmsis:
        regs = db.query(VesselRegistry).filter(VesselRegistry.mmsi.in_(fs_mmsis)).all()
        fs_reg_map = {reg.mmsi: reg for reg in regs}

    events = []
    for r in rows:
        cls_name, _ = classify_vessel(r.ship_name, r.ship_type)
        reg = fs_reg_map.get(r.mmsi)
        events.append(
            {
                "mmsi": r.mmsi,
                "ship_name": r.ship_name,
                "ship_type": r.ship_type,
                "ship_class": reg.ship_class if reg else cls_name,
                "zone": r.zone,
                "lat": r.latitude,
                "lon": r.longitude,
                "first_seen": r.first_seen.isoformat() if r.first_seen else None,
                "last_seen": r.last_seen.isoformat() if r.last_seen else None,
                "duration_days": r.duration_days,
                "avg_sog": r.avg_sog,
                "status": r.status,
                "estimated_dwt": reg.dwt if reg else None,
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


@router.get("/registry")
async def get_vessel_registry(
    mmsi: str = Query(..., description="Vessel MMSI"),
    db: Session = Depends(get_db),
):
    """Get enriched vessel metadata from registry."""
    reg = db.query(VesselRegistry).filter(VesselRegistry.mmsi == mmsi).first()
    if not reg:
        return {"found": False, "mmsi": mmsi}

    return {
        "found": True,
        "mmsi": reg.mmsi,
        "imo": reg.imo,
        "ship_name": reg.ship_name,
        "ship_type": reg.ship_type,
        "ship_type_detailed": reg.ship_type_detailed,
        "ship_class": reg.ship_class,
        "dwt": reg.dwt,
        "dwt_estimated": bool(reg.dwt_estimated),
        "gross_tonnage": reg.gross_tonnage,
        "length": reg.length,
        "beam": reg.beam,
        "draft": reg.draft,
        "flag_state": reg.flag_state,
        "destination": reg.destination,
        "year_built": reg.year_built,
        "last_updated": reg.last_updated.isoformat() if reg.last_updated else None,
    }
