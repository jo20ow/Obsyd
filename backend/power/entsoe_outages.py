"""ENTSO-E A77 generation unavailability → PowerOutage events.

Spiked live 2026-07-11 (DE_LU): the API returns a ZIP with one
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
from backend.power.zones import POWER_ZONES

logger = logging.getLogger(__name__)

_PAGE_SIZE = 200

# Window must stay under the API's one-year span limit.
DEFAULT_LOOKBACK_DAYS = 7
DEFAULT_LOOKAHEAD_DAYS = 355

#: docStatus values that mean "this message no longer stands".
_WITHDRAWN_STATUSES = {"A09", "A13"}  # cancelled / withdrawn


def _text(root: ET.Element, localname: str) -> str | None:
    """First element text matching a local tag name, namespace-agnostic."""
    for e in root.iter():
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
    nominal = _text(root, "production_RegisteredResource.pSRType.powerSystemResources.nominalP")

    return {
        "mrid": mrid,
        "revision": int(revision),
        "business_type": _text(root, "businessType") or "A53",
        "psr_type": _text(root, "production_RegisteredResource.pSRType.psrType"),
        "unit_name": _text(root, "production_RegisteredResource.name"),
        "unit_eic": _text(root, "production_RegisteredResource.mRID"),
        "location": _text(root, "production_RegisteredResource.location.name"),
        "nominal_mw": float(nominal) if nominal else None,
        "available_mw": min(quantities) if quantities else None,
        "start_utc": start,
        "end_utc": end,
        "status": "withdrawn" if status_val in _WITHDRAWN_STATUSES else "active",
    }


async def _fetch_outages_page(
    eic: str, window_start: str, window_end: str, offset: int, *, doc_type: str = "A77"
) -> bytes | None:
    """One page of the unavailability ZIP (raw bytes), or None on empty/no data."""
    params = {
        "securityToken": _token(),
        "documentType": doc_type,
        "biddingZone_Domain": eic,
        "periodStart": window_start,
        "periodEnd": window_end,
        # ALWAYS explicit, including 0 — without it the API refuses to paginate
        # and answers >200-document windows with HTTP 400.
        "offset": str(offset),
    }
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
    product; history accumulates from running daily."""
    if not settings.entsoe_api_token:
        logger.warning("entsoe_outages: ENTSOE_API_TOKEN not set — skipping")
        return {"skipped": "no token"}

    zones = zones if zones is not None else list(POWER_ZONES)
    now = datetime.now(timezone.utc)
    window_start = (now - timedelta(days=lookback_days)).strftime("%Y%m%d0000")
    window_end = (now + timedelta(days=lookahead_days)).strftime("%Y%m%d0000")

    written = 0
    seen_docs = 0
    for zone in zones:
        eic = POWER_ZONES.get(zone, {}).get("eic")
        if not eic:
            continue
        offset = 0
        while True:
            try:
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
                exists = (
                    db.query(PowerOutage.id)
                    .filter(PowerOutage.mrid == event["mrid"], PowerOutage.revision == event["revision"])
                    .first()
                )
                if exists:
                    continue
                db.add(PowerOutage(zone=zone, doc_type=doc_type, **event))
                written += 1
            db.commit()

            if len(names) < _PAGE_SIZE:
                break
            offset += _PAGE_SIZE

    return {"written": written, "documents": seen_docs, "zones": len(zones)}
