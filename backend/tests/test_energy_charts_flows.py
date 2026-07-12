"""Tests for Energy-Charts CBPF cross-border flow ingestion.

Covers:
  - parse_cbpf: sign convention, GW→MW conversion, canonical border ordering,
                dedup/average across both-side queries, unmapped country skip
  - ingest_cbpf: fetch→parse→upsert pipeline; idempotency; empty days
  - GET /api/power/flows: route shape, attribution field, sorting, available/unavailable
"""
from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

import backend.power.energy_charts_flows as flows
from backend.models.energy import PowerFlow
from backend.power.energy_charts_flows import parse_cbpf


def test_country_code_to_zone_covers_all_base_countries():
    """The generalized reverse map must resolve EVERY base country — the exact
    invariant the old hardcoded {de,fr,nl} map violated for any 4th flow zone."""
    assert flows.COUNTRY_CODE_TO_ZONE == {v: k for k, v in flows.ZONE_TO_COUNTRY.items()}
    assert set(flows.BASE_COUNTRIES) <= set(flows.COUNTRY_CODE_TO_ZONE)

# ─── autouse: clear dependency overrides between tests ───────────────────────


@pytest.fixture(autouse=True)
def _clear_dependency_overrides():
    """Prevent app.dependency_overrides leaking between test files."""
    yield
    from backend.main import app

    app.dependency_overrides.clear()


# ─── helpers ─────────────────────────────────────────────────────────────────

_TODAY = date.today()
_D1 = (_TODAY - timedelta(days=3)).isoformat()
_D2 = (_TODAY - timedelta(days=2)).isoformat()
_D3 = (_TODAY - timedelta(days=1)).isoformat()

# 2026-06-01 00:00 UTC = 1748736000
# 15-min intervals: 1748736000, 1748736900, 1748737800, ...
_BASE_TS = 1780272000  # 2026-06-01 00:00 UTC


def _make_payload(
    country_series: dict[str, list[float]],
    n_points: int = 96,
    base_ts: int = _BASE_TS,
) -> dict:
    """Build a synthetic CBPF payload with 15-min intervals."""
    unix_seconds = [base_ts + i * 900 for i in range(n_points)]
    countries = [
        {"name": name, "data": vals}
        for name, vals in country_series.items()
    ]
    return {"unix_seconds": unix_seconds, "countries": countries}


def _seed_flows(db: Session, rows: list[dict]) -> None:
    for r in rows:
        db.add(
            PowerFlow(
                date=r["date"],
                from_zone=r.get("from_zone", "DE_LU"),
                to_zone=r.get("to_zone", "FR"),
                net_mw=r["net_mw"],
            )
        )
    db.commit()


def _make_client(db: Session) -> TestClient:
    from backend.database import get_db
    from backend.main import app

    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app, raise_server_exceptions=True)


# ─── parse_cbpf unit tests ────────────────────────────────────────────────────


def test_parse_gw_to_mw_conversion():
    """API returns GW; parse_cbpf must convert to MW (× 1000)."""
    # country=de, France series = 3.0 GW constantly
    # DE_LU is sorted-first in (DE_LU, FR) → sign = -1 → net_mw = -3.0 × 1000 = -3000 MW
    payload = _make_payload({"France": [3.0] * 96})
    result = parse_cbpf(payload, "DE_LU")
    canon = ("DE_LU", "FR")
    assert canon in result
    # One day bucket (all 96 points fall on the same UTC day)
    assert len(result[canon]) == 1
    day_val = list(result[canon].values())[0]
    assert day_val == pytest.approx(-3000.0)


def test_parse_sign_convention_queried_is_from_zone():
    """When queried_zone == from_zone (sorted-first), sign is negated.

    Raw = import into queried_zone → positive raw means queried imports
    → from_zone (queried) imports, i.e. net_mw(from→to) < 0.
    """
    # DE_LU sorted before FR → DE_LU is from_zone
    # raw France = +2.0 GW → DE_LU imports → net(DE_LU→FR) = -2.0 GW = -2000 MW
    payload = _make_payload({"France": [2.0] * 96})
    result = parse_cbpf(payload, "DE_LU")
    val = list(result[("DE_LU", "FR")].values())[0]
    assert val == pytest.approx(-2000.0)


