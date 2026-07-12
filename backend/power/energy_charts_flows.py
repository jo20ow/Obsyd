"""Fraunhofer Energy-Charts cross-border physical electricity flows (/cbpf).

Replaces the previous ENTSO-E A11 (entsoe_flows.py) implementation.

Source:  https://api.energy-charts.info/cbpf?country=<de|fr|nl>&start=YYYY-MM-DD&end=YYYY-MM-DD
License: CC BY 4.0 -- attribution required in any public-facing display.
Auth:    None (free, no token).
"""

# Sign convention (empirically verified 2026-06-24)
# -------------------------------------------------------
# In the Energy-Charts CBPF response, a POSITIVE value in series Y for
# `country=X` means country X **imports** from Y (flow Y->X).
#
# Verification:
#   GET /cbpf?country=de -> France series June-01 avg ~= +2.86 GW
#   GET /cbpf?country=fr -> Germany series June-01 avg ~= -2.86 GW
#   Same absolute magnitude, opposite sign -> raw value = import into queried country.
#
# We want net_mw > 0 = export FROM from_zone TO to_zone (canonical convention
# used throughout OBSYD, inherited from the A11 implementation).
#
# Conversion: for a canonical border (from_zone, to_zone) stored in sorted
# order (sorted_first=from_zone), we query `country=from_zone`, look up the
# to_zone series, and NEGATE the raw value:
#
#   raw_import = cbpf(country=from_zone)[to_zone]   # + = from_zone imports
#   net_mw(from_zone->to_zone) = -raw_import         # + = from_zone EXPORTS

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy.orm import Session

from backend.gas import raw_cache
from backend.models.energy import PowerFlow
from backend.power.hourly_store import upsert_hourly
from backend.power.zones import ENABLED_ZONES, ZONE_REGISTRY

logger = logging.getLogger(__name__)

CBPF_BASE = "https://api.energy-charts.info/cbpf"
ATTRIBUTION = "Energy-Charts (CC BY 4.0)"
CACHE_SOURCE = "energy_charts_cbpf"
# Pause between cache-miss month fetches in the backfill path. Energy-Charts is
# free and unauthenticated and rate-limits hard: the 2026-07-12 prod backfill got
# HTTP 429 after ~6 back-to-back month requests at 0.5 s spacing, and the block
# persisted for the rest of the run. 2 s spacing plus the 429 backoff below is
# what actually completes a ~600-request historical sweep.
CACHE_THROTTLE_SECONDS = 2.0
RATE_LIMIT_ATTEMPTS = 6
RATE_LIMIT_BASE_SECONDS = 30.0

# Energy-Charts country names -> OBSYD zone codes.
# "Germany" maps to DE_LU because the DE ENTSO-E bidding zone is the DE-LU
# combined zone; Energy-Charts /cbpf for country=de covers this zone.
# Luxembourg has no separate interconnection in the /cbpf data (it is
# subsumed in DE_LU), so we leave it out of the lookup.
COUNTRY_TO_ZONE: dict[str, str] = {
    "Germany": "DE_LU",
    "France": "FR",
    "Netherlands": "NL",
    "Belgium": "BE",
    "Italy": "IT",
    "Spain": "ES",
    "Portugal": "PT",
    "Switzerland": "CH",
    "United Kingdom": "GB",
    "Denmark": "DK",
    "Norway": "NO",
    "Austria": "AT",
    "Poland": "PL",
    "Czech Republic": "CZ",
    "Hungary": "HU",
    "Romania": "RO",
    "Greece": "GR",
    "Bulgaria": "BG",
    "Croatia": "HR",
    "Slovenia": "SI",
    "Slovakia": "SK",
    "Finland": "FI",
    "Ireland": "IE_SEM",
    "Luxembourg": "LU",
    "Sweden": "SE",
}

