"""A78 transmission-infrastructure unavailability → PowerOutage + /api/power/outages.

Task P12 (see docs/findings/2026-07-20-umm-feasibility.md for why): A78 describes an
ASSET (interconnector/line/transformer — Asset_RegisteredResource), not a production
unit, and needs a DIRECTED zone pair (in_Domain/out_Domain) instead of A77's single
biddingZone_Domain. Fixture structure mirrors the live API (spiked 2026-07-21,
DE_LU<->FR): confirmed live findings this suite pins down —
  * in_Domain/out_Domain, NOT biddingZone_Domain (that fails "Mandatory parameter
    In_Domain is missing"); in_Domain==out_Domain (same zone twice) answers EMPTY.
  * both directions of one border return DISJOINT message sets (zero mRID overlap
    across a 7-week window) — ingest must query both, not just the canonical order.
  * Asset_RegisteredResource nests its OWN <mRID>/<name>, which local-name lookups
    must not confuse with the document's or the TimeSeries's <mRID>.
  * nominal_mw is NEVER published for a transmission asset (0/52 sampled) — every
    generation-only MW total (forced_outage_mw_now/_totals_now, outage_history's
    offline_mw_at) must keep excluding these rows even though they now share the table.
  * businessType (A53/A54) and docStatus/revision withdrawal semantics are IDENTICAL
    to A77.
"""

from __future__ import annotations

import io
import zipfile
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine, text

from backend.power import entsoe_outages as out

NS = "urn:iec62325.351:tc57wg16:451-6:outagedocument:3:0"

DE_LU_EIC = "10Y1001A1001A82H"
FR_EIC = "10YFR-RTE------C"


def _a77_doc(
    mrid: str = "gen1",
    revision: int = 1,
    *,
    business_type: str = "A53",
    doc_status: str | None = None,
    start: str = "2026-07-10T22:00Z",
    end: str = "2026-08-21T14:00Z",
) -> str:
    status = f"<docStatus><value>{doc_status}</value></docStatus>" if doc_status else ""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Unavailability_MarketDocument xmlns="{NS}">
  <mRID>{mrid}</mRID>
  <revisionNumber>{revision}</revisionNumber>
  <type>A77</type>
  <unavailability_Time_Period.timeInterval>
    <start>{start}</start><end>{end}</end>
  </unavailability_Time_Period.timeInterval>
  {status}
  <TimeSeries>
    <mRID>1</mRID>
    <businessType>{business_type}</businessType>
    <production_RegisteredResource.mRID codingScheme="A01">11WD43VIWXHOILLM</production_RegisteredResource.mRID>
    <production_RegisteredResource.name>Kraftwerk X</production_RegisteredResource.name>
    <production_RegisteredResource.pSRType.psrType>B14</production_RegisteredResource.pSRType.psrType>
    <production_RegisteredResource.pSRType.powerSystemResources.nominalP unit="MAW">1400.0</production_RegisteredResource.pSRType.powerSystemResources.nominalP>
    <Available_Period>
      <timeInterval><start>{start}</start><end>{end}</end></timeInterval>
      <resolution>PT1M</resolution>
      <Point><position>1</position><quantity>400</quantity></Point>
    </Available_Period>
  </TimeSeries>
</Unavailability_MarketDocument>"""


def _a78_doc(
    mrid: str = "asset1",
    revision: int = 1,
    *,
    business_type: str = "A53",
    doc_status: str | None = None,
    start: str = "2026-06-15T05:30Z",
    end: str = "2026-07-03T15:00Z",
    in_domain: str = DE_LU_EIC,
    out_domain: str = FR_EIC,
    asset_name: str = "Eichstetten-Vogelgrun",
    asset_eic: str = "10T-DE-FR-00003E",
    location: str = "cross-zonal",
    psr: str = "B21",
    available: tuple[tuple[int, float], ...] = ((1, 2500.0), (6751, 1150.0)),
) -> str:
    """Byte-for-byte shape of the live A78 sample (spiked 2026-07-21, DE_LU->FR)."""
    status = f"<docStatus><value>{doc_status}</value></docStatus>" if doc_status else ""
    points = "".join(
        f"<Point><position>{p}</position><quantity>{q}</quantity></Point>"
        for p, q in available
    )
    return f"""<?xml version="1.0" encoding="utf-8"?>
