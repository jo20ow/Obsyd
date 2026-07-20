"""ENTSO-E activated balancing energy (aFRR/mFRR) → hourly `balancing.<product>.<price|vol>.<up|down>`.

LIVE SPIKE (2026-07-20, curl against https://web-api.tp.entsoe.eu/api, DE_LU/FR/NL, a 3-day
and a full-month window) — trust this over the pre-spike assumptions in the task brief:

  * PRICES (documentType A84, ENTSO-E data item 17.1.F "PRICES_OF_ACTIVATED_BALANCING_ENERGY_R3")
    WORK over `controlArea_Domain`. Price element is `activation_Price.amount` (verified).
    Resolution is PT15M exclusively in every response seen (matches the 2025-10-01 SDAC
    15-min-MTU move noted elsewhere in this repo). A SINGLE fetch per zone-month with NO
    businessType filter returns every business type present in one document — verified: a
    TenneT full-month pull (no filter) contained 444 aFRR (A96) TimeSeries AND 25 mFRR (A97)
    TimeSeries side by side. So the raw_cache key stays zone+month (no product component,
    matching entsoe_imbalance's shape) and the PARSER splits by each TimeSeries' own
    (businessType, flowDirection.direction), not by a request-time filter.

  * VOLUMES (documentType A83, data item 17.1.E) FAIL UNIVERSALLY in this spike. Every
    combination tried — DE country EIC (10Y1001A1001A83F), DE bidding-zone EIC
    (10Y1001A1001A82H), TenneT's control-area EIC, FR's control-area EIC; with/without
    businessType (A95-A98), with/without processType (A16/A60/A61/A68/A51), with
    contract_MarketAgreement.Type, with psrType — every one answers HTTP 400 "The
    combination of [DOCUMENT_TYPE=A83, ...] is not valid, or the requested data is not
    allowed to be fetched via this service." That wording (naming only DOCUMENT_TYPE /
    BUSINESS_TYPE, never the domain) is ENTSO-E's STRUCTURAL-rejection message — distinct
    from the "No matching data found" Acknowledgement A84 returns for a genuinely empty
    zone/window. Conclusion: activated-balancing-energy VOLUMES are not obtainable through
    this public Web API / token today, independent of zone. The fetcher below treats the
    rejection exactly like this codebase's existing "clean no-data ACK" convention
    (entsoe_exchange.py's A09/A25 fetchers: any >=400 response → cache the emptiness, no
    exception) so ingestion stays green and the 4 volume series simply go unwritten.
    REVERIFY AT DEPLOY — if ENTSO-E ever restores A83 or a different access tier unlocks it,
    `parse_balancing_volumes` is ready: same Point-walk as prices, reading `quantity` (the
    element name every other MW-quantity ENTSO-E document in this repo uses — A75, A09, A25 —
    but UNVERIFIED against an actual A83 payload, since none was ever returned).

  * DE_LU HAS NO COUNTRY- OR BIDDING-ZONE-LEVEL BALANCING DATA. Unlike A85's reBAP (nationally
    uniform, published once), aFRR/mFRR activation is published PER TSO CONTROL AREA. Both the
    DE_LU country EIC (entsoe_imbalance's override, 10Y1001A1001A83F) and the DE_LU
    bidding-zone EIC (ZONE_REGISTRY, 10Y1001A1001A82H) answer a clean "No matching data found"
    for A84. TenneT's control area (10YDE-EON------1) DOES carry real aFRR+mFRR price data, so
    DE_LU is mapped to it below — a PARTIAL, single-TSO proxy for the country (one of four
    German control areas: TenneT/Amprion/50Hertz/TransnetBW), not the national total.
    Documented rather than hidden, same honesty precedent as entsoe_imbalance.py's own
    country-EIC override note.

  * FR/NL use their bidding-zone EIC directly (`control_area_eic`'s ZONE_REGISTRY fallback,
    same as entsoe_imbalance.py). In the spiked 3-day window, FR's A84 response carried FCR
    (A95), mFRR (A97) and RR (A98) prices but NO aFRR (A96); NL's carried ONLY FCR (A95) — no
    aFRR/mFRR at all. Coverage varies by zone AND by window (a quiet day can simply have no
    activation in a given direction/product — see the curveType note below), so `product=afrr`
    legitimately coming back empty for FR on a given day is ordinary "no data", not an error —
    the same posture this vertical already takes for A85/A25 zone gaps.

  * ZIP WRAPPING: NOT observed in this spike. A full-month, single-control-area A84 pull
    (1.57 MB, 469 TimeSeries) came back as plain XML text, not a ZIP archive — contradicting
    the assumption that A83/A84 always ZIP. The fetcher below still branches on the "PK" ZIP
    magic bytes (unzip + merge every inner .xml member, unlike A85's single-doc unzip) since
    that costs nothing and is a strict superset of "always plain XML" — a busier control area,
    a longer window, or ENTSO-E batching multiple TSOs into one country response could still
    ZIP in practice.

  * curveType is declared A03 in every TimeSeries (the same code entsoe_exchange.py's A09 step
    function reads) but does NOT behave like a step function here: a gap between two Periods
    (e.g. one ends 08:00Z, the next starts 08:15Z) means NO activation happened in between —
    genuinely absent, not a value to hold forward (verified by walking real Periods: gaps
    exist at random points through the day, exactly where TenneT called on zero aFRR in that
    direction). So this parser does a plain namespace-agnostic Point walk — entsoe_imbalance's
    A85 pattern, not entsoe_exchange's `parse_step_series` — and never invents an hour nothing
    activated in.

HOURLY CANONICALIZATION: PRICES are the MEAN of the quarter-hour points inside each hour (a
price is a rate — averaging is the only sound rollup, the same rule parse_imbalance_prices
uses). VOLUMES are the SUM of the quarter-hour points inside each hour (MWh is additive: four
25 MWh quarter-hour activations make a 100 MWh hour, not a 25 MWh one).

FCR (A95) and RR (A98) business types, when present, are read off the wire and then dropped —
this desk tracks aFRR/mFRR only for now.
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

_RESOLUTION_HOURS = {"PT60M": 1.0, "PT30M": 0.5, "PT15M": 0.25}

#: aFRR/mFRR only (see module docstring) — FCR (A95) and RR (A98) are read but ignored.
_PRODUCT = {"A96": "afrr", "A97": "mfrr"}

_DIRECTION = {"A01": "up", "A02": "down"}

#: DE_LU's balancing energy has no country/bidding-zone-level publication (spiked 2026-07-20)
#: — TenneT's control area is the one verified-working proxy. See module docstring.
_CONTROL_AREA_OVERRIDE = {"DE_LU": "10YDE-EON------1"}

PRICE_DOCTYPE = "A84"
VOLUME_DOCTYPE = "A83"
PRICE_CACHE_SOURCE = "entsoe_a84"
VOLUME_CACHE_SOURCE = "entsoe_a83"


def control_area_eic(zone: str) -> str | None:
    """Control-area domain EIC for A83/A84 (single-TSO zones = the bidding EIC)."""
    if zone in _CONTROL_AREA_OVERRIDE:
        return _CONTROL_AREA_OVERRIDE[zone]
    return ZONE_REGISTRY.get(zone, {}).get("eic")


def _walk_points(xml_text: str, amount_tag: str) -> dict[tuple[str, str], dict[str, dict[int, list[float]]]]:
    """Shared Point walk for both A83 and A84: {(product, direction): {day: {hour: [values]}}}.

    Namespace-agnostic (matches local tag names, like every other parser in this vertical). An
    Acknowledgement document (no TimeSeries) yields {}. TimeSeries whose businessType isn't
    aFRR/mFRR, or whose direction isn't recognised, are skipped — not accumulated under a
    placeholder key.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ValueError(f"ENTSO-E balancing XML parse error: {exc}") from exc

    out: dict[tuple[str, str], dict[str, dict[int, list[float]]]] = {}
    for ts in root.iter():
        if _localname(ts.tag) != "TimeSeries":
            continue
        bt_el = next((e for e in ts.iter() if _localname(e.tag) == "businessType"), None)
        dir_el = next((e for e in ts.iter() if _localname(e.tag) == "flowDirection.direction"), None)
        product = _PRODUCT.get((bt_el.text or "").strip()) if bt_el is not None else None
        direction = _DIRECTION.get((dir_el.text or "").strip()) if dir_el is not None else None
        if product is None or direction is None:
            continue  # FCR/RR or an unrecognised direction — not tracked by this desk

        bucket = out.setdefault((product, direction), {})
        for period in (e for e in ts.iter() if _localname(e.tag) == "Period"):
            start_el = next((e for e in period.iter() if _localname(e.tag) == "start"), None)
            res_el = next((e for e in period.iter() if _localname(e.tag) == "resolution"), None)
            if start_el is None or res_el is None:
                continue
            start = _parse_utc(start_el.text)
            res_hours = _RESOLUTION_HOURS.get((res_el.text or "").strip())
            if start is None or res_hours is None:
                continue
            for point in (e for e in period.iter() if _localname(e.tag) == "Point"):
                pos = next((e.text for e in point if _localname(e.tag) == "position"), None)
                amt = next((e.text for e in point if _localname(e.tag) == amount_tag), None)
                if pos is None or amt is None:
                    continue
                try:
                    t = start + timedelta(hours=res_hours * (int(pos) - 1))
                    v = float(amt)
                except (ValueError, TypeError):
                    continue
                utc = t.astimezone(timezone.utc)
                bucket.setdefault(utc.strftime("%Y-%m-%d"), {}).setdefault(utc.hour, []).append(v)
    return out


