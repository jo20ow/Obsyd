"""ENTSO-E procured balancing-capacity prices (A15: FCR/aFRR/mFRR) → hourly
`capacity.<fcr.price|afrr.price.pos|afrr.price.neg|mfrr.price.pos|mfrr.price.neg>` (measure
BEFORE direction — unified 2026-07-21 to the repo-wide `family.product.measure[.direction]`
grammar, matching `balancing.afrr.price.up`). Mirrors test_balancing.py's shape, adapted for
A15's different pagination/error semantics (see entsoe_reserves.py's docstring)."""
from __future__ import annotations

import httpx
import pytest

from backend.models.energy import PowerHourly, SeriesDim, ZoneDim  # noqa: F401 — register tables
from backend.power import entsoe_reserves as cap
from backend.power.entsoe_reserves import (
    aggregate_bids,
    parse_capacity_document,
)
from backend.power.hourly_store import read_hourly

NS = "urn:iec62325.351:tc57wg16:451-6:balancingdocument:4:1"


def _capacity_doc(process_type, bids, start="2026-06-01T00:00Z", end="2026-06-01T04:00Z"):
    """Build a Balancing_MarketDocument with one TimeSeries per bid.

    `bids`: list of {"direction": "A01"/"A02"/"A03", "quantity": float, "price": float}.
    All bids share the SAME block [start, end) unless a bid overrides start/end itself.
    """
    ts_blocks = []
    for i, b in enumerate(bids, start=1):
        b_start = b.get("start", start)
        b_end = b.get("end", end)
        ts_blocks.append(
            f"<TimeSeries><mRID>{i}</mRID><businessType>B95</businessType>"
            f"<flowDirection.direction>{b['direction']}</flowDirection.direction>"
            f"<curveType>A03</curveType><Period>"
            f"<timeInterval><start>{b_start}</start><end>{b_end}</end></timeInterval>"
            f"<resolution>PT15M</resolution>"
            f"<Point><position>1</position><quantity>{b['quantity']}</quantity>"
            f"<procurement_Price.amount>{b['price']}</procurement_Price.amount></Point>"
            f"</Period></TimeSeries>"
        )
    return (
        f'<?xml version="1.0"?><Balancing_MarketDocument xmlns="{NS}">'
        f"<process.processType>{process_type}</process.processType>"
        + "".join(ts_blocks)
        + "</Balancing_MarketDocument>"
    )


# ─── aggregate_bids ────────────────────────────────────────────────────────────


def test_aggregate_bids_weighted_average():
    out = aggregate_bids([(10.0, 20.0), (30.0, 10.0)])
    assert out["weighted_avg"] == pytest.approx((10 * 20 + 30 * 10) / 40)
    assert out["marginal"] == 20.0
    assert out["total_qty"] == 40.0


def test_aggregate_bids_marginal_is_max_not_weighted_avg():
    """The marginal (highest accepted bid) and the weighted average must differ whenever bid
    sizes aren't uniform — proving these are genuinely two different numbers, not aliases."""
    out = aggregate_bids([(90.0, 5.0), (10.0, 50.0)])
    assert out["weighted_avg"] == pytest.approx((90 * 5 + 10 * 50) / 100)
    assert out["marginal"] == 50.0
    assert out["weighted_avg"] != out["marginal"]


def test_aggregate_bids_ignores_non_positive_quantity():
    out = aggregate_bids([(0.0, 100.0), (-5.0, 200.0), (10.0, 30.0)])
    assert out["weighted_avg"] == 30.0
    assert out["marginal"] == 30.0
    assert out["total_qty"] == 10.0


def test_aggregate_bids_empty_is_all_none():
    out = aggregate_bids([])
    assert out == {"weighted_avg": None, "marginal": None, "total_qty": 0.0}


# ─── parse_capacity_document ────────────────────────────────────────────────────


