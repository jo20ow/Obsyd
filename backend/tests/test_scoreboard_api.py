"""/api/v1/scoreboard/* — the Honest-Record forecast scoreboard read API (B2).

Covers: per-zone summary window math (day-weighted by n_hours, hand-computed,
incl. the NULL-baseline drop-out from the skill ratio only), the cross-zone
ranking (load MAPE / wind+solar capacity-normalized MAE with seeded A68
capacity and honest signposting when a zone has none / residual plain-MAE
caveat), monthly bucketing across a calendar boundary, the on-read hour-of-day
profile riding the REAL engine path (FORECAST_PAIRS + score_hours over the
hourly store), the bias sign convention (table convention: forecast − actual,
declared on the wire), helpful 400s, honest available:false, and the v1 guard
stack (shared rate budget, heavy slots, per-window ranking cache).

Posture B: every asserted number GRADES a forecast ENTSO-E published — OBSYD
forecasts nothing. Times read the UTC clock, never date.today() (repo rule #111).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from backend.api_guard import _reset_coverage_cache
from backend.auth.ratelimit import reset_limits
from backend.database import get_db
from backend.main import app
from backend.models.energy import ForecastScoreDaily, InstalledCapacity
from backend.power.forecast_score import compute_and_store_scores
from backend.power.hourly_store import day_hour_ts, upsert_hourly

# Rebound per TEST by the _clock fixture below — the endpoints read the live
# clock, so a suite that imports before UTC midnight and asserts after it must
# not do its day math from a frozen import-time NOW (repo rule #111's flake class).
NOW = datetime.now(UTC)
NOW_S = int(NOW.timestamp())
_H = 3600
_D = 86400


@pytest.fixture(autouse=True)
def _clock():
    global NOW, NOW_S
    NOW = datetime.now(UTC)
    NOW_S = int(NOW.timestamp())


@pytest.fixture(autouse=True)
def _isolate():
    reset_limits()
    _reset_coverage_cache()  # keyed cache is process-global — the ranking would leak
    yield
    app.dependency_overrides.clear()
    reset_limits()
    _reset_coverage_cache()


def _client(db) -> TestClient:
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


def _day(days_ago: int) -> str:
    return (NOW.date() - timedelta(days=days_ago)).isoformat()


def _score(db, zone, series, day, *, n=24, mae=100.0, rmse=120.0, bias=10.0,
           mape=None, mae_p=150.0, mae_s=200.0):
    db.add(ForecastScoreDaily(zone=zone, series=series, date=day, n_hours=n,
                              mae=mae, rmse=rmse, bias=bias, mape=mape,
                              mae_persistence=mae_p, mae_seasonal=mae_s))


def _cap(db, zone, psr, mw, year=2025):
    db.add(InstalledCapacity(zone=zone, year=year, psr_type=psr, capacity_mw=mw))


# ─── /summary ─────────────────────────────────────────────────────────────────


def test_summary_window_math_day_weighted(db_session):
    """Hand-computed 30/90/365 aggregates. Day-weighted by n_hours: mae/bias are
    exact per-hour window means, rmse recombines quadratically, and a day whose
    baseline MAE is NULL drops out of the SKILL ratio only (its mae still
    counts in the headline mae)."""
    _score(db_session, "DE_LU", "load", _day(1), n=24, mae=100.0, rmse=100.0,
           bias=10.0, mape=2.0, mae_p=200.0, mae_s=250.0)
    _score(db_session, "DE_LU", "load", _day(40), n=12, mae=200.0, rmse=200.0,
           bias=-20.0, mape=4.0, mae_p=250.0, mae_s=None)  # NULL seasonal baseline
    _score(db_session, "DE_LU", "load", _day(200), n=24, mae=50.0, rmse=50.0,
           bias=0.0, mape=1.0, mae_p=100.0, mae_s=100.0)
    db_session.commit()

    body = _client(db_session).get("/api/v1/scoreboard/summary?zone=DE_LU").json()
    assert body["available"] is True
    assert body["zone"] == "DE_LU"
    (cell,) = body["series"]  # only the carried series appears
    assert cell["series"] == "load"

    w30 = cell["windows"]["30d"]
    assert w30["days_covered"] == 1 and w30["n_hours"] == 24
    assert w30["mae"] == pytest.approx(100.0)
    assert w30["rmse"] == pytest.approx(100.0)
    assert w30["bias"] == pytest.approx(10.0)
    assert w30["mape"] == pytest.approx(2.0)
    assert w30["skill_persistence"] == pytest.approx(0.5)    # 1 − 100/200
    assert w30["skill_seasonal"] == pytest.approx(0.6)       # 1 − 100/250

    w90 = cell["windows"]["90d"]
    assert w90["days_covered"] == 2 and w90["n_hours"] == 36
    assert w90["mae"] == pytest.approx(133.3, abs=0.05)      # (100·24+200·12)/36
    assert w90["rmse"] == pytest.approx(141.4, abs=0.05)     # sqrt((100²·24+200²·12)/36)
    assert w90["bias"] == pytest.approx(0.0)                 # (10·24 − 20·12)/36
    assert w90["mape"] == pytest.approx(2.67, abs=0.01)      # (2·24+4·12)/36
    # skill vs persistence uses BOTH days: 1 − 4800/7800
    assert w90["skill_persistence"] == pytest.approx(0.385, abs=1e-3)
    # NULL-baseline day drops out of the seasonal ratio ONLY → same as 30d
    assert w90["skill_seasonal"] == pytest.approx(0.6)

    w365 = cell["windows"]["365d"]
    assert w365["days_covered"] == 3 and w365["n_hours"] == 60
    assert w365["mae"] == pytest.approx(100.0)               # 6000/60
    assert w365["mape"] == pytest.approx(2.0)                # 120/60
    assert w365["skill_persistence"] == pytest.approx(0.412, abs=1e-3)  # 1 − 6000/10200

    # freshness triple, keyed to the forecast_scoreboard spec (2d window)
    assert body["as_of"] == _day(1)
    assert body["age_days"] == 1
    assert body["stale"] is False
    # the sign convention is declared on the wire, table convention
    assert "mean(forecast - actual)" in body["bias_convention"]
    assert "forecast-error" in body["bias_convention"]  # warns about the old route


def test_summary_lists_only_carried_series_but_names_all(db_session):
    _score(db_session, "DE_LU", "wind", _day(40), mape=None)
    db_session.commit()
    body = _client(db_session).get("/api/v1/scoreboard/summary?zone=DE_LU").json()
    assert [c["series"] for c in body["series"]] == ["wind"]
    assert body["series_keys"] == ["load", "residual", "wind", "solar"]
    windows = body["series"][0]["windows"]
    assert windows["30d"] is None  # a window with no scored days is honest null
    assert windows["90d"]["days_covered"] == 1
    # wind carries no MAPE by design — the field is present and honestly null
    assert windows["90d"]["mape"] is None


def test_summary_valid_but_empty_zone_is_available_false(db_session):
    body = _client(db_session).get("/api/v1/scoreboard/summary?zone=FR").json()
    assert body["available"] is False
    assert body["series"] == []
    assert body["as_of"] is None and body["stale"] is False  # inert, not a crash


def test_summary_bad_zone_is_helpful_400(db_session):
    r = _client(db_session).get("/api/v1/scoreboard/summary?zone=NOPE")
    assert r.status_code == 400
    assert "DE_LU" in r.json()["detail"]


# ─── sign convention (through the real engine) ────────────────────────────────


def test_bias_positive_when_forecast_leans_high_end_to_end(db_session):
    """Seed forecast = actual + 50 in the hourly store, score it through the
    REAL engine (compute_and_store_scores), and assert the summary reports
    bias +50 — the TABLE convention (forecast − actual), opposite of the old
    /api/power/forecast-error route. The profile (which reads the hourly store
    directly through the engine's score_hours) must agree."""
    for h in range(4):
        ts = day_hour_ts(_day(5), h)
        upsert_hourly(db_session, "load.forecast", "DE_LU", [(ts, 10050.0)], unit="MW")
        upsert_hourly(db_session, "load.actual", "DE_LU", [(ts, 10000.0)], unit="MW")
    compute_and_store_scores(db_session, "DE_LU", _day(5))

    c = _client(db_session)
    body = c.get("/api/v1/scoreboard/summary?zone=DE_LU").json()
    (cell,) = body["series"]
    assert cell["series"] == "load"
    assert cell["windows"]["30d"]["bias"] == pytest.approx(50.0)  # positive = leaned HIGH
    assert "mean(forecast - actual)" in body["bias_convention"]

    prof = c.get("/api/v1/scoreboard/profile?zone=DE_LU&series=load").json()
    assert prof["available"] is True
    covered = [row for row in prof["hours"] if row["n"] > 0]
    assert len(covered) == 4
    assert all(row["bias"] == pytest.approx(50.0) for row in covered)
    assert "mean(forecast - actual)" in prof["bias_convention"]


# ─── /ranking ─────────────────────────────────────────────────────────────────


def test_ranking_load_by_mape(db_session):
    _score(db_session, "DE_LU", "load", _day(1), mae=500.0, mape=5.0)
    _score(db_session, "FR", "load", _day(1), mae=900.0, mape=2.0)  # bigger MW, better %
    db_session.commit()
    body = _client(db_session).get("/api/v1/scoreboard/ranking").json()
    assert body["available"] is True
    assert body["window_days"] == 90
    load = body["series"]["load"]
    assert load["metric"] == "mape"
    assert [(e["zone"], e["rank"]) for e in load["ranking"]] == [("FR", 1), ("DE_LU", 2)]
    assert load["ranking"][0]["mape"] == pytest.approx(2.0)
    assert load["ranking"][0]["days_covered"] == 1


def test_ranking_nmae_math_and_capacity_signposting(db_session):
    # wind capacity: DE_LU onshore+offshore summed, each at its own latest year
    _cap(db_session, "DE_LU", "Wind Onshore", 90000.0, year=2024)  # stale year — must lose
    _cap(db_session, "DE_LU", "Wind Onshore", 20000.0, year=2025)
    _cap(db_session, "DE_LU", "Wind Offshore", 5000.0, year=2025)
    _cap(db_session, "FR", "Wind Onshore", 10000.0, year=2025)
    _cap(db_session, "DE_LU", "Solar", 10000.0, year=2025)
    _score(db_session, "DE_LU", "wind", _day(1), mae=500.0)   # 100·500/25000 = 2.0 %
    _score(db_session, "FR", "wind", _day(1), mae=300.0)      # 100·300/10000 = 3.0 %
    _score(db_session, "NL", "wind", _day(1), mae=100.0)      # no A68 capacity seeded
    _score(db_session, "DE_LU", "solar", _day(1), mae=250.0)  # 100·250/10000 = 2.5 %
    db_session.commit()

    body = _client(db_session).get("/api/v1/scoreboard/ranking").json()
    wind = body["series"]["wind"]
    assert wind["metric"] == "nmae_pct"
    assert [(e["zone"], e["rank"]) for e in wind["ranking"]] == [
        ("DE_LU", 1), ("FR", 2), ("NL", None)]  # listed, never silently hidden
    de, fr, nl = wind["ranking"]
    assert de["nmae_pct"] == pytest.approx(2.0)
    assert de["capacity_mw"] == pytest.approx(25000.0)  # 2025 values, onshore+offshore
    assert fr["nmae_pct"] == pytest.approx(3.0)
    assert nl["nmae_pct"] is None and nl["capacity_mw"] is None
    assert "no A68 capacity" in nl["signposted"]
    assert nl["mae"] == pytest.approx(100.0)  # the absolute number still served

    solar = body["series"]["solar"]
    assert solar["ranking"][0]["nmae_pct"] == pytest.approx(2.5)


def test_ranking_residual_plain_mae_with_caveat(db_session):
    _score(db_session, "DE_LU", "residual", _day(1), mae=800.0)
    _score(db_session, "FR", "residual", _day(1), mae=400.0)
    db_session.commit()
    res = _client(db_session).get("/api/v1/scoreboard/ranking").json()["series"]["residual"]
    assert res["metric"] == "mae"
    assert [(e["zone"], e["rank"]) for e in res["ranking"]] == [("FR", 1), ("DE_LU", 2)]
    assert "not" in res["caveat"] and "MW" in res["caveat"]  # cross-zone comparability caveat


def test_ranking_window_filters_and_validates(db_session):
    _score(db_session, "DE_LU", "load", _day(50), mape=3.0)
    db_session.commit()
    c = _client(db_session)
    assert c.get("/api/v1/scoreboard/ranking").json()["available"] is True  # 90d default
    body = c.get("/api/v1/scoreboard/ranking?window=30").json()
    assert body["available"] is False  # the only row lies outside 30d
    assert body["series"]["load"]["ranking"] == []
    r = c.get("/api/v1/scoreboard/ranking?window=7")
    assert r.status_code == 400
    assert "30" in r.json()["detail"] and "365" in r.json()["detail"]  # lists valid windows


def test_ranking_is_cached_per_window_and_stamped_per_request(db_session, monkeypatch):
    import backend.routes.scoreboard as sb

    _score(db_session, "DE_LU", "load", _day(1), mape=3.0)
    db_session.commit()
    calls = {"n": 0}
    orig = sb._ranking_payload

    def counting(db, window):
        calls["n"] += 1
        return orig(db, window)

    monkeypatch.setattr(sb, "_ranking_payload", counting)
    c = _client(db_session)
    first = c.get("/api/v1/scoreboard/ranking").json()
    second = c.get("/api/v1/scoreboard/ranking").json()
    assert calls["n"] == 1  # second hit served from the keyed cache
    assert first["series"] == second["series"]
    assert "age_days" in second and "stale" in second  # triple rides each request
    c.get("/api/v1/scoreboard/ranking?window=30")
    assert calls["n"] == 2  # each window is its own cache entry


# ─── /monthly ─────────────────────────────────────────────────────────────────


def test_monthly_buckets_on_utc_calendar_months(db_session):
    _score(db_session, "DE_LU", "load", "2026-05-31", n=24, mae=100.0, rmse=100.0,
           bias=10.0, mape=2.0, mae_p=200.0, mae_s=None)
    _score(db_session, "DE_LU", "load", "2026-06-01", n=12, mae=200.0, rmse=200.0,
           bias=-20.0, mape=4.0, mae_p=250.0, mae_s=250.0)
    _score(db_session, "DE_LU", "load", "2026-06-15", n=24, mae=50.0, rmse=50.0,
           bias=0.0, mape=1.0, mae_p=None, mae_s=100.0)
    db_session.commit()

    body = _client(db_session).get(
        "/api/v1/scoreboard/monthly?zone=DE_LU&series=load").json()
    assert body["available"] is True
    may, june = body["data"]  # oldest first — a chart-ready history
    assert may["month"] == "2026-05" and may["days"] == 1
    assert may["mae"] == pytest.approx(100.0)
    assert may["skill_persistence"] == pytest.approx(0.5)  # 1 − 100/200
    assert may["skill_seasonal"] is None                   # only baseline row is NULL

    assert june["month"] == "2026-06" and june["days"] == 2 and june["n_hours"] == 36
    assert june["mae"] == pytest.approx(100.0)             # (200·12+50·24)/36
    assert june["bias"] == pytest.approx(-6.7)             # (−20·12+0·24)/36, wire-rounded 0.1
    assert june["mape"] == pytest.approx(2.0)              # (4·12+1·24)/36
    # persistence: only the 06-01 row carries the baseline → 1 − 2400/3000
    assert june["skill_persistence"] == pytest.approx(0.2)
    # seasonal: 06-01 and 06-15 → 1 − 3600/(250·12+100·24) = 1 − 3600/5400
    assert june["skill_seasonal"] == pytest.approx(0.333, abs=1e-3)

    assert body["as_of"] == "2026-06-15"
    assert body["stale"] is True  # months old — honestly stale
    assert "mean(forecast - actual)" in body["bias_convention"]


def test_monthly_bad_params_and_empty(db_session):
    c = _client(db_session)
    r = c.get("/api/v1/scoreboard/monthly?zone=DE_LU&series=hack")
    assert r.status_code == 400
    assert "load" in r.json()["detail"]  # lists the valid series keys
    r = c.get("/api/v1/scoreboard/monthly?zone=NOPE&series=load")
    assert r.status_code == 400
    assert "DE_LU" in r.json()["detail"]
    body = c.get("/api/v1/scoreboard/monthly?zone=FR&series=load").json()
    assert body["available"] is False and body["data"] == []


# ─── /profile ─────────────────────────────────────────────────────────────────


def test_profile_hour_of_day_math_via_engine(db_session):
    """Two days of seeded hourly pairs → per-UTC-hour buckets through the real
    engine scoring (score_hours): h0 has errors +100 and −100 (mae 100, bias 0),
    h1 has a single +50."""
    upsert_hourly(db_session, "load.forecast", "DE_LU",
                  [(day_hour_ts(_day(3), 0), 10100.0), (day_hour_ts(_day(3), 1), 10050.0),
                   (day_hour_ts(_day(2), 0), 9900.0)], unit="MW")
    upsert_hourly(db_session, "load.actual", "DE_LU",
                  [(day_hour_ts(_day(3), 0), 10000.0), (day_hour_ts(_day(3), 1), 10000.0),
                   (day_hour_ts(_day(2), 0), 10000.0)], unit="MW")

    body = _client(db_session).get(
        "/api/v1/scoreboard/profile?zone=DE_LU&series=load").json()
    assert body["available"] is True
    assert body["window_days"] == 90
    assert [row["hour_utc"] for row in body["hours"]] == list(range(24))
    h0, h1 = body["hours"][0], body["hours"][1]
    assert h0["n"] == 2 and h0["mae"] == pytest.approx(100.0) and h0["bias"] == pytest.approx(0.0)
    assert h1["n"] == 1 and h1["mae"] == pytest.approx(50.0) and h1["bias"] == pytest.approx(50.0)
    assert body["hours"][5] == {"hour_utc": 5, "n": 0, "mae": None, "bias": None}
    assert "UTC" in body["note"]  # hour-of-day is UTC, said on the wire
    # as_of = the newest scored hour, ISO 8601 UTC
    assert body["as_of"] == datetime.fromtimestamp(day_hour_ts(_day(2), 0), UTC).isoformat()


def test_profile_wind_sums_the_two_actual_gen_series(db_session):
    """The wind pair's actual is gen.B18+gen.B19 summed — FORECAST_PAIRS is the
    single source of that alignment and the profile must ride it."""
    ts = day_hour_ts(_day(2), 12)
    upsert_hourly(db_session, "wind.forecast", "DE_LU", [(ts, 800.0)], unit="MW")
    upsert_hourly(db_session, "gen.B18", "DE_LU", [(ts, 300.0)], unit="MW")
    upsert_hourly(db_session, "gen.B19", "DE_LU", [(ts, 400.0)], unit="MW")

    body = _client(db_session).get(
        "/api/v1/scoreboard/profile?zone=DE_LU&series=wind").json()
    h12 = body["hours"][12]
    assert h12["n"] == 1
    assert h12["bias"] == pytest.approx(100.0)  # 800 − (300+400), forecast leaned high
    assert h12["mae"] == pytest.approx(100.0)


def test_profile_param_validation(db_session):
    c = _client(db_session)
    assert c.get("/api/v1/scoreboard/profile?zone=DE_LU&series=load&window=400").status_code == 422
    assert c.get("/api/v1/scoreboard/profile?zone=DE_LU&series=load&window=0").status_code == 422
    r = c.get("/api/v1/scoreboard/profile?zone=DE_LU&series=hack")
    assert r.status_code == 400
    assert "load" in r.json()["detail"]
    r = c.get("/api/v1/scoreboard/profile?zone=NOPE&series=load")
    assert r.status_code == 400
    assert "DE_LU" in r.json()["detail"]


def test_profile_valid_but_empty_is_available_false(db_session):
    body = _client(db_session).get(
        "/api/v1/scoreboard/profile?zone=FR&series=load").json()
    assert body["available"] is False
    assert body["hours"] == []
    assert body["as_of"] is None and body["stale"] is False


# ─── guard stack ──────────────────────────────────────────────────────────────


def test_scoreboard_shares_the_v1_rate_budget(db_session, monkeypatch):
    import backend.routes.api_v1 as v1

    monkeypatch.setattr(v1, "RATE_PER_MIN", 2)
    c = _client(db_session)
    assert c.get("/api/v1/scoreboard/summary?zone=DE_LU").status_code == 200
    assert c.get("/api/v1/scoreboard/monthly?zone=DE_LU&series=load").status_code == 200
    assert c.get("/api/v1/scoreboard/ranking").status_code == 429


def test_ranking_and_profile_hold_a_heavy_slot(db_session):
    """Drained semaphore → fail-fast 503 for the guarded reads; the light
    single-zone reads still answer."""
    import backend.api_guard as guard

    c = _client(db_session)
    acquired = [guard._heavy_sem.acquire(blocking=False)
                for _ in range(guard.HEAVY_QUERY_SLOTS)]
    try:
        assert all(acquired)
        assert c.get("/api/v1/scoreboard/ranking").status_code == 503
        assert c.get("/api/v1/scoreboard/profile?zone=DE_LU&series=load").status_code == 503
        assert c.get("/api/v1/scoreboard/summary?zone=DE_LU").status_code == 200
        assert c.get("/api/v1/scoreboard/monthly?zone=DE_LU&series=load").status_code == 200
    finally:
        for ok in acquired:
            if ok:
                guard._heavy_sem.release()
