"""ENTSO-E procured balancing CAPACITY prices (FCR/aFRR/mFRR tenders) → hourly
`capacity.<fcr.price|afrr.price.pos|afrr.price.neg|mfrr.price.pos|mfrr.price.neg>`
(EUR/MW/h), DE_LU only. Series-key grammar unified 2026-07-21 to the repo-wide
`family.product.measure[.direction]` shape (measure BEFORE direction — this module
originally put the direction segment before the measure segment, diverging from
entsoe_balancing.py's `balancing.afrr.price.up`). Nothing was deployed under the old
grammar, so this was a rename, not a migration.

NOT to be confused with `backend/power/entsoe_capacity.py` (installed GENERATION capacity,
A68, pan-EU) or `backend/power/entsoe_balancing.py` (activated balancing ENERGY, A83/A84,
what the TSO actually called on). This module is the THIRD leg: what Germany pays to have
reserve capacity on standby at all, before any of it is ever activated. See
docs/findings/2026-07-20-regelleistung-capacity-prices.md for the feasibility spike that
picked ENTSO-E documentType A15 over regelleistung.net (no data licence there).

LIVE SPIKE (2026-07-21, curl against https://web-api.tp.entsoe.eu/api, DE-LU LFC block,
2026-06-01) — trust this over the prior feasibility spike's pagination-limit assumption:

  * documentType=A15, area_Domain=10Y1001A1001A82H (DE-LU LFC BLOCK — verified live: an
    individual German TSO control area, e.g. TenneT's 10YDE-EON------1, returns a clean
    empty Acknowledgement instead; must be the LFC block). processType A52=FCR / A51=aFRR /
    A47=mFRR. The LFC block's EIC happens to be BYTE-IDENTICAL to the DE-LU bidding-zone EIC
    (ZONE_REGISTRY["DE_LU"]["eic"]) — that is a coincidence for this one zone, not a general
    rule, so it is re-exported as its own named constant below rather than silently reused.

  * SHAPE: each response page is a ZIP (one `.xml` member observed every time, but every
    member is still merged defensively) containing a `Balancing_MarketDocument` with a
    document-level `process.processType` (A52/A51/A47 — read off the wire per document
    rather than trusted from the request, matching this repo's convention of parsing what
    ENTSO-E actually says). Each `TimeSeries` = ONE accepted bid: `businessType` B95,
    `flowDirection.direction` (A01=up/positive, A02=down/negative for aFRR/mFRR; **A03
    ("symmetric") for FCR — FCR is NOT split by direction**, confirming the finding doc's
    "FCR is symmetric" call). Each TimeSeries has exactly one `Period`, whose
    `timeInterval` (start/end) IS the traded 4-hour product block — the declared
    `resolution` (PT15M) is boilerplate: there is only ONE `Point` (`position`=1) per
    Period, and its `quantity` (MW) + `procurement_Price.amount` apply to the WHOLE block,
    not a 15-minute slice of it. (Defensive fallback: if a Period ever carries more than one
    Point — never observed — every point is still folded in as its own bid against that same
    block rather than silently dropped or crashing.)

  * PAGINATION: `offset` steps of 100 TimeSeries per page, as documented — but the prior
    feasibility spike's "documented max 4900" is CONTRADICTED here: on the spiked day,
    aFRR (A51) returned FULL 100-entry pages through offset=6700 (6893 TimeSeries total,
    confirmed by bisection — offset=6800 gave a final short page of 93, offset=6900+ gave a
    clean empty Acknowledgement) and FCR/mFRR total well under that anyway (771 / 1687). A
    strict 4900 cutoff would have silently dropped ~2000 real aFRR bids on an ordinary day.
    `_MAX_OFFSET` below is therefore a generous runaway-loop safety valve (20,000 — ~3x the
    highest observed count), not an expected truncation point; a page short of 100 TimeSeries
    (including a genuinely empty one) is the real, verified stop condition. Precisely BECAUSE
    a block's bids routinely span many pages this way (~574 bids per block/direction on the
    busiest spiked aFRR day), `parse_capacity_bids` returns raw, un-aggregated bids and
    `ingest_capacity_prices` accumulates them across every page of a day BEFORE aggregating —
    see those two docstrings. An earlier version of this module aggregated per page/document
    and merged the results, which let the LAST page's partial (and, since ENTSO-E orders bids
    by price, systematically skewed) average silently overwrite every earlier page's.

  * ERROR DISCIPLINE DIFFERS FROM A83/A84: every "nothing here" case tried — a future date
    (2030-01-01), a pre-tender date (2010-01-01), the wrong (control-area) domain, and
    exhausted pagination — answered with a clean **HTTP 200** zero-TimeSeries
    `Acknowledgement_MarketDocument`, never a 400. So, unlike entsoe_balancing.py's A83/A84
    fetchers, there is NO "structural 400 = empty" text-matching special case for A15: every
    4xx/5xx (401 verified for a bad token, 400 verified for an invalid processType) is a
    genuine failure and must `raise_for_status()` / propagate as `httpx.HTTPError` — caching
    it would risk poisoning the write-once raw_cache the same way a mis-cached auth failure
    would elsewhere in this vertical.

  * MARKET SEMANTICS (from the feasibility spike, unchanged): daily tenders, six 4-hour
    blocks per day (00-04, 04-08, … 20-24 LOCAL time — hence a block's UTC boundaries shift
    with DST, e.g. the spiked day's first block ran 2026-05-31T22:00Z–2026-06-01T02:00Z).
    FCR is pay-as-CLEARED (uniform price — the highest accepted bid IS the clearing price
    for every accepted MW in that block); aFRR/mFRR are pay-as-BID (each accepted bid is
    paid its own price, so no single "the" price exists — a volume-weighted average is what
    procurement actually cost per MW).

CANONICAL STORAGE DECISION: only the volume-weighted average price is stored (5 series,
zone DE_LU), normalized to **EUR/MW/h** so every series shares one unit — FCR's native
EUR-per-4h-block price is divided by 4; aFRR/mFRR are already EUR/MW/h. The MARGINAL price
(highest accepted bid — mathematically the same number as the pay-as-cleared FCR price) is
computed by `aggregate_bids` for completeness/testability but deliberately NEVER stored: a
second parallel set of 5 series would double the storage and the caption surface for a
number that, for the two pay-as-bid products, describes only the single most expensive
accepted bid — not what the TSO actually paid on average. A trader who wants the marginal
bid can derive it from the raw ENTSO-E document; this desk publishes the cost number.

Each block's single weighted-average value is written to EVERY hour it covers (a step
function, like entsoe_exchange.py's A09) via `upsert_day_hours` — unlike activated-balancing
energy's gaps (entsoe_balancing.py), a capacity-tender price genuinely HOLDS for its whole
block, so densifying it across all 4 (or, at a DST boundary, 3/5) hours is correct, not a
fabricated fill-forward.

FIDELITY CAVEAT (carried into the route's `note`): FCR settles across borders under a single
clearing mechanism, so a specific country's OWN settlement price can differ slightly from
this DE-LU-block reconstruction on days when export limits bind (see the feasibility-spike
finding doc). Acceptable for a German-desk view; must stay visible, not silently smoothed
over.
"""
from __future__ import annotations

