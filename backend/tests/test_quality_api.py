"""/api/v1/quality/* — the Honest-Record read API (slice A3).

Covers: summary matrix shape + hand-computed completeness/flag/revision/lag
math, the per-series drill-down (flags decoded, day cap, arrival stats), the
revision ledger (maturity filter both ways, delta_pct, per-hour roll-up),
helpful 400s on bad params, honest available:false on valid-but-empty
combinations, and the v1 guard stack (shared rate budget, heavy slots).

Posture B: every asserted field describes what the SOURCE published/restated.
Times read the UTC clock, never date.today() (repo rule #111).
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from backend.api_guard import _reset_coverage_cache
from backend.auth.ratelimit import reset_limits
from backend.database import get_db
from backend.main import app
from backend.models.energy import IngestArrival, PowerRevision, QualityDaily
from backend.power.hourly_store import resolve_series_id, resolve_zone_id, upsert_hourly

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
    _reset_coverage_cache()  # keyed cache is process-global — quality_summary would leak
    yield
    app.dependency_overrides.clear()
    reset_limits()
    _reset_coverage_cache()


def _client(db) -> TestClient:
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


def _day(days_ago: int) -> str:
    return (NOW.date() - timedelta(days=days_ago)).isoformat()


def _q(db, zone, series_key, day, present, expected, flags=None):
    db.add(QualityDaily(zone=zone, series_key=series_key, date=day,
                        hours_present=present, hours_expected=expected,
                        flags=json.dumps(flags or [])))


def _ids(db, series: str, zone: str) -> tuple[int, int]:
    sid, zid = resolve_series_id(db, series), resolve_zone_id(db, zone)
    db.commit()
    return sid, zid


FLAG = {"rule": "zero_run", "hours": [], "detail": {"longest_run_hours": 6}}


# ─── /summary ─────────────────────────────────────────────────────────────────


def test_summary_completeness_and_flag_math(db_session):
    # 30d window: 24/24 and 12/24 → 0.75; 90d adds a 24/24 day at d60 → 0.8333
    _q(db_session, "DE_LU", "load.actual", _day(1), 24, 24)
    _q(db_session, "DE_LU", "load.actual", _day(2), 12, 24, flags=[FLAG])
    _q(db_session, "DE_LU", "load.actual", _day(60), 24, 24)
    _q(db_session, "DE_LU", "load.actual", _day(100), 0, 24)  # outside 90d — ignored
    db_session.commit()

    body = _client(db_session).get("/api/v1/quality/summary").json()
    assert body["available"] is True
    assert [z["zone"] for z in body["zones"]] == ["DE_LU"]
    (cell,) = body["zones"][0]["series"]  # only the carried series appears
    assert cell["series_key"] == "load.actual"
    assert cell["completeness_30d"] == pytest.approx(0.75)
    assert cell["completeness_90d"] == pytest.approx(0.8333, abs=1e-4)
    assert cell["flagged_days_30d"] == 1
    assert cell["revisions_30d"] == 0
    # freshness triple, stamped per request: newest quality day is yesterday
    assert body["as_of"] == _day(1)
    assert body["age_days"] == 1
    assert body["stale"] is False


def test_summary_revision_count_and_arrival_lag(db_session):
    _q(db_session, "DE_LU", "load.actual", _day(1), 24, 24)
    sid, zid = _ids(db_session, "load.actual", "DE_LU")
    ts = NOW_S // _H * _H - 5 * _D
    # 3 revisions inside 30d, 1 outside — only the 3 count
    for i, obs in enumerate((NOW_S - _D, NOW_S - 2 * _D, NOW_S - 3 * _D, NOW_S - 31 * _D)):
        db_session.add(PowerRevision(series_id=sid, zone_id=zid, ts_utc=ts + i * _H,
                                     old_value=100.0, new_value=200.0, observed_at=obs))
    # newest arrival brought nothing new (no frontier) — the lag must come from
    # the newest arrival that DID bring new hours: lag exactly 1h
    db_session.add(IngestArrival(series_id=sid, zone_id=zid, observed_at=NOW_S - 50,
                                 n_new=0, n_changed=0, min_ts_new=None, max_ts_new=None))
    db_session.add(IngestArrival(series_id=sid, zone_id=zid, observed_at=NOW_S - 100,
                                 n_new=4, n_changed=0, min_ts_new=ts,
                                 max_ts_new=NOW_S - 100 - _H))
    db_session.commit()

    (cell,) = _client(db_session).get("/api/v1/quality/summary").json()["zones"][0]["series"]
    assert cell["revisions_30d"] == 3
    assert cell["arrival_lag_s"] == _H


def test_summary_zone_flag_cell_carries_no_completeness(db_session):
    _q(db_session, "DE_LU", "_zone", _day(1), 0, 0,
       flags=[{"rule": "gen_below_load_exports", "hours": [], "detail": {}}])
    db_session.commit()
    (cell,) = _client(db_session).get("/api/v1/quality/summary").json()["zones"][0]["series"]
    assert cell["series_key"] == "_zone"
    assert cell["completeness_30d"] is None and cell["completeness_90d"] is None
    assert cell["flagged_days_30d"] == 1
    assert cell["revisions_30d"] is None and cell["arrival_lag_s"] is None


def test_summary_empty_is_honest(db_session):
    body = _client(db_session).get("/api/v1/quality/summary").json()
    assert body["available"] is False
    assert body["zones"] == []
    assert body["as_of"] is None and body["stale"] is False  # inert, not a crash


def test_summary_payload_select_budget(db_session):
    """The matrix must stay a fixed handful of queries — never O(cells) point
    lookups (test_bulk_uses_fixed_query_count pattern). Budget: quality scan,
    two dim reads, revision GROUP BY, frontier join, max-date + headroom."""
    from sqlalchemy import event

    import backend.routes.quality as q

    for zone in ("DE_LU", "FR"):
        for series in ("load.actual", "gen.B16"):
            _q(db_session, zone, series, _day(1), 24, 24)
            sid, zid = _ids(db_session, series, zone)
            db_session.add(IngestArrival(series_id=sid, zone_id=zid, observed_at=NOW_S - 100,
                                         n_new=1, n_changed=0, min_ts_new=NOW_S - 100 - _H,
                                         max_ts_new=NOW_S - 100 - _H))
            db_session.add(PowerRevision(series_id=sid, zone_id=zid,
                                         ts_utc=NOW_S // _H * _H - 5 * _D,
                                         old_value=1.0, new_value=3.0, observed_at=NOW_S - 50))
    db_session.commit()

    statements = []

    def _count(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", _count)
    try:
        payload = q._summary_payload(db_session)
    finally:
        event.remove(engine, "before_cursor_execute", _count)
    assert len(payload["zones"]) == 2  # the seed actually exercised the matrix
    assert len(statements) <= 8, f"{len(statements)} SELECTs — summary regressed to per-cell reads"


def test_summary_is_cached_and_stamped_per_request(db_session, monkeypatch):
    """The matrix computes once per TTL; the freshness triple must ride each
    REQUEST (a warm cache must not freeze age_days — marginal/overview rule)."""
    import backend.routes.quality as q

    _q(db_session, "DE_LU", "load.actual", _day(1), 24, 24)
    db_session.commit()
    calls = {"n": 0}
    orig = q._summary_payload

    def counting(db):
        calls["n"] += 1
        return orig(db)

    monkeypatch.setattr(q, "_summary_payload", counting)
    c = _client(db_session)
    first = c.get("/api/v1/quality/summary").json()
    second = c.get("/api/v1/quality/summary").json()
    assert calls["n"] == 1  # second hit served from the keyed cache
    assert first["zones"] == second["zones"]
    assert "age_days" in second and "stale" in second


# ─── /series ──────────────────────────────────────────────────────────────────


def test_series_rows_newest_first_flags_decoded(db_session):
    from backend.power.hourly_store import day_hour_ts

    flagged_hour = day_hour_ts(_day(2), 23)
    _q(db_session, "DE_LU", "load.actual", _day(2), 18, 24,
       flags=[{"rule": "zero_run", "hours": [flagged_hour], "detail": {"longest_run_hours": 6}}])
    _q(db_session, "DE_LU", "load.actual", _day(1), 24, 24)
    db_session.commit()

    body = _client(db_session).get(
        "/api/v1/quality/series?series=load.actual&zone=DE_LU").json()
    assert body["available"] is True
    assert [r["date"] for r in body["data"]] == [_day(1), _day(2)]  # newest first
    flag = body["data"][1]["flags"][0]
    assert flag["rule"] == "zero_run"
    assert flag["hours"] == [f"{_day(2)}T23:00:00+00:00"]  # epoch → ISO at the edge
    assert flag["detail"] == {"longest_run_hours": 6}
    assert body["as_of"] == _day(1)


def test_series_corrupt_flags_row_degrades_visibly(db_session):
    """A row whose flags JSON won't parse yields a `_decode_error` flag for THAT
    row — never a 500 that hides the healthy rows around it."""
    db_session.add(QualityDaily(zone="DE_LU", series_key="load.actual", date=_day(2),
                                hours_present=24, hours_expected=24, flags="{not json"))
    _q(db_session, "DE_LU", "load.actual", _day(1), 24, 24)
    db_session.commit()
    body = _client(db_session).get(
        "/api/v1/quality/series?series=load.actual&zone=DE_LU").json()
    assert len(body["data"]) == 2  # the healthy row still served
    assert body["data"][1]["flags"] == [{"rule": "_decode_error", "hours": [], "detail": {}}]


def test_series_day_window_filters_and_caps(db_session):
    _q(db_session, "DE_LU", "load.actual", _day(10), 24, 24)
    _q(db_session, "DE_LU", "load.actual", _day(100), 24, 24)
    db_session.commit()
    c = _client(db_session)
    body = c.get("/api/v1/quality/series?series=load.actual&zone=DE_LU&days=30").json()
    assert [r["date"] for r in body["data"]] == [_day(10)]
    # cap: days > 365 is a validation error, not a silent clamp
    assert c.get("/api/v1/quality/series?series=load.actual&zone=DE_LU&days=400").status_code == 422


def test_series_arrival_median_and_p90(db_session):
    _q(db_session, "DE_LU", "load.actual", _day(1), 24, 24)
    sid, zid = _ids(db_session, "load.actual", "DE_LU")
    for i, lag in enumerate((_H, 2 * _H, 3 * _H)):
        obs = NOW_S - 1000 - i
        db_session.add(IngestArrival(series_id=sid, zone_id=zid, observed_at=obs,
                                     n_new=1, n_changed=0,
                                     min_ts_new=obs - lag, max_ts_new=obs - lag))
    db_session.commit()
    arrival = _client(db_session).get(
        "/api/v1/quality/series?series=load.actual&zone=DE_LU").json()["arrival"]
    assert arrival["n_batches"] == 3
    assert arrival["median_lag_s"] == 2 * _H
    # p90 over [3600, 7200, 10800]: pos 1.8 → 7200 + 0.8·3600 = 10080
    assert arrival["p90_lag_s"] == 10080


def test_series_bad_params_are_helpful_400s(db_session):
    c = _client(db_session)
    r = c.get("/api/v1/quality/series?series=hack&zone=DE_LU")
    assert r.status_code == 400
    assert "load.actual" in r.json()["detail"]  # lists the valid keys
    r = c.get("/api/v1/quality/series?series=load.actual&zone=NOPE")
    assert r.status_code == 400
    assert "DE_LU" in r.json()["detail"]  # lists the valid zones


def test_series_valid_but_empty_is_available_false(db_session):
    body = _client(db_session).get(
        "/api/v1/quality/series?series=load.actual&zone=FR").json()
    assert body["available"] is False
    assert body["data"] == []
    assert body["arrival"] == {"n_batches": 0, "median_lag_s": None, "p90_lag_s": None}


# ─── /revisions ───────────────────────────────────────────────────────────────


def test_revisions_maturity_filter_both_ways(db_session):
    sid, zid = _ids(db_session, "load.actual", "DE_LU")
    settled = NOW_S // _H * _H - 5 * _D  # restated 5 days after the hour → mature
    fresh = NOW_S // _H * _H - _H       # restated ~1h after the hour → fill-in
    db_session.add(PowerRevision(series_id=sid, zone_id=zid, ts_utc=settled,
                                 old_value=100.0, new_value=120.0, observed_at=NOW_S - 100))
    db_session.add(PowerRevision(series_id=sid, zone_id=zid, ts_utc=fresh,
                                 old_value=50.0, new_value=60.0, observed_at=NOW_S - 50))
    db_session.commit()
    c = _client(db_session)

    body = c.get("/api/v1/quality/revisions?series=load.actual&zone=DE_LU").json()
    assert body["mature"] is True and body["maturity_threshold_s"] == 48 * 3600
    assert body["count"] == 1
    row = body["data"][0]
    assert row["old_value"] == 100.0 and row["new_value"] == 120.0
    assert row["delta_pct"] == pytest.approx(20.0)
    assert row["ts_utc"] == datetime.fromtimestamp(settled, UTC).isoformat()

    body = c.get("/api/v1/quality/revisions?series=load.actual&zone=DE_LU&mature=false").json()
    assert body["count"] == 2  # fill-in included on request


def test_revisions_rollup_counts_multiply_restated_hours(db_session):
    sid, zid = _ids(db_session, "load.actual", "DE_LU")
    hour = NOW_S // _H * _H - 5 * _D
    other = hour + _H
    db_session.add(PowerRevision(series_id=sid, zone_id=zid, ts_utc=hour,
                                 old_value=100.0, new_value=110.0, observed_at=NOW_S - 2000))
    db_session.add(PowerRevision(series_id=sid, zone_id=zid, ts_utc=hour,
                                 old_value=110.0, new_value=130.0, observed_at=NOW_S - 1000))
    db_session.add(PowerRevision(series_id=sid, zone_id=zid, ts_utc=other,
                                 old_value=200.0, new_value=250.0, observed_at=NOW_S - 1500))
    db_session.commit()

    body = _client(db_session).get(
        "/api/v1/quality/revisions?series=load.actual&zone=DE_LU").json()
    assert body["count"] == 3
    (roll,) = body["restated_hours"]  # only the twice-restated hour rolls up
    assert roll["ts_utc"] == datetime.fromtimestamp(hour, UTC).isoformat()
    assert roll["n_revisions"] == 2
    # last change: 110 → 130 = +18.18% of the previously published value
    assert roll["last_change_pct"] == pytest.approx(18.18, abs=0.01)


def test_revisions_delta_pct_null_when_old_value_zero(db_session):
    sid, zid = _ids(db_session, "gen.B16", "DE_LU")
    db_session.add(PowerRevision(series_id=sid, zone_id=zid,
                                 ts_utc=NOW_S // _H * _H - 5 * _D,
                                 old_value=0.0, new_value=5.0, observed_at=NOW_S - 10))
    db_session.commit()
    body = _client(db_session).get(
        "/api/v1/quality/revisions?series=gen.B16&zone=DE_LU").json()
    assert body["data"][0]["delta_pct"] is None  # no honest % of a zero base


def test_revisions_ride_the_real_write_path(db_session):
    """upsert_hourly twice with a moved value → the ledger row the endpoint
    serves, mature because the hour lies 5 days back."""
    ts = NOW_S // _H * _H - 5 * _D
    upsert_hourly(db_session, "load.actual", "DE_LU", [(ts, 100.0)], unit="MW")
    upsert_hourly(db_session, "load.actual", "DE_LU", [(ts, 200.0)], unit="MW")

    body = _client(db_session).get(
        "/api/v1/quality/revisions?series=load.actual&zone=DE_LU").json()
    assert body["available"] is True and body["count"] == 1
    assert body["data"][0]["delta_pct"] == pytest.approx(100.0)
    assert body["as_of"] is not None  # arrival log = last time the source was polled
    assert body["age_days"] == 0 and body["stale"] is False


def test_revisions_bad_params_are_helpful_400s(db_session):
    c = _client(db_session)
    r = c.get("/api/v1/quality/revisions?series=hack&zone=DE_LU")
    assert r.status_code == 400
    assert "series/catalog" in r.json()["detail"]  # points at the queryable keys
    r = c.get("/api/v1/quality/revisions?series=hack&zone=NOPE")
    assert r.status_code == 400
    assert "DE_LU" in r.json()["detail"]


def test_revisions_derived_series_get_an_honest_reason(db_session):
    body = _client(db_session).get(
        "/api/v1/quality/revisions?series=residual.actual&zone=DE_LU").json()
    assert body["available"] is False
    assert "not revision-ledgered" in body["reason"]


def test_revisions_row_cap_refuses_instead_of_truncating(db_session, monkeypatch):
    """More rows than the cap → available:false with a narrow-the-window reason
    (a truncated ledger is a wrong ledger), never a silent cut."""
    import backend.routes.quality as q

    monkeypatch.setattr(q, "MAX_REVISION_ROWS", 2)
    sid, zid = _ids(db_session, "load.actual", "DE_LU")
    hour = NOW_S // _H * _H - 5 * _D
    for i in range(3):
        db_session.add(PowerRevision(series_id=sid, zone_id=zid, ts_utc=hour + i * _H,
                                     old_value=100.0, new_value=120.0,
                                     observed_at=NOW_S - 100 - i))
    db_session.commit()
    body = _client(db_session).get(
        "/api/v1/quality/revisions?series=load.actual&zone=DE_LU").json()
    assert body["available"] is False
    assert "narrow" in body["reason"]
    assert "stale" in body  # the triple rides even the refusal


def test_revisions_valid_but_empty_is_available_false(db_session):
    upsert_hourly(db_session, "load.actual", "DE_LU",
                  [(NOW_S // _H * _H - 5 * _D, 100.0)], unit="MW")  # series known, no restatement
    body = _client(db_session).get(
        "/api/v1/quality/revisions?series=load.actual&zone=DE_LU").json()
    assert body["available"] is False
    assert body["data"] == [] and body["restated_hours"] == []
    assert "reason" in body
    assert body["as_of"] is not None  # the source WAS polled — that much is on record


# ─── guard stack ──────────────────────────────────────────────────────────────


def test_quality_shares_the_v1_rate_budget(db_session, monkeypatch):
    """All three endpoints draw from the same per-IP v1 bucket as /series."""
    import backend.routes.api_v1 as v1

    monkeypatch.setattr(v1, "RATE_PER_MIN", 2)
    c = _client(db_session)
    assert c.get("/api/v1/quality/summary").status_code == 200
    assert c.get("/api/v1/quality/series?series=load.actual&zone=DE_LU").status_code == 200
    assert c.get("/api/v1/quality/revisions?series=x&zone=DE_LU").status_code == 429


def test_summary_and_revisions_hold_a_heavy_slot(db_session):
    """Drained semaphore → fail-fast 503 for the guarded reads; the light
    per-series drill-down still answers."""
    import backend.api_guard as guard

    upsert_hourly(db_session, "load.actual", "DE_LU", [(NOW_S // _H * _H, 1.0)], unit="MW")
    c = _client(db_session)
    acquired = [guard._heavy_sem.acquire(blocking=False)
                for _ in range(guard.HEAVY_QUERY_SLOTS)]
    try:
        assert all(acquired)
        assert c.get("/api/v1/quality/summary").status_code == 503
        assert c.get("/api/v1/quality/revisions?series=load.actual&zone=DE_LU").status_code == 503
        assert c.get("/api/v1/quality/series?series=load.actual&zone=DE_LU").status_code == 200
    finally:
        for ok in acquired:
            if ok:
                guard._heavy_sem.release()
