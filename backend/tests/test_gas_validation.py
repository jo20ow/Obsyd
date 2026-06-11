"""Validation milestone tests — supply decomposition + Bruegel ±5% comparison."""

from __future__ import annotations

from pathlib import Path

from backend.gas import validation
from backend.models.gas import GasFlow, GasLng, GasPoint

FIXTURES = Path(__file__).parent / "fixtures" / "gas"


def _seed_point(db, pid, cls):
    db.add(GasPoint(point_id=pid, name=pid, operator="op", point_class=cls, counterparty="x", active=1))


def _flow(db, day, pid, direction, gwh):
    db.add(GasFlow(date=day, point_id=pid, direction=direction, value_gwh=gwh, provisional=0, interpolated=0))


def test_supply_sums_pipeline_lng_and_net_uk(db_session):
    db = db_session
    _seed_point(db, "IMP", "import_pipeline")
    _seed_point(db, "UKI", "interconnector_uk")
    _flow(db, "2026-06-01", "IMP", "entry", 1000.0)
    _flow(db, "2026-06-01", "UKI", "entry", 200.0)
    _flow(db, "2026-06-01", "UKI", "exit", 50.0)  # net UK +150
    db.add(GasLng(date="2026-06-01", send_out_gwh=500.0, inventory_twh=30.0))
    db.commit()

    rows = validation.compute_daily_supply(db, "2026-06-01", "2026-06-01")
    assert len(rows) == 1
    r = rows[0]
    assert r["pipeline_gwh"] == 1000.0
    assert r["lng_gwh"] == 500.0
    assert r["uk_net_gwh"] == 150.0
    assert r["supply_gwh"] == 1650.0  # 1000 + 500 + 150


def test_net_uk_export_does_not_subtract_from_supply(db_session):
    db = db_session
    _seed_point(db, "IMP", "import_pipeline")
    _seed_point(db, "UKI", "interconnector_uk")
    _flow(db, "2026-06-01", "IMP", "entry", 1000.0)
    _flow(db, "2026-06-01", "UKI", "entry", 50.0)
    _flow(db, "2026-06-01", "UKI", "exit", 200.0)  # net UK -150 (EU exporting to UK)
    db.commit()
    r = validation.compute_daily_supply(db, "2026-06-01", "2026-06-01")[0]
    assert r["uk_net_gwh"] == -150.0
    assert r["supply_gwh"] == 1000.0  # export not counted as supply


def test_production_and_export_excluded(db_session):
    db = db_session
    _seed_point(db, "IMP", "import_pipeline")
    _seed_point(db, "PROD", "production_entry")
    _seed_point(db, "UA", "export_ua")
    _flow(db, "2026-06-01", "IMP", "entry", 1000.0)
    _flow(db, "2026-06-01", "PROD", "entry", 300.0)
    _flow(db, "2026-06-01", "UA", "exit", 200.0)
    db.commit()
    r = validation.compute_daily_supply(db, "2026-06-01", "2026-06-01")[0]
    assert r["supply_gwh"] == 1000.0  # only imports


def test_weekly_supply_aggregates_iso_weeks():
    daily = [
        {"date": "2026-06-01", "supply_gwh": 100.0},  # 2026-W23
        {"date": "2026-06-02", "supply_gwh": 100.0},  # 2026-W23
        {"date": "2026-06-08", "supply_gwh": 50.0},   # 2026-W24
    ]
    weekly = validation.weekly_supply(daily)
    assert weekly["2026-W23"] == 200.0
    assert weekly["2026-W24"] == 50.0


def test_compare_to_bruegel_pass_fail_and_fraction():
    model = {"2026-W23": 11550.0, "2026-W24": 13000.0, "2026-W25": 9999.0}
    bruegel = {"2026-W23": 11550.0, "2026-W24": 11000.0}  # W25 not in bruegel → ignored
    res = validation.compare_to_bruegel(model, bruegel)
    assert res["weeks_compared"] == 2
    rows = {r["iso_week"]: r for r in res["table"]}
    assert rows["2026-W23"]["pass"] is True   # exact
    assert rows["2026-W24"]["pass"] is False  # +18% > 5%
    assert res["pass_fraction"] == 0.5
    assert res["milestone_pass"] is False     # 50% < 80%


def test_validate_end_to_end_with_csv(db_session):
    db = db_session
    _seed_point(db, "IMP", "import_pipeline")
    # Week 2026-W23 is Mon 2026-06-01 .. Sun 2026-06-07. Seed 7 days × 1650 = 11550.
    for day in ["2026-06-01", "2026-06-02", "2026-06-03", "2026-06-04", "2026-06-05", "2026-06-06", "2026-06-07"]:
        _flow(db, day, "IMP", "entry", 1650.0)
    db.commit()
    res = validation.validate(db, "2026-06-01", "2026-06-07", FIXTURES / "bruegel_weekly.csv")
    assert res["weeks_compared"] == 1
    assert res["table"][0]["iso_week"] == "2026-W23"
    assert res["table"][0]["model_gwh"] == 11550.0
    assert res["table"][0]["pass"] is True