import io
import logging
import xml.etree.ElementTree as ET
import zipfile
from datetime import date, datetime, timedelta, timezone

import httpx
from sqlalchemy.orm import Session

from backend.config import settings
from backend.gas import raw_cache
from backend.gas.entsoe import ENTSOE_BASE, _localname, _parse_utc, _token
from backend.power.hourly_store import upsert_day_hours
from backend.power.zones import ZONE_REGISTRY

logger = logging.getLogger(__name__)

#: DE-LU LFC block domain for A15. Verified live 2026-07-21 to be the ONLY domain that
#: answers for this zone (an individual German TSO control area returns nothing) — and to
#: be byte-identical to the DE-LU bidding-zone EIC, a coincidence documented in the module
#: docstring, not assumed to generalise to any other zone.
AREA_DOMAIN = ZONE_REGISTRY["DE_LU"]["eic"]

DOCUMENT_TYPE = "A15"
CACHE_SOURCE = "entsoe_a15"

#: processType per product, as ENTSO-E's own code list defines them for A15.
PROCESS_TYPES: dict[str, str] = {"fcr": "A52", "afrr": "A51", "mfrr": "A47"}

#: direction code -> series-key direction segment. FCR (symmetric, A03) is handled
#: separately below and never consults this map. Deliberately "pos"/"neg" — A15's own
#: contract vocabulary (Positiv-/Negativsekundärleistung) — NOT activation's "up"/"down"
#: (entsoe_balancing.py); the two vocabularies are kept distinct on purpose, not an
#: inconsistency to fix.
_DIRECTION = {"A01": "pos", "A02": "neg"}

