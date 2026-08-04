"""Nightly data-quality aggregates: completeness + rule-based anomaly flags
per (zone, series, UTC day), persisted in quality_daily.

Posture B: every flag DESCRIBES the published data (solar at night, a zero-run
in load, an 8-IQR step) — nothing here predicts. Per rule there is one
synthetic-positive and one boundary-negative case; conservatism is the design
constraint (a false positive costs exactly the trust the product exists for).

Times are epoch-UTC throughout, and the tests read the UTC clock instead of
hardcoding calendar days (repo rule #111: date.today() made suites fail between
00–02 local).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import sessionmaker

import backend.models  # noqa: F401 — registers vessel/alert tables run_retention touches
from backend.models.energy import IngestArrival, PowerHourly, PowerRevision, QualityDaily, SeriesDim
from backend.power.hourly_store import day_hour_ts, upsert_hourly
from backend.power.quality import (
    QUALITY_SERIES,
    ZONE_SERIES_KEY,
    compute_and_store_quality,
    compute_and_store_range,
)


def _day(days_ago: int) -> str:
    return (datetime.now(timezone.utc).date() - timedelta(days=days_ago)).isoformat()


DAY = _day(2)  # the examined day (safely finished)


def _row(db, series_key: str, zone: str = "DE_LU", day: str = DAY) -> QualityDaily:
    return db.query(QualityDaily).filter_by(zone=zone, series_key=series_key, date=day).one()


def _flags(row: QualityDaily) -> list[dict]:
    return json.loads(row.flags)


def _rules(row: QualityDaily) -> set[str]:
    return {f["rule"] for f in _flags(row)}


def _seed_hours(db, series: str, zone: str, day: str, values: dict[int, float], unit="MW"):
    upsert_hourly(db, series, zone, [(day_hour_ts(day, h), v) for h, v in values.items()], unit=unit)


# ── Completeness ──────────────────────────────────────────────────────────────

def test_completeness_counts_present_hours_of_24(db_session):
    _seed_hours(db_session, "load.actual", "DE_LU", DAY, {h: 1000.0 for h in range(20)})
    db_session.commit()
    result = compute_and_store_quality(db_session, "DE_LU", DAY)
    assert result["written"] == 1  # only load.actual has data

    row = _row(db_session, "load.actual")
    assert row.hours_present == 20
    assert row.hours_expected == 24
    assert _flags(row) == []


def test_qh_series_expects_96_intervals(db_session):
    day_start = day_hour_ts(DAY, 0)
    points = [(day_start + i * 900, 50.0) for i in range(90)]  # 90 of 96 quarter-hours
    upsert_hourly(db_session, "price.dayahead.qh", "DE_LU", points, unit="EUR/MWh")
    db_session.commit()
    compute_and_store_quality(db_session, "DE_LU", DAY)

    row = _row(db_session, "price.dayahead.qh")
    assert row.hours_present == 90
    assert row.hours_expected == 96


def test_empty_day_gets_zero_row_only_with_surrounding_activity(db_session):
    """Activity within 30 days → the gap is information (hours_present=0).
    Series with no data anywhere stay silent — the zone doesn't carry them."""
    _seed_hours(db_session, "load.actual", "DE_LU", _day(5), {10: 900.0})
    db_session.commit()
    compute_and_store_quality(db_session, "DE_LU", DAY)

    row = _row(db_session, "load.actual")
    assert row.hours_present == 0
    assert row.hours_expected == 24
    assert _flags(row) == []
    # the other five series have no data at all → no rows, no noise
    assert db_session.query(QualityDaily).count() == 1


def test_no_row_when_activity_is_beyond_the_30_day_window(db_session):
    _seed_hours(db_session, "load.actual", "DE_LU", _day(40), {10: 900.0})
    db_session.commit()
    result = compute_and_store_quality(db_session, "DE_LU", DAY)
    assert result == {"written": 0, "removed": 0}
    assert db_session.query(QualityDaily).count() == 0


