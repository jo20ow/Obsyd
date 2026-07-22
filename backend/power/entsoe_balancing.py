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
    this public Web API / token today, independent of zone. The fetcher below recognises
    BOTH of ENTSO-E's "genuinely nothing here" wordings — this structural rejection and
    A84's "No matching data found" empty-window Acknowledgement — and, ONLY for those, caches
    the emptiness like this codebase's existing convention (entsoe_exchange.py's A09/A25
    fetchers) so ingestion stays green and the 4 volume series simply go unwritten. Any OTHER
    >=400 (401 from a rotated/expired token, 429, 5xx) is deliberately NOT treated as no-data:
    it raises and propagates to ingest_balancing's `except httpx.HTTPError`, which logs and
    skips the month WITHOUT caching it — caching an outage would permanently poison that
    zone-month in the write-once raw_cache for as long as the token stays broken.
    REVERIFY AT DEPLOY — if ENTSO-E ever restores A83 or a different access tier unlocks it,
    `parse_balancing_volumes` is ready: same Point-walk as prices, reading `quantity` (the
    element name every other MW-quantity ENTSO-E document in this repo uses — A75, A09, A25 —
    but UNVERIFIED against an actual A83 payload, since none was ever returned). ONE THING TO
    RE-CHECK FIRST: `_walk_points` accumulates every Point from every TimeSeries matching a
    given (product, direction) into one list per hour, and `parse_balancing_volumes` SUMS
    that list. That is correct for genuinely-disjoint quarter-hours, but if a real A83
    document ever has OVERLAPPING TimeSeries for the same (product, direction, hour) — e.g. a
    revision superseding an earlier one, the way A85/A09 do — those values would be SUMMED
    together (double-counted energy), not replaced or averaged. Prices average, so the same
    overlap there is harmless; volumes are not — verify real A83 documents don't overlap like
    that before trusting a sum.

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

#: Zones whose A83/A84 domain differs from their bidding-zone EIC: {zone: (control-area EIC,
#: coverage caveat)}. DE_LU's balancing energy has no country/bidding-zone-level publication
#: (spiked 2026-07-20) — TenneT's control area is the one verified-working proxy, but it is
#: only one of Germany's four TSOs, not the national total. Kept as ONE dict (not a separate
#: EIC map + caveat map) so `control_area_eic` and `coverage_caveat` can never drift apart —
#: see module docstring.
_CONTROL_AREA_OVERRIDE: dict[str, tuple[str, str]] = {
    "DE_LU": (
        "10YDE-EON------1",
        "TenneT control area only (one of four German TSOs), not the national total.",
    ),
}

PRICE_DOCTYPE = "A84"
VOLUME_DOCTYPE = "A83"
PRICE_CACHE_SOURCE = "entsoe_a84"
VOLUME_CACHE_SOURCE = "entsoe_a83"


def control_area_eic(zone: str) -> str | None:
    """Control-area domain EIC for A83/A84 (single-TSO zones = the bidding EIC)."""
    override = _CONTROL_AREA_OVERRIDE.get(zone)
    if override is not None:
        return override[0]
    return ZONE_REGISTRY.get(zone, {}).get("eic")


