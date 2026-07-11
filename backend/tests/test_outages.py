"""A77 generation unavailability → PowerOutage events + /api/power/outages.

Fixture structure mirrors the live API (spiked 2026-07-11, DE_LU): one
Unavailability_MarketDocument per outage message, delivered inside a ZIP.
Revision semantics are the core — of 31 live documents, 26 carried docStatus
A09 (withdrawn); showing those as active would put 26 ghost outages on the
desk. Only the highest revision per mRID counts, withdrawals hide the event.
"""

from __future__ import annotations

import io
import zipfile
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from backend.power import entsoe_outages as out

NS = "urn:iec62325.351:tc57wg16:451-6:outagedocument:3:0"


def _doc(
    mrid: str = "abc123",
    revision: int = 1,
    *,
    business_type: str = "A53",
    doc_status: str | None = None,
    start: str = "2026-07-10T22:00Z",
    end: str = "2026-08-21T14:00Z",
    unit_name: str = "Kraftwerk X Block 1",
    unit_eic: str = "11WD43VIWXHOILLM",
    psr: str = "B14",
    nominal_mw: float = 1400.0,
    available: list[tuple[int, float]] = ((1, 400.0),),
    location: str = "Somewhere",
) -> str:
    status = f"<docStatus><value>{doc_status}</value></docStatus>" if doc_status else ""
    points = "".join(
        f"<Point><position>{p}</position><quantity>{q}</quantity></Point>"
        for p, q in available
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Unavailability_MarketDocument xmlns="{NS}">
  <mRID>{mrid}</mRID>
  <revisionNumber>{revision}</revisionNumber>
  <type>A77</type>
  <createdDateTime>2026-07-01T08:00:00Z</createdDateTime>
  <unavailability_Time_Period.timeInterval>
    <start>{start}</start><end>{end}</end>
  </unavailability_Time_Period.timeInterval>
  {status}
  <TimeSeries>
    <mRID>1</mRID>
    <businessType>{business_type}</businessType>
    <biddingZone_Domain.mRID codingScheme="A01">10Y1001A1001A82H</biddingZone_Domain.mRID>
    <production_RegisteredResource.mRID codingScheme="A01">{unit_eic}</production_RegisteredResource.mRID>
    <production_RegisteredResource.name>{unit_name}</production_RegisteredResource.name>
    <production_RegisteredResource.location.name>{location}</production_RegisteredResource.location.name>
    <production_RegisteredResource.pSRType.psrType>{psr}</production_RegisteredResource.pSRType.psrType>
    <production_RegisteredResource.pSRType.powerSystemResources.nominalP unit="MAW">{nominal_mw}</production_RegisteredResource.pSRType.powerSystemResources.nominalP>
    <Available_Period>
      <timeInterval><start>{start}</start><end>{end}</end></timeInterval>
      <resolution>PT1M</resolution>
      {points}
    </Available_Period>
  </TimeSeries>
</Unavailability_MarketDocument>"""


def _zip(*docs: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for i, d in enumerate(docs):
            zf.writestr(f"doc_{i}.xml", d)
    return buf.getvalue()


# ─── parse ────────────────────────────────────────────────────────────────────


def test_parse_extracts_the_event():
    ev = out.parse_unavailability(_doc())
    assert ev["mrid"] == "abc123"
    assert ev["revision"] == 1
    assert ev["business_type"] == "A53"
    assert ev["status"] == "active"
    assert ev["unit_name"] == "Kraftwerk X Block 1"
    assert ev["unit_eic"] == "11WD43VIWXHOILLM"
    assert ev["psr_type"] == "B14"
    assert ev["nominal_mw"] == 1400.0
    assert ev["available_mw"] == 400.0
    assert ev["start_utc"] == "2026-07-10T22:00Z"
    assert ev["end_utc"] == "2026-08-21T14:00Z"


def test_parse_docstatus_a09_is_withdrawn():
    """26 of 31 live documents carried A09 — miss this and the desk shows ghosts."""
    ev = out.parse_unavailability(_doc(doc_status="A09"))
    assert ev["status"] == "withdrawn"


def test_parse_available_mw_is_the_minimum_over_the_window():
    """The Available_Period is a step function (curveType A03, sparse PT1M points);
    the panel's headline is the worst case, so take the minimum quantity."""
    ev = out.parse_unavailability(_doc(available=((1, 1968.0), (10561, 1912.0), (20000, 1950.0))))
    assert ev["available_mw"] == 1912.0


def test_parse_acknowledgement_returns_none():
    ack = '<?xml version="1.0"?><Acknowledgement_MarketDocument><Reason><code>999</code></Reason></Acknowledgement_MarketDocument>'
    assert out.parse_unavailability(ack) is None


# ─── ingest: revision upsert ──────────────────────────────────────────────────


async def _run_ingest(db, monkeypatch, zip_bytes: bytes, zone: str = "DE_LU"):
    async def fake_fetch(eic, window_start, window_end, offset, *, doc_type="A77"):
        return zip_bytes if offset == 0 else None

    monkeypatch.setattr(out, "_fetch_outages_page", fake_fetch)
    monkeypatch.setattr(out.settings, "entsoe_api_token", SecretStr("tok"))
    return await out.ingest_outages(db, zones=[zone])


async def test_ingest_stores_events(db_session, monkeypatch):
    r = await _run_ingest(db_session, monkeypatch, _zip(_doc(), _doc(mrid="other", revision=3)))
    assert r["written"] == 2
    rows = db_session.query(out.PowerOutage).all()
    assert {(x.mrid, x.revision) for x in rows} == {("abc123", 1), ("other", 3)}
    assert all(x.zone == "DE_LU" for x in rows)


async def test_ingest_is_idempotent_per_revision(db_session, monkeypatch):
    await _run_ingest(db_session, monkeypatch, _zip(_doc(revision=2)))
    r = await _run_ingest(db_session, monkeypatch, _zip(_doc(revision=2)))
    assert r["written"] == 0
    assert db_session.query(out.PowerOutage).count() == 1


async def test_ingest_keeps_every_revision(db_session, monkeypatch):
    """Revisions are history — the read side picks the highest, ingest keeps all."""
    await _run_ingest(db_session, monkeypatch, _zip(_doc(revision=1)))
    await _run_ingest(db_session, monkeypatch, _zip(_doc(revision=2, doc_status="A09")))
    assert db_session.query(out.PowerOutage).count() == 2


# ─── route: active outages, highest revision wins ─────────────────────────────


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


def _seed(db, **kw):
    now = datetime.now(timezone.utc)
    defaults = dict(
        mrid="m1", revision=1, doc_type="A77", zone="DE_LU", business_type="A53",
        psr_type="B14", unit_name="Unit", unit_eic="11WXX", location="X",
        nominal_mw=1400.0, available_mw=400.0,
        start_utc=(now - timedelta(days=2)).strftime("%Y-%m-%dT%H:%MZ"),
        end_utc=(now + timedelta(days=5)).strftime("%Y-%m-%dT%H:%MZ"),
        status="active",
    )
    defaults.update(kw)
    db.add(out.PowerOutage(**defaults))
    db.commit()


def test_route_reports_active_outages_with_mw_offline(db_session):
    _seed(db_session)
    body = _client(db_session).get("/api/power/outages?zone=DE_LU").json()
    assert body["available"] is True
    assert body["total_offline_mw"] == pytest.approx(1000.0)  # 1400 nominal − 400 available
    assert len(body["outages"]) == 1
    o = body["outages"][0]
    assert o["unit_name"] == "Unit"
    assert o["offline_mw"] == pytest.approx(1000.0)
    assert o["kind"] == "planned"


def test_route_highest_revision_wins_and_withdrawal_hides(db_session):
    _seed(db_session, revision=1)
    _seed(db_session, revision=5, status="withdrawn")
    body = _client(db_session).get("/api/power/outages?zone=DE_LU").json()
    assert body["outages"] == []


def test_route_excludes_ended_and_far_future(db_session):
    now = datetime.now(timezone.utc)
    _seed(db_session, mrid="past", end_utc=(now - timedelta(days=1)).strftime("%Y-%m-%dT%H:%MZ"))
    _seed(db_session, mrid="future", start_utc=(now + timedelta(days=40)).strftime("%Y-%m-%dT%H:%MZ"),
          end_utc=(now + timedelta(days=50)).strftime("%Y-%m-%dT%H:%MZ"))
    body = _client(db_session).get("/api/power/outages?zone=DE_LU").json()
    assert body["outages"] == [], "ended and >30d-out outages are not 'offline now'"
    upcoming = _client(db_session).get("/api/power/outages?zone=DE_LU&horizon_days=60").json()
    assert {o["mrid"] for o in upcoming["outages"]} == {"future"}


def test_route_forced_outages_are_flagged(db_session):
    _seed(db_session, business_type="A54")
    body = _client(db_session).get("/api/power/outages?zone=DE_LU").json()
    assert body["outages"][0]["kind"] == "forced"
    assert body["forced_offline_mw"] == pytest.approx(1000.0)


def test_route_empty_is_honest(db_session):
    body = _client(db_session).get("/api/power/outages?zone=DE_LU").json()
    assert body["available"] is False
    assert "reason" in body
