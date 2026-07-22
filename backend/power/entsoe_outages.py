"""ENTSO-E outage messages → PowerOutage events. A77 (generation units) and A78
(transmission infrastructure — interconnectors/lines) share this pipeline.

Spiked live 2026-07-11 (DE_LU, A77): the API returns a ZIP with one
Unavailability_MarketDocument per outage MESSAGE — and revision semantics are
the whole game. Of 31 documents in a 2-day window, 26 carried docStatus A09
(withdrawn); a naive reader would put 26 ghost outages on the desk. Ingest
stores every (mRID, revision) as history; the read side takes the highest
revision per mRID and hides withdrawn events.

Deliberately NO raw-disk cache: outage messages mutate (revisions), the
payloads are small (tens of KB per zone-window), and a write-once cache would
either serve stale revisions or need its own retention machinery — the
2026-07-07 disk incident says keep transient payloads off the disk. The DB
row per (mrid, revision) IS the idempotency layer.

Pagination — learned from the live API, both the hard way:
  * >200 documents WITHOUT an offset param is HTTP 400 ("number of instances
    exceeds the allowed maximum"); the API only paginates when offset is sent
    EXPLICITLY, including offset=0. DE_LU alone had 362 documents in a
    362-day window.
  * The request window must span less than one year, or HTTP 400
    ("must not span more than one year").
We page in 200s until a page comes back non-full.

A78 spike (2026-07-21, DE_LU<->FR/NL/BE/CZ/AT — see docs/findings/2026-07-20-umm-feasibility.md
for why this task exists): A77's single `biddingZone_Domain` param does NOT work for A78
("Mandatory parameter In_Domain is missing"). A78 needs a DIRECTED zone pair, `in_Domain` +
`out_Domain` (same pattern as A09 scheduled exchanges / A25 net position). Querying the SAME
zone as both (a common "give me everything for this area" trick on other ENTSO-E document
types) answers an EMPTY zip — a real border pair is mandatory. And the two directions of one
border are NOT mirrors of each other: DE_LU->FR and FR->DE_LU shared zero mRIDs across a
2026-06-01→2026-07-20 window (26 documents each, fully disjoint) — each TSO evidently
publishes its own side, so skipping either direction silently drops real outages. Ingest
therefore reuses `border_registry.directed_pairs()` (already swept from ENTSO-E for A09) for
BOTH directions of every canonical border instead of re-deriving its own list.

Schema differences from A77: A78 describes an ASSET (line/PST/transformer —
Asset_RegisteredResource), not a production unit. The name/EIC/location/psrType live
NESTED inside that one container (`<Asset_RegisteredResource><mRID/><name/>
<asset_PSRType.psrType/><location.name/></Asset_RegisteredResource>`) instead of A77's flat
`production_RegisteredResource.*` element names, and — checked across 52 live-sampled
documents — nominalP is NEVER published for a transmission asset; only the reduced
available_mw exists, so nominal_mw/offline_mw stay null for every A78 row (existing code
already guards `nominal_mw is not None` everywhere it sums offline capacity, so A78 rows
self-exclude from every generation-only MW total by construction, not by a doc_type filter
alone). businessType (A53 planned / A54 forced) and docStatus/revision withdrawal semantics
are IDENTICAL to A77 (both seen live: 50 A53 + 2 A54, 8/52 withdrawn A09 across the sample).
"""

from __future__ import annotations

import io
import logging
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy.orm import Session

from backend.config import settings
from backend.gas.entsoe import ENTSOE_BASE, _localname, _token
from backend.models.energy import PowerOutage
from backend.power.border_registry import directed_pairs
from backend.power.zones import POWER_ZONES, ZONE_REGISTRY

logger = logging.getLogger(__name__)

_PAGE_SIZE = 200

# Window must stay under the API's one-year span limit.
DEFAULT_LOOKBACK_DAYS = 7
DEFAULT_LOOKAHEAD_DAYS = 355

#: docStatus values that mean "this message no longer stands".
_WITHDRAWN_STATUSES = {"A09", "A13"}  # cancelled / withdrawn

#: ENTSO-E asset_PSRType codes for A78 (network elements — B21-B24 are deliberately
#: excluded from entsoe_grid.PSR_LABELS, which is fuels-only; B21 is the only one seen
#: live so far, 52/52 sampled documents).
ASSET_TYPE_LABELS: dict[str, str] = {
    "B21": "AC Line",
    "B22": "DC Link",
    "B23": "Substation",
    "B24": "Transformer",
}

#: Reverse of ZONE_REGISTRY, for mapping A78's in_Domain/out_Domain EICs back to zone
#: keys. Built from the full registry (not just the enabled POWER_ZONES subset) so a
#: counterparty outside the enabled set still gets a readable key instead of a raw EIC.
_EIC_TO_ZONE: dict[str, str] = {meta["eic"]: key for key, meta in ZONE_REGISTRY.items()}


