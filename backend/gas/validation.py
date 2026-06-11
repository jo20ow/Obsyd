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
import re
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session

from backend.models.gas import GasFlow, GasLng, GasPoint

TOLERANCE_PCT = 5.0
MILESTONE_PASS_FRACTION = 0.80


def _physical(name: str) -> str:
    """Strip the trailing operator suffix so the same physical point reported by
    several TSOs collapses to one key, e.g. 'Emden (EPT1) (OGE)' → 'Emden (EPT1)'."""
    return re.sub(r"\s*\([^)]*\)\s*$", "", name or "").strip()


def compute_daily_supply(db: Session, date_from: str, date_to: str) -> list[dict]:
    """Per-day supply (GWh/d) = pipeline imports + LNG send-out + max(0, net UK).

    Multi-operator double-reporting: several TSOs report the SAME physical entry
    (e.g. Emden EPT1) under different pointKeys, often with near-identical
    values. Summing them over-counts (~9% vs Bruegel). The physical flow at a
    point is the SINGLE LARGEST operator report (sub-reports are ≤ the total),
    so we take the max per (physical point, direction, day), then sum.
    """
    rows = (
        db.query(GasPoint.name, GasPoint.point_class, GasFlow.date, GasFlow.direction, GasFlow.value_gwh)
        .join(GasFlow, GasFlow.point_id == GasPoint.point_id)
        .filter(GasFlow.date >= date_from, GasFlow.date <= date_to, GasPoint.active == 1)
        .filter(GasPoint.point_class.in_(["import_pipeline", "interconnector_uk"]))
        .all()
    )

    # max value per (date, physical point, direction)
    best: dict[tuple[str, str, str], float] = {}
    for name, pclass, d, direction, value in rows:
        key = (d, _physical(name), direction)
        tagged = (pclass, value if value is not None else 0.0)
        if key not in best or abs(tagged[1]) > abs(best[key][1]):
            best[key] = tagged

    by_day: dict[str, dict] = {}
    for (d, _phys, direction), (pclass, value) in best.items():
        agg = by_day.setdefault(d, {"pipeline": 0.0, "uk_entry": 0.0, "uk_exit": 0.0})
        if pclass == "import_pipeline":
            agg["pipeline"] += value
        elif pclass == "interconnector_uk":
            agg["uk_entry" if direction == "entry" else "uk_exit"] += value

    lng = {
        r.date: (r.send_out_gwh or 0.0)
        for r in db.query(GasLng).filter(GasLng.date >= date_from, GasLng.date <= date_to).all()
    }

    out = []
    for d in sorted(set(by_day) | set(lng)):
        agg = by_day.get(d, {"pipeline": 0.0, "uk_entry": 0.0, "uk_exit": 0.0})
        uk_net = agg["uk_entry"] - agg["uk_exit"]
        pipeline = agg["pipeline"]
        lng_gwh = lng.get(d, 0.0)
        supply = pipeline + lng_gwh + max(0.0, uk_net)
        out.append(
            {
                "date": d,
                "pipeline_gwh": round(pipeline, 1),
                "lng_gwh": round(lng_gwh, 1),
                "uk_net_gwh": round(uk_net, 1),
                "supply_gwh": round(supply, 1),
            }
        )
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