def test_parse_sign_convention_queried_is_to_zone():
    """When queried_zone == to_zone (sorted-second), sign is kept positive.

    country=fr, Germany series = -3.0 GW (real observed value).
    Canonical border is (DE_LU, FR): DE_LU=from_zone, FR=to_zone.
    raw = flow INTO FR from DE_LU = export from DE_LU → FR.
    net_mw(DE_LU→FR) = +raw = -3.0 GW × (-1? No — raw is already negative)

    Wait: from the real API, DE France series = +2.86 GW and FR Germany = -2.86 GW.
    When country=fr, Germany series = -2.86 means FR imports -2.86 GW from DE,
    i.e., FR actually EXPORTS 2.86 GW to DE.
    queried_zone=FR is to_zone in (DE_LU, FR) → sign=+1.
    net_mw(DE_LU→FR) = +(-2.86) × 1000 = -2860 MW (DE_LU imports from FR).

    This is consistent with the DE side: DE raw = +2.86 → sign=-1 → net=-2860.
    Both sides yield the same value.
    """
    # FR queried, Germany series = -2.86 GW
    payload = _make_payload({"Germany": [-2.86] * 96})
    result = parse_cbpf(payload, "FR")
    val = list(result[("DE_LU", "FR")].values())[0]
    assert val == pytest.approx(-2860.0, rel=1e-3)


def test_parse_both_sides_consistent():
    """DE-side and FR-side estimates for the same border agree (within tolerance)."""
    de_payload = _make_payload({"France": [3.0] * 96})
    fr_payload = _make_payload({"Germany": [-3.0] * 96})
    de_result = parse_cbpf(de_payload, "DE_LU")
    fr_result = parse_cbpf(fr_payload, "FR")
    de_val = list(de_result[("DE_LU", "FR")].values())[0]
    fr_val = list(fr_result[("DE_LU", "FR")].values())[0]
    assert de_val == pytest.approx(fr_val)


def test_parse_sum_skipped():
    """The 'sum' series must not produce a border entry."""
    payload = _make_payload({"sum": [10.0] * 96, "France": [2.0] * 96})
    result = parse_cbpf(payload, "DE_LU")
    # Only DE_LU↔FR; no sum border
    assert all("sum" not in str(k) for k in result.keys())
    assert ("DE_LU", "FR") in result


def test_parse_unmapped_country_skipped():
    """Country names not in COUNTRY_TO_ZONE are silently skipped."""
    payload = _make_payload({"Atlantis": [5.0] * 96, "France": [1.0] * 96})
    result = parse_cbpf(payload, "DE_LU")
    assert ("DE_LU", "FR") in result
    assert len(result) == 1  # only FR border


def test_parse_empty_unix_seconds():
    """Empty unix_seconds → empty dict."""
    result = parse_cbpf({"unix_seconds": [], "countries": []}, "DE_LU")
    assert result == {}


def test_parse_multiple_neighbors():
    """DE query with France + Netherlands + Belgium → 3 canonical borders."""
    payload = _make_payload({
        "France": [2.0] * 96,
        "Netherlands": [-1.0] * 96,
        "Belgium": [0.5] * 96,
    })
    result = parse_cbpf(payload, "DE_LU")
    assert ("DE_LU", "FR") in result
    assert ("DE_LU", "NL") in result
    assert ("BE", "DE_LU") in result


def test_parse_daily_mean_aggregation():
    """Average of two days' 15-min data is correct."""
    # 96 points for day1, then 96 for day2
    ts1 = 1780272000  # 2026-06-01 00:00 UTC
    ts2 = 1780272000 + 96 * 900  # 2026-06-02 00:00 UTC (= 1780358400)
    all_ts = [ts1 + i * 900 for i in range(96)] + [ts2 + i * 900 for i in range(96)]
    # day1 = 2.0 GW, day2 = 4.0 GW
    all_vals = [2.0] * 96 + [4.0] * 96
    payload = {"unix_seconds": all_ts, "countries": [{"name": "France", "data": all_vals}]}
    result = parse_cbpf(payload, "DE_LU")
    border = ("DE_LU", "FR")
    assert border in result
    days = result[border]
    assert len(days) == 2
    vals_list = sorted(days.values())
    # Day1: -2.0×1000 = -2000; Day2: -4.0×1000 = -4000
    assert vals_list[0] == pytest.approx(-4000.0)
    assert vals_list[1] == pytest.approx(-2000.0)