def _text(root: ET.Element, localname: str) -> str | None:
    """First element text matching a local tag name, namespace-agnostic."""
    for e in root.iter():
        if _localname(e.tag) == localname:
            return (e.text or "").strip() or None
    return None


def _child_text(parent: ET.Element | None, localname: str) -> str | None:
    """First DIRECT child of `parent` matching a local tag name, namespace-agnostic.

    Unlike `_text` (which walks the WHOLE subtree from an arbitrary root), this only
    looks one level down — needed for A78's Asset_RegisteredResource container, whose
    child <mRID> would otherwise be shadowed by the document's own top-level <mRID>
    and the TimeSeries's <mRID>1</mRID> (all three share the same local tag name).
    """
    if parent is None:
        return None
    for e in parent:
        if _localname(e.tag) == localname:
            return (e.text or "").strip() or None
    return None


def parse_unavailability(xml_text: str) -> dict | None:
    """One Unavailability_MarketDocument → one event dict, or None for
    acknowledgements/unparseable documents (fail-soft: a single bad inner
    document must not sink the whole ZIP)."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None
    if _localname(root.tag) != "Unavailability_MarketDocument":
        return None

    mrid = _text(root, "mRID")
    revision = _text(root, "revisionNumber")
    if not mrid or revision is None:
        return None

    # Top-level unavailability window
    interval = next((e for e in root.iter()
                     if _localname(e.tag) == "unavailability_Time_Period.timeInterval"), None)
    start = _text(interval, "start") if interval is not None else None
    end = _text(interval, "end") if interval is not None else None
    if not start or not end:
        return None

    # docStatus nests its code in <value>; absent docStatus means the message stands.
    status_val = None
    for e in root.iter():
        if _localname(e.tag) == "docStatus":
            status_val = _text(e, "value")
            break

    # Point quantities: the Available_Period is a step function; the desk
    # headline counts the worst case, so take the minimum available MW.
    quantities: list[float] = []
    for e in root.iter():
        if _localname(e.tag) == "quantity":
            try:
                quantities.append(float(e.text))
            except (TypeError, ValueError):
                continue

    # A78 (transmission) wraps its identity in <Asset_RegisteredResource> instead of A77's
    # flat production_RegisteredResource.* names — branch on which container is present
    # rather than trusting the document's <type> text, so any future doc type sharing this
    # shape (A79 offshore grid?) is handled the same way without a new branch.
    asset = next((e for e in root.iter() if _localname(e.tag) == "Asset_RegisteredResource"), None)
    if asset is not None:
        unit_name = _child_text(asset, "name")
        unit_eic = _child_text(asset, "mRID")
        location = _child_text(asset, "location.name")
        psr_type = _child_text(asset, "asset_PSRType.psrType")
        nominal_mw = None  # never published for a transmission asset (52/52 live-sampled)
        in_domain_eic = _text(root, "in_Domain.mRID")
        out_domain_eic = _text(root, "out_Domain.mRID")
    else:
        nominal = _text(root, "production_RegisteredResource.pSRType.powerSystemResources.nominalP")
        unit_name = _text(root, "production_RegisteredResource.name")
        unit_eic = _text(root, "production_RegisteredResource.mRID")
        location = _text(root, "production_RegisteredResource.location.name")
        psr_type = _text(root, "production_RegisteredResource.pSRType.psrType")
        nominal_mw = float(nominal) if nominal else None
        in_domain_eic = None
        out_domain_eic = None

    return {
        "mrid": mrid,
        "revision": int(revision),
        "business_type": _text(root, "businessType") or "A53",
        "psr_type": psr_type,
        "unit_name": unit_name,
        "unit_eic": unit_eic,
        "location": location,
        "nominal_mw": nominal_mw,
        "available_mw": min(quantities) if quantities else None,
        "start_utc": start,
        "end_utc": end,
        "status": "withdrawn" if status_val in _WITHDRAWN_STATUSES else "active",
        # Transient — consumed by ingest_outages to resolve zone/counterparty_zone via
        # ZONE_REGISTRY, then popped before the dict reaches PowerOutage(**event). None
        # for A77 (it carries no domain information at all).
        "in_domain_eic": in_domain_eic,
        "out_domain_eic": out_domain_eic,
    }


async def _fetch_outages_page(
    eic: str, window_start: str, window_end: str, offset: int, *,
    doc_type: str = "A77", counterparty_eic: str | None = None,
) -> bytes | None:
    """One page of the unavailability ZIP (raw bytes), or None on empty/no data.

    A78 needs a DIRECTED zone pair (in_Domain=eic / out_Domain=counterparty_eic) instead
    of A77's single biddingZone_Domain — passing `counterparty_eic` switches the param
    shape. Live-spiked 2026-07-21: in_Domain==out_Domain (same zone twice) answers an
    empty ZIP, so this is not an optional convenience for A78, it is mandatory.
    """
    params = {
        "securityToken": _token(),
        "documentType": doc_type,
        "periodStart": window_start,
        "periodEnd": window_end,
        # ALWAYS explicit, including 0 — without it the API refuses to paginate
        # and answers >200-document windows with HTTP 400.
        "offset": str(offset),
    }
    if counterparty_eic is not None:
        params["in_Domain"] = eic
        params["out_Domain"] = counterparty_eic
    else:
        params["biddingZone_Domain"] = eic
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.get(ENTSOE_BASE, params=params)
        if resp.status_code == 400:
            # Past the last page is a 400/ACK; on the FIRST page it is a real
            # error (bad window, bad domain) that must not be silent — the
            # initial prod run wrote 0 documents without a single log line.
            if offset == 0:
                logger.warning("entsoe_outages [%s]: HTTP 400 on first page: %.200s",
                               eic, resp.text)
            return None
        resp.raise_for_status()
        return resp.content if resp.content[:2] == b"PK" else None


async def ingest_outages(
    db: Session,
    *,
    zones: list[str] | None = None,
    doc_type: str = "A77",
    lookahead_days: int = DEFAULT_LOOKAHEAD_DAYS,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
) -> dict:
    """Fetch the rolling outage window for `zones` and store new (mrid, revision)
    rows. No deep backfill by design — active + upcoming messages are the
    product; history accumulates from running daily.

    A78 (doc_type="A78") iterates BORDER PAIRS instead of zones — a directed
    (in_Domain, out_Domain) query per side of every canonical border in
    `border_registry.directed_pairs()`, restricted to pairs where both ends are in
    `zones`. See the module docstring for why both directions are needed and why a
    same-zone pair does not substitute for them.
    """
    if not settings.entsoe_api_token:
        logger.warning("entsoe_outages: ENTSOE_API_TOKEN not set — skipping")
        return {"skipped": "no token"}

    zones = zones if zones is not None else list(POWER_ZONES)
    now = datetime.now(timezone.utc)
    window_start = (now - timedelta(days=lookback_days)).strftime("%Y%m%d0000")
    window_end = (now + timedelta(days=lookahead_days)).strftime("%Y%m%d0000")

    if doc_type == "A78":
        zone_set = set(zones)
        pairs: list[tuple[str, str | None]] = [
            (a, b) for a, b in directed_pairs() if a in zone_set and b in zone_set
        ]
    else:
        pairs = [(zone, None) for zone in zones]

    written = 0
    seen_docs = 0
    for zone, counterparty in pairs:
        eic = POWER_ZONES.get(zone, {}).get("eic")
        if not eic:
            continue
        counterparty_eic = POWER_ZONES.get(counterparty, {}).get("eic") if counterparty else None
        offset = 0
        while True:
            try:
                if counterparty_eic is not None:
                    blob = await _fetch_outages_page(
                        eic, window_start, window_end, offset,
                        doc_type=doc_type, counterparty_eic=counterparty_eic,
                    )
                else:
                    blob = await _fetch_outages_page(eic, window_start, window_end, offset, doc_type=doc_type)
            except httpx.HTTPError as exc:
                logger.warning("entsoe_outages [%s offset=%d]: fetch failed: %s", zone, offset, exc)
                break
            if not blob:
                break
            try:
                zf = zipfile.ZipFile(io.BytesIO(blob))
                names = [n for n in zf.namelist() if n.endswith(".xml")]
            except zipfile.BadZipFile:
                logger.warning("entsoe_outages [%s]: bad zip at offset %d", zone, offset)
                break

            for name in names:
                seen_docs += 1
                event = parse_unavailability(zf.read(name).decode("utf-8", "replace"))
                if event is None:
                    continue
                in_eic = event.pop("in_domain_eic", None)
                out_eic = event.pop("out_domain_eic", None)
                # The message's OWN domain pair is authoritative when present (A78) — trust
                # it over the query loop variable, which only matters if ENTSO-E ever answers
                # a broader match than what was asked. zone falls back to the query zone if
                # in_Domain maps to nothing (should not happen: it is the zone we just queried
                # with), counterparty_zone falls back to the raw EIC if unmapped.
                row_zone = zone
                counterparty_zone = None
                if in_eic or out_eic:
                    row_zone = _EIC_TO_ZONE.get(in_eic, zone)
                    counterparty_zone = _EIC_TO_ZONE.get(out_eic, out_eic)
                exists = (
                    db.query(PowerOutage.id)
                    .filter(PowerOutage.mrid == event["mrid"], PowerOutage.revision == event["revision"])
                    .first()
                )
                if exists:
                    continue
                db.add(PowerOutage(zone=row_zone, doc_type=doc_type, counterparty_zone=counterparty_zone, **event))
                written += 1
            db.commit()

            if len(names) < _PAGE_SIZE:
                break
            offset += _PAGE_SIZE

    return {"written": written, "documents": seen_docs, "zones": len(zones), "pairs": len(pairs)}
