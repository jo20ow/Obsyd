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

import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy.orm import Session

from backend.models.energy import PowerFlow
from backend.power.zones import ENABLED_ZONES, ZONE_REGISTRY

logger = logging.getLogger(__name__)

CBPF_BASE = "https://api.energy-charts.info/cbpf"
ATTRIBUTION = "Energy-Charts (CC BY 4.0)"

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
    unix_seconds: list[int] = payload.get("unix_seconds", [])
    countries: list[dict] = payload.get("countries", [])

    if not unix_seconds:
        logger.debug("parse_cbpf(%s): empty unix_seconds", queried_zone)
        return {}

    # Pre-bucket timestamps to UTC date strings
    ts_dates: list[str] = [
        datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
        for ts in unix_seconds
    ]

    result: dict[tuple[str, str], dict[str, float]] = {}

    for series in countries:
        name: str = series.get("name", "")
        if name == "sum":
            continue  # aggregate series, not a real border
        neighbor_zone = COUNTRY_TO_ZONE.get(name)
        if neighbor_zone is None:
            logger.debug(
                "parse_cbpf(%s): unmapped country name %r -- skipping",
                queried_zone, name,
            )
            continue

        raw_values: list[float | None] = series.get("data", [])
        if len(raw_values) != len(unix_seconds):
            logger.warning(
                "parse_cbpf(%s): length mismatch for %r -- %d values vs %d timestamps",
                queried_zone, name, len(raw_values), len(unix_seconds),
            )
            continue

        # Determine canonical border direction (sorted lexicographic)
        canon = tuple(sorted([queried_zone, neighbor_zone]))
        from_zone, to_zone = canon

        # Sign multiplier: raw = import INTO queried_zone.
        # net_mw(from_zone->to_zone) > 0 = export from from_zone.
        if queried_zone == from_zone:
            # queried_zone is sorted-first (from_zone); raw positive = it imports.
            # net_mw = -raw so that positive means from_zone EXPORTS.
            sign = -1.0
        else:
            # queried_zone is sorted-second (to_zone); raw positive = to_zone imports
            # from from_zone = export from from_zone = net_mw directly.
            sign = +1.0

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


async def ingest_cbpf(
    db: Session,
    days: list[str],
    *,
    overwrite: bool = False,  # noqa: ARG001
) -> dict:
    """Ingest CBPF cross-border flows for every flow-mapped enabled base zone.

    For each base country (derived from the enabled zones), fetches the full date range in one request
    (Energy-Charts accepts arbitrary start/end).  Parses each response into
    canonical (from_zone, to_zone) daily net_mw values.  Where a border is
    seen from both queried sides (e.g. DE_LU/FR appears in both the DE and
    FR queries), the two estimates are averaged before upserting.

    Args:
        db:        SQLAlchemy session.
        days:      List of YYYY-MM-DD strings to ingest.
        overwrite: Accepted for API compatibility; upsert is always unconditional.

    Returns:
        {"days": n, "borders": n_borders, "written": n_rows}
        or {"days": 0, "borders": 0, "written": 0} for empty input.
    """
    if not days:
        return {"days": 0, "borders": 0, "written": 0}

    wanted: set[str] = set(days)
    start_date = min(days)
    end_date = max(days)

    # Accumulate: {canon_border: {date: [net_mw_estimate, ...]}}
    # Multiple queries (DE + FR) can provide estimates for the same border.
    accumulated: dict[tuple[str, str], dict[str, list[float]]] = defaultdict(
        lambda: defaultdict(list)
    )

    for country_code in BASE_COUNTRIES:
        zone = COUNTRY_CODE_TO_ZONE.get(country_code)
        if zone is None:
            continue
        try:
            payload = await fetch_cbpf(country_code, start_date, end_date)
        except httpx.HTTPError as exc:
            logger.warning(
                "energy_charts_flows: fetch_cbpf(%s, %s..%s) failed: %s",
                country_code, start_date, end_date, exc,
            )
            continue

        border_data = parse_cbpf(payload, zone)

        for canon, daily in border_data.items():
            for day, val in daily.items():
                if day in wanted:
                    accumulated[canon][day].append(val)

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
    logger.info(
        "energy_charts_flows.ingest_cbpf: %d rows written across %d borders (%s..%s)",
        written, len(borders_seen), start_date, end_date,
    )
    return {"days": len(days), "borders": len(borders_seen), "written": written}