def parse_balancing_prices(xml_text: str) -> dict[tuple[str, str], dict[str, dict[int, float]]]:
    """A84 document → {(product, direction): {day: {hour: mean EUR/MWh}}}.

    Reads `activation_Price.amount` (verified live — see module docstring). Sub-hourly slots
    (PT15M) are AVERAGED to hourly: a price is a rate, not a quantity, so a mean is the only
    sound rollup — the same rule parse_imbalance_prices (A85) uses.
    """
    raw = _walk_points(xml_text, "activation_Price.amount")
    return {
        key: {day: {h: sum(v) / len(v) for h, v in hours.items() if v} for day, hours in days.items()}
        for key, days in raw.items()
    }


def parse_balancing_volumes(xml_text: str) -> dict[tuple[str, str], dict[str, dict[int, float]]]:
    """A83 document → {(product, direction): {day: {hour: summed MWh}}}.

    Reads `quantity` (UNVERIFIED against a live payload — A83 answered a structural rejection
    for every combination tried in the 2026-07-20 spike; see module docstring). Sub-hourly
    slots are SUMMED to hourly: MWh is additive energy, not a rate — four 25 MWh quarter-hour
    activations make a 100 MWh hour, unlike the price mean above.
    """
    raw = _walk_points(xml_text, "quantity")
    return {
        key: {day: {h: sum(v) for h, v in hours.items() if v} for day, hours in days.items()}
        for key, days in raw.items()
    }