def test_zone_with_no_data_writes_nothing_and_does_not_crash(db_session):
    result = compute_and_store_quality(db_session, "FR", DAY)
    assert result == {"written": 0, "removed": 0}
    assert db_session.query(QualityDaily).count() == 0


# ── pv_at_night ───────────────────────────────────────────────────────────────

def test_pv_at_night_flags_solar_inside_the_dark_window(db_session):
    # day max 4000 → 1% = 40 → floor 50 governs; 300 MW at 23:00 UTC is a flag
    _seed_hours(db_session, "gen.B16", "DE_LU", DAY, {12: 4000.0, 23: 300.0})
    db_session.commit()
    compute_and_store_quality(db_session, "DE_LU", DAY)

    flags = _flags(_row(db_session, "gen.B16"))
    assert [f["rule"] for f in flags] == ["pv_at_night"]
    assert flags[0]["hours"] == [day_hour_ts(DAY, 23)]
    assert flags[0]["detail"] == {"max_mw": 300.0, "threshold_mw": 50.0}


def test_pv_at_night_boundary_values_do_not_flag(db_session):
    # 1% of a 60 GW day = 600 MW: exactly 600 at night is NOT above the threshold
    _seed_hours(db_session, "gen.B16", "DE_LU", DAY, {12: 60000.0, 23: 600.0})
    # exactly the 50 MW floor is NOT above it either
    _seed_hours(db_session, "gen.B16", "FR", DAY, {12: 100.0, 22: 50.0})
    # 21:00 UTC is OUTSIDE the conservative window — high values there are dusk, not night
    _seed_hours(db_session, "gen.B16", "NL", DAY, {12: 8000.0, 21: 5000.0})
    db_session.commit()
    for zone in ("DE_LU", "FR", "NL"):
        compute_and_store_quality(db_session, zone, DAY)
        assert _flags(_row(db_session, "gen.B16", zone=zone)) == []


# ── zero_run ──────────────────────────────────────────────────────────────────

def test_zero_run_flags_six_consecutive_zero_load_hours(db_session):
    values = {h: 0.0 for h in range(6)} | {h: 1000.0 for h in range(6, 24)}
    _seed_hours(db_session, "load.actual", "DE_LU", DAY, values)
    db_session.commit()
    compute_and_store_quality(db_session, "DE_LU", DAY)

    flags = [f for f in _flags(_row(db_session, "load.actual")) if f["rule"] == "zero_run"]
    assert len(flags) == 1
    assert flags[0]["hours"] == [day_hour_ts(DAY, h) for h in range(6)]
    assert flags[0]["detail"] == {"longest_run_hours": 6}


def test_zero_run_five_hours_is_below_the_bar(db_session):
    values = {h: 0.0 for h in range(5)} | {h: 1000.0 for h in range(5, 24)}
    _seed_hours(db_session, "load.actual", "DE_LU", DAY, values)
    db_session.commit()
    compute_and_store_quality(db_session, "DE_LU", DAY)
    assert "zero_run" not in _rules(_row(db_session, "load.actual"))


def test_zero_run_never_applies_to_prices(db_session):
    """Zero (and negative) PRICES are real market outcomes — six zero price
    hours must produce no flag of any kind (the 50 EUR recovery step is also
    under the 100 EUR floor)."""
    values = {h: 0.0 for h in range(6)} | {h: 50.0 for h in range(6, 24)}
    _seed_hours(db_session, "price.dayahead", "DE_LU", DAY, values, unit="EUR/MWh")
    db_session.commit()
    compute_and_store_quality(db_session, "DE_LU", DAY)
    assert _flags(_row(db_session, "price.dayahead")) == []


# ── step_jump ─────────────────────────────────────────────────────────────────