<Unavailability_MarketDocument xmlns="{NS}">
  <mRID>{mrid}</mRID>
  <revisionNumber>{revision}</revisionNumber>
  <type>A78</type>
  <process.processType>A26</process.processType>
  <createdDateTime>2026-06-23T15:34:35Z</createdDateTime>
  <unavailability_Time_Period.timeInterval>
    <start>{start}</start>
    <end>{end}</end>
  </unavailability_Time_Period.timeInterval>
  {status}
  <TimeSeries>
    <mRID>1</mRID>
    <businessType>{business_type}</businessType>
    <in_Domain.mRID codingScheme="A01">{in_domain}</in_Domain.mRID>
    <out_Domain.mRID codingScheme="A01">{out_domain}</out_Domain.mRID>
    <quantity_Measure_Unit.name>MAW</quantity_Measure_Unit.name>
    <curveType>A03</curveType>
    <Asset_RegisteredResource>
      <mRID codingScheme="A01">{asset_eic}</mRID>
      <name>{asset_name}</name>
      <asset_PSRType.psrType>{psr}</asset_PSRType.psrType>
      <location.name>{location}</location.name>
    </Asset_RegisteredResource>
    <Available_Period>
      <timeInterval>
        <start>{start}</start>
        <end>{end}</end>
      </timeInterval>
      <resolution>PT1M</resolution>
      {points}
    </Available_Period>
  </TimeSeries>
  <Reason>
    <code>B19</code>
    <text></text>
  </Reason>
</Unavailability_MarketDocument>"""


def _zip(*docs: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for i, d in enumerate(docs):
            zf.writestr(f"doc_{i}.xml", d)
    return buf.getvalue()


# ─── parse: A78's Asset_RegisteredResource shape ──────────────────────────────


def test_parse_a78_extracts_asset_and_domains():
    ev = out.parse_unavailability(_a78_doc())
    assert ev["mrid"] == "asset1"
    assert ev["unit_name"] == "Eichstetten-Vogelgrun"
    assert ev["unit_eic"] == "10T-DE-FR-00003E"
    assert ev["location"] == "cross-zonal"
    assert ev["psr_type"] == "B21"
    assert ev["business_type"] == "A53"
    assert ev["status"] == "active"
    assert ev["in_domain_eic"] == DE_LU_EIC
    assert ev["out_domain_eic"] == FR_EIC


def test_parse_a78_never_has_a_nominal_capacity():
    """Live spike (0/52 sampled documents): a transmission asset never publishes a
    nominalP-equivalent, only the reduced available_mw. offline_mw must therefore
    stay derivable-as-null, not silently default to 0."""
    ev = out.parse_unavailability(_a78_doc())
    assert ev["nominal_mw"] is None
    assert ev["available_mw"] == 1150.0  # min() over the step function, same rule as A77


def test_parse_a78_asset_mrid_is_not_shadowed_by_document_or_timeseries_mrid():
    """Document, TimeSeries, AND Asset_RegisteredResource each have their own <mRID> —
    a naive first-match global lookup would return the document's ID as the asset EIC."""
    ev = out.parse_unavailability(_a78_doc(mrid="DOC-ID-1", asset_eic="10T-REAL-ASSET-EIC"))
    assert ev["mrid"] == "DOC-ID-1"
    assert ev["unit_eic"] == "10T-REAL-ASSET-EIC"


def test_parse_a78_docstatus_a09_is_withdrawn():
    ev = out.parse_unavailability(_a78_doc(doc_status="A09"))
    assert ev["status"] == "withdrawn"


def test_parse_a78_forced_business_type():
    ev = out.parse_unavailability(_a78_doc(business_type="A54"))
    assert ev["business_type"] == "A54"