#: (json_key, series_suffix) for the 5 canonical series, in a fixed display order.
#: json_key is what the /api/power/capacity-prices response uses; series_suffix is the
#: tail of the `capacity.<suffix>` hourly-store key — it already carries the `price`
#: segment ("fcr.price", "afrr.price.pos", ...) so the final key is simply
#: `f"capacity.{suffix}"`. Single source of truth shared with the route.
PRODUCT_SUFFIXES: list[tuple[str, str]] = [
    ("fcr", "fcr.price"),
    ("afrr_pos", "afrr.price.pos"),
    ("afrr_neg", "afrr.price.neg"),
    ("mfrr_pos", "mfrr.price.pos"),
    ("mfrr_neg", "mfrr.price.neg"),
]

_PAGE_SIZE = 100
#: Runaway-loop safety valve, NOT an expected truncation point — see module docstring's
#: pagination finding (real aFRR volume on the spiked day: 6893, comfortably under this).
_MAX_OFFSET = 20_000


def aggregate_bids(bids: list[tuple[float, float]]) -> dict[str, float | None]:
    """One (product, direction, block)'s accepted bids -> {weighted_avg, marginal, total_qty}.

    `weighted_avg` (sum(qty*price)/sum(qty)) is the canonical stored number — see module
    docstring. `marginal` (the highest accepted bid price) is returned for
    completeness/testability only and must never be written to the hourly store. Bids with
    non-positive quantity are excluded from both; an all-non-positive (or empty) list yields
    all-None rather than a division by zero.
    """
    priced = [(q, p) for q, p in bids if q is not None and q > 0 and p is not None]
    if not priced:
        return {"weighted_avg": None, "marginal": None, "total_qty": 0.0}
    total_qty = sum(q for q, _ in priced)
    weighted_avg = sum(q * p for q, p in priced) / total_qty
    marginal = max(p for _, p in priced)
    return {"weighted_avg": weighted_avg, "marginal": marginal, "total_qty": total_qty}


def _series_suffix(product: str, direction_code: str | None) -> str | None:
    """product ('fcr'/'afrr'/'mfrr') + raw flowDirection code -> series-key suffix (already
    including the `price` measure segment, e.g. 'fcr.price' / 'afrr.price.pos' — the final
    hourly-store key is `f"capacity.{suffix}"`), or None for a direction this desk doesn't
    recognise (skip rather than guess)."""
    if product == "fcr":
        return "fcr.price"  # symmetric — direction (A03) is deliberately ignored
    direction = _DIRECTION.get(direction_code or "")
    return f"{product}.price.{direction}" if direction else None