def _seed_alternating_load(db, days_ago_from: int, days_ago_to: int, except_hours: dict[int, float] | None = None):
    """1000/1100 alternating hours over [days_ago_from..days_ago_to] — trailing
    deltas of ±100 → IQR 200 → step threshold max(8·200, 500) = 1600."""
    points = []
    for days_ago in range(days_ago_from, days_ago_to - 1, -1):
        day = _day(days_ago)
        for h in range(24):
            v = 1000.0 if h % 2 == 0 else 1100.0
            if days_ago == days_ago_to and except_hours and h in except_hours:
                v = except_hours[h]
            points.append((day_hour_ts(day, h), v))
    upsert_hourly(db, "load.actual", "DE_LU", points, unit="MW")
    db.commit()


def test_step_jump_flags_a_spike_far_beyond_the_series_own_iqr(db_session):
    _seed_alternating_load(db_session, 32, 2, except_hours={12: 9000.0})
    compute_and_store_quality(db_session, "DE_LU", DAY)

    flags = [f for f in _flags(_row(db_session, "load.actual")) if f["rule"] == "step_jump"]
    assert len(flags) == 1
    # the spike enters at hour 12 and leaves at hour 13 — both deltas flag
    assert flags[0]["hours"] == [day_hour_ts(DAY, 12), day_hour_ts(DAY, 13)]
    assert flags[0]["detail"]["threshold"] == pytest.approx(1600.0)
    assert flags[0]["detail"]["max_abs_delta"] == pytest.approx(7900.0)


def test_step_jump_exactly_at_threshold_does_not_flag(db_session):
    # hour 11 is 1100; hours 12..23 at 2700 give one delta of exactly 8×IQR = 1600
    _seed_alternating_load(db_session, 32, 2, except_hours={h: 2700.0 for h in range(12, 24)})
    compute_and_store_quality(db_session, "DE_LU", DAY)
    assert "step_jump" not in _rules(_row(db_session, "load.actual"))


def test_step_jump_absolute_floor_guards_series_without_history(db_session):
    """No trailing deltas → IQR 0 → the absolute floor governs: 400 MW stays
    quiet, 600 MW flags (load floor 500)."""
    _seed_hours(db_session, "load.actual", "DE_LU", DAY, {0: 1000.0, 1: 1400.0})
    _seed_hours(db_session, "load.actual", "FR", DAY, {0: 1000.0, 1: 1600.0})
    db_session.commit()
    compute_and_store_quality(db_session, "DE_LU", DAY)
    compute_and_store_quality(db_session, "FR", DAY)

    assert "step_jump" not in _rules(_row(db_session, "load.actual"))
    fr_flags = [f for f in _flags(_row(db_session, "load.actual", zone="FR")) if f["rule"] == "step_jump"]
    assert fr_flags and fr_flags[0]["detail"]["threshold"] == pytest.approx(500.0)


def test_step_jump_price_floor_is_100_eur(db_session):
    _seed_hours(db_session, "price.dayahead", "DE_LU", DAY, {0: 10.0, 1: 160.0}, unit="EUR/MWh")
    db_session.commit()
    compute_and_store_quality(db_session, "DE_LU", DAY)

    flags = [f for f in _flags(_row(db_session, "price.dayahead")) if f["rule"] == "step_jump"]
    assert flags and flags[0]["detail"]["threshold"] == pytest.approx(100.0)


# ── gen_below_load_exports (zone-level, series_key "_zone") ──────────────────

def _seed_balance(db, zone: str, gen_mw: float, load_mw: float = 1000.0, export_mw: float | None = 100.0):
    _seed_hours(db, "load.actual", zone, DAY, {h: load_mw for h in range(24)})
    _seed_hours(db, "gen.B01", zone, DAY, {h: gen_mw for h in range(24)})
    if export_mw is not None:
        # zone is the storing (FROM) side: positive = zone exports
        _seed_hours(db, "flow.XX", zone, DAY, {h: export_mw for h in range(24)})
    db.commit()


