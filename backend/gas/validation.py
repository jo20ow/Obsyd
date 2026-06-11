"""Phase-1 milestone: validate modeled daily SUPPLY (imports) against Bruegel
weekly aggregates.

Supply here matches Bruegel's "European natural gas imports" definition:
  pipeline imports (import_pipeline entries)
  + LNG send-out (ALSI, the canonical LNG source)
  + net UK interconnector flow, counted only when net-importing
Production and EU→UA exports are excluded (Bruegel measures imports).

Bruegel reference is a checked-in CSV (no stable API): columns
  iso_week,import_gwh        e.g.  2026-W23,168000
where import_gwh is GWh delivered that ISO week. Refresh manually from the
Bruegel "European natural gas imports" dataset.
"""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from sqlalchemy import case, func
from sqlalchemy.orm import Session

from backend.models.gas import GasFlow, GasLng, GasPoint

TOLERANCE_PCT = 5.0
MILESTONE_PASS_FRACTION = 0.80


def compute_daily_supply(db: Session, date_from: str, date_to: str) -> list[dict]:
    """Per-day supply (GWh/d) = pipeline imports + LNG send-out + max(0, net UK)."""
    # Pipeline imports (entries) + UK entry/exit, grouped by day, via a flow⋈point join.
    rows = (
        db.query(
            GasFlow.date.label("date"),
            func.sum(
                case((GasPoint.point_class == "import_pipeline", GasFlow.value_gwh), else_=0.0)
            ).label("pipeline"),
            func.sum(
                case(
                    ((GasPoint.point_class == "interconnector_uk") & (GasFlow.direction == "entry"), GasFlow.value_gwh),
                    else_=0.0,
                )
            ).label("uk_entry"),
            func.sum(
                case(
                    ((GasPoint.point_class == "interconnector_uk") & (GasFlow.direction == "exit"), GasFlow.value_gwh),
                    else_=0.0,
                )
            ).label("uk_exit"),
        )
        .join(GasPoint, GasFlow.point_id == GasPoint.point_id)
        .filter(GasFlow.date >= date_from, GasFlow.date <= date_to)
        .group_by(GasFlow.date)
        .all()
    )
    lng = {
        r.date: (r.send_out_gwh or 0.0)
        for r in db.query(GasLng).filter(GasLng.date >= date_from, GasLng.date <= date_to).all()
    }
    out = []
    for r in rows:
        uk_net = (r.uk_entry or 0.0) - (r.uk_exit or 0.0)
        pipeline = r.pipeline or 0.0
        lng_gwh = lng.get(r.date, 0.0)
        supply = pipeline + lng_gwh + max(0.0, uk_net)
        out.append(
            {
                "date": r.date,
                "pipeline_gwh": round(pipeline, 1),
                "lng_gwh": round(lng_gwh, 1),
                "uk_net_gwh": round(uk_net, 1),
                "supply_gwh": round(supply, 1),
            }
        )
    out.sort(key=lambda x: x["date"])
    return out


def _iso_week(date_str: str) -> str:
    y, w, _ = datetime.strptime(date_str, "%Y-%m-%d").isocalendar()
    return f"{y}-W{w:02d}"


def weekly_supply(daily: list[dict]) -> dict[str, float]:
    """Sum daily supply (GWh/d) into ISO-week totals (GWh)."""
    weekly: dict[str, float] = {}
    for row in daily:
        weekly[_iso_week(row["date"])] = weekly.get(_iso_week(row["date"]), 0.0) + row["supply_gwh"]
    return weekly


def load_bruegel_csv(path: str | Path) -> dict[str, float]:
    """iso_week -> import_gwh from the checked-in reference CSV."""
    out: dict[str, float] = {}
    with open(path, newline="") as fh:
        for row in csv.DictReader(fh):
            out[row["iso_week"].strip()] = float(row["import_gwh"])
    return out


def compare_to_bruegel(model_weekly: dict[str, float], bruegel_weekly: dict[str, float]) -> dict:
    """Per-week comparison table + milestone verdict (≥80% of overlapping weeks
    within ±5%). Only weeks present in both are compared."""
    weeks = sorted(set(model_weekly) & set(bruegel_weekly))
    table = []
    passed = 0
    for wk in weeks:
        model = model_weekly[wk]
        bruegel = bruegel_weekly[wk]
        diff = model - bruegel
        pct = (diff / bruegel * 100.0) if bruegel else float("inf")
        ok = abs(pct) <= TOLERANCE_PCT
        passed += ok
        table.append(
            {
                "iso_week": wk,
                "model_gwh": round(model, 1),
                "bruegel_gwh": round(bruegel, 1),
                "diff_gwh": round(diff, 1),
                "pct_diff": round(pct, 2),
                "pass": ok,
            }
        )
    n = len(weeks)
    pass_fraction = (passed / n) if n else 0.0
    return {
        "weeks_compared": n,
        "weeks_passed": passed,
        "pass_fraction": round(pass_fraction, 3),
        "tolerance_pct": TOLERANCE_PCT,
        "milestone_pass": bool(n > 0 and pass_fraction >= MILESTONE_PASS_FRACTION),
        "table": table,
    }


def validate(db: Session, date_from: str, date_to: str, bruegel_csv: str | Path) -> dict:
    daily = compute_daily_supply(db, date_from, date_to)
    model_weekly = weekly_supply(daily)
    bruegel_weekly = load_bruegel_csv(bruegel_csv)
    return compare_to_bruegel(model_weekly, bruegel_weekly)
