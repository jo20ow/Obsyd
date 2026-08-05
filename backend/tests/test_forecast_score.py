"""Nightly forecast scoring: per (zone, series, UTC-day) error metrics for
ENTSO-E's published day-ahead forecasts vs the published actuals.

Posture B: OBSYD grades ENTSO-E's OWN forecasts — it makes none of its own.
The naive baselines (persistence = actual(t−24h), seasonal = actual(t−168h))
are built from published actuals alone; they exist so a reader can later judge
the TSO forecast against "no model at all". Only the baseline MAEs are stored —
skill is derived at read time.

n semantics (documented + asserted here): `n_hours` counts the hours where BOTH
forecast and actual exist. MAPE and the baseline MAEs are means over their own
(possibly smaller) subsets of those hours — MAPE drops hours whose |actual| is
below the division floor, a baseline MAE drops hours whose lagged actual is
missing — and each is NULL when its subset is empty. `n_hours` never shrinks
for either.

Times are epoch-UTC throughout, and the tests read the UTC clock instead of
hardcoding calendar days (repo rule #111: date.today() made suites fail between
00–02 local).
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pytest

from backend.models.energy import ForecastScoreDaily, PowerHourly, SeriesDim
from backend.power.forecast_score import (
    FORECAST_PAIRS,
    MAPE_ACTUAL_FLOOR_MW,
    compute_and_store_range,
    compute_and_store_scores,
)
from backend.power.hourly_store import day_hour_ts, upsert_hourly


def _day(days_ago: int) -> str:
    return (datetime.now(timezone.utc).date() - timedelta(days=days_ago)).isoformat()


DAY = _day(2)            # the scored day (safely finished)
PERSIST_DAY = _day(3)    # DAY − 24 h
SEASONAL_DAY = _day(9)   # DAY − 168 h


def _seed_load(db, zone: str = "DE_LU") -> None:
    """Four hand-checkable hours on DAY, plus the actuals both baselines need.

    errors (forecast − actual): [−10, +10, +20, −40]
      → mae 20 · rmse √550 · bias −5 (the forecast leaned LOW)
    persistence |actual(t) − actual(t−24h)|: [10, 10, 20, 40] → 20
    seasonal    |actual(t) − actual(t−168h)|: [0, 0, 0, 40]   → 10
    """
    fc = [(day_hour_ts(DAY, h), v) for h, v in zip((0, 1, 2, 3), (100.0, 200.0, 300.0, 400.0))]
    ac = [(day_hour_ts(DAY, h), v) for h, v in zip((0, 1, 2, 3), (110.0, 190.0, 280.0, 440.0))]
    persist = [(day_hour_ts(PERSIST_DAY, h), v) for h, v in zip((0, 1, 2, 3), (100.0, 200.0, 300.0, 400.0))]
    seasonal = [(day_hour_ts(SEASONAL_DAY, h), v) for h, v in zip((0, 1, 2, 3), (110.0, 190.0, 280.0, 400.0))]
    upsert_hourly(db, "load.forecast", zone, fc, unit="MW")
    upsert_hourly(db, "load.actual", zone, ac + persist + seasonal, unit="MW")
    db.commit()


def test_hand_computed_mae_rmse_bias_mape_and_baselines(db_session):
    _seed_load(db_session)
    result = compute_and_store_scores(db_session, "DE_LU", DAY)
    assert result["written"] == 1  # only the load pair has data

    row = (
        db_session.query(ForecastScoreDaily)
        .filter_by(zone="DE_LU", series="load", date=DAY)
        .one()
    )
    assert row.n_hours == 4
    assert row.mae == pytest.approx(20.0)
    assert row.rmse == pytest.approx(math.sqrt(550.0))
    # bias = mean(forecast − actual): NEGATIVE = the published forecast leaned LOW.
    # (The /api/power/forecast-error endpoint reports the opposite sign, unchanged.)
    assert row.bias == pytest.approx(-5.0)
    assert row.mape == pytest.approx(100.0 * (10 / 110 + 10 / 190 + 20 / 280 + 40 / 440) / 4)
    assert row.mae_persistence == pytest.approx(20.0)
    assert row.mae_seasonal == pytest.approx(10.0)


def test_mape_guard_excludes_tiny_actuals_but_mae_keeps_them(db_session):
    """An hour whose |actual| is under the floor is a division trap (or an ingest
    artifact), not a percentage — it leaves MAPE but stays in MAE and n_hours."""
    h0, h1 = day_hour_ts(DAY, 0), day_hour_ts(DAY, 1)
    upsert_hourly(db_session, "load.forecast", "DE_LU", [(h0, 200.0), (h1, 50.0)], unit="MW")
    upsert_hourly(
        db_session, "load.actual", "DE_LU",
        [(h0, MAPE_ACTUAL_FLOOR_MW), (h1, 0.0)], unit="MW",
    )
    db_session.commit()
    compute_and_store_scores(db_session, "DE_LU", DAY)

    row = db_session.query(ForecastScoreDaily).filter_by(series="load", date=DAY).one()
    assert row.n_hours == 2
    assert row.mae == pytest.approx(75.0)          # both hours count: (100 + 50) / 2
    assert row.mape == pytest.approx(100.0)        # only h0: |200 − 100| / 100


def test_baselines_score_only_hours_where_the_lagged_actual_exists(db_session):
    """No prior-day actual → no persistence term for that hour; no prior-week data
    at all → mae_seasonal is NULL. n_hours never shrinks for a missing baseline."""
    upsert_hourly(
        db_session, "load.forecast", "DE_LU",
        [(day_hour_ts(DAY, 0), 100.0), (day_hour_ts(DAY, 1), 100.0)], unit="MW",
    )
    upsert_hourly(
        db_session, "load.actual", "DE_LU",
        [
            (day_hour_ts(DAY, 0), 110.0),
            (day_hour_ts(DAY, 1), 130.0),
            (day_hour_ts(PERSIST_DAY, 0), 140.0),  # hour 1 of DAY−1 is missing
        ],
        unit="MW",
    )
    db_session.commit()
    compute_and_store_scores(db_session, "DE_LU", DAY)

    row = db_session.query(ForecastScoreDaily).filter_by(series="load", date=DAY).one()
    assert row.n_hours == 2
    assert row.mae_persistence == pytest.approx(30.0)  # |110 − 140|, the one covered hour
    assert row.mae_seasonal is None


def test_wind_actual_is_the_b18_plus_b19_sum(db_session):
    """There is no wind.actual series — realised wind is gen.B18 + gen.B19,
    the same derivation the residual ingest and the forecast-error route use."""
    t = day_hour_ts(DAY, 12)
    upsert_hourly(db_session, "wind.forecast", "DE_LU", [(t, 10_000.0)], unit="MW")
    upsert_hourly(db_session, "gen.B18", "DE_LU", [(t, 3_000.0)], unit="MW")
    upsert_hourly(db_session, "gen.B19", "DE_LU", [(t, 5_000.0)], unit="MW")
    db_session.commit()
    compute_and_store_scores(db_session, "DE_LU", DAY)

    row = db_session.query(ForecastScoreDaily).filter_by(series="wind", date=DAY).one()
    assert row.n_hours == 1
    assert row.bias == pytest.approx(2_000.0)  # 10 GW promised, 8 GW delivered → leaned HIGH
    assert row.mae == pytest.approx(2_000.0)
    assert row.mape is None                    # MAPE is load-only by design


def test_recompute_is_idempotent(db_session):
    _seed_load(db_session)
    compute_and_store_scores(db_session, "DE_LU", DAY)
    compute_and_store_scores(db_session, "DE_LU", DAY)

    rows = (
        db_session.query(ForecastScoreDaily)
        .filter_by(zone="DE_LU", series="load", date=DAY)
        .all()
    )
    assert len(rows) == 1, "recompute replaces the row, never duplicates it"
    assert rows[0].n_hours == 4
    assert rows[0].mae == pytest.approx(20.0)


def test_zone_with_no_data_writes_nothing_and_does_not_crash(db_session):
    result = compute_and_store_scores(db_session, "FR", DAY)
    assert result == {"written": 0, "removed": 0}
    assert db_session.query(ForecastScoreDaily).count() == 0


def test_recompute_retracts_a_day_the_data_no_longer_supports(db_session):
    """Episodes doctrine: a row a later revision of the data no longer supports
    disappears, rather than sitting in the scoreboard forever."""
    _seed_load(db_session)
    compute_and_store_scores(db_session, "DE_LU", DAY)
    assert db_session.query(ForecastScoreDaily).filter_by(series="load", date=DAY).count() == 1

    sid = db_session.query(SeriesDim.id).filter_by(key="load.forecast").scalar()
    db_session.query(PowerHourly).filter(PowerHourly.series_id == sid).delete()
    db_session.commit()

    result = compute_and_store_scores(db_session, "DE_LU", DAY)
    assert result["removed"] == 1
    assert db_session.query(ForecastScoreDaily).filter_by(series="load", date=DAY).count() == 0


def test_range_scores_each_day_and_skips_days_without_both_sides(db_session):
    """PERSIST_DAY/SEASONAL_DAY carry actuals but no forecast — they feed the
    baselines, they do not get scored themselves."""
    _seed_load(db_session)
    result = compute_and_store_range(db_session, "DE_LU", SEASONAL_DAY, DAY)
    assert result["written"] == 1

    dates = [r.date for r in db_session.query(ForecastScoreDaily).filter_by(series="load").all()]
    assert dates == [DAY]


def test_routes_share_the_canonical_pair_table():
    """The forecast-error route must import THE pair table, not carry a copy —
    a drifted copy would grade a different forecast than the scoreboard stores."""
    from backend.routes import power as power_routes

    assert power_routes.FORECAST_PAIRS is FORECAST_PAIRS
    assert set(FORECAST_PAIRS) == {"load", "residual", "wind", "solar"}
