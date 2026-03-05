from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import func

from backend.database import get_db
from backend.models.ports import PortActivity
from backend.collectors.portwatch import PORTS, CHOKEPOINTS

router = APIRouter(prefix="/api/ports", tags=["ports"])


@router.get("/activity")
async def get_port_activity(
    kind: str = Query(None, description="Filter by 'port' or 'chokepoint'"),
    port_id: str = Query(None, description="Filter by port/chokepoint ID"),
    days: int = Query(7, ge=1, le=90),
    db: Session = Depends(get_db),
):
    """Get port activity and chokepoint transit data from IMF PortWatch."""
    # Subquery: latest N days with data
    query = db.query(PortActivity).order_by(PortActivity.date.desc())

    if kind:
        query = query.filter(PortActivity.kind == kind)
    if port_id:
        query = query.filter(PortActivity.port_id == port_id)

    rows = query.limit(days * 10).all()

    return {
        "source": "IMF PortWatch",
        "attribution": "Source: IMF PortWatch (https://portwatch.imf.org)",
        "data": [
            {
                "port_id": r.port_id,
                "port_name": r.port_name,
                "date": r.date,
                "kind": r.kind,
                "vessel_count": r.vessel_count,
                "vessel_count_tanker": r.vessel_count_tanker,
                "import_total": r.import_total,
                "export_total": r.export_total,
                "import_tanker": r.import_tanker,
                "export_tanker": r.export_tanker,
                "capacity": r.capacity,
                "capacity_tanker": r.capacity_tanker,
            }
            for r in rows
        ],
    }


@router.get("/summary")
async def get_port_summary(
    db: Session = Depends(get_db),
):
    """Get latest-day summary per port and chokepoint for dashboard display."""
    # Find the most recent date in the data
    latest_date = db.query(func.max(PortActivity.date)).scalar()
    if not latest_date:
        return {"source": "IMF PortWatch", "ports": [], "chokepoints": []}

    rows = db.query(PortActivity).filter(PortActivity.date == latest_date).all()

    ports = []
    chokepoints = []
    for r in rows:
        entry = {
            "port_id": r.port_id,
            "port_name": r.port_name,
            "date": r.date,
            "vessel_count": r.vessel_count,
            "vessel_count_tanker": r.vessel_count_tanker,
        }
        if r.kind == "port":
            entry["import_total"] = r.import_total
            entry["export_total"] = r.export_total
            entry["import_tanker"] = r.import_tanker
            entry["export_tanker"] = r.export_tanker
            ports.append(entry)
        else:
            entry["capacity"] = r.capacity
            entry["capacity_tanker"] = r.capacity_tanker
            # Map chokepoint to our geofence zone name
            cp = CHOKEPOINTS.get(r.port_id, {})
            entry["zone"] = cp.get("zone", "")
            chokepoints.append(entry)

    return {
        "source": "IMF PortWatch",
        "date": latest_date,
        "ports": ports,
        "chokepoints": chokepoints,
    }