def coverage_caveat(zone: str) -> str | None:
    """A short caveat for zones whose balancing-energy domain is a partial, single-TSO
    override rather than the normal bidding-zone EIC (see module docstring) — None for every
    other zone. Callers (e.g. GET /api/power/balancing) read this instead of hardcoding a
    zone check, so the route can never drift from `_CONTROL_AREA_OVERRIDE`."""
    override = _CONTROL_AREA_OVERRIDE.get(zone)
    return override[1] if override is not None else None


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
    A plain-XML response (the observed common case) comes back as a one-element list.

    Only a 400 whose body contains ENTSO-E's "No matching data found", "not allowed to be
    fetched via this service", or "combination of" wording (the distinctive tail of its two
    "nothing here" messages — see module docstring) is a clean "no data" — cached and
    returned as `[""]`, matching entsoe_exchange.py's A09/A25 fetchers. Every OTHER error
    response (401, 429, 5xx, or a 400 matching none of those phrases — e.g. an ordinary
    parameter-validation error) calls `resp.raise_for_status()` and propagates as
    `httpx.HTTPError` — deliberately NOT cached, so a token outage or rate limit gets retried
    next run instead of permanently freezing that zone-month as false emptiness.

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
            if resp.status_code == 400 and (
                b"No matching data found" in resp.content
                or b"not allowed to be fetched via this service" in resp.content
                or b"combination of" in resp.content
            ):
                # ENTSO-E's two "there is genuinely nothing here" wordings (see module
                # docstring): A84's clean empty-window Acknowledgement ("No matching data
                # found"), and A83's structural rejection ("The combination of [...] is not
                # valid, or the requested data is not allowed to be fetched via this
                # service."). Matched on the DISTINCTIVE tail of that sentence, not the bare
                # "is not valid" fragment — that phrase alone is generic enough to appear in
                # an ordinary parameter-validation error, which must NOT be cached as no-data.
                # ONLY this specific 400 shape is cacheable (matches entsoe_exchange.py's
                # A09/A25 fetchers) — anything else must raise below. Caching every >=400
                # blindly would also cache a 401 from a rotated/expired token (CLAUDE.md flags
                # rotation as pending) or a transient 429/5xx into the write-once raw_cache,
                # permanently poisoning that zone-month for the life of the cache.
                return {"xml": [""]}
            resp.raise_for_status()
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
    accumulator, keeping only days the caller actually asked for.

    WHY last-wins across documents: `ingest_balancing` calls this once per inner .xml member
    of a fetch (plain-XML responses are a one-element list; see `_fetch_balancing_month`'s
    ZIP-merge). `bucket.setdefault(day, {}).update(hours)` means if the SAME (product,
    direction, day, hour) is present in TWO different documents, the later document's value
    silently overwrites the earlier one — no averaging or summing across documents. That is
    deliberate for the shape observed in the 2026-07-20 spike (a single plain-XML document per
    zone-month, never multiple ZIP members), where there is nothing to merge. If ENTSO-E ever
    starts batching several TSOs' documents into one ZIPped response for the SAME zone, this
    would need real aggregation (sum for volumes, a defined tie-break for prices) instead of
    last-wins — reassess before trusting it in that scenario.
    """
    for key, day_hours in parsed.items():
        bucket = acc.setdefault(key, {})
        for day, hours in day_hours.items():
            if day in wanted:
                bucket.setdefault(day, {}).update(hours)


async def ingest_balancing(
    db: Session,
    days: list[str],
    *,
    zone: str = "DE_LU",
    overwrite: bool = False,
    overwrite_volumes: bool | None = None,
) -> dict:
    """Fetch + upsert activated-balancing-energy prices (A84) and volumes (A83) for `zone`
    over the month(s) spanning `days`, writing up to 8 canonical series:
    `balancing.{afrr,mfrr}.{price,vol}.{up,down}`.

    Prices and volumes are fetched and folded independently — a failure or structural
    rejection in one (see module docstring re: A83) must never block the other, matching the
    per-step isolation the rest of the daily power ingest uses (backend/collectors/scheduler.py
    ::_run_power_daily).

    `overwrite_volumes` lets the A83 fetch use a DIFFERENT overwrite than the A84 fetch;
    `None` (the default) means "same as `overwrite`", preserving the original one-flag
    behaviour for every existing caller. This exists for the hourly job: A83's structural
    rejection (see module docstring — it fails for every zone/param combination tried) is
    stable and cheap to cache, but re-running with `overwrite=True` for BOTH doctypes every
    hour would re-issue that known-futile A83 request for all 37 zones × 24 times a day —
    ~888 guaranteed 400s against ENTSO-E for nothing. Passing `overwrite_volumes=False` lets
    the cached `[""]` short-circuit with zero network once it exists, while prices (which
    DO change hour to hour) keep refreshing via `overwrite=True`. The once-a-day full-overwrite
    pass (both flags True) is deliberately kept as the "REVERIFY" probe for whether A83 has
    come back.
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
    vol_overwrite = overwrite if overwrite_volumes is None else overwrite_volumes

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
                VOLUME_DOCTYPE, VOLUME_CACHE_SOURCE, ca_eic, month_start, overwrite=vol_overwrite
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