def test_gen_below_load_exports_flags_a_covered_zone_with_a_deficit(db_session):
    # gen 16800 vs load 24000 + exports 2400: coverage gate 0.7 ≥ 0.6 passes,
    # 16800 < 0.9 × 26400 → flag
    _seed_balance(db_session, "DE_LU", gen_mw=700.0)
    compute_and_store_quality(db_session, "DE_LU", DAY)

    row = _row(db_session, ZONE_SERIES_KEY)
    assert row.hours_present == 0 and row.hours_expected == 0
    (flag,) = _flags(row)
    assert flag["rule"] == "gen_below_load_exports"
    assert flag["hours"] == []
    assert flag["detail"]["gen_mwh"] == pytest.approx(16800.0)
    assert flag["detail"]["load_mwh"] == pytest.approx(24000.0)
    assert flag["detail"]["net_export_mwh"] == pytest.approx(2400.0)
    assert flag["detail"]["deficit_pct"] == pytest.approx(36.4)

    # retraction: the feed heals (gen now covers supply) → the _zone row disappears
    _seed_hours(db_session, "gen.B01", "DE_LU", DAY, {h: 1200.0 for h in range(24)})
    db_session.commit()
    result = compute_and_store_quality(db_session, "DE_LU", DAY)
    assert result["removed"] == 1
    assert db_session.query(QualityDaily).filter_by(series_key=ZONE_SERIES_KEY).count() == 0


def test_gen_below_load_exports_exempts_structurally_under_covered_zones(db_session):
    # gen/load 0.4 < coverage_min_ratio 0.6 — the SE4-shaped structural gap
    # (or a genuine net importer) is exempt, coverage.py's fail-safe doctrine
    _seed_balance(db_session, "DE_LU", gen_mw=400.0)
    compute_and_store_quality(db_session, "DE_LU", DAY)
    assert db_session.query(QualityDaily).filter_by(series_key=ZONE_SERIES_KEY).count() == 0


def test_gen_below_load_exports_boundary_at_ninety_percent_does_not_flag(db_session):
    # gen 23760 == exactly 0.9 × (24000 + 2400) → within tolerance, no flag
    _seed_balance(db_session, "DE_LU", gen_mw=990.0)
    compute_and_store_quality(db_session, "DE_LU", DAY)
    assert db_session.query(QualityDaily).filter_by(series_key=ZONE_SERIES_KEY).count() == 0


def test_gen_below_load_exports_needs_flow_data(db_session):
    """Without border flows an unmeasured import is indistinguishable from a
    deficit — a healthy importer would flag every day. No flows, no flag."""
    _seed_balance(db_session, "DE_LU", gen_mw=700.0, export_mw=None)
    compute_and_store_quality(db_session, "DE_LU", DAY)
    assert db_session.query(QualityDaily).filter_by(series_key=ZONE_SERIES_KEY).count() == 0


# ── Recompute discipline ──────────────────────────────────────────────────────

def test_recompute_is_idempotent(db_session):
    _seed_hours(db_session, "load.actual", "DE_LU", DAY, {h: 1000.0 for h in range(24)})
    db_session.commit()
    compute_and_store_quality(db_session, "DE_LU", DAY)
    compute_and_store_quality(db_session, "DE_LU", DAY)

    rows = db_session.query(QualityDaily).filter_by(zone="DE_LU", series_key="load.actual", date=DAY).all()
    assert len(rows) == 1, "recompute replaces the row, never duplicates it"
    assert rows[0].hours_present == 24


def test_recompute_retracts_a_day_the_data_no_longer_supports(db_session):
    """Episodes doctrine: a row a later revision of the data no longer supports
    disappears, rather than sitting in the record forever."""
    _seed_hours(db_session, "load.actual", "DE_LU", DAY, {h: 1000.0 for h in range(24)})
    db_session.commit()
    compute_and_store_quality(db_session, "DE_LU", DAY)
    assert db_session.query(QualityDaily).count() == 1

    sid = db_session.query(SeriesDim.id).filter_by(key="load.actual").scalar()
    db_session.query(PowerHourly).filter(PowerHourly.series_id == sid).delete()
    db_session.commit()

    result = compute_and_store_quality(db_session, "DE_LU", DAY)
    assert result["removed"] == 1
    assert db_session.query(QualityDaily).count() == 0