def parse_capacity_bids(xml_text: str) -> dict[tuple[str, int, int], list[tuple[float, float]]]:
    """One A15 document/page -> RAW, un-aggregated
    {(series_suffix, block_start_epoch, block_end_epoch): [(quantity, price), ...]}.

    Namespace-agnostic (matches local tag names, like every other parser in this vertical).
    An Acknowledgement (no `process.processType`, no TimeSeries) yields {}. The document's
    OWN `process.processType` decides the product for every TimeSeries in it — read off the
    wire rather than trusted from the request, so a mislabelled or merged document can never
    silently write to the wrong product's series.

    Deliberately returns raw bids rather than an aggregated price: a busy day's bids for ONE
    block are split across many paginated pages (live-spiked: ~574 bids per block/direction on
    the busiest aFRR day, ~69 pages total for that day). A caller that aggregated per page (as
    an earlier version of this module did, via a since-removed per-document aggregation step)
    would average only that page's partial subset — and since ENTSO-E returns bids in
    ascending-price (merit) order, that silently biases every page's average toward whichever
    price band it happened to land on, with the LAST page's partial average winning once pages
    are merged. Callers MUST accumulate every page's raw bids for the SAME block across a
    whole fetch (see `ingest_capacity_prices`) and call `aggregate_bids` on the COMBINED list
    exactly once per block.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ValueError(f"ENTSO-E A15 XML parse error: {exc}") from exc

    process_el = next((e for e in root.iter() if _localname(e.tag) == "process.processType"), None)
    process_type = (process_el.text or "").strip() if process_el is not None else None
    product = next((p for p, code in PROCESS_TYPES.items() if code == process_type), None)
    if product is None:
        return {}  # Acknowledgement, or a processType this desk doesn't track

    # (series_suffix, block_start_epoch, block_end_epoch) -> [(quantity, price), ...]
    bids: dict[tuple[str, int, int], list[tuple[float, float]]] = {}

    for ts in root.iter():
        if _localname(ts.tag) != "TimeSeries":
            continue
        bt_el = next((e for e in ts.iter() if _localname(e.tag) == "businessType"), None)
        if bt_el is None or (bt_el.text or "").strip() != "B95":
            continue
        dir_el = next((e for e in ts.iter() if _localname(e.tag) == "flowDirection.direction"), None)
        direction_code = (dir_el.text or "").strip() if dir_el is not None else None
        suffix = _series_suffix(product, direction_code)
        if suffix is None:
            continue

        for period in (e for e in ts.iter() if _localname(e.tag) == "Period"):
            start_el = next((e for e in period.iter() if _localname(e.tag) == "start"), None)
            end_el = next((e for e in period.iter() if _localname(e.tag) == "end"), None)
            if start_el is None or end_el is None:
                continue
            start = _parse_utc(start_el.text)
            end = _parse_utc(end_el.text)
            if start is None or end is None or end <= start:
                continue
            block_start = int(start.astimezone(timezone.utc).timestamp())
            block_end = int(end.astimezone(timezone.utc).timestamp())
            key = (suffix, block_start, block_end)

            for point in (e for e in period.iter() if _localname(e.tag) == "Point"):
                qty_el = next((e for e in point if _localname(e.tag) == "quantity"), None)
                price_el = next((e for e in point if _localname(e.tag) == "procurement_Price.amount"), None)
                if qty_el is None or price_el is None:
                    continue
                try:
                    qty = float(qty_el.text)
                    price = float(price_el.text)
                except (TypeError, ValueError):
                    continue
                bids.setdefault(key, []).append((qty, price))

    return bids


def _aggregate_and_densify(
    bids_by_block: dict[tuple[str, int, int], list[tuple[float, float]]],
) -> dict[str, dict[str, dict[int, float]]]:
    """{(series_suffix, block_start, block_end): [(qty, price), ...]} -> {series_suffix:
    {day: {hour: normalized EUR/MW/h}}}. `aggregate_bids` runs EXACTLY ONCE per block here, on
    the caller's already-fully-combined bid list — see `parse_capacity_bids`'s docstring for
    why that combining must happen first."""
    out: dict[str, dict[str, dict[int, float]]] = {}
    for (suffix, block_start, block_end), pairs in bids_by_block.items():
        agg = aggregate_bids(pairs)
        weighted_avg = agg["weighted_avg"]
        if weighted_avg is None:
            continue
        # FCR is quoted EUR per 4h block (pay-as-cleared); aFRR/mFRR are already EUR/MW/h
        # (pay-as-bid) — divide only FCR so every stored series shares one unit.
        value = weighted_avg / 4.0 if suffix == "fcr.price" else weighted_avg
        day_hours = out.setdefault(suffix, {})
        t = block_start
        while t < block_end:
            dt = datetime.fromtimestamp(t, tz=timezone.utc)
            day_hours.setdefault(dt.strftime("%Y-%m-%d"), {})[dt.hour] = value
            t += 3600

    return out


def parse_capacity_document(xml_text: str) -> dict[str, dict[str, dict[int, float]]]:
    """One SINGLE, already-complete A15 document -> {series_suffix: {day: {hour: normalized
    EUR/MW/h}}} — a thin convenience wrapper (`parse_capacity_bids` + `_aggregate_and_densify`)
    for tests and any caller that genuinely has one whole document's bids for a block in hand.

    Real (paginated) production fetches must NOT use this per-document: `ingest_capacity_prices`
    accumulates raw bids across every page of a day via `parse_capacity_bids` before aggregating
    — see that function's docstring for why per-page aggregation silently biases the result.
    """
    return _aggregate_and_densify(parse_capacity_bids(xml_text))


def _count_timeseries(xml_text: str) -> int:
    """How many TimeSeries a page carries — the pagination stop signal. A malformed page
    (never observed) counts as 0 (stop) rather than looping forever on unparsable input;
    `parse_capacity_bids` is the one that raises on real malformed XML during ingest."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return 0
    return sum(1 for e in root.iter() if _localname(e.tag) == "TimeSeries")