def test_parse_fcr_symmetric_weighted_average_and_quartered_normalization():
    """FCR (A52) ignores flowDirection entirely (symmetric) and its block price (EUR per 4h)
    is divided by 4 to land in the shared EUR/MW/h unit every series uses."""
    xml = _capacity_doc("A52", [
        {"direction": "A03", "quantity": 10.0, "price": 20.0},
        {"direction": "A03", "quantity": 30.0, "price": 10.0},
    ])
    out = parse_capacity_document(xml)
    assert set(out.keys()) == {"fcr.price"}
    weighted_avg_block = (10 * 20 + 30 * 10) / 40  # 12.5 EUR/MW per 4h block
    expected = weighted_avg_block / 4.0  # 3.125 EUR/MW/h
    day = out["fcr.price"]["2026-06-01"]
    assert day == {0: pytest.approx(expected), 1: pytest.approx(expected),
                   2: pytest.approx(expected), 3: pytest.approx(expected)}


def test_parse_afrr_splits_by_direction_no_normalization():
    """aFRR (A51) is already EUR/MW/h (pay-as-bid) — no /4 division — and splits pos/neg."""
    xml = _capacity_doc("A51", [
        {"direction": "A01", "quantity": 100.0, "price": 8.0},
        {"direction": "A02", "quantity": 50.0, "price": 3.0},
    ])
    out = parse_capacity_document(xml)
    assert set(out.keys()) == {"afrr.price.pos", "afrr.price.neg"}
    assert out["afrr.price.pos"]["2026-06-01"][0] == 8.0
    assert out["afrr.price.neg"]["2026-06-01"][0] == 3.0


def test_parse_mfrr_splits_by_direction():
    xml = _capacity_doc("A47", [
        {"direction": "A01", "quantity": 10.0, "price": 5.0},
        {"direction": "A02", "quantity": 10.0, "price": 2.0},
    ])
    out = parse_capacity_document(xml)
    assert set(out.keys()) == {"mfrr.price.pos", "mfrr.price.neg"}
    assert out["mfrr.price.pos"]["2026-06-01"][0] == 5.0
    assert out["mfrr.price.neg"]["2026-06-01"][0] == 2.0


def test_parse_marginal_is_never_the_stored_value():
    """The canonical stored series is the weighted average, not the marginal (highest
    accepted) bid — the two must differ here, and the stored value must equal the former."""
    xml = _capacity_doc("A51", [
        {"direction": "A01", "quantity": 90.0, "price": 5.0},
        {"direction": "A01", "quantity": 10.0, "price": 50.0},
    ])
    out = parse_capacity_document(xml)
    stored = out["afrr.price.pos"]["2026-06-01"][0]
    agg = aggregate_bids([(90.0, 5.0), (10.0, 50.0)])
    assert stored == pytest.approx(agg["weighted_avg"])
    assert stored != agg["marginal"]
    assert agg["marginal"] == 50.0


def test_parse_densifies_across_all_hours_in_the_block():
    xml = _capacity_doc("A51", [{"direction": "A01", "quantity": 1.0, "price": 42.0}],
                        start="2026-06-01T08:00Z", end="2026-06-01T12:00Z")
    day = parse_capacity_document(xml)["afrr.price.pos"]["2026-06-01"]
    assert set(day.keys()) == {8, 9, 10, 11}
    assert all(v == 42.0 for v in day.values())


def test_parse_block_crossing_midnight_splits_across_two_days():
    """A real block's UTC boundaries shift with DST (spiked live: the first local block of a
    CEST day runs 22:00Z the PREVIOUS day to 02:00Z) — hours must land under their OWN date,
    not all be attributed to one day."""
    xml = _capacity_doc("A52", [{"direction": "A03", "quantity": 5.0, "price": 8.0}],
                        start="2026-05-31T22:00Z", end="2026-06-01T02:00Z")
    out = parse_capacity_document(xml)["fcr.price"]
    assert set(out["2026-05-31"].keys()) == {22, 23}
    assert set(out["2026-06-01"].keys()) == {0, 1}
    expected = 8.0 / 4.0
    assert out["2026-05-31"][22] == pytest.approx(expected)
    assert out["2026-06-01"][1] == pytest.approx(expected)


def test_parse_unrecognised_direction_is_skipped_not_guessed():
    xml = _capacity_doc("A51", [{"direction": "A99", "quantity": 1.0, "price": 1.0}])
    assert parse_capacity_document(xml) == {}