# Base countries whose neighbours we ingest — derived from the enabled zones'
# Energy-Charts country code (registry). Falls back to de/fr/nl if none resolve.
# Zones without an ec_country (Italian sub-zones, DK1/DK2) don't seed a base query;
# their prices/load/gen still ingest — only cross-border flows are country-level.
_seen: set[str] = set()
BASE_COUNTRIES: list[str] = []
for _z in ENABLED_ZONES:
    _ec = ZONE_REGISTRY.get(_z, {}).get("ec_country")
    if _ec and _ec not in _seen:
        _seen.add(_ec)
        BASE_COUNTRIES.append(_ec)
if not BASE_COUNTRIES:
    BASE_COUNTRIES = ["de", "fr", "nl"]

# Reverse map: zone code -> Energy-Charts country query string, for enabled base zones.
ZONE_TO_COUNTRY: dict[str, str] = {
    _z: ZONE_REGISTRY[_z]["ec_country"]
    for _z in ENABLED_ZONES
    if ZONE_REGISTRY.get(_z, {}).get("ec_country")
}

# Country query code -> canonical zone key (reverse of ZONE_TO_COUNTRY). Drives
# ingest_cbpf so it scales to every flow-mapped enabled zone, not just de/fr/nl.
COUNTRY_CODE_TO_ZONE: dict[str, str] = {v: k for k, v in ZONE_TO_COUNTRY.items()}


# -- fetch --------------------------------------------------------------------


async def fetch_cbpf(
    country: str,
    start: str,
    end: str,
) -> dict[str, Any]:
    """GET /cbpf for one country code and date range.

    Returns the parsed JSON payload (keys: unix_seconds, countries, ...).
    Raises httpx.HTTPError on network/HTTP failure.
    """
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.get(
            CBPF_BASE,
            params={"country": country, "start": start, "end": end},
        )
        resp.raise_for_status()
        return resp.json()


# -- parser -------------------------------------------------------------------


def _border_series(
    payload: dict[str, Any],
    queried_zone: str,
) -> tuple[list[int], list[tuple[tuple[str, str], float, list[float | None]]]]:
    """Common series walk for the daily and hourly parsers: map each neighbour
    series to its canonical border and sign multiplier (see module docstring).

    Returns (unix_seconds, [(canon_border, sign, raw_values), ...]).
    """
    unix_seconds: list[int] = payload.get("unix_seconds", [])
    out: list[tuple[tuple[str, str], float, list[float | None]]] = []
    if not unix_seconds:
        logger.debug("_border_series(%s): empty unix_seconds", queried_zone)
        return unix_seconds, out

    for series in payload.get("countries", []):
        name: str = series.get("name", "")
        if name == "sum":
            continue  # aggregate series, not a real border
        neighbor_zone = COUNTRY_TO_ZONE.get(name)
        if neighbor_zone is None:
            logger.debug(
                "_border_series(%s): unmapped country name %r -- skipping",
                queried_zone, name,
            )
            continue

        raw_values: list[float | None] = series.get("data", [])
        if len(raw_values) != len(unix_seconds):
            logger.warning(
                "_border_series(%s): length mismatch for %r -- %d values vs %d timestamps",
                queried_zone, name, len(raw_values), len(unix_seconds),
            )
            continue

        canon = tuple(sorted([queried_zone, neighbor_zone]))
        # raw = import INTO queried_zone; net_mw(from->to) > 0 = from_zone exports.
        sign = -1.0 if queried_zone == canon[0] else +1.0
        out.append((canon, sign, raw_values))
    return unix_seconds, out