# ─── ingest_cbpf integration tests ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_ingest_empty_days(db_session):
    """Empty day list → {days: 0, borders: 0, written: 0}."""
    from backend.power.energy_charts_flows import ingest_cbpf

    result = await ingest_cbpf(db_session, [])
    assert result == {"days": 0, "borders": 0, "written": 0, "hourly_written": 0}


@pytest.mark.asyncio
async def test_ingest_upsert_idempotent(db_session):
    """Running ingest twice with same data overwrites, no duplicate rows."""
    import httpx

    from backend.power.energy_charts_flows import ingest_cbpf

    # Synthetic: one day, DE query only (FR/NL will raise HTTPError in mock)
    day = "2026-06-01"

    def make_de_payload():
        return _make_payload({"France": [3.0] * 96}, base_ts=1780272000)

    async def mock_fetch(country, start, end):
        if country == "de":
            return make_de_payload()
        # Simulate a rate-limit error for other countries
        raise httpx.HTTPStatusError("429", request=None, response=None)

    with patch("backend.power.energy_charts_flows.fetch_cbpf", side_effect=mock_fetch):
        r1 = await ingest_cbpf(db_session, [day])

    assert r1["written"] >= 1

    with patch("backend.power.energy_charts_flows.fetch_cbpf", side_effect=mock_fetch):
        await ingest_cbpf(db_session, [day])

    # Still only one row per border after second run
    count = db_session.query(PowerFlow).filter(PowerFlow.date == day).count()
    assert count == r1["written"]


@pytest.mark.asyncio
async def test_ingest_dedup_averages_both_sides(db_session):
    """If both DE and FR queries provide data for DE_LU/FR, the values are averaged."""
    import httpx

    from backend.power.energy_charts_flows import ingest_cbpf

    day = "2026-06-01"
    ts_base = 1780272000  # 2026-06-01 00:00 UTC

    # DE says France = +3.0 GW -> net(DE_LU->FR) = -3000 MW
    # FR says Germany = -3.0 GW -> net(DE_LU->FR) = -3000 MW
    # Average = -3000 MW
    de_payload = _make_payload({"France": [3.0] * 96}, base_ts=ts_base)
    fr_payload = _make_payload({"Germany": [-3.0] * 96}, base_ts=ts_base)

    async def mock_fetch(country, start, end):
        if country == "de":
            return de_payload
        if country == "fr":
            return fr_payload
        raise httpx.HTTPStatusError("429", request=None, response=None)

    with patch("backend.power.energy_charts_flows.fetch_cbpf", side_effect=mock_fetch):
        result = await ingest_cbpf(db_session, [day])

    assert result["written"] >= 1
    row = (
        db_session.query(PowerFlow)
        .filter(
            PowerFlow.date == day,
            PowerFlow.from_zone == "DE_LU",
            PowerFlow.to_zone == "FR",
        )
        .first()
    )
    assert row is not None
    assert row.net_mw == pytest.approx(-3000.0)


@pytest.mark.asyncio
async def test_ingest_fetch_failure_graceful(db_session):
    """A 429/network error on one country does not abort the whole ingest."""
    import httpx

    from backend.power.energy_charts_flows import ingest_cbpf

    day = "2026-06-01"
    ts_base = 1780272000  # 2026-06-01 00:00 UTC
    de_payload = _make_payload({"France": [2.0] * 96}, base_ts=ts_base)

    async def mock_fetch(country, start, end):
        if country == "de":
            return de_payload
        raise httpx.HTTPStatusError("429", request=None, response=None)

    with patch("backend.power.energy_charts_flows.fetch_cbpf", side_effect=mock_fetch):
        result = await ingest_cbpf(db_session, [day])

    # At least the DE borders were written
    assert result["written"] >= 1


# ─── route tests ─────────────────────────────────────────────────────────────