def test_parse_ignores_non_b95_business_type():
    xml = (
        f'<?xml version="1.0"?><Balancing_MarketDocument xmlns="{NS}">'
        "<process.processType>A51</process.processType>"
        "<TimeSeries><mRID>1</mRID><businessType>A96</businessType>"
        "<flowDirection.direction>A01</flowDirection.direction><curveType>A03</curveType>"
        "<Period><timeInterval><start>2026-06-01T00:00Z</start><end>2026-06-01T04:00Z</end>"
        "</timeInterval><resolution>PT15M</resolution>"
        "<Point><position>1</position><quantity>1</quantity>"
        "<procurement_Price.amount>1</procurement_Price.amount></Point></Period></TimeSeries>"
        "</Balancing_MarketDocument>"
    )
    assert parse_capacity_document(xml) == {}


def test_parse_acknowledgement_is_empty():
    ack = (
        '<?xml version="1.0"?><Acknowledgement_MarketDocument>'
        "<mRID>x</mRID></Acknowledgement_MarketDocument>"
    )
    assert parse_capacity_document(ack) == {}


def test_parse_unknown_process_type_is_empty():
    xml = _capacity_doc("A99", [{"direction": "A01", "quantity": 1.0, "price": 1.0}])
    assert parse_capacity_document(xml) == {}


def test_parse_malformed_raises():
    with pytest.raises(ValueError):
        parse_capacity_document("<not-xml")


# ─── ZIP unwrap: multiple inner XML documents merge ────────────────────────────


async def test_fetch_multi_xml_zip_members_merge(tmp_path, monkeypatch):
    import io
    import zipfile

    doc1 = _capacity_doc("A51", [{"direction": "A01", "quantity": 1.0, "price": 111.0}])
    doc2 = _capacity_doc("A51", [{"direction": "A02", "quantity": 1.0, "price": 222.0}])
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("doc1.xml", doc1)
        zf.writestr("doc2.xml", doc2)
    zip_bytes = buf.getvalue()

    class _FakeResp:
        status_code = 200
        content = zip_bytes
        text = ""

        def raise_for_status(self):
            pass

    class _FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, *a, **kw):
            return _FakeResp()

    import backend.gas.raw_cache as rc

    monkeypatch.setattr(rc, "DATA_ROOT", tmp_path)
    monkeypatch.setattr(cap.httpx, "AsyncClient", _FakeClient)
    monkeypatch.setattr(cap, "_token", lambda: "tok")

    from datetime import date

    docs = await cap._fetch_capacity_day("A51", date(2026, 6, 1))
    merged: dict = {}
    for xml in docs:
        for key, days in parse_capacity_document(xml).items():
            merged.setdefault(key, {}).update(days)
    assert merged["afrr.price.pos"]["2026-06-01"][0] == 111.0
    assert merged["afrr.price.neg"]["2026-06-01"][0] == 222.0


# ─── pagination loop ────────────────────────────────────────────────────────────


def _n_bid_doc(n, price_start=1.0):
    """A single-page A51 document with N distinct-priced TimeSeries."""
    bids = [{"direction": "A01", "quantity": 1.0, "price": price_start + i} for i in range(n)]
    return _capacity_doc("A51", bids)


class _PagingClient:
    """Fake httpx.AsyncClient serving canned pages by offset, counting calls."""

    def __init__(self, pages: dict[int, bytes]):
        self._pages = pages
        self.calls: list[int] = []

    def __call__(self, *a, **kw):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, params=None):
        offset = params["offset"]
        self.calls.append(offset)
        body = self._pages[offset]
        return httpx.Response(200, content=body, request=httpx.Request("GET", url, params=params))