def parse_cbpf_hourly(
    payload: dict[str, Any],
    queried_zone: str,
) -> dict[tuple[str, str], dict[int, float]]:
    """Parse a /cbpf response into hourly-mean net_mw per canonical border.

    Returns {(from_zone, to_zone): {top_of_hour_epoch_utc: net_mw}} — the shape
    upsert_hourly consumes for the canonical store (roadmap Block 2.4). Sign and
    unit conventions are identical to parse_cbpf (net_mw > 0 = from_zone exports,
    GW → MW); the 15-min raw points are averaged per UTC hour.
    """
    unix_seconds, borders = _border_series(payload, queried_zone)
    result: dict[tuple[str, str], dict[int, float]] = {}
    for canon, sign, raw_values in borders:
        hour_accum: dict[int, list[float]] = defaultdict(list)
        for ts, raw_val in zip(unix_seconds, raw_values):
            if raw_val is not None:
                hour_accum[ts - ts % 3600].append(raw_val)
        hourly = {
            hour_ts: sign * (sum(vals) / len(vals)) * 1000.0
            for hour_ts, vals in hour_accum.items()
        }
        if canon in result:
            for hour_ts, val in hourly.items():
                prev = result[canon].get(hour_ts)
                result[canon][hour_ts] = val if prev is None else (prev + val) / 2.0
        else:
            result[canon] = hourly
    return result


def parse_cbpf(
    payload: dict[str, Any],
    queried_zone: str,
) -> dict[tuple[str, str], dict[str, float]]:
    """Parse a /cbpf JSON response into daily-mean net_mw per canonical border.

    queried_zone is the OBSYD zone code for the country that was queried
    (e.g. "DE_LU" for country=de).

    Returns:
        {(from_zone, to_zone): {YYYY-MM-DD: net_mw}}

    Border key is canonical (sorted lexicographically):
        sorted_pair = tuple(sorted([queried_zone, neighbor_zone]))
        from_zone, to_zone = sorted_pair

    Sign convention:
        raw value from the API = import INTO queried_zone from neighbor
        net_mw(from_zone -> to_zone) is positive when from_zone exports.

        If queried_zone == from_zone (sorted-first):
            net_mw = -raw_import  (negate: positive import means from_zone imports)
        If queried_zone == to_zone (sorted-second):
            raw_import = export from from_zone -> to_zone = net_mw directly
            net_mw = +raw_import

    Daily mean: average all 15-min (or hourly) data points that fall within
    each UTC calendar day. Values are in GW; converted to MW (* 1000).
    """
    unix_seconds, borders = _border_series(payload, queried_zone)

    # Pre-bucket timestamps to UTC date strings
    ts_dates: list[str] = [
        datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
        for ts in unix_seconds
    ]

    result: dict[tuple[str, str], dict[str, float]] = {}

    for canon, sign, raw_values in borders:
        # Bucket to daily means (GW -> MW)
        day_accum: dict[str, list[float]] = defaultdict(list)
        for day_str, raw_val in zip(ts_dates, raw_values):
            if raw_val is not None:
                day_accum[day_str].append(raw_val)

        daily: dict[str, float] = {
            day: sign * (sum(vals) / len(vals)) * 1000.0
            for day, vals in day_accum.items()
            if vals
        }

        if canon not in result:
            result[canon] = daily
        else:
            # Merge: average duplicate dates (guard for future multi-call usage)
            for day, val in daily.items():
                if day in result[canon]:
                    result[canon][day] = (result[canon][day] + val) / 2.0
                else:
                    result[canon][day] = val

    return result


# -- upsert -------------------------------------------------------------------


def _upsert_flow(
    db: Session,
    day: str,
    from_zone: str,
    to_zone: str,
    net_mw: float,
) -> None:
    """Insert or update a PowerFlow row (idempotent)."""
    existing = (
        db.query(PowerFlow)
        .filter(
            PowerFlow.date == day,
            PowerFlow.from_zone == from_zone,
            PowerFlow.to_zone == to_zone,
        )
        .first()
    )
    if existing:
        existing.net_mw = net_mw
    else:
        db.add(
            PowerFlow(
                date=day,
                from_zone=from_zone,
                to_zone=to_zone,
                net_mw=net_mw,
            )
        )


# -- ingest -------------------------------------------------------------------


def _month_windows(start: date, end: date) -> list[tuple[date, date]]:
    windows, cur = [], start.replace(day=1)
    while cur <= end:
        nxt = date(cur.year + 1, 1, 1) if cur.month == 12 else date(cur.year, cur.month + 1, 1)
        windows.append((max(start, cur), min(end, nxt - timedelta(days=1))))
        cur = nxt
    return windows