def test_route_flows_unavailable_when_empty(db_session):
    """No PowerFlow rows → available=False."""
    client = _make_client(db_session)
    resp = client.get("/api/power/flows?days=30")
    assert resp.status_code == 200
    assert resp.json()["available"] is False


def test_route_flows_available_with_data(db_session):
    """Seeded rows → available=True with required fields."""
    _seed_flows(db_session, [
        {"date": _D1, "from_zone": "DE_LU", "to_zone": "FR",  "net_mw": 1200.0},
        {"date": _D1, "from_zone": "DE_LU", "to_zone": "NL",  "net_mw": -800.0},
        {"date": _D1, "from_zone": "BE",    "to_zone": "DE_LU", "net_mw": 500.0},
        {"date": _D2, "from_zone": "DE_LU", "to_zone": "FR",  "net_mw": 1500.0},
    ])
    client = _make_client(db_session)
    resp = client.get("/api/power/flows?days=30")
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is True
    assert "borders" in body
    assert "data" in body
    assert "latest" in body
    assert "unit" in body
    assert body["unit"] == "MW"


def test_route_flows_attribution(db_session):
    """Response includes source attribution field (CC BY 4.0)."""
    _seed_flows(db_session, [
        {"date": _D1, "from_zone": "DE_LU", "to_zone": "FR", "net_mw": 1200.0},
    ])
    client = _make_client(db_session)
    body = client.get("/api/power/flows?days=30").json()
    assert "source" in body
    assert "CC BY 4.0" in body["source"]
    assert "Energy-Charts" in body["source"]


def test_route_flows_borders_sorted_by_abs_net_mw(db_session):
    """borders[] is sorted descending by |net_mw|."""
    _seed_flows(db_session, [
        {"date": _D1, "from_zone": "DE_LU", "to_zone": "FR",  "net_mw": 500.0},
        {"date": _D1, "from_zone": "DE_LU", "to_zone": "NL",  "net_mw": -2000.0},
        {"date": _D1, "from_zone": "CH",    "to_zone": "DE_LU", "net_mw": 1200.0},
    ])
    client = _make_client(db_session)
    body = client.get("/api/power/flows?days=30").json()
    borders = body["borders"]
    assert len(borders) == 3
    abs_vals = [abs(b["net_mw"]) for b in borders]
    assert abs_vals == sorted(abs_vals, reverse=True)


def test_route_flows_direction_arrows(db_session):
    """Positive net_mw → from_zone→to_zone; negative → to_zone→from_zone."""
    _seed_flows(db_session, [
        {"date": _D1, "from_zone": "DE_LU", "to_zone": "FR", "net_mw":  2000.0},
        {"date": _D1, "from_zone": "DE_LU", "to_zone": "NL", "net_mw": -1500.0},
    ])
    client = _make_client(db_session)
    body = client.get("/api/power/flows?days=30").json()
    borders = {f"{b['from_zone']}-{b['to_zone']}": b for b in body["borders"]}

    de_fr = borders["DE_LU-FR"]
    assert de_fr["net_mw"] == pytest.approx(2000.0)
    assert "DE-LU" in de_fr["direction"]
    assert "FR" in de_fr["direction"]
    # Positive → DE-LU→FR direction
    assert de_fr["direction"].index("DE-LU") < de_fr["direction"].index("FR")

    de_nl = borders["DE_LU-NL"]
    assert de_nl["net_mw"] == pytest.approx(-1500.0)
    # Negative → NL→DE-LU
    assert de_nl["direction"].index("NL") < de_nl["direction"].index("DE-LU")


def test_route_flows_non_power_zones_in_borders(db_session):
    """Border zones not in POWER_ZONES (e.g. BE, CH, GB) appear correctly."""
    _seed_flows(db_session, [
        {"date": _D1, "from_zone": "BE",  "to_zone": "DE_LU", "net_mw": 800.0},
        {"date": _D1, "from_zone": "CH",  "to_zone": "FR",    "net_mw": 600.0},
        {"date": _D1, "from_zone": "FR",  "to_zone": "GB",    "net_mw": -400.0},
    ])
    client = _make_client(db_session)
    body = client.get("/api/power/flows?days=30").json()
    from_zones = {b["from_zone"] for b in body["borders"]}
    to_zones   = {b["to_zone"]   for b in body["borders"]}
    assert "BE" in from_zones
    assert "CH" in from_zones
    assert "GB" in to_zones