async def test_pagination_stops_on_short_page(db_session, tmp_path, monkeypatch):
    """Two full 100-entry pages then a short (< 100) page: 3 requests, all docs merged.

    All 240 bids across the 3 pages belong to the SAME (afrr.price.pos) block — this is the
    regression case for aggregating per page and merging via dict.update: the LAST page's
    partial average would silently win instead of the weighted average over all 240 bids.
    """
    import backend.gas.raw_cache as rc

    pages = {
        0: _n_bid_doc(100, price_start=1.0).encode(),      # prices 1..100,   sum=5050
        100: _n_bid_doc(100, price_start=200.0).encode(),  # prices 200..299, sum=24950
        200: _n_bid_doc(40, price_start=400.0).encode(),    # prices 400..439, sum=16780
    }
    client = _PagingClient(pages)
    monkeypatch.setattr(rc, "DATA_ROOT", tmp_path)
    monkeypatch.setattr(cap.httpx, "AsyncClient", client)
    monkeypatch.setattr(cap, "_token", lambda: "tok")

    from datetime import date

    docs = await cap._fetch_capacity_day("A51", date(2026, 6, 1))
    assert client.calls == [0, 100, 200]
    assert len(docs) == 3

    # Combining every page's RAW bids for the block before aggregating (the fix) yields the
    # true weighted average over all 240 bids (quantity is uniform 1.0, so this is a plain
    # mean): (5050 + 24950 + 16780) / 240.
    merged_raw: dict = {}
    for xml in docs:
        for key, pairs in cap.parse_capacity_bids(xml).items():
            merged_raw.setdefault(key, []).extend(pairs)
    assert len(merged_raw) == 1  # one block only
    ((suffix, _start, _end), pairs), = merged_raw.items()
    assert suffix == "afrr.price.pos"
    assert len(pairs) == 240
    correct_avg = aggregate_bids(pairs)["weighted_avg"]
    assert correct_avg == pytest.approx((5050 + 24950 + 16780) / 240)  # 194.91666...

    # The regression this guards: aggregating PER PAGE (parse_capacity_document, which runs
    # aggregate_bids WITHIN one document) and then merging by (day, hour) via dict.update lets
    # the LAST page's partial average silently overwrite every earlier page's — concretely a
    # different (and, since bids arrive in ascending-price/merit order, systematically biased
    # high) number here, not the correct 194.92.
    old_style_value = None
    for xml in docs:
        parsed = parse_capacity_document(xml)
        if "afrr.price.pos" in parsed:
            old_style_value = parsed["afrr.price.pos"]["2026-06-01"][0]
    assert old_style_value == pytest.approx(419.5)  # mean of the LAST page alone (400..439)
    assert old_style_value != pytest.approx(correct_avg)

    # And the production entry point (ingest_capacity_prices) must land on the CORRECT
    # cross-page value in the actual hourly store, not the old per-page-last-wins one.
    async def fake_fetch(process_type, day, *, overwrite=False):
        return docs if process_type == "A51" else []

    monkeypatch.setattr(cap.settings, "entsoe_api_token", "x")
    monkeypatch.setattr(cap, "_fetch_capacity_day", fake_fetch)
    r = await cap.ingest_capacity_prices(db_session, ["2026-06-01"], overwrite=True)
    assert r["written"] > 0
    stored = read_hourly(db_session, "capacity.afrr.price.pos", "DE_LU")
    assert stored
    assert stored[0][1] == pytest.approx(correct_avg)
    assert stored[0][1] != pytest.approx(old_style_value)


async def test_pagination_stops_on_clean_empty_acknowledgement(tmp_path, monkeypatch):
    """A15's real 'no more data' shape: a clean HTTP 200 zero-TimeSeries Acknowledgement,
    not a 400 (see module docstring) — a single request must be enough to recognise it."""
    import backend.gas.raw_cache as rc

    ack = (
        b'<?xml version="1.0"?><Acknowledgement_MarketDocument>'
        b"<mRID>x</mRID></Acknowledgement_MarketDocument>"
    )
    client = _PagingClient({0: ack})
    monkeypatch.setattr(rc, "DATA_ROOT", tmp_path)
    monkeypatch.setattr(cap.httpx, "AsyncClient", client)
    monkeypatch.setattr(cap, "_token", lambda: "tok")

    from datetime import date

    docs = await cap._fetch_capacity_day("A51", date(2026, 6, 1))
    assert client.calls == [0]
    assert docs == [ack.decode()]


