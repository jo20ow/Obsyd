"""A72 weekly reservoir filling → series hydro.reservoir + same-week-vs-history route.

Spiked live 2026-07-11 (NO2): GL_MarketDocument, resolution P7D, unit MWh,
one point per week (~21 TWh for southern Norway). Reservoir levels are hard
seasonal — a trailing z-score would flag every spring melt as an anomaly, so
"vs. normal" compares against the SAME ISO week across prior years.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from backend.power import entsoe_hydro as hydro
from backend.power.hourly_store import read_hourly

NS = "urn:iec62325.351:tc57wg16:451-6:generationloaddocument:3:0"


def _a72(start: str, quantities: list[float], res: str = "P7D") -> str:
    pts = "".join(
        f"<Point><position>{i + 1}</position><quantity>{q}</quantity></Point>"
        for i, q in enumerate(quantities)
    )
    return (
        f'<?xml version="1.0"?><GL_MarketDocument xmlns="{NS}"><type>A72</type>'
        f"<TimeSeries><Period>"
        f"<timeInterval><start>{start}</start></timeInterval>"
        f"<resolution>{res}</resolution>{pts}</Period></TimeSeries></GL_MarketDocument>"
    )


def _epoch(iso: str) -> int:
    return int(datetime.fromisoformat(iso).replace(tzinfo=timezone.utc).timestamp())


# ─── parse ────────────────────────────────────────────────────────────────────


def test_parse_weekly_points():
    xml = _a72("2026-01-04T23:00Z", [21_000_000.0, 20_500_000.0, 20_100_000.0])
    pts = hydro.parse_reservoir_filling(xml)
    assert pts == [
        (_epoch("2026-01-04T23:00"), 21_000_000.0),
        (_epoch("2026-01-11T23:00"), 20_500_000.0),
        (_epoch("2026-01-18T23:00"), 20_100_000.0),
    ]


def test_parse_ignores_non_weekly_resolution():
    """Only P7D is the reservoir product; anything else is a different document."""
    xml = _a72("2026-01-04T23:00Z", [1.0] * 24, res="PT60M")
    assert hydro.parse_reservoir_filling(xml) == []


def test_parse_acknowledgement_is_empty():
    ack = '<?xml version="1.0"?><Acknowledgement_MarketDocument><Reason><code>999</code></Reason></Acknowledgement_MarketDocument>'
    assert hydro.parse_reservoir_filling(ack) == []


# ─── ingest ───────────────────────────────────────────────────────────────────


async def test_ingest_writes_series_for_hydro_zones(db_session, monkeypatch):
    seen = []

    async def fake_fetch(eic, year, *, overwrite=False):
        seen.append(eic)
        return _a72("2026-01-04T23:00Z", [21_000_000.0, 20_500_000.0])

    monkeypatch.setattr(hydro, "_fetch_zone_year", fake_fetch)
    monkeypatch.setattr(hydro.settings, "entsoe_api_token", SecretStr("tok"))

    r = await hydro.ingest_hydro(db_session, years=[2026], zones=["NO2", "CH"])

    assert r["written"] == 4  # 2 zones × 2 weekly points
    assert len(seen) == 2
    no2 = read_hourly(db_session, "hydro.reservoir", "NO2")
    assert no2[0] == (_epoch("2026-01-04T23:00"), 21_000_000.0)
    assert len(read_hourly(db_session, "hydro.reservoir", "CH")) == 2


async def test_ingest_skips_without_token(db_session, monkeypatch):
    monkeypatch.setattr(hydro.settings, "entsoe_api_token", None)
    assert await hydro.ingest_hydro(db_session, years=[2026]) == {"skipped": "no token"}


def test_hydro_zone_list_is_the_reservoir_geography():
    """The point of 'A72 light': reservoir zones, independent of ENABLED_ZONES."""
    for z in ("NO2", "SE1", "CH", "AT", "ES", "PT", "FR"):
        assert z in hydro.HYDRO_ZONES


# ─── route: filling vs the same week across prior years ──────────────────────


def _seed_history(db, zone: str, *, this_year_twh: float, prior_twh: list[float]) -> str:
    """One point per year at the same ISO week; returns the current point's date."""
    from backend.power.hourly_store import upsert_hourly

    now = datetime.now(timezone.utc)
    current = now - timedelta(days=7)
    pts = [(int(current.timestamp()), this_year_twh * 1e6)]
    for i, twh in enumerate(prior_twh, start=1):
        prior = current - timedelta(days=364 * i)  # 52 weeks → same ISO week
        pts.append((int(prior.timestamp()), twh * 1e6))
    upsert_hourly(db, "hydro.reservoir", zone, pts, unit="MWh")
    db.commit()
    return current.strftime("%Y-%m-%d")


def _client(db) -> TestClient:
    from backend.database import get_db
    from backend.main import app

    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app, raise_server_exceptions=True)


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    from backend.main import app

    app.dependency_overrides.clear()


def test_route_reports_filling_vs_same_week_band(db_session):
    _seed_history(db_session, "NO2", this_year_twh=15.0, prior_twh=[20.0, 21.0, 22.0])
    body = _client(db_session).get("/api/power/hydro").json()

    assert body["available"] is True
    zones = {z["zone"]: z for z in body["zones"]}
    no2 = zones["NO2"]
    assert no2["reservoir_twh"] == pytest.approx(15.0, abs=0.01)
    assert no2["band_min_twh"] == pytest.approx(20.0, abs=0.01)
    assert no2["band_max_twh"] == pytest.approx(22.0, abs=0.01)
    assert no2["band_n"] == 3
    assert no2["vs_band"] == "below", "15 TWh against a 20-22 band is below normal"


def test_route_within_band(db_session):
    _seed_history(db_session, "CH", this_year_twh=21.0, prior_twh=[20.0, 22.0])
    body = _client(db_session).get("/api/power/hydro").json()
    ch = {z["zone"]: z for z in body["zones"]}["CH"]
    assert ch["vs_band"] == "within"


def test_route_no_band_with_short_history(db_session):
    """First year of data: no prior same-week points → band absent, not fabricated."""
    _seed_history(db_session, "AT", this_year_twh=3.0, prior_twh=[])
    body = _client(db_session).get("/api/power/hydro").json()
    at = {z["zone"]: z for z in body["zones"]}["AT"]
    assert at["band_n"] == 0
    assert at["vs_band"] is None


def test_route_empty_is_honest(db_session):
    body = _client(db_session).get("/api/power/hydro").json()
    assert body["available"] is False
    assert "reason" in body