def _month_bounds(month_start: date) -> tuple[str, str]:
    nxt = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1)
    return f"{month_start:%Y%m%d}0000", f"{nxt:%Y%m%d}0000"


async def _fetch_balancing_month(
    document_type: str, cache_source: str, eic: str, month_start: date, *, overwrite: bool = False
) -> list[str]:
    """Fetch one control area's A83/A84 for a calendar month, disk-cached.

    Returns a LIST of inner XML documents: a ZIP archive (if the response is one — see module
    docstring, none was observed in the spike) can hold MULTIPLE members, unlike A85's
    single-doc ZIP, so every `.xml` member is decoded and returned rather than just the first.
    A plain-XML response (the observed common case) comes back as a one-element list; any
    >=400 response is a clean "no data" (matches entsoe_exchange.py's A09/A25 fetchers) and
    comes back as `[""]`.

    NOTE on window size: verified to work at full-calendar-month granularity for a single
    control area (1.57 MB / 469 TimeSeries, no timeout). If a future zone/month combination
    times out, `ingest_net_positions`'s ISO-week fallback (backend/power/entsoe_exchange.py) is
    the documented pattern to drop to — this function's window is a plain (start, end) pair
    internally, so swapping the caller's chunking to weekly needs no change here.
    """

    period_start, period_end = _month_bounds(month_start)

    async def _do() -> dict:
        params = {
            "securityToken": _token(),
            "documentType": document_type,
            "controlArea_Domain": eic,
            "periodStart": period_start,
            "periodEnd": period_end,
        }
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.get(ENTSOE_BASE, params=params)
            if resp.status_code >= 400:
                # A83 in particular answers 400 for volumes today (see module docstring), and
                # any TSO/month/product combination can legitimately have nothing published.
                # Treat exactly like entsoe_exchange.py's A09/A25 fetchers: a clean "no data"
                # response is data, not an error — cache the emptiness so we never ask again.
                return {"xml": [""]}
            body = resp.content
            if body[:2] == b"PK":  # ZIP archive — merge every inner .xml member
                zf = zipfile.ZipFile(io.BytesIO(body))
                names = [n for n in zf.namelist() if n.endswith(".xml")]
                docs = [zf.read(n).decode("utf-8", "replace") for n in names] or [""]
            else:
                docs = [resp.text]
            return {"xml": docs}

    payload = await raw_cache.fetch_or_cache(
        cache_source, f"{eic}_{month_start:%Y-%m}", month_start, _do, overwrite=overwrite
    )
    docs = payload.get("xml") if isinstance(payload, dict) else None
    return docs or [""]