async def test_pagination_hard_stop_is_a_safety_valve_not_the_real_limit(tmp_path, monkeypatch):
    """If a feed pathologically never returned a short page, the loop must still terminate at
    _MAX_OFFSET rather than looping forever — verified live 2026-07-21 that real ENTSO-E data
    can exceed the PRIOR feasibility spike's assumed 4900 cap (a real aFRR day reached 6893),
    so this cap must be generous, but it must still exist."""
    import backend.gas.raw_cache as rc

    class _AlwaysFullClient:
        def __init__(self):
            self.calls = 0

        def __call__(self, *a, **kw):
            return self

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None):
            self.calls += 1
            body = _n_bid_doc(100, price_start=float(params["offset"])).encode()
            return httpx.Response(200, content=body, request=httpx.Request("GET", url, params=params))

    client = _AlwaysFullClient()
    monkeypatch.setattr(rc, "DATA_ROOT", tmp_path)
    monkeypatch.setattr(cap.httpx, "AsyncClient", client)
    monkeypatch.setattr(cap, "_token", lambda: "tok")
    monkeypatch.setattr(cap, "_MAX_OFFSET", 300)  # shrink the cap so the test is fast

    from datetime import date

    docs = await cap._fetch_capacity_day("A51", date(2026, 6, 1))
    # offsets 0,100,200,300 all fetched (300 == the shrunk cap, inclusive), then stop.
    assert client.calls == 4
    assert len(docs) == 4


# ─── error discipline: A15 has no "structural 400 = empty" case (unlike A83/A84) ───────────


async def test_fetch_400_always_raises_never_cached_as_empty(tmp_path, monkeypatch):
    """Verified live 2026-07-21: EVERY no-data case for A15 (future date, wrong domain,
    exhausted pagination) comes back as a clean HTTP 200 empty Acknowledgement, never a 400.
    So, unlike entsoe_balancing.py's A83/A84 fetchers, ANY 400 here is a genuine failure and
    must raise — there is no text to match as 'this 400 actually means no data'."""
    import backend.gas.raw_cache as rc

    monkeypatch.setattr(rc, "DATA_ROOT", tmp_path)
    body = b"The provided parameters do not match the definition of the request"
    monkeypatch.setattr(cap.httpx, "AsyncClient", _PagingClientRaising(400, body))
    monkeypatch.setattr(cap, "_token", lambda: "tok")

    from datetime import date

    with pytest.raises(httpx.HTTPStatusError):
        await cap._fetch_capacity_day("A51", date(2026, 6, 1))
    assert rc.read_cached("entsoe_a15", "A51_2026-06-01", date(2026, 6, 1)) is None


async def test_fetch_401_raises_and_is_not_cached(tmp_path, monkeypatch):
    import backend.gas.raw_cache as rc

    monkeypatch.setattr(rc, "DATA_ROOT", tmp_path)
    monkeypatch.setattr(cap.httpx, "AsyncClient", _PagingClientRaising(401, b"Unauthorized"))
    monkeypatch.setattr(cap, "_token", lambda: "tok")

    from datetime import date

    with pytest.raises(httpx.HTTPStatusError):
        await cap._fetch_capacity_day("A51", date(2026, 6, 1))
    assert rc.read_cached("entsoe_a15", "A51_2026-06-01", date(2026, 6, 1)) is None


async def test_fetch_500_raises_and_is_not_cached(tmp_path, monkeypatch):
    import backend.gas.raw_cache as rc

    monkeypatch.setattr(rc, "DATA_ROOT", tmp_path)
    monkeypatch.setattr(cap.httpx, "AsyncClient", _PagingClientRaising(503, b"Service Unavailable"))
    monkeypatch.setattr(cap, "_token", lambda: "tok")

    from datetime import date

    with pytest.raises(httpx.HTTPStatusError):
        await cap._fetch_capacity_day("A47", date(2026, 6, 1))
    assert rc.read_cached("entsoe_a15", "A47_2026-06-01", date(2026, 6, 1)) is None


class _PagingClientRaising:
    """Fake httpx.AsyncClient answering every request with one canned error Response."""

    def __init__(self, status_code: int, content: bytes):
        self._status_code = status_code
        self._content = content

    def __call__(self, *a, **kw):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, params=None):
        return httpx.Response(self._status_code, content=self._content, request=httpx.Request("GET", url, params=params))


# ─── ingest_capacity_prices ──────────────────────────────────────────────────────


async def test_ingest_no_token_skips(db_session, monkeypatch):
    monkeypatch.setattr(cap.settings, "entsoe_api_token", None)
    r = await cap.ingest_capacity_prices(db_session, ["2026-06-01"])
    assert r == {"skipped": "no token"}


async def test_ingest_no_days_returns_zero(db_session, monkeypatch):
    monkeypatch.setattr(cap.settings, "entsoe_api_token", "x")
    r = await cap.ingest_capacity_prices(db_session, [])
    assert r["days"] == 0