# ─── regression: A77 parsing is untouched by the A78 branch ──────────────────


def test_parse_a77_still_reads_production_registered_resource():
    ev = out.parse_unavailability(_a77_doc())
    assert ev["unit_name"] == "Kraftwerk X"
    assert ev["unit_eic"] == "11WD43VIWXHOILLM"
    assert ev["nominal_mw"] == 1400.0
    assert ev["available_mw"] == 400.0


def test_parse_a77_carries_no_domain_information():
    """A77 has no in_Domain/out_Domain concept at all — must stay None, never leak a
    stale value from a previous A78 parse in the same process."""
    ev = out.parse_unavailability(_a77_doc())
    assert ev["in_domain_eic"] is None
    assert ev["out_domain_eic"] is None


# ─── fetch: A78 needs in_Domain/out_Domain, not biddingZone_Domain ────────────


async def test_fetch_a78_sends_in_and_out_domain(monkeypatch):
    captured = {}

    class _FakeResponse:
        status_code = 200
        content = b"PK\x03\x04fake"

        def raise_for_status(self):
            pass

    class _FakeClient:
        def __init__(self, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None):
            captured.update(params or {})
            return _FakeResponse()

    monkeypatch.setattr(out.httpx, "AsyncClient", _FakeClient)
    monkeypatch.setattr(out.settings, "entsoe_api_token", SecretStr("tok"))
    await out._fetch_outages_page(
        DE_LU_EIC, "202607040000", "202608040000", 0,
        doc_type="A78", counterparty_eic=FR_EIC,
    )
    assert captured.get("in_Domain") == DE_LU_EIC
    assert captured.get("out_Domain") == FR_EIC
    assert "biddingZone_Domain" not in captured
    assert captured.get("documentType") == "A78"


# ─── ingest: A78 walks directed border pairs, not zones ───────────────────────


async def test_ingest_a78_queries_both_directions_of_a_real_border(db_session, monkeypatch):
    """DE_LU-FR is a real canonical border (border_registry.SCHEDULED_BORDERS). Both
    directions must be queried — the live spike found them fully disjoint."""
    calls = []

    async def fake_fetch(eic, window_start, window_end, offset, *, doc_type="A77", counterparty_eic=None):
        calls.append((eic, counterparty_eic))
        if offset != 0:
            return None
        if eic == DE_LU_EIC and counterparty_eic == FR_EIC:
            return _zip(_a78_doc(mrid="de-to-fr", in_domain=DE_LU_EIC, out_domain=FR_EIC))
        if eic == FR_EIC and counterparty_eic == DE_LU_EIC:
            return _zip(_a78_doc(mrid="fr-to-de", in_domain=FR_EIC, out_domain=DE_LU_EIC))
        return None

    monkeypatch.setattr(out, "_fetch_outages_page", fake_fetch)
    monkeypatch.setattr(out.settings, "entsoe_api_token", SecretStr("tok"))
    r = await out.ingest_outages(db_session, zones=["DE_LU", "FR"], doc_type="A78")

    assert r["written"] == 2
    assert r["pairs"] == 2
    assert set(calls) == {(DE_LU_EIC, FR_EIC), (FR_EIC, DE_LU_EIC)}
    rows = {(row.mrid, row.zone, row.counterparty_zone) for row in db_session.query(out.PowerOutage).all()}
    assert rows == {("de-to-fr", "DE_LU", "FR"), ("fr-to-de", "FR", "DE_LU")}
    assert all(row.doc_type == "A78" for row in db_session.query(out.PowerOutage).all())