async def _fetch_capacity_day(process_type: str, day: date, *, overwrite: bool = False) -> list[str]:
    """Fetch one processType's A15 documents for one calendar day, disk-cached, paginating
    `offset` in steps of 100 TimeSeries until a short page (see module docstring — this
    includes the genuinely-empty terminal Acknowledgement, which is NOT an error for A15).

    Returns every inner `.xml` member across every page (each page is a ZIP; every member
    decoded and merged, matching entsoe_balancing.py's multi-member convention even though
    only one member per page has ever been observed).
    """
    nxt = day + timedelta(days=1)

    async def _do() -> dict:
        docs: list[str] = []
        offset = 0
        async with httpx.AsyncClient(timeout=120) as client:
            while True:
                params = {
                    "securityToken": _token(),
                    "documentType": DOCUMENT_TYPE,
                    "processType": process_type,
                    "area_Domain": AREA_DOMAIN,
                    "periodStart": f"{day:%Y%m%d}0000",
                    "periodEnd": f"{nxt:%Y%m%d}0000",
                    "offset": offset,
                }
                resp = await client.get(ENTSOE_BASE, params=params)
                # Every no-data case for A15 is a clean HTTP 200 empty Acknowledgement (see
                # module docstring) — there is no "structural 400 = empty" text to match
                # here, unlike A83/A84. Any 4xx/5xx is therefore a genuine failure.
                resp.raise_for_status()
                body = resp.content
                if body[:2] == b"PK":
                    zf = zipfile.ZipFile(io.BytesIO(body))
                    names = [n for n in zf.namelist() if n.endswith(".xml")]
                    page_docs = [zf.read(n).decode("utf-8", "replace") for n in names] or [""]
                else:
                    page_docs = [resp.text]
                docs.extend(page_docs)
                page_count = sum(_count_timeseries(d) for d in page_docs if d)
                if page_count < _PAGE_SIZE:
                    break  # natural end: a short (or empty-Acknowledgement) page
                if offset >= _MAX_OFFSET:
                    # A FULL page still sitting at the safety-valve ceiling means there is
                    # real data beyond it we are choosing not to fetch. That truncated payload
                    # is about to land in the write-once raw cache — silent truncation there
                    # would be permanent on a backfill path, so this must be loud, not just a
                    # comment in the source.
                    logger.warning(
                        "A15 %s %s hit _MAX_OFFSET (%d) — page truncated", process_type, day, _MAX_OFFSET
                    )
                    break
                offset += _PAGE_SIZE
        return {"xml": docs}

    payload = await raw_cache.fetch_or_cache(
        CACHE_SOURCE, f"{process_type}_{day:%Y-%m-%d}", day, _do, overwrite=overwrite
    )
    docs = payload.get("xml") if isinstance(payload, dict) else None
    return docs or []