async def test_ingest_writes_all_five_series(db_session, monkeypatch):
    monkeypatch.setattr(cap.settings, "entsoe_api_token", "x")

    fcr_xml = _capacity_doc("A52", [{"direction": "A03", "quantity": 10.0, "price": 40.0}])
    afrr_xml = _capacity_doc("A51", [
        {"direction": "A01", "quantity": 10.0, "price": 8.0},
        {"direction": "A02", "quantity": 10.0, "price": 3.0},
    ])
    mfrr_xml = _capacity_doc("A47", [
        {"direction": "A01", "quantity": 10.0, "price": 5.0},
        {"direction": "A02", "quantity": 10.0, "price": 2.0},
    ])

    async def fake_fetch(process_type, day, *, overwrite=False):
        return {"A52": [fcr_xml], "A51": [afrr_xml], "A47": [mfrr_xml]}[process_type]

    monkeypatch.setattr(cap, "_fetch_capacity_day", fake_fetch)
    r = await cap.ingest_capacity_prices(db_session, ["2026-06-01"], overwrite=True)
    assert r["written"] > 0

    assert read_hourly(db_session, "capacity.fcr.price", "DE_LU")[0][1] == pytest.approx(10.0)
    assert read_hourly(db_session, "capacity.afrr.price.pos", "DE_LU")[0][1] == 8.0
    assert read_hourly(db_session, "capacity.afrr.price.neg", "DE_LU")[0][1] == 3.0
    assert read_hourly(db_session, "capacity.mfrr.price.pos", "DE_LU")[0][1] == 5.0
    assert read_hourly(db_session, "capacity.mfrr.price.neg", "DE_LU")[0][1] == 2.0


async def test_ingest_isolates_fetch_failure_per_process_type(db_session, monkeypatch):
    monkeypatch.setattr(cap.settings, "entsoe_api_token", "x")
    afrr_xml = _capacity_doc("A51", [{"direction": "A01", "quantity": 1.0, "price": 9.0}])

    async def fake_fetch(process_type, day, *, overwrite=False):
        if process_type == "A52":
            raise httpx.HTTPError("boom")
        return [afrr_xml] if process_type == "A51" else []

    monkeypatch.setattr(cap, "_fetch_capacity_day", fake_fetch)
    r = await cap.ingest_capacity_prices(db_session, ["2026-06-01"], overwrite=True)
    assert read_hourly(db_session, "capacity.afrr.price.pos", "DE_LU")
    assert read_hourly(db_session, "capacity.fcr.price", "DE_LU") == []
    assert r["written"] > 0


async def test_ingest_has_no_zone_parameter():
    """DE_LU only, by design — this ingest function must not accept a `zone` kwarg."""
    import inspect

    sig = inspect.signature(cap.ingest_capacity_prices)
    assert "zone" not in sig.parameters


# ─── freshness spec + PANEL_MAX_AGE_DAYS mirror ─────────────────────────────────


def test_freshness_spec_registered():
    from backend.collectors.freshness import SPECS

    spec = next((s for s in SPECS if s.key == "capacity_prices"), None)
    assert spec is not None
    assert spec.hourly_series == "capacity.fcr.price"
    assert spec.max_age.days == 2


def test_panel_max_age_mirrors_freshness_spec():
    from backend.collectors.freshness import SPECS
    from backend.routes.power import PANEL_MAX_AGE_DAYS

    spec = next(s for s in SPECS if s.key == "capacity_prices")
    assert PANEL_MAX_AGE_DAYS["capacity_prices"] == spec.max_age.days


# ─── backfill registration ──────────────────────────────────────────────────────


def test_backfill_source_registered():
    from backend.scripts import power_backfill as pb

    assert "capacity" in pb.ALL_SOURCES