async def _fetch_with_rate_limit(country_code: str, start_iso: str, end_iso: str) -> dict:
    """fetch_cbpf with HTTP-429 backoff for the backfill path.

    A month-sweep is hundreds of requests; without honouring the rate limit the
    2026-07-12 run "completed" in 20 s having skipped almost every country-month
    (ingest_cbpf logs per-country failures as warnings, so nothing retried).
    Non-429 errors propagate immediately.
    """
    for attempt in range(RATE_LIMIT_ATTEMPTS):
        try:
            return await fetch_cbpf(country_code, start_iso, end_iso)
        except httpx.HTTPStatusError as exc:
            resp = exc.response
            if resp is None or resp.status_code != 429 or attempt == RATE_LIMIT_ATTEMPTS - 1:
                raise
            retry_after = resp.headers.get("Retry-After", "")
            try:
                wait = float(retry_after)
            except ValueError:
                wait = RATE_LIMIT_BASE_SECONDS * (2**attempt)
            logger.warning(
                "energy_charts_flows: 429 for %s %s..%s — backing off %.0fs (attempt %d/%d)",
                country_code, start_iso, end_iso, wait, attempt + 1, RATE_LIMIT_ATTEMPTS,
            )
            await asyncio.sleep(wait)
    raise AssertionError("unreachable")  # loop always returns or raises


async def _fetch_range(
    country_code: str,
    start_date: date,
    end_date: date,
    *,
    use_cache: bool,
    overwrite: bool,
) -> list[dict]:
    """Fetch /cbpf payload(s) covering [start_date, end_date] for one country.

    Live path (use_cache=False): one direct request for the exact range — the
    scheduler's small rolling windows change intraday and must not be cached.
    Backfill path (use_cache=True): month-chunked through raw_cache so a crashed
    or re-run backfill never re-hits the API. Only COMPLETED months are written
    to the cache — a current-month blob would freeze mid-month and starve later
    re-runs of the month's remainder.
    """
    if not use_cache:
        return [await fetch_cbpf(country_code, start_date.isoformat(), end_date.isoformat())]

    today = datetime.now(timezone.utc).date()
    payloads: list[dict] = []
    for m_start, m_end in _month_windows(start_date, end_date):
        month_first = m_start.replace(day=1)
        nxt = (
            date(month_first.year + 1, 1, 1)
            if month_first.month == 12
            else date(month_first.year, month_first.month + 1, 1)
        )
        month_last = nxt - timedelta(days=1)

        if month_last >= today:
            # Running (incomplete) month: fetch the requested window live, never
            # persist — a mid-month blob would freeze and shadow the remainder.
            payloads.append(
                await _fetch_with_rate_limit(country_code, m_start.isoformat(), m_end.isoformat())
            )
            await asyncio.sleep(CACHE_THROTTLE_SECONDS)
            continue

        key = f"{country_code}_{month_first:%Y-%m}"
        if not overwrite:
            hit = raw_cache.read_cached(CACHE_SOURCE, key, month_first)
            if hit is not None:
                payloads.append(hit)
                continue
        # Cached blobs always span the FULL month, whatever day subset was
        # requested — the key claims the month, so the content must deliver it.
        payload = await _fetch_with_rate_limit(country_code, month_first.isoformat(), month_last.isoformat())
        raw_cache.write_cached(CACHE_SOURCE, key, month_first, payload, overwrite=overwrite)
        payloads.append(payload)
        await asyncio.sleep(CACHE_THROTTLE_SECONDS)
    return payloads


