"""A73 per-unit generation: parse, transport semantics, ingest, and the two routes.

The probe/smoke-anchored facts worth pinning (2026-07-28):

* **curveType is A03** (smoke, live documents: 151/151 TS) — a step function. A
  point publishes only where the value changes and HOLDS to the next one; the last
  holds to the Period's own end. A sequential read showed 85 units on chunk-start
  days and 5 at the frontier (every TS has a position 1). The hold-forward and
  gap tests below are the regression guard for that bug.
* A73's "no data" is a **200-ACK, not a 400** — the still-filling frontier answers
  HTTP 200 carrying an Acknowledgement document, which must be cached as genuine
  emptiness; any >= 400 must raise and cache nothing (a parameter bug must never
  become permanent emptiness on disk). Inverse of the A61/A09 shape.
* A consumption TimeSeries (outBiddingZone_Domain — pumped-storage pumping) must
  be EXCLUDED or pumping counts as generation. The smoke found none in the live
  German documents; the guard is defensive (the A75 precedent proves the shape
  exists) and these tests keep it honest.
* Multiple TimeSeries cover one unit (68 TS / 35 units at Amprion): per-TS hourly
  means are averaged per unit-hour, so a PT60M and a PT15M series weigh equally
  (mean-of-means, not a raw-point average that weights one series 4:1).
* Control areas publish at DIFFERENT lags (smoke: TenneT D-2, the rest D-5) — the
  board must list a unit whose TSO has not reached the frontier yet as
  "not reporting" (null), never drop it.
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from backend.models.energy import PowerOutage, ProductionUnit, UnitGeneration
from backend.power.entsoe_unit_generation import (
    ingest_unit_generation,
    parse_unit_generation,
    upsert_unit_generation,
)

CTA_50HERTZ = "10YDE-VE-------2"
DAY = "2026-07-20T00:00Z"
DAY_EPOCH = int(datetime(2026, 7, 20, tzinfo=timezone.utc).timestamp())

NS = "urn:iec62325.351:tc57wg16:451-6:generationloaddocument:3:0"


def _ts_block(unit: str, points, *, resolution="PT60M", start=DAY,
              end="2026-07-20T01:00Z", direction="in", psr="B04") -> str:
    """One TimeSeries. The default Period end is ONE hour after start — under the
    A03 hold-forward semantics an end further out would extend the last point
    across the gap, so tests that mean 'exactly these slots' keep the end tight
    and the step tests choose their spans explicitly."""
    dom = ("inBiddingZone_Domain.mRID" if direction == "in"
           else "outBiddingZone_Domain.mRID")
    pts = "".join(
        f"<Point><position>{pos}</position><quantity>{qty}</quantity></Point>"
        for pos, qty in points
    )
    return f"""<TimeSeries>
      <registeredResource.mRID>{unit}</registeredResource.mRID>
      <{dom}>{CTA_50HERTZ}</{dom}>
      <MktPSRType><psrType>{psr}</psrType></MktPSRType>
      <Period>
        <timeInterval><start>{start}</start><end>{end}</end></timeInterval>
        <resolution>{resolution}</resolution>
        {pts}
      </Period>
    </TimeSeries>"""


def _doc(*ts_blocks: str) -> str:
    return (f'<?xml version="1.0" encoding="UTF-8"?>'
            f'<GL_MarketDocument xmlns="{NS}">{"".join(ts_blocks)}</GL_MarketDocument>')


# ─── parse ────────────────────────────────────────────────────────────────────


def test_parse_pt60m_lands_on_the_hour_grid():
    out = parse_unit_generation(_doc(
        _ts_block("U60", [(1, 500.0), (2, 640.0)], end="2026-07-20T02:00Z")))
    assert out == {"U60": {DAY_EPOCH: 500.0, DAY_EPOCH + 3600: 640.0}}


def test_parse_expands_the_a03_step_function():
    """THE bug the smoke caught: curveType A03 publishes a point only where the
    value changes. One point + a 4-hour Period is four hours of that value —
    the sequential read that kept only position 1 produced a board where most
    of the timeline (and most units, beyond chunk-start days) vanished."""
    out = parse_unit_generation(_doc(
        _ts_block("UC", [(1, 500.0)], end="2026-07-20T04:00Z")))
    assert out == {"UC": {DAY_EPOCH: 500.0, DAY_EPOCH + 3600: 500.0,
                          DAY_EPOCH + 7200: 500.0, DAY_EPOCH + 10800: 500.0}}


def test_parse_holds_across_position_gaps():
    """27 of 38 live 50Hertz TS have position gaps: position 1 holds until the
    published change, the change holds to the Period end."""
    out = parse_unit_generation(_doc(
        _ts_block("UG", [(1, 100.0), (3, 300.0)], end="2026-07-20T04:00Z")))
    assert out == {"UG": {DAY_EPOCH: 100.0, DAY_EPOCH + 3600: 100.0,
                          DAY_EPOCH + 7200: 300.0, DAY_EPOCH + 10800: 300.0}}


def test_parse_the_period_end_bounds_the_hold():
    """The Period end is the TSO's own published data end (mid-window on the
    frontier) — the hold must stop THERE, never extend to the requested window."""
    out = parse_unit_generation(_doc(
        _ts_block("UE", [(1, 200.0)], end="2026-07-20T02:00Z")))
    assert len(out["UE"]) == 2, "two hours published, not a fabricated full day"


def test_parse_pt15m_quarters_average_onto_the_hour():
    """Four quarter values become ONE hourly mean, not four rows."""
    out = parse_unit_generation(_doc(
        _ts_block("U15", [(1, 100.0), (2, 200.0), (3, 300.0), (4, 400.0)],
                  resolution="PT15M")))
    assert out == {"U15": {DAY_EPOCH: 250.0}}


def test_parse_two_generation_timeseries_for_one_unit_hour_average():
    """The probe found more TimeSeries than units (68 TS / 35 units at Amprion) —
    overlapping generation series average, the same rule every ENTSO-E parser in
    this repo follows (a sum would double-count a republished period)."""
    out = parse_unit_generation(_doc(
        _ts_block("UD", [(1, 100.0)]),
        _ts_block("UD", [(1, 300.0)]),
    ))
    assert out == {"UD": {DAY_EPOCH: 200.0}}


def test_parse_mixed_resolution_series_weigh_equally():
    """Mean of per-TS hourly means: a PT60M series (one point) and a PT15M series
    (four points) covering the same unit-hour count once each — a raw-point
    average would weight the quarter series 4:1."""
    out = parse_unit_generation(_doc(
        _ts_block("UM", [(1, 100.0)]),
        _ts_block("UM", [(1, 300.0), (2, 300.0), (3, 300.0), (4, 300.0)],
                  resolution="PT15M"),
    ))
    assert out == {"UM": {DAY_EPOCH: 200.0}}


def test_parse_excludes_the_consumption_timeseries_of_a_pumped_storage_unit():
    """A B10 unit publishes generation (inBiddingZone) AND pumping (outBiddingZone).
    Only generation may be read — summing or averaging in the consumption leg would
    book pumping as output. Mirrors the A75 discrimination in gas/entsoe.py."""
    out = parse_unit_generation(_doc(
        _ts_block("UPS", [(1, 400.0)], psr="B10", direction="in"),
        _ts_block("UPS", [(1, 999.0)], psr="B10", direction="out"),
    ))
    assert out == {"UPS": {DAY_EPOCH: 400.0}}


def test_parse_a_unit_with_only_a_consumption_timeseries_is_absent():
    out = parse_unit_generation(_doc(
        _ts_block("UPUMP", [(1, 500.0)], psr="B10", direction="out")))
    assert out == {}


def test_parse_rejects_malformed_xml():
    with pytest.raises(ValueError):
        parse_unit_generation("<GL_MarketDocument><unclosed")


# ─── transport: 200-ACK is cached emptiness, >=400 raises and never caches ────


@pytest.fixture
def stub_http(monkeypatch, tmp_path):
    """A token, an isolated raw-cache root, and a stub transport with a chosen
    status/body — the level where the 200-ACK-vs-400 discrimination lives (the
    entsoe_ntc test pattern, inverted semantics)."""
    import httpx
    from pydantic import SecretStr

    from backend.gas import raw_cache
    from backend.power import entsoe_unit_generation as ug

    monkeypatch.setattr(raw_cache, "DATA_ROOT", tmp_path)
    monkeypatch.setattr(ug.settings, "entsoe_api_token", SecretStr("test-token"))

    def _install(status: int, body: str):
        response = httpx.Response(status, text=body,
                                  request=httpx.Request("GET", "http://test"))

        class _Client:
            def __init__(self, *a, **kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def get(self, url, params=None):
                return response

        monkeypatch.setattr(ug.httpx, "AsyncClient", _Client)
        return ug

    return _install


def test_a_200_ack_is_empty_and_cached(stub_http):
    """A73's "no data" shape (probe-verified): HTTP 200 carrying an Acknowledgement
    document — the still-filling frontier answers this daily. That IS data: it must
    come back as "" and be cached so the backfill never re-asks a settled void."""
    from backend.gas import raw_cache

    ug = stub_http(200, '<Acknowledgement_MarketDocument xmlns="x">'
                        "<Reason><text>No matching data found</text></Reason>"
                        "</Acknowledgement_MarketDocument>")
    xml = asyncio.run(ug._fetch_units_window(CTA_50HERTZ, date(2026, 7, 20), date(2026, 7, 27)))

    assert xml == ""
    assert raw_cache.read_cached("entsoe_a73", f"{CTA_50HERTZ}_2026-07-20",
                                 date(2026, 7, 20)) == {"xml": ""}


def test_a_200_with_data_is_returned_and_cached(stub_http):
    from backend.gas import raw_cache

    doc = _doc(_ts_block("U60", [(1, 500.0)]))
    ug = stub_http(200, doc)
    xml = asyncio.run(ug._fetch_units_window(CTA_50HERTZ, date(2026, 7, 20), date(2026, 7, 27)))

    assert xml == doc
    assert raw_cache.read_cached("entsoe_a73", f"{CTA_50HERTZ}_2026-07-20",
                                 date(2026, 7, 20)) == {"xml": doc}


def test_any_400_raises_and_never_caches(stub_http):
    """Unlike A61/A09, a 400 here is NEVER a clean no-data answer (those arrive as
    200-ACKs) — it is a malformed request, and caching it would freeze a parameter
    bug into permanent emptiness on disk."""
    import httpx

    from backend.gas import raw_cache

    ug = stub_http(400, "<Acknowledgement_MarketDocument>No matching data found"
                        "</Acknowledgement_MarketDocument>")
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(ug._fetch_units_window(CTA_50HERTZ, date(2026, 7, 20), date(2026, 7, 27)))

    assert raw_cache.read_cached("entsoe_a73", f"{CTA_50HERTZ}_2026-07-20",
                                 date(2026, 7, 20)) is None


def test_the_cache_source_collides_with_nothing():
    from backend.power.entsoe_exchange import CACHE_SOURCE as A09_SOURCE
    from backend.power.entsoe_exchange import NET_POSITION_CACHE_SOURCE
    from backend.power.entsoe_ntc import CACHE_SOURCE as A61_SOURCE
    from backend.power.entsoe_unit_generation import CACHE_SOURCE

    assert CACHE_SOURCE == "entsoe_a73"
    assert CACHE_SOURCE not in (A09_SOURCE, NET_POSITION_CACHE_SOURCE, A61_SOURCE,
                                "entsoe_genmix", "entsoe_load", "entsoe_hydro",
                                "entsoe_gen_total_forecast")


# ─── ingest ───────────────────────────────────────────────────────────────────


@pytest.fixture
def ingest(monkeypatch):
    """Token set, fetch faked per CTA EIC — the ntc test-fixture doctrine (no-token
    guard would otherwise make these pass while writing nothing)."""
    from pydantic import SecretStr

    from backend.power import entsoe_unit_generation as ug

    monkeypatch.setattr(ug.settings, "entsoe_api_token", SecretStr("test-token"))

    def _install(doc_by_eic: dict[str, str]):
        async def _fake(eic, start, end, *, overwrite=False):
            return doc_by_eic.get(eic, "")  # unlisted CTA = clean 200-ACK

        monkeypatch.setattr(ug, "_fetch_units_window", _fake)
        return ug

    return _install


def test_ingest_without_a_token_skips_loudly(db_session, monkeypatch):
    from backend.power import entsoe_unit_generation as ug

    monkeypatch.setattr(ug.settings, "entsoe_api_token", None)
    out = asyncio.run(ingest_unit_generation(db_session))

    assert out == {"skipped": "no token"}


def test_ingest_writes_rows_for_a_configured_zone(db_session, ingest):
    ingest({CTA_50HERTZ: _doc(
        _ts_block("U60", [(1, 500.0), (2, 640.0)], end="2026-07-20T02:00Z"))})
    out = asyncio.run(ingest_unit_generation(db_session))

    rows = db_session.query(UnitGeneration).order_by(UnitGeneration.ts_utc).all()
    assert [(r.unit_eic, r.ts_utc, r.mw, r.zone) for r in rows] == [
        ("U60", DAY_EPOCH, 500.0, "DE_LU"),
        ("U60", DAY_EPOCH + 3600, 640.0, "DE_LU"),
    ]
    assert out["units"] == 1 and out["written"] == 2


def test_ingest_gates_on_the_zone_config(db_session, ingest):
    """A zone without an A73 domain config (FR) is skipped — no fetch reaches the
    fake, nothing is written. The registry knows more zones than answer A73."""
    ug = ingest({CTA_50HERTZ: _doc(_ts_block("U60", [(1, 500.0)]))})
    out = asyncio.run(ug.ingest_unit_generation(db_session, zones=["FR"]))

    assert db_session.query(UnitGeneration).count() == 0
    assert out["units"] == 0 and out["written"] == 0


def test_ingest_is_idempotent_and_a_rerun_updates_mw(db_session, ingest):
    """The publication-lag design depends on this: the scheduler re-ingests the
    same window with overwrite=True, so the same (unit, hour) must never dupe and
    a revised value must win."""
    ingest({CTA_50HERTZ: _doc(_ts_block("U60", [(1, 500.0)]))})
    asyncio.run(ingest_unit_generation(db_session))
    ingest({CTA_50HERTZ: _doc(_ts_block("U60", [(1, 555.0)]))})
    asyncio.run(ingest_unit_generation(db_session))

    rows = db_session.query(UnitGeneration).all()
    assert len(rows) == 1, "same (unit_eic, ts_utc) — updated, not duplicated"
    assert rows[0].mw == 555.0


def test_upsert_helper_skips_none_and_reports_written(db_session):
    written = upsert_unit_generation(
        db_session, "DE_LU", [("U", DAY_EPOCH, 100.0), ("U", DAY_EPOCH + 3600, None)])
    db_session.commit()
    assert written == 1
    assert db_session.query(UnitGeneration).count() == 1


# ─── routes ───────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    from backend.main import app

    app.dependency_overrides.clear()


def _client(db) -> TestClient:
    from backend.database import get_db
    from backend.main import app

    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app, raise_server_exceptions=True)


def _latest_hour_epoch(days_back: int = 6) -> int:
    """Top of the current UTC hour, minus exactly `days_back` days — so the route's
    whole-day lag_days computation is deterministic (now − latest < days_back+1h)."""
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    return int((now - timedelta(days=days_back)).timestamp())


def _seed_board(db):
    """Four units, 6 days behind the wall clock:

    * 11WA NUCLEAR-1 — registry with nominal 800, current 400 → utilization 50%
    * 11WB GAS-1     — registry WITHOUT nominal → utilization null
    * 11WNOREG       — no registry row at all → name/fuel null
    * 11WLAG COAL-1  — rows only 3 days BEFORE the latest day (its CTA has not
      published the frontier yet — the smoke's lag-skew case) → listed as
      "not reporting", null current/day-avg, still in totals.units
    """
    latest = _latest_hour_epoch()
    day_start = latest - (latest % 86_400)
    prev = latest - 3600 if latest - 3600 >= day_start else None

    db.add(ProductionUnit(unit_eic="11WA", zone="DE_LU", year=2026, name="NUCLEAR-1",
                          psr_type="B14", nominal_mw=800.0))
    db.add(ProductionUnit(unit_eic="11WB", zone="DE_LU", year=2026, name="GAS-1",
                          psr_type="B04", nominal_mw=None))
    db.add(ProductionUnit(unit_eic="11WLAG", zone="DE_LU", year=2026, name="COAL-1",
                          psr_type="B05", nominal_mw=600.0))
    db.add(UnitGeneration(unit_eic="11WA", ts_utc=latest, mw=400.0, zone="DE_LU"))
    if prev is not None:
        db.add(UnitGeneration(unit_eic="11WA", ts_utc=prev, mw=200.0, zone="DE_LU"))
    db.add(UnitGeneration(unit_eic="11WB", ts_utc=latest, mw=100.0, zone="DE_LU"))
    db.add(UnitGeneration(unit_eic="11WNOREG", ts_utc=latest, mw=50.0, zone="DE_LU"))
    db.add(UnitGeneration(unit_eic="11WLAG", ts_utc=latest - 3 * 86_400, mw=550.0,
                          zone="DE_LU"))
    db.commit()
    return latest, prev


def test_generation_route_joins_registry_and_computes_utilization(db_session):
    latest, prev = _seed_board(db_session)
    body = _client(db_session).get("/api/power/units/generation?zone=DE_LU").json()

    assert body["available"] is True
    by_eic = {u["unit_eic"]: u for u in body["units"]}
    a = by_eic["11WA"]
    assert a["name"] == "NUCLEAR-1"
    assert a["fuel"] == "Nuclear"        # PSR_LABELS join, same as /api/v1/units
    assert a["nominal_mw"] == 800.0
    assert a["current_mw"] == 400.0
    assert a["utilization_pct"] == 50.0
    if prev is not None:
        assert a["day_avg_mw"] == 300.0  # mean over the day's reported hours

    assert by_eic["11WB"]["utilization_pct"] is None, "null nominal → no ratio"
    assert by_eic["11WNOREG"]["name"] is None and by_eic["11WNOREG"]["fuel"] is None

    # Sorted by current_mw desc, not-reporting units last.
    assert [u["unit_eic"] for u in body["units"]] == ["11WA", "11WB", "11WNOREG", "11WLAG"]
    assert body["totals"] == {"units": 4, "reporting": 3,
                              "nominal_mw": 1400.0, "generating_mw": 550.0}


def test_generation_route_keeps_lagging_units_visible_as_not_reporting(db_session):
    """The smoke's per-CTA lag skew: TenneT published D-2 while the others sat at
    D-5 — a unit whose TSO has not reached the frontier day must stay LISTED with
    null output, or the board silently presents one TSO's plants as the zone."""
    _seed_board(db_session)
    body = _client(db_session).get("/api/power/units/generation?zone=DE_LU").json()

    lag = {u["unit_eic"]: u for u in body["units"]}["11WLAG"]
    assert lag["name"] == "COAL-1", "registry join still applies"
    assert lag["current_mw"] is None
    assert lag["day_avg_mw"] is None, "no rows on the latest day — no fabricated mean"
    assert lag["utilization_pct"] is None
    assert body["totals"]["units"] == 4 and body["totals"]["reporting"] == 3


def test_generation_route_reports_the_honest_lag(db_session):
    latest, _prev = _seed_board(db_session)
    body = _client(db_session).get("/api/power/units/generation?zone=DE_LU").json()

    latest_dt = datetime.fromtimestamp(latest, tz=timezone.utc)
    expected = int((datetime.now(timezone.utc) - latest_dt).total_seconds() // 86_400)
    assert body["lag_days"] == expected == 6
    assert body["latest_hour_utc"] == latest_dt.strftime("%Y-%m-%dT%H:%MZ")
    assert body["as_of"] == latest_dt.strftime("%Y-%m-%d")
    assert body["stale"] is False, "6 days behind is A73's NORMAL lag, not staleness"
    assert "Not live" in body["note"] and "NOT the installed fleet" in body["note"]


def _seed_outage(db, eic, *, mrid, revision=1, bt="A54", status="active",
                 start_off_h=-48, end_off_h=48):
    now = datetime.now(timezone.utc)
    db.add(PowerOutage(
        mrid=mrid, revision=revision, doc_type="A77", zone="DE_LU",
        business_type=bt, psr_type="B14", unit_name=None, unit_eic=eic,
        location=None, nominal_mw=800.0, available_mw=0.0,
        start_utc=(now + timedelta(hours=start_off_h)).strftime("%Y-%m-%dT%H:%MZ"),
        end_utc=(now + timedelta(hours=end_off_h)).strftime("%Y-%m-%dT%H:%MZ"),
        status=status,
    ))
    db.commit()


def test_generation_route_attaches_outages_with_revision_semantics(db_session):
    """The outage join must ride the same highest-revision/withdrawn-hidden rules
    as the outage board: a withdrawn latest revision hides the event even though
    an older active revision exists, and an event overlapping the latest hour
    attaches {kind, offline_mw}."""
    latest, _prev = _seed_board(db_session)
    # 11WA: rev1 active AND covering the latest hour, rev2 WITHDRAWN → only the
    # withdrawal (not the window) may be what hides it.
    _seed_outage(db_session, "11WA", mrid="mA", revision=1, start_off_h=-24 * 8)
    _seed_outage(db_session, "11WA", mrid="mA", revision=2, status="withdrawn",
                 start_off_h=-24 * 8)
    # 11WB: active forced outage spanning the latest hour (start 8 days ago).
    _seed_outage(db_session, "11WB", mrid="mB", start_off_h=-24 * 8)

    body = _client(db_session).get("/api/power/units/generation?zone=DE_LU").json()
    by_eic = {u["unit_eic"]: u for u in body["units"]}
    assert by_eic["11WA"]["outage"] is None, "withdrawn latest revision hides the event"
    assert by_eic["11WB"]["outage"] == {"kind": "forced", "offline_mw": 800.0}


def test_generation_route_ignores_outages_not_covering_the_latest_hour(db_session):
    """The board's hour is ~6 days in the past — an outage that starts tomorrow
    (running_now for the outage panel's wall clock!) must NOT badge it."""
    _seed_board(db_session)
    _seed_outage(db_session, "11WA", mrid="mF", start_off_h=24, end_off_h=72)

    body = _client(db_session).get("/api/power/units/generation?zone=DE_LU").json()
    by_eic = {u["unit_eic"]: u for u in body["units"]}
    assert by_eic["11WA"]["outage"] is None


def test_generation_route_unconfigured_zone_is_honest(db_session):
    """FR is an enabled POWER zone but has no A73 ingest config — the answer is
    available:false with the config-extensible reason, never an empty board."""
    body = _client(db_session).get("/api/power/units/generation?zone=FR").json()
    assert body["available"] is False
    assert body["zone"] == "FR"
    assert "DE-LU only" in body["reason"]


def test_generation_route_configured_but_empty_zone_is_honest(db_session):
    body = _client(db_session).get("/api/power/units/generation?zone=DE_LU").json()
    assert body["available"] is False
    assert "reason" in body


# ─── history route ────────────────────────────────────────────────────────────


def test_history_returns_the_unit_series_anchored_at_its_latest_hour(db_session):
    latest, _ = _seed_board(db_session)
    body = _client(db_session).get(
        "/api/power/units/history?zone=DE_LU&unit=11WA&hours=24").json()

    assert body["available"] is True
    assert body["unit_eic"] == "11WA"
    assert body["name"] == "NUCLEAR-1"
    assert body["fuel"] == "Nuclear"
    assert body["nominal_mw"] == 800.0
    assert body["data"][-1] == {"ts_utc": latest, "mw": 400.0}
    assert all(p["ts_utc"] > latest - 24 * 3600 for p in body["data"])


def test_history_caps_at_744_hours(db_session):
    _seed_board(db_session)
    ok = _client(db_session).get("/api/power/units/history?zone=DE_LU&unit=11WA&hours=744")
    assert ok.status_code == 200
    over = _client(db_session).get("/api/power/units/history?zone=DE_LU&unit=11WA&hours=745")
    assert over.status_code == 422, "the /v1/snapshot cap precedent"
    under = _client(db_session).get("/api/power/units/history?zone=DE_LU&unit=11WA&hours=23")
    assert under.status_code == 422


def test_history_unknown_unit_is_honest(db_session):
    _seed_board(db_session)
    body = _client(db_session).get(
        "/api/power/units/history?zone=DE_LU&unit=11WGHOST").json()
    assert body["available"] is False
    assert "11WGHOST" in body["reason"]


def test_history_holds_a_heavy_query_slot(db_session):
    """First heavy_query_guard use on /api/power, deliberately: this endpoint is
    the bulk-pull surface for a table that is not exportable via /api/v1/series."""
    from backend.api_guard import heavy_query_guard
    from backend.main import app

    route = next(r for r in app.routes
                 if getattr(r, "path", None) == "/api/power/units/history")
    assert heavy_query_guard in [d.call for d in route.dependant.dependencies]


# ─── freshness: the epoch_column probe ────────────────────────────────────────


def test_epoch_column_spec_resolves_max_and_stales_correctly(db_session):
    from backend.collectors.freshness import SPECS, evaluate_freshness

    spec = next(s for s in SPECS if s.key == "unit_generation")
    assert spec.epoch_column == "ts_utc"
    assert spec.hourly_series is None, \
        "must not join the hourly_series population test_outage_history pins"

    ts = int(datetime(2026, 7, 20, 12, tzinfo=timezone.utc).timestamp())
    db_session.add(UnitGeneration(unit_eic="U", ts_utc=ts, mw=1.0, zone="DE_LU"))
    db_session.commit()

    fresh = evaluate_freshness(
        db_session, now=datetime(2026, 7, 26, tzinfo=timezone.utc))["unit_generation"]
    assert fresh["fresh"] is True, "6 days behind is inside the 10-day window"
    assert fresh["last_seen"] == datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()

    stale = evaluate_freshness(
        db_session, now=datetime(2026, 8, 5, tzinfo=timezone.utc))["unit_generation"]
    assert stale["fresh"] is False, "beyond 10 days the collector is dead, not lagging"


def test_epoch_column_spec_with_an_empty_table_is_not_fresh(db_session):
    from backend.collectors.freshness import evaluate_freshness

    out = evaluate_freshness(db_session)["unit_generation"]
    assert out["fresh"] is False
    assert out["last_seen"] is None