async def test_backfill_capacity_runs_once_per_month_not_per_zone(monkeypatch):
    """`ingest_capacity_prices` is imported LOCALLY inside run_backfill's "capacity" branch
    (like the "scheduled"/"netpos" sources) rather than at power_backfill.py's module level —
    so the fake must be patched on the SOURCE module, which the local import re-resolves at
    call time, not on the `pb` module itself."""
    from datetime import date

    from backend.scripts import power_backfill as pb

    calls = []

    async def _capacity(db, days, **kwargs):
        calls.append((days[0], days[-1], kwargs))

    async def _noop(*a, **k):
        pass

    monkeypatch.setattr(cap, "ingest_capacity_prices", _capacity)
    monkeypatch.setattr(pb, "ingest_day_ahead", _noop)

    res = await pb.run_backfill(
        db=None, start=date(2026, 1, 1), end=date(2026, 2, 28),
        zones=["DE_LU", "FR"], sources={"price", "capacity"},
        overwrite=False, dry_run=False, throttle=0,
    )
    assert res["capacity_months"] == 2
    assert [(c[0], c[1]) for c in calls] == [
        ("2026-01-01", "2026-01-31"), ("2026-02-01", "2026-02-28"),
    ]


# ─── GET /api/power/capacity-prices ─────────────────────────────────────────────


def _client(db):
    from fastapi.testclient import TestClient

    from backend.database import get_db
    from backend.main import app

    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    from backend.main import app
    app.dependency_overrides.clear()


def _seed_capacity(db):
    import time as _time

    from backend.power.hourly_store import upsert_hourly

    now = (int(_time.time()) // 3600) * 3600
    for suffix, price in (
        ("fcr.price", 3.0), ("afrr.price.pos", 8.0), ("afrr.price.neg", 3.5),
        ("mfrr.price.pos", 5.0), ("mfrr.price.neg", 2.0),
    ):
        points = [(now - i * 3600, price + i * 0.01) for i in range(24, 0, -1)]
        upsert_hourly(db, f"capacity.{suffix}", "DE_LU", points, unit="EUR/MW/h")


def test_route_happy_path(db_session):
    _seed_capacity(db_session)
    body = _client(db_session).get("/api/power/capacity-prices?zone=DE_LU").json()
    assert body["available"] is True
    assert body["zone"] == "DE_LU"
    assert body["zones"] == ["DE_LU"]
    assert body["unit"] == "EUR/MW/h"
    for key in ("fcr", "afrr_pos", "afrr_neg", "mfrr_pos", "mfrr_neg"):
        assert key in body["products"]
        assert len(body["products"][key]) >= 24
        assert body["latest"][key] is not None
    assert body["as_of"] is not None
    assert "stale" in body and "age_days" in body


def test_route_note_mentions_fcr_cross_border_caveat(db_session):
    _seed_capacity(db_session)
    body = _client(db_session).get("/api/power/capacity-prices?zone=DE_LU").json()
    note = body["note"].lower()
    assert "fcr" in note
    assert "settlement" in note or "cross-border" in note or "settles" in note
    assert "weighted" in note
    assert "4" in note  # the /4 normalization is called out


def test_route_non_de_lu_zone_is_a_structural_absence(db_session):
    body = _client(db_session).get("/api/power/capacity-prices?zone=FR").json()
    assert body["available"] is False
    assert "DE-LU" in body["reason"]
    assert body["zone"] == "FR"


def test_route_days_clamped(db_session):
    resp_low = _client(db_session).get("/api/power/capacity-prices?zone=DE_LU&days=0")
    resp_high = _client(db_session).get("/api/power/capacity-prices?zone=DE_LU&days=91")
    assert resp_low.status_code == 422
    assert resp_high.status_code == 422


def test_route_no_data_yet_is_honest(db_session):
    body = _client(db_session).get("/api/power/capacity-prices?zone=DE_LU").json()
    assert body["available"] is False
    assert "reason" in body


def test_route_stale_data_is_declared(db_session):
    from datetime import datetime, timedelta, timezone

    from backend.power.hourly_store import upsert_hourly

    old = int((datetime.now(timezone.utc) - timedelta(days=10)).timestamp() // 3600) * 3600
    for suffix in ("fcr.price", "afrr.price.pos", "afrr.price.neg", "mfrr.price.pos", "mfrr.price.neg"):
        upsert_hourly(db_session, f"capacity.{suffix}", "DE_LU", [(old, 5.0)], unit="EUR/MW/h")

    body = _client(db_session).get("/api/power/capacity-prices?zone=DE_LU").json()
    assert body["available"] is True
    assert body["stale"] is True
    assert body["age_days"] >= 9
