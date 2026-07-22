"""GET /api/power/live — the near-real-time read path for TODAY.

The situation hero and daily panels read only the DAILY rollup tables, which
exclude the running (incomplete) day — so the desk never shows today until
the nightly job closes it out. This is the missing read path: it reads the
canonical hourly store (backend/power/hourly_store.py) directly, joining
today's published actuals against the published day-ahead forecast/price for
the SAME hours. Descriptive (Posture B): actuals are compared against what
ENTSO-E/the auction already published, never predicted.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from backend.database import get_db
from backend.main import app
from backend.models.energy import PowerHourly, SeriesDim, ZoneDim  # noqa: F401 — register tables
from backend.power.hourly_store import upsert_hourly
from backend.power.live import compute_live
from backend.power.zones import DEFAULT_ZONE

H = 3600


def _day_start(dt: datetime) -> int:
    return int(dt.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())


def _client(db) -> TestClient:
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


# ─── mid-day: today is partially published ────────────────────────────────────


def _seed_midday(db, day_start: int, zone: str = "DE_LU"):
    # Actuals published through hour 12 only (13:45 read, ~1h ENTSO-E lag).
    upsert_hourly(db, "load.actual", zone,
                  [(day_start + h * H, 50_000.0 + h * 100) for h in range(13)], unit="MW")
    upsert_hourly(db, "residual.actual", zone,
                  [(day_start + h * H, 30_000.0 + h * 50) for h in range(13)], unit="MW")
    # Forecast + price published for the whole day (day-ahead auction already ran).
    upsert_hourly(db, "load.forecast", zone,
                  [(day_start + h * H, 49_500.0 + h * 100) for h in range(24)], unit="MW")
    upsert_hourly(db, "residual.forecast", zone,
                  [(day_start + h * H, 29_500.0 + h * 50) for h in range(24)], unit="MW")
    upsert_hourly(db, "price.dayahead", zone,
                  [(day_start + h * H, 80.0 + h) for h in range(24)], unit="EUR/MWh")
    # Solar only in daylight hours (6-12); wind onshore all morning (0-12) —
    # exercises "gen omits fuels with no data that hour".
    upsert_hourly(db, "gen.B16", zone,
                  [(day_start + h * H, 200.0 + h * 10) for h in range(6, 13)], unit="MW")
    upsert_hourly(db, "gen.B19", zone,
                  [(day_start + h * H, 1_000.0 + h * 5) for h in range(13)], unit="MW")


def test_midday_shows_today_with_actuals_stopping_at_the_published_hour(db_session):
    now = datetime(2026, 7, 20, 13, 45, tzinfo=timezone.utc)
    day_start = _day_start(now)
    _seed_midday(db_session, day_start)

    out = compute_live(db_session, "DE_LU", now=now)

    assert out["available"] is True
    assert out["zone"] == "DE_LU"
    assert out["zone_label"] == "DE-LU"
    assert out["showing"] == "today"
    assert len(out["hours"]) == 24

    h12, h13 = out["hours"][12], out["hours"][13]
    assert h12["load"] == pytest.approx(51_200.0)
    assert h13["load"] is None, "no published actual yet for hour 13"
    assert h13["load_fc"] == pytest.approx(50_800.0), "forecast covers the whole day"
    assert h13["price"] == pytest.approx(93.0)

    expected_ts0 = datetime.fromtimestamp(day_start, tz=timezone.utc).isoformat()
    assert out["hours"][0]["ts_utc"] == expected_ts0


def test_midday_gen_dict_omits_fuels_absent_that_hour(db_session):
    now = datetime(2026, 7, 20, 13, 45, tzinfo=timezone.utc)
    day_start = _day_start(now)
    _seed_midday(db_session, day_start)

    out = compute_live(db_session, "DE_LU", now=now)

    h3 = out["hours"][3]
    assert h3["gen"] == {"B19": pytest.approx(1_015.0)}, "solar hasn't started yet at 03:00"

    h8 = out["hours"][8]
    assert h8["gen"]["B16"] == pytest.approx(280.0)
    assert h8["gen"]["B19"] == pytest.approx(1_040.0)


def test_midday_latest_actual_and_lag(db_session):
    now = datetime(2026, 7, 20, 13, 45, tzinfo=timezone.utc)
    day_start = _day_start(now)
    _seed_midday(db_session, day_start)

    out = compute_live(db_session, "DE_LU", now=now)

    expected_latest = datetime.fromtimestamp(day_start + 12 * H, tz=timezone.utc).isoformat()
    assert out["latest_actual_ts"] == expected_latest
    # hour 12 (12:00-13:00) ended at 13:00; read at 13:45 -> 45 minutes of lag.
    assert out["lag_minutes"] == 45


def test_midday_summary(db_session):
    now = datetime(2026, 7, 20, 13, 45, tzinfo=timezone.utc)
    day_start = _day_start(now)
    _seed_midday(db_session, day_start)

    out = compute_live(db_session, "DE_LU", now=now)
    summary = out["summary"]

    # (51200 - 50700) / 50700 * 100 = 0.9861... -> rounded to 2dp
    assert summary["load_vs_forecast_pct"] == pytest.approx(0.99, abs=1e-9)
    # B16(h12)=320 + B19(h12)=1060
    assert summary["gen_total_now"] == pytest.approx(1_380.0)
    # now=13:45 falls in hour 13 (13:00-14:00): price = 80 + 13
    assert summary["price_now"] == pytest.approx(93.0)


def test_note_is_descriptive_not_predictive(db_session):
    now = datetime(2026, 7, 20, 13, 45, tzinfo=timezone.utc)
    day_start = _day_start(now)
    _seed_midday(db_session, day_start)

    out = compute_live(db_session, "DE_LU", now=now)
    note = out["note"].lower()
    assert "lag" in note or "publish" in note
    assert "never predict" in note, "must explicitly disclaim any forecast of its own"


# ─── just after midnight: today has no actuals yet → yesterday fallback ───────


def test_just_after_midnight_falls_back_to_yesterday(db_session):
    now = datetime(2026, 7, 21, 0, 15, tzinfo=timezone.utc)
    yesterday_start = _day_start(now) - 24 * H

    upsert_hourly(db_session, "load.actual", "DE_LU",
                  [(yesterday_start + h * H, 50_000.0 + h * 10) for h in range(24)], unit="MW")
    upsert_hourly(db_session, "load.forecast", "DE_LU",
                  [(yesterday_start + h * H, 49_000.0 + h * 10) for h in range(24)], unit="MW")
    upsert_hourly(db_session, "price.dayahead", "DE_LU",
                  [(yesterday_start + h * H, 70.0 + h) for h in range(24)], unit="EUR/MWh")

    out = compute_live(db_session, "DE_LU", now=now)

    assert out["available"] is True
    assert out["showing"] == "yesterday"
    assert len(out["hours"]) == 24
    expected_ts0 = datetime.fromtimestamp(yesterday_start, tz=timezone.utc).isoformat()
    assert out["hours"][0]["ts_utc"] == expected_ts0

    expected_latest = datetime.fromtimestamp(yesterday_start + 23 * H, tz=timezone.utc).isoformat()
    assert out["latest_actual_ts"] == expected_latest
    # hour 23 (23:00-24:00 yesterday) ended exactly at today's midnight; now is 00:15.
    assert out["lag_minutes"] == 15
    # `now` (00:15 today) falls outside the shown (yesterday's) window, whose
    # price series was never loaded — price_now is null until today's first
    # actual lands and the endpoint switches back to showing="today".
    assert out["summary"]["price_now"] is None


def test_lag_minutes_never_negative_for_a_partial_current_hour(db_session):
    """parse_load_hourly (backend/power/entsoe_grid.py) averages whatever
    quarter-hours ENTSO-E has published for an hour with no completeness
    guard, so an in-progress hour (e.g. 2 of 4 quarter-hours in) can already
    land a load.actual point. That makes `latest_actual_ts + 1h` land in the
    future relative to `now` — lag must clamp to 0, never go negative."""
    now = datetime(2026, 7, 20, 13, 20, tzinfo=timezone.utc)
    day_start = _day_start(now)
    current_hour_ts = day_start + 13 * H  # 13:00-14:00, still in progress at 13:20
    upsert_hourly(db_session, "load.actual", "DE_LU",
                  [(current_hour_ts, 45_000.0)], unit="MW")

    out = compute_live(db_session, "DE_LU", now=now)

    expected_latest = datetime.fromtimestamp(current_hour_ts, tz=timezone.utc).isoformat()
    assert out["latest_actual_ts"] == expected_latest
    assert out["lag_minutes"] == 0


def test_no_data_at_all_is_unavailable_not_an_error(db_session):
    now = datetime(2026, 7, 21, 0, 15, tzinfo=timezone.utc)

    out = compute_live(db_session, "DE_LU", now=now)

    assert out["available"] is False
    assert out["zone"] == "DE_LU"
    assert "reason" in out


# ─── missing forecast series entirely ──────────────────────────────────────────


def test_missing_forecast_series_leaves_forecast_fields_null(db_session):
    now = datetime(2026, 7, 20, 13, 45, tzinfo=timezone.utc)
    day_start = _day_start(now)
    upsert_hourly(db_session, "load.actual", "DE_LU",
                  [(day_start + h * H, 50_000.0 + h * 100) for h in range(13)], unit="MW")
    upsert_hourly(db_session, "price.dayahead", "DE_LU",
                  [(day_start + h * H, 80.0 + h) for h in range(24)], unit="EUR/MWh")

    out = compute_live(db_session, "DE_LU", now=now)

    assert out["available"] is True
    assert all(h["load_fc"] is None for h in out["hours"])
    assert all(h["residual_fc"] is None for h in out["hours"])
    assert all(h["residual"] is None for h in out["hours"])
    assert all(h["gen_fc"] is None for h in out["hours"]), "generation.forecast wasn't seeded either"
    assert out["summary"]["load_vs_forecast_pct"] is None, "no forecast to compare against"


# ─── flow sign convention: zone may be the storing side OR the counterparty ───


def test_flow_native_sign_for_the_storing_zone(db_session):
    """Border DE_LU<->FR is canonically stored as series flow.FR under zone
    DE_LU (DE_LU sorts first); positive = DE_LU exports. Querying DE_LU itself
    reads the native sign."""
    now = datetime(2026, 7, 20, 13, 45, tzinfo=timezone.utc)
    day_start = _day_start(now)
    upsert_hourly(db_session, "load.actual", "DE_LU",
                  [(day_start + h * H, 50_000.0) for h in range(13)], unit="MW")
    upsert_hourly(db_session, "flow.FR", "DE_LU",
                  [(day_start + 12 * H, 500.0)], unit="MW")

    out = compute_live(db_session, "DE_LU", now=now)
    assert out["hours"][12]["net_flow"] == pytest.approx(500.0)


def test_flow_sign_flips_for_the_counterparty_zone(db_session):
    """Same stored row (DE_LU exports 500 MW to FR) queried from FR's side must
    flip sign: FR is net IMPORTING, i.e. its own export figure is negative."""
    now = datetime(2026, 7, 20, 13, 45, tzinfo=timezone.utc)
    day_start = _day_start(now)
    upsert_hourly(db_session, "load.actual", "FR",
                  [(day_start + h * H, 40_000.0) for h in range(13)], unit="MW")
    upsert_hourly(db_session, "flow.FR", "DE_LU",
                  [(day_start + 12 * H, 500.0)], unit="MW")

    out = compute_live(db_session, "FR", now=now)
    assert out["hours"][12]["net_flow"] == pytest.approx(-500.0)


def test_zone_with_no_flow_series_gets_null_not_zero(db_session):
    now = datetime(2026, 7, 20, 13, 45, tzinfo=timezone.utc)
    day_start = _day_start(now)
    upsert_hourly(db_session, "load.actual", "NL",
                  [(day_start + h * H, 20_000.0) for h in range(13)], unit="MW")
    # A border exists elsewhere in the store, but never touches NL.
    upsert_hourly(db_session, "flow.FR", "DE_LU",
                  [(day_start + 12 * H, 500.0)], unit="MW")

    out = compute_live(db_session, "NL", now=now)
    assert all(h["net_flow"] is None for h in out["hours"])


# ─── unknown zone ───────────────────────────────────────────────────────────────


def test_compute_live_unknown_zone_is_structurally_unavailable(db_session):
    out = compute_live(db_session, "NOT_A_ZONE")
    assert out["available"] is False
    assert "reason" in out


def test_route_unknown_zone_mirrors_get_imbalance_fallback(db_session):
    client = _client(db_session)

    live_resp = client.get("/api/power/live?zone=NOT_A_ZONE").json()
    imbalance_resp = client.get("/api/power/imbalance?zone=NOT_A_ZONE").json()

    # Both endpoints resolve an unknown zone the SAME way: silent fallback to
    # DEFAULT_ZONE (never a loud 400, never available:false framed as "unknown
    # zone" — see backend/routes/power.py::_resolve_zone).
    assert live_resp["zone"] == DEFAULT_ZONE
    assert imbalance_resp["zone"] == DEFAULT_ZONE
    # Every sibling endpoint tells the caller what zones ARE valid alongside the
    # silent fallback (that's the justification for not 400ing) — /live must too.
    assert set(live_resp["zones"]) == {"DE_LU", "FR", "NL"}


def test_route_unavailable_shape_also_carries_zones(db_session):
    """The unavailable branch (no data at all) must carry `zones` too — not
    just the happy path. compute_live itself stays pure/zones-agnostic; the
    route wrapper attaches it either way."""
    resp = _client(db_session).get("/api/power/live?zone=DE_LU")
    body = resp.json()

    assert body["available"] is False
    assert set(body["zones"]) == {"DE_LU", "FR", "NL"}


# ─── HTTP route: freshness fields ──────────────────────────────────────────────


def test_route_carries_freshness_fields(db_session):
    now = datetime.now(timezone.utc)
    day_start = _day_start(now)
    upsert_hourly(db_session, "load.actual", "DE_LU",
                  [(day_start, 50_000.0)], unit="MW")
    upsert_hourly(db_session, "price.dayahead", "DE_LU",
                  [(day_start + h * H, 80.0 + h) for h in range(24)], unit="EUR/MWh")

    resp = _client(db_session).get("/api/power/live?zone=DE_LU")
    assert resp.status_code == 200
    body = resp.json()

    assert body["available"] is True
    assert "as_of" in body and body["as_of"] is not None
    assert "age_days" in body
    assert "stale" in body
    assert body["stale"] is False
    assert set(body["zones"]) == {"DE_LU", "FR", "NL"}


def test_route_fallback_freshness_is_one_day_old_not_stale(db_session):
    """Real-clock yesterday-fallback via HTTP: seed ONLY yesterday's (relative
    to whatever wall-clock time the suite runs at) load.actual + price, so
    compute_live falls back regardless of what hour it actually is right now.
    as_of then lands on yesterday's calendar date, so age_days is exactly 1 —
    the boundary case for LIVE_MAX_AGE_DAYS=1 (not stale, not fresh-looking
    either)."""
    real_today_start = _day_start(datetime.now(timezone.utc))
    yesterday_start = real_today_start - 24 * H

    upsert_hourly(db_session, "load.actual", "DE_LU",
                  [(yesterday_start + h * H, 50_000.0 + h * 10) for h in range(24)], unit="MW")
    upsert_hourly(db_session, "price.dayahead", "DE_LU",
                  [(yesterday_start + h * H, 70.0 + h) for h in range(24)], unit="EUR/MWh")

    body = _client(db_session).get("/api/power/live?zone=DE_LU").json()

    assert body["available"] is True
    assert body["showing"] == "yesterday"
    assert body["age_days"] == 1
    assert body["stale"] is False


def test_route_max_age_matches_live_load_freshness_spec():
    """UI/route freshness threshold and the health-check spec must share one
    truth — mirrors test_power_panel_freshness.py's
    test_panel_thresholds_match_health_specs for PANEL_MAX_AGE_DAYS vs SPECS."""
    from backend.collectors.freshness import SPECS
    from backend.routes.power import LIVE_MAX_AGE_DAYS

    spec = next(s for s in SPECS if s.key == "live_load")
    assert spec.max_age.days == LIVE_MAX_AGE_DAYS