def test_route_flows_wide_format_data(db_session):
    """data rows are wide-format with date + border arrow keys."""
    _seed_flows(db_session, [
        {"date": _D1, "from_zone": "DE_LU", "to_zone": "FR", "net_mw": 1200.0},
        {"date": _D1, "from_zone": "DE_LU", "to_zone": "NL", "net_mw":  300.0},
    ])
    client = _make_client(db_session)
    body = client.get("/api/power/flows?days=30").json()
    d1_row = next((r for r in body["data"] if r["date"] == _D1), None)
    assert d1_row is not None
    assert "DE-LU→FR" in d1_row
    assert "DE-LU→NL" in d1_row


# ─── hourly grain (roadmap Block 2.4) ────────────────────────────────────────


def test_parse_hourly_means_sign_and_mw():
    """15-min points average per UTC hour with the daily parser's sign/unit rules."""
    from backend.power.energy_charts_flows import parse_cbpf_hourly

    # Hour 0: 2,2,4,4 GW → 3 GW mean; hour 1: 1 GW flat. DE_LU sorted-first → sign -1.
    vals = [2.0, 2.0, 4.0, 4.0] + [1.0] * 4 + [None] * 88
    payload = _make_payload({"France": vals})
    hourly = parse_cbpf_hourly(payload, "DE_LU")
    border = hourly[("DE_LU", "FR")]
    assert border[_BASE_TS] == pytest.approx(-3000.0)
    assert border[_BASE_TS + 3600] == pytest.approx(-1000.0)
    assert len(border) == 2, "hours with only None points must not appear"


def test_parse_hourly_queried_is_to_zone_keeps_sign():
    from backend.power.energy_charts_flows import parse_cbpf_hourly

    payload = _make_payload({"Germany": [-2.86] * 96})
    hourly = parse_cbpf_hourly(payload, "FR")
    assert hourly[("DE_LU", "FR")][_BASE_TS] == pytest.approx(-2860.0, rel=1e-3)


@pytest.mark.asyncio
async def test_ingest_writes_hourly_series(db_session):
    """ingest_cbpf writes flow.<TO> under zone <FROM> into power_hourly, averaging
    both-side estimates like the daily grain."""
    import httpx

    from backend.power.energy_charts_flows import ingest_cbpf
    from backend.power.hourly_store import read_hourly

    day = "2026-06-01"
    de_payload = _make_payload({"France": [3.0] * 96}, base_ts=_BASE_TS)
    fr_payload = _make_payload({"Germany": [-3.0] * 96}, base_ts=_BASE_TS)

    async def mock_fetch(country, start, end):
        if country == "de":
            return de_payload
        if country == "fr":
            return fr_payload
        raise httpx.HTTPStatusError("429", request=None, response=None)

    with patch("backend.power.energy_charts_flows.fetch_cbpf", side_effect=mock_fetch):
        result = await ingest_cbpf(db_session, [day])

    points = read_hourly(db_session, "flow.FR", "DE_LU")
    assert len(points) == 24
    assert all(v == pytest.approx(-3000.0) for _, v in points)
    assert points[0][0] == _BASE_TS
    assert result["hourly_written"] >= 24


@pytest.mark.asyncio
async def test_ingest_hourly_respects_wanted_days(db_session):
    """Hourly points outside the requested day list are dropped (same as daily)."""
    import httpx

    from backend.power.energy_charts_flows import ingest_cbpf
    from backend.power.hourly_store import read_hourly

    # Payload spans two days; only day 1 is requested.
    all_ts = [_BASE_TS + i * 900 for i in range(192)]
    payload = {"unix_seconds": all_ts,
               "countries": [{"name": "France", "data": [2.0] * 192}]}

    async def mock_fetch(country, start, end):
        if country == "de":
            return payload
        raise httpx.HTTPStatusError("429", request=None, response=None)

    with patch("backend.power.energy_charts_flows.fetch_cbpf", side_effect=mock_fetch):
        await ingest_cbpf(db_session, ["2026-06-01"])

    assert len(read_hourly(db_session, "flow.FR", "DE_LU")) == 24