async def test_ingest_a78_falls_back_to_raw_eic_when_counterparty_unmapped(db_session, monkeypatch):
    """A counterparty EIC outside ZONE_REGISTRY must not crash or vanish — keep it raw."""
    async def fake_fetch(eic, window_start, window_end, offset, *, doc_type="A77", counterparty_eic=None):
        if offset != 0:
            return None
        return _zip(_a78_doc(mrid="odd", in_domain=DE_LU_EIC, out_domain="10Y-UNMAPPED-EIC-X"))

    monkeypatch.setattr(out, "_fetch_outages_page", fake_fetch)
    monkeypatch.setattr(out.settings, "entsoe_api_token", SecretStr("tok"))
    await out.ingest_outages(db_session, zones=["DE_LU", "FR"], doc_type="A78")

    row = db_session.query(out.PowerOutage).filter_by(mrid="odd").first()
    assert row is not None
    assert row.zone == "DE_LU"
    assert row.counterparty_zone == "10Y-UNMAPPED-EIC-X"


async def test_ingest_a78_revision_and_withdrawal_semantics_match_a77(db_session, monkeypatch):
    async def fake_fetch(eic, window_start, window_end, offset, *, doc_type="A77", counterparty_eic=None):
        if offset != 0:
            return None
        if eic == DE_LU_EIC and counterparty_eic == FR_EIC:
            return _zip(_a78_doc(mrid="w1", revision=2, doc_status="A09"))
        return None

    monkeypatch.setattr(out, "_fetch_outages_page", fake_fetch)
    monkeypatch.setattr(out.settings, "entsoe_api_token", SecretStr("tok"))
    await out.ingest_outages(db_session, zones=["DE_LU", "FR"], doc_type="A78")

    row = db_session.query(out.PowerOutage).filter_by(mrid="w1").one()
    assert row.status == "withdrawn"
    assert row.revision == 2


# ─── regression: A77 ingest is byte-identical (still single biddingZone_Domain) ──


async def test_ingest_a77_still_uses_single_zone_loop_not_pairs(db_session, monkeypatch):
    calls = []

    async def fake_fetch(eic, window_start, window_end, offset, *, doc_type="A77"):
        calls.append(eic)
        return _zip(_a77_doc()) if offset == 0 else None

    monkeypatch.setattr(out, "_fetch_outages_page", fake_fetch)
    monkeypatch.setattr(out.settings, "entsoe_api_token", SecretStr("tok"))
    r = await out.ingest_outages(db_session, zones=["DE_LU"])

    assert r["written"] == 1
    assert calls == [DE_LU_EIC]
    row = db_session.query(out.PowerOutage).one()
    assert row.zone == "DE_LU" and row.counterparty_zone is None and row.doc_type == "A77"


# ─── latest_outage_revisions: doc_type scoping ────────────────────────────────


def _mixed_zone_rows(db, now):
    fmt = "%Y-%m-%dT%H:%MZ"
    common = dict(zone="DE_LU", business_type="A53", status="active",
                  start_utc=(now - timedelta(days=1)).strftime(fmt),
                  end_utc=(now + timedelta(days=1)).strftime(fmt))
    db.add(out.PowerOutage(mrid="gen", revision=1, doc_type="A77", **common))
    db.add(out.PowerOutage(mrid="trans", revision=1, doc_type="A78", counterparty_zone="FR", **common))
    db.commit()


def test_latest_outage_revisions_defaults_to_a77_only(db_session):
    from backend.signals.detectors.power import latest_outage_revisions

    now = datetime.now(timezone.utc)
    _mixed_zone_rows(db_session, now)

    assert {r.mrid for r in latest_outage_revisions(db_session, "DE_LU")} == {"gen"}
    assert {r.mrid for r in latest_outage_revisions(db_session, "DE_LU", doc_type="A78")} == {"trans"}
    assert {r.mrid for r in latest_outage_revisions(db_session, "DE_LU", doc_type=None)} == {"gen", "trans"}