async def ingest_cbpf(
    db: Session,
    days: list[str],
    *,
    overwrite: bool = False,
    use_cache: bool = False,
) -> dict:
    """Ingest CBPF cross-border flows for every flow-mapped enabled base zone.

    For each base country (derived from the enabled zones), fetches the date
    range (one request in the live path; month-chunked through raw_cache with
    use_cache=True — the backfill path). Parses each response into canonical
    (from_zone, to_zone) net_mw values. Where a border is seen from both
    queried sides (e.g. DE_LU/FR appears in both the DE and FR queries), the
    two estimates are averaged before upserting.

    Writes two grains per border (roadmap Block 2.4):
      * daily mean  → PowerFlow            (map + freshness, unchanged)
      * hourly mean → power_hourly, series ``flow.<TO>`` under zone ``<FROM>``
        (canonical sorted border, net_mw > 0 = <FROM> exports)

    Args:
        db:        SQLAlchemy session.
        days:      List of YYYY-MM-DD strings to ingest.
        overwrite: Re-fetch cached months (backfill path only).
        use_cache: Month-chunk the fetches through raw_cache (backfill path).

    Returns:
        {"days": n, "borders": n_borders, "written": n_rows, "hourly_written": n}
        or the zero dict for empty input.
    """
    if not days:
        return {"days": 0, "borders": 0, "written": 0, "hourly_written": 0}

    wanted: set[str] = set(days)
    start_date = datetime.strptime(min(days), "%Y-%m-%d").date()
    end_date = datetime.strptime(max(days), "%Y-%m-%d").date()

    # Accumulate: {canon_border: {date/hour_ts: [net_mw_estimate, ...]}}
    # Multiple queries (DE + FR) can provide estimates for the same border.
    accumulated: dict[tuple[str, str], dict[str, list[float]]] = defaultdict(
        lambda: defaultdict(list)
    )
    accumulated_hourly: dict[tuple[str, str], dict[int, list[float]]] = defaultdict(
        lambda: defaultdict(list)
    )

    for country_code in BASE_COUNTRIES:
        zone = COUNTRY_CODE_TO_ZONE.get(country_code)
        if zone is None:
            continue
        try:
            payloads = await _fetch_range(
                country_code, start_date, end_date,
                use_cache=use_cache, overwrite=overwrite,
            )
        except httpx.HTTPError as exc:
            logger.warning(
                "energy_charts_flows: fetch_cbpf(%s, %s..%s) failed: %s",
                country_code, start_date, end_date, exc,
            )
            continue

        for payload in payloads:
            for canon, daily in parse_cbpf(payload, zone).items():
                for day, val in daily.items():
                    if day in wanted:
                        accumulated[canon][day].append(val)
            for canon, hourly in parse_cbpf_hourly(payload, zone).items():
                for hour_ts, val in hourly.items():
                    hour_day = datetime.fromtimestamp(hour_ts, tz=timezone.utc).strftime("%Y-%m-%d")
                    if hour_day in wanted:
                        accumulated_hourly[canon][hour_ts].append(val)

    # Write deduped (averaged across both-side queries) values
    written = 0
    borders_seen: set[tuple[str, str]] = set()

    for canon, day_estimates in accumulated.items():
        from_zone, to_zone = canon
        borders_seen.add(canon)
        for day in sorted(day_estimates):
            estimates = day_estimates[day]
            # Average the estimates from both sides (ideally identical, but float
            # rounding and intra-day aggregation order may differ slightly)
            net_mw = sum(estimates) / len(estimates)
            _upsert_flow(db, day, from_zone, to_zone, net_mw)
            written += 1

    db.commit()

    hourly_written = 0
    for canon, hour_estimates in accumulated_hourly.items():
        from_zone, to_zone = canon
        points = [
            (hour_ts, sum(vals) / len(vals))
            for hour_ts, vals in sorted(hour_estimates.items())
        ]
        hourly_written += upsert_hourly(
            db, f"flow.{to_zone}", from_zone, points, unit="MW"
        )

    logger.info(
        "energy_charts_flows.ingest_cbpf: %d daily + %d hourly rows across %d borders (%s..%s)",
        written, hourly_written, len(borders_seen), start_date, end_date,
    )
    return {
        "days": len(days),
        "borders": len(borders_seen),
        "written": written,
        "hourly_written": hourly_written,
    }