# ─── raw-cache month chunking (backfill path) ────────────────────────────────


@pytest.mark.asyncio
async def test_use_cache_month_chunks_and_never_refetches(db_session, tmp_path, monkeypatch):
    """use_cache=True fetches completed months once, then serves them from disk."""
    import backend.gas.raw_cache as rc
    from backend.power.energy_charts_flows import ingest_cbpf

    monkeypatch.setattr(rc, "DATA_ROOT", tmp_path)
    monkeypatch.setattr(flows, "CACHE_THROTTLE_SECONDS", 0.0)
    # A safely-completed month (June 2026 lies in the past for this repo's clock
    # only if today > 2026-06-30 — keep the assertion time-independent by using
    # a fixed historic month instead).
    base = 1717200000  # 2024-06-01 00:00 UTC
    payload = _make_payload({"France": [1.0] * 96}, base_ts=base)
    calls = []

    async def mock_fetch(country, start, end):
        calls.append((country, start, end))
        import httpx
        if country == "de":
            return payload
        raise httpx.HTTPStatusError("429", request=None, response=None)

    days = ["2024-06-01"]
    with patch("backend.power.energy_charts_flows.fetch_cbpf", side_effect=mock_fetch):
        r1 = await ingest_cbpf(db_session, days, use_cache=True)
    assert r1["written"] >= 1
    de_calls_first = [c for c in calls if c[0] == "de"]
    assert de_calls_first == [("de", "2024-06-01", "2024-06-30")], \
        "backfill path must request the full month so the cached blob is complete"

    calls.clear()
    with patch("backend.power.energy_charts_flows.fetch_cbpf", side_effect=mock_fetch):
        r2 = await ingest_cbpf(db_session, days, use_cache=True)
    assert [c for c in calls if c[0] == "de"] == [], "second run must hit the disk cache"
    assert r2["written"] == r1["written"]


@pytest.mark.asyncio
async def test_use_cache_never_caches_the_current_month(db_session, tmp_path, monkeypatch):
    """The running month is fetched live and NOT persisted — a mid-month blob
    would freeze and starve later re-runs of the month's remainder."""
    from datetime import datetime, timezone

    import backend.gas.raw_cache as rc
    from backend.power.energy_charts_flows import ingest_cbpf

    monkeypatch.setattr(rc, "DATA_ROOT", tmp_path)
    monkeypatch.setattr(flows, "CACHE_THROTTLE_SECONDS", 0.0)
    today = datetime.now(timezone.utc).date()
    day = today.replace(day=1).isoformat()
    base = int(datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())
    payload = _make_payload({"France": [1.0] * 96}, base_ts=base)
    calls = []

    async def mock_fetch(country, start, end):
        calls.append(country)
        import httpx
        if country == "de":
            return payload
        raise httpx.HTTPStatusError("429", request=None, response=None)

    for _ in range(2):
        with patch("backend.power.energy_charts_flows.fetch_cbpf", side_effect=mock_fetch):
            await ingest_cbpf(db_session, [day], use_cache=True)

    assert calls.count("de") == 2, "current month must be re-fetched, never cached"
    assert list(tmp_path.rglob("*.json.gz")) == []


# ─── 429 backoff (backfill path) ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_use_cache_backs_off_on_429_and_recovers(db_session, tmp_path, monkeypatch):
    """Energy-Charts 429s after short bursts (observed in prod 2026-07-12); the
    backfill path must back off and retry instead of silently skipping the month."""
    import httpx

    import backend.gas.raw_cache as rc
    from backend.power.energy_charts_flows import ingest_cbpf

    monkeypatch.setattr(rc, "DATA_ROOT", tmp_path)
    monkeypatch.setattr(flows, "CACHE_THROTTLE_SECONDS", 0.0)
    base = 1717200000  # 2024-06-01 00:00 UTC
    payload = _make_payload({"France": [1.0] * 96}, base_ts=base)
    attempts = {"de": 0}

    async def mock_fetch(country, start, end):
        if country == "de":
            attempts["de"] += 1
            if attempts["de"] <= 2:
                resp = httpx.Response(429, headers={"Retry-After": "0"},
                                      request=httpx.Request("GET", "http://x"))
                raise httpx.HTTPStatusError("429", request=resp.request, response=resp)
            return payload
        resp = httpx.Response(404, request=httpx.Request("GET", "http://x"))
        raise httpx.HTTPStatusError("404", request=resp.request, response=resp)

    with patch("backend.power.energy_charts_flows.fetch_cbpf", side_effect=mock_fetch):
        result = await ingest_cbpf(db_session, ["2024-06-01"], use_cache=True)

    assert attempts["de"] == 3, "two 429s then success"
    assert result["written"] >= 1
    assert len(list(tmp_path.rglob("de_2024-06.json.gz"))) == 1, "recovered month is cached"