def test_forced_outage_totals_ignore_transmission_rows_even_with_a_hypothetical_nominal(db_session):
    """Belt-and-braces: even if a future A78 schema DID carry a nominal figure (it does
    not today — 0/52 live-sampled), doc_type must still gate it out of the
    generation-only forced-MW totals, not just the nominal_mw-is-null coincidence."""
    from backend.signals.detectors.power import forced_outage_mw_now, forced_outage_totals_now

    now = datetime.now(timezone.utc)
    fmt = "%Y-%m-%dT%H:%MZ"
    common = dict(zone="DE_LU", business_type="A54", status="active", available_mw=0.0,
                  start_utc=(now - timedelta(days=1)).strftime(fmt),
                  end_utc=(now + timedelta(days=1)).strftime(fmt))
    db_session.add(out.PowerOutage(mrid="gen1", revision=1, doc_type="A77", nominal_mw=1000.0, **common))
    db_session.add(out.PowerOutage(mrid="trans1", revision=1, doc_type="A78", counterparty_zone="FR",
                                    nominal_mw=500.0, **common))
    db_session.commit()

    total, running = forced_outage_mw_now(db_session, "DE_LU")
    assert total == 1000.0
    assert {r.mrid for r in running} == {"gen1"}
    assert forced_outage_totals_now(db_session) == {"DE_LU": 1000.0}


# ─── route: /api/power/outages transmission section ───────────────────────────


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


def _seed_generation(db, **kw):
    now = datetime.now(timezone.utc)
    defaults = dict(
        mrid="g1", revision=1, doc_type="A77", zone="DE_LU", business_type="A53",
        psr_type="B14", unit_name="Unit", unit_eic="11WXX", location="X",
        nominal_mw=1400.0, available_mw=400.0,
        start_utc=(now - timedelta(days=2)).strftime("%Y-%m-%dT%H:%MZ"),
        end_utc=(now + timedelta(days=5)).strftime("%Y-%m-%dT%H:%MZ"),
        status="active",
    )
    defaults.update(kw)
    db.add(out.PowerOutage(**defaults))
    db.commit()


def _seed_transmission(db, **kw):
    now = datetime.now(timezone.utc)
    defaults = dict(
        mrid="t1", revision=1, doc_type="A78", zone="DE_LU", counterparty_zone="FR",
        business_type="A53", psr_type="B21", unit_name="Eichstetten-Vogelgrun",
        unit_eic="10T-DE-FR-00003E", location="cross-zonal",
        nominal_mw=None, available_mw=1150.0,
        start_utc=(now - timedelta(days=2)).strftime("%Y-%m-%dT%H:%MZ"),
        end_utc=(now + timedelta(days=5)).strftime("%Y-%m-%dT%H:%MZ"),
        status="active",
    )
    defaults.update(kw)
    db.add(out.PowerOutage(**defaults))
    db.commit()


def test_route_returns_transmission_section(db_session):
    _seed_transmission(db_session)
    body = _client(db_session).get("/api/power/outages?zone=DE_LU").json()
    assert body["available"] is True
    assert body["outages"] == []
    assert len(body["transmission"]) == 1
    t = body["transmission"][0]
    assert t["asset_name"] == "Eichstetten-Vogelgrun"
    assert t["asset_eic"] == "10T-DE-FR-00003E"
    assert t["counterparty_zone"] == "FR"
    assert t["asset_type"] == "AC Line"
    assert t["available_mw"] == 1150.0
    assert t["offline_mw"] is None  # no nominal baseline to subtract from
    assert t["kind"] == "planned"
    assert t["running_now"] is True


def test_route_generation_and_transmission_are_both_present_by_default(db_session):
    _seed_generation(db_session)
    _seed_transmission(db_session)
    body = _client(db_session).get("/api/power/outages?zone=DE_LU").json()
    assert len(body["outages"]) == 1
    assert len(body["transmission"]) == 1
    assert body["total_offline_mw"] == pytest.approx(1000.0)  # generation-only, unaffected


def test_route_kind_generation_excludes_transmission(db_session):
    _seed_generation(db_session)
    _seed_transmission(db_session)
    body = _client(db_session).get("/api/power/outages?zone=DE_LU&kind=generation").json()
    assert len(body["outages"]) == 1
    assert body["transmission"] == []