async def ingest_capacity_prices(db: Session, days: list[str], *, overwrite: bool = False) -> dict:
    """Fetch + upsert FCR/aFRR/mFRR procured-capacity prices for DE_LU over `days` (a flat
    list of 'YYYY-MM-DD' strings — fetched per DAY per processType, not batched by month; see
    module docstring's pagination finding), writing up to 5 canonical series:
    `capacity.{fcr.price,afrr.price.pos,afrr.price.neg,mfrr.price.pos,mfrr.price.neg}`.

    DE_LU only, deliberately no `zone` parameter — the German balancing-capacity market has
    no per-zone equivalent on this desk (see AREA_DOMAIN). Each day/processType fetch is
    isolated (log + continue), matching the rest of this vertical's per-step failure
    isolation — one bad day must never blank the others.

    CROSS-PAGE ACCUMULATION (the correctness-critical part): every page/ZIP-member of every
    day/processType fetch is parsed with `parse_capacity_bids` into RAW bids first, and all of
    them are folded into ONE `all_bids` accumulator keyed by (series_suffix, block_start,
    block_end) across the ENTIRE call — never aggregated per page or per document. Only after
    every fetch has landed does `_aggregate_and_densify` run `aggregate_bids` exactly once per
    block, on that block's COMPLETE bid list. See `parse_capacity_bids`'s docstring: a block's
    bids routinely span many pages, and aggregating page-by-page then merging (an earlier,
    incorrect version of this function did that, with the LAST page silently overwriting every
    earlier one) would average only whichever partial subset of bids the last page happened to
    contain.
    """
    if not settings.entsoe_api_token:
        return {"skipped": "no token"}
    if not days:
        return {"days": 0, "written": 0}

    all_bids: dict[tuple[str, int, int], list[tuple[float, float]]] = {}
    for day_str in days:
        day = datetime.strptime(day_str, "%Y-%m-%d").date()
        for process_type in PROCESS_TYPES.values():
            try:
                docs = await _fetch_capacity_day(process_type, day, overwrite=overwrite)
            except httpx.HTTPError as exc:
                logger.warning("capacity prices [%s %s] fetch failed: %s", process_type, day, exc)
                continue
            for xml in docs:
                if not xml:
                    continue
                try:
                    parsed_bids = parse_capacity_bids(xml)
                except ValueError as exc:
                    logger.warning("capacity prices [%s %s] parse failed: %s", process_type, day, exc)
                    continue
                for key, pairs in parsed_bids.items():
                    all_bids.setdefault(key, []).extend(pairs)

    acc = _aggregate_and_densify(all_bids)

    written = 0
    for suffix, day_hours in acc.items():
        if day_hours:
            written += upsert_day_hours(db, f"capacity.{suffix}", "DE_LU", day_hours, unit="EUR/MW/h")

    return {"days": len(days), "written": written}