def _merge_wanted(
    acc: dict[tuple[str, str], dict[str, dict[int, float]]],
    parsed: dict[tuple[str, str], dict[str, dict[int, float]]],
    wanted: set[str],
) -> None:
    """Fold a parsed month's {(product, direction): {day: {hour: value}}} into the running
    accumulator, keeping only days the caller actually asked for."""
    for key, day_hours in parsed.items():
        bucket = acc.setdefault(key, {})
        for day, hours in day_hours.items():
            if day in wanted:
                bucket.setdefault(day, {}).update(hours)


async def ingest_balancing(
    db: Session, days: list[str], *, zone: str = "DE_LU", overwrite: bool = False
) -> dict:
    """Fetch + upsert activated-balancing-energy prices (A84) and volumes (A83) for `zone`
    over the month(s) spanning `days`, writing up to 8 canonical series:
    `balancing.{afrr,mfrr}.{price,vol}.{up,down}`.

    Prices and volumes are fetched and folded independently — a failure or structural
    rejection in one (see module docstring re: A83) must never block the other, matching the
    per-step isolation the rest of the daily power ingest uses (backend/collectors/scheduler.py
    ::_run_power_daily).
    """
    if not settings.entsoe_api_token:
        return {"skipped": "no token"}
    if not days:
        return {"days": 0, "written": 0}
    ca_eic = control_area_eic(zone)
    if ca_eic is None:
        return {"skipped": f"no balancing domain for zone {zone}"}

    wanted = set(days)
    months = sorted({datetime.strptime(d, "%Y-%m-%d").date().replace(day=1) for d in days})

    price_acc: dict[tuple[str, str], dict[str, dict[int, float]]] = {}
    vol_acc: dict[tuple[str, str], dict[str, dict[int, float]]] = {}

    for month_start in months:
        try:
            price_docs = await _fetch_balancing_month(
                PRICE_DOCTYPE, PRICE_CACHE_SOURCE, ca_eic, month_start, overwrite=overwrite
            )
        except httpx.HTTPError as exc:
            logger.warning("balancing prices [%s %s] fetch failed: %s", zone, month_start, exc)
            price_docs = []
        for xml in price_docs:
            if xml:
                _merge_wanted(price_acc, parse_balancing_prices(xml), wanted)

        try:
            vol_docs = await _fetch_balancing_month(
                VOLUME_DOCTYPE, VOLUME_CACHE_SOURCE, ca_eic, month_start, overwrite=overwrite
            )
        except httpx.HTTPError as exc:
            logger.warning("balancing volumes [%s %s] fetch failed: %s", zone, month_start, exc)
            vol_docs = []
        for xml in vol_docs:
            if xml:
                _merge_wanted(vol_acc, parse_balancing_volumes(xml), wanted)

    written = 0
    for (product, direction), day_hours in price_acc.items():
        if day_hours:
            written += upsert_day_hours(db, f"balancing.{product}.price.{direction}", zone, day_hours, unit="EUR/MWh")
    for (product, direction), day_hours in vol_acc.items():
        if day_hours:
            written += upsert_day_hours(db, f"balancing.{product}.vol.{direction}", zone, day_hours, unit="MWh")

    return {"days": len(wanted), "written": written}