def test_route_kind_transmission_excludes_generation(db_session):
    _seed_generation(db_session)
    _seed_transmission(db_session)
    body = _client(db_session).get("/api/power/outages?zone=DE_LU&kind=transmission").json()
    assert body["outages"] == []
    assert len(body["transmission"]) == 1
    assert body["total_offline_mw"] is None
    assert body["forced_offline_mw"] is None


def test_route_forced_offline_mw_excludes_transmission(db_session):
    _seed_generation(db_session, business_type="A54")
    _seed_transmission(db_session, business_type="A54")
    body = _client(db_session).get("/api/power/outages?zone=DE_LU").json()
    assert body["forced_offline_mw"] == pytest.approx(1000.0)


def test_route_transmission_highest_revision_wins_and_withdrawal_hides(db_session):
    _seed_transmission(db_session, revision=1)
    _seed_transmission(db_session, revision=5, status="withdrawn")
    body = _client(db_session).get("/api/power/outages?zone=DE_LU").json()
    assert body["transmission"] == []


def test_route_transmission_only_zone_is_still_available(db_session):
    """A zone with ONLY A78 messages (no A77 at all) must not hit the 'unavailable'
    early return — it just has an empty generation list."""
    _seed_transmission(db_session)
    body = _client(db_session).get("/api/power/outages?zone=DE_LU").json()
    assert body["available"] is True


def test_route_empty_kind_all_is_unavailable(db_session):
    body = _client(db_session).get("/api/power/outages?zone=DE_LU").json()
    assert body["available"] is False


# ─── freshness: A78 gets its own watchdog probe ───────────────────────────────


def test_transmission_outage_collector_is_watched(db_session):
    from backend.collectors.freshness import SPECS, evaluate_freshness

    spec = next(s for s in SPECS if s.key == "power_outages_transmission")
    assert spec.filter_col == "doc_type"
    assert spec.filter_val == "A78"

    db_session.add(out.PowerOutage(
        mrid="t1", revision=1, doc_type="A78", zone="DE_LU", counterparty_zone="FR",
        business_type="A53", start_utc="2026-07-01T00:00Z", end_utc="2026-08-01T00:00Z",
        status="active",
    ))
    db_session.commit()
    result = evaluate_freshness(db_session)
    assert result["power_outages_transmission"]["fresh"] is True
    # A78 volume is much lower than A77's — the window must not be TIGHTER than A77's,
    # or quiet weeks on real borders would false-alarm.
    a77_spec = next(s for s in SPECS if s.key == "power_outages")
    assert spec.max_age >= a77_spec.max_age


def test_transmission_outage_collector_reports_stale_when_silent(db_session):
    from backend.collectors.freshness import evaluate_freshness

    db_session.add(out.PowerOutage(
        mrid="old", revision=1, doc_type="A78", zone="DE_LU", counterparty_zone="FR",
        business_type="A53", start_utc="2020-01-01T00:00Z", end_utc="2020-02-01T00:00Z",
        status="active",
        created_at=datetime(2020, 1, 1),
    ))
    db_session.commit()
    result = evaluate_freshness(db_session, now=datetime(2026, 7, 21, tzinfo=timezone.utc))
    assert result["power_outages_transmission"]["fresh"] is False


# ─── migration: counterparty_zone is idempotent ───────────────────────────────


def test_counterparty_zone_migration_is_idempotent(tmp_path, monkeypatch):
    import backend.migrations as migrations
    from backend.database import Base

    db_path = tmp_path / "mig.db"
    test_engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(bind=test_engine)
    monkeypatch.setattr(migrations, "engine", test_engine)

    with test_engine.begin() as conn:
        conn.execute(text("ALTER TABLE power_outage DROP COLUMN counterparty_zone"))
    assert "counterparty_zone" not in migrations._existing_columns("power_outage")

    migrations.run_migrations()
    assert "counterparty_zone" in migrations._existing_columns("power_outage")

    # Idempotent: running again must not raise or duplicate the column.
    migrations.run_migrations()
    assert "counterparty_zone" in migrations._existing_columns("power_outage")