def test_range_covers_each_day(db_session):
    for day in (_day(4), _day(3), DAY):
        _seed_hours(db_session, "load.actual", "DE_LU", day, {h: 1000.0 for h in range(24)})
    db_session.commit()
    result = compute_and_store_range(db_session, "DE_LU", _day(4), DAY)
    assert result["written"] == 3

    dates = [r.date for r in db_session.query(QualityDaily).order_by(QualityDaily.date).all()]
    assert dates == [_day(4), _day(3), DAY]


def test_quality_series_config_matches_the_task_charter():
    assert QUALITY_SERIES == (
        "load.actual", "price.dayahead", "price.dayahead.qh",
        "gen.B16", "gen.B18", "gen.B19",
    )


# ── Retention (ingest_arrival pruning, A1's TODO) ────────────────────────────

async def test_retention_prunes_old_arrivals_keeps_recent_and_never_touches_revisions(
    db_session, monkeypatch
):
    import backend.collectors.retention as retention_mod

    now = int(datetime.now(timezone.utc).timestamp())
    old = now - 100 * 86400
    recent = now - 86400
    db_session.add(IngestArrival(series_id=1, zone_id=1, observed_at=old, n_new=1, n_changed=0))
    db_session.add(IngestArrival(series_id=1, zone_id=1, observed_at=recent, n_new=1, n_changed=0))
    # the revision ledger is the product — even a 100-day-old row must survive
    db_session.add(PowerRevision(series_id=1, zone_id=1, ts_utc=old,
                                 old_value=1.0, new_value=2.0, observed_at=old))
    db_session.commit()

    # run_retention binds SessionLocal at import time and is not in conftest's
    # consumer list — point it at this test's engine explicitly.
    factory = sessionmaker(autocommit=False, autoflush=False, bind=db_session.get_bind())
    monkeypatch.setattr(retention_mod, "SessionLocal", factory)
    await retention_mod.run_retention()

    db_session.expire_all()
    assert [r.observed_at for r in db_session.query(IngestArrival).all()] == [recent]
    assert db_session.query(PowerRevision).count() == 1


# ── Wiring: scheduler job + freshness spec ───────────────────────────────────

class _RecordingScheduler:
    """Stands in for the module-global AsyncIOScheduler: records add_job calls."""

    def __init__(self):
        self.jobs: dict[str, object] = {}

    def add_job(self, func, trigger, *, id, **kwargs):  # noqa: A002 - mirrors APScheduler
        assert id not in self.jobs, f"duplicate scheduler job id: {id}"
        self.jobs[id] = (func, trigger)

    def start(self):
        pass


def test_quality_nightly_registered_between_episodes_and_scoreboard(monkeypatch):
    import backend.collectors.scheduler as sched_mod

    rec = _RecordingScheduler()
    monkeypatch.setattr(sched_mod, "scheduler", rec)
    sched_mod.start_scheduler()

    func, trigger = rec.jobs["quality_nightly"]
    assert func is sched_mod._run_quality_nightly
    assert str(trigger) == "cron[hour='23', minute='55']"
    # the nightly derived-table chain keeps its order: records → episodes → quality → scoreboard
    assert str(rec.jobs["episodes_nightly"][1]) == "cron[hour='23', minute='50']"
    assert str(rec.jobs["forecast_scoreboard_nightly"][1]) == "cron[hour='23', minute='58']"


def test_quality_daily_freshness_spec_watches_the_nightly_recompute():
    from backend.collectors.freshness import SPECS

    spec = next((s for s in SPECS if s.key == "quality_daily"), None)
    assert spec is not None, "quality_daily must be freshness-monitored"
    assert spec.model is QualityDaily
    assert spec.column == "updated_at"
    assert spec.max_age == timedelta(days=2)