@pytest.mark.asyncio
async def test_use_cache_gives_up_after_max_429s(db_session, tmp_path, monkeypatch):
    """Persistent 429 must not loop forever: after RATE_LIMIT_ATTEMPTS the country
    is skipped (uncached), so a later re-run heals it."""
    import httpx

    import backend.gas.raw_cache as rc
    from backend.power.energy_charts_flows import ingest_cbpf

    monkeypatch.setattr(rc, "DATA_ROOT", tmp_path)
    monkeypatch.setattr(flows, "CACHE_THROTTLE_SECONDS", 0.0)
    calls = {"n": 0}

    async def mock_fetch(country, start, end):
        calls["n"] += 1
        resp = httpx.Response(429, headers={"Retry-After": "0"},
                              request=httpx.Request("GET", "http://x"))
        raise httpx.HTTPStatusError("429", request=resp.request, response=resp)

    with patch("backend.power.energy_charts_flows.fetch_cbpf", side_effect=mock_fetch):
        result = await ingest_cbpf(db_session, ["2024-06-01"], use_cache=True)

    assert result["written"] == 0
    assert calls["n"] == len(flows.BASE_COUNTRIES) * flows.RATE_LIMIT_ATTEMPTS
    assert list(tmp_path.rglob("*.json.gz")) == [], "a failed month must never cache"


# ─── GET /api/power/flows/hourly ──────────────────────────────────────────────


def _seed_hourly_flows(db):
    """DE_LU-FR border (DE_LU sorted-first: series flow.FR under DE_LU) and
    CH-DE_LU border (CH sorted-first: series flow.DE_LU under CH)."""
    import time as _time

    from backend.power.hourly_store import upsert_hourly

    now = (int(_time.time()) // 3600) * 3600
    # DE_LU exports 1.5 GW to FR (native sign already "DE_LU exports")
    upsert_hourly(db, "flow.FR", "DE_LU",
                  [(now - i * 3600, 1500.0) for i in range(24, 0, -1)], unit="MW")
    # CH exports 2 GW to DE_LU → from DE_LU's perspective an IMPORT (−2000)
    upsert_hourly(db, "flow.DE_LU", "CH",
                  [(now - i * 3600, 2000.0) for i in range(24, 0, -1)], unit="MW")


def test_flows_hourly_normalises_both_border_directions(db_session):
    _seed_hourly_flows(db_session)
    body = _make_client(db_session).get("/api/power/flows/hourly?zone=DE_LU&hours=48").json()
    assert body["available"] is True
    by_neighbor = {b["neighbor"]: b for b in body["borders"]}
    assert by_neighbor["FR"]["latest_mw"] == 1500.0
    assert by_neighbor["FR"]["direction"] == "export"
    assert by_neighbor["CH"]["latest_mw"] == -2000.0, \
        "sorted-second border must flip sign to the selected zone's perspective"
    assert by_neighbor["CH"]["direction"] == "import"
    assert body["stale"] is False and body["unit"] == "MW"
    assert body["borders"][0]["neighbor"] == "CH", "sorted by |latest| desc"


def test_flows_hourly_subzone_without_series_is_honest(db_session):
    _seed_hourly_flows(db_session)
    body = _make_client(db_session).get("/api/power/flows/hourly?zone=NL").json()
    assert body["available"] is False
    assert "country-level" in body["reason"]
