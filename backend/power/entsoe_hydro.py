"""ENTSO-E A72: weekly reservoir filling → series `hydro.reservoir` (MWh).

Spiked live 2026-07-11 (NO2): GL_MarketDocument, resolution P7D, unit MWh, one
point per week — southern Norway alone holds ~21 TWh, which is why Nordic and
Alpine reservoir levels move power prices continent-wide.

"A72 light": this collector has its OWN zone list (HYDRO_ZONES) independent of
ENABLED_ZONES — the trader question is "how full are the reservoirs vs normal",
which needs no full price/load ingest for those zones. Data is tiny (52
points/zone/year), so the deep backfill is trivial.
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from datetime import date, timedelta, timezone

import httpx
from sqlalchemy.orm import Session

from backend.config import settings
from backend.gas import raw_cache
from backend.gas.entsoe import ENTSOE_BASE, _localname, _parse_utc, _token
from backend.power.hourly_store import upsert_hourly
from backend.power.zones import ZONE_REGISTRY

logger = logging.getLogger(__name__)

#: The reservoir geography — Nordics, Alps, Iberia, France. Deliberately NOT
#: tied to ENABLED_ZONES; see module docstring.
HYDRO_ZONES: list[str] = [
    "NO1", "NO2", "NO3", "NO4", "NO5",
    "SE1", "SE2", "SE3", "SE4",
    "FI", "CH", "AT", "ES", "PT", "FR",
]

_WEEK = timedelta(days=7)


def parse_reservoir_filling(xml_text: str) -> list[tuple[int, float]]:
    """Parse an A72 GL_MarketDocument into [(epoch_sec_week_start, stored_mwh)].

    Only P7D periods contribute — that is the reservoir product's native
    cadence; anything else is a different document. Acknowledgements (no
    TimeSeries) yield []. Overlapping series average per timestamp.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ValueError(f"ENTSO-E A72 XML parse error: {exc}") from exc

    by_ts: dict[int, list[float]] = {}
    for ts in root.iter():
        if _localname(ts.tag) != "TimeSeries":
            continue
        for period in (e for e in ts.iter() if _localname(e.tag) == "Period"):
            start_el = next((e for e in period.iter() if _localname(e.tag) == "start"), None)
            res_el = next((e for e in period.iter() if _localname(e.tag) == "resolution"), None)
            if start_el is None or res_el is None:
                continue
            if (res_el.text or "").strip() != "P7D":
                continue
            start = _parse_utc(start_el.text)
            if start is None:
                continue
            for point in (e for e in period.iter() if _localname(e.tag) == "Point"):
                pos = next((e.text for e in point if _localname(e.tag) == "position"), None)
                qty = next((e.text for e in point if _localname(e.tag) == "quantity"), None)
                if pos is None or qty is None:
                    continue
                try:
                    week_start = start + _WEEK * (int(pos) - 1)
                    mwh = float(qty)
                except (ValueError, TypeError):
                    continue
                epoch = int(week_start.astimezone(timezone.utc).timestamp())
                by_ts.setdefault(epoch, []).append(mwh)

    return [(t, sum(vs) / len(vs)) for t, vs in sorted(by_ts.items())]


async def _fetch_zone_year(eic: str, year: int, *, overwrite: bool = False) -> str:
    """One zone's A72 for a calendar year (52 points — a year per request keeps
    the call count trivial). Cached year-keyed; the CURRENT year must be fetched
    with overwrite=True or the write-once cache would freeze January's frontier."""
    start = date(year, 1, 1)

    async def _do() -> dict:
        params = {
            "securityToken": _token(),
            "documentType": "A72",
            "processType": "A16",
            "in_Domain": eic,
            "periodStart": f"{year}01010000",
            "periodEnd": f"{year + 1}01010000",
        }
        async with httpx.AsyncClient(timeout=90) as client:
            resp = await client.get(ENTSOE_BASE, params=params)
            resp.raise_for_status()
            return {"xml": resp.text}

    payload = await raw_cache.fetch_or_cache(
        "entsoe_hydro", f"{eic}_{year}", start, _do, overwrite=overwrite
    )
    return payload.get("xml", "")


async def ingest_hydro(
    db: Session,
    *,
    years: list[int],
    zones: list[str] | None = None,
    overwrite: bool = False,
) -> dict:
    """Fetch + upsert weekly reservoir filling for the hydro zones."""
    if not settings.entsoe_api_token:
        logger.warning("entsoe_hydro.ingest_hydro: ENTSOE_API_TOKEN not set — skipping")
        return {"skipped": "no token"}

    zones = zones if zones is not None else HYDRO_ZONES
    written = 0
    for zone in zones:
        eic = ZONE_REGISTRY.get(zone, {}).get("eic")
        if not eic:
            logger.warning("entsoe_hydro: zone %s not in registry — skipping", zone)
            continue
        points: list[tuple[int, float]] = []
        for year in years:
            try:
                xml = await _fetch_zone_year(eic, year, overwrite=overwrite)
            except httpx.HTTPError as exc:
                logger.warning("entsoe_hydro: %s %s fetch failed: %s", zone, year, exc)
                continue
            if not xml:
                continue
            try:
                points.extend(parse_reservoir_filling(xml))
            except ValueError as exc:
                logger.warning("entsoe_hydro: %s %s parse failed: %s", zone, year, exc)
        if points:
            written += upsert_hourly(db, "hydro.reservoir", zone, points, unit="MWh")

    return {"written": written, "zones": len(zones), "years": years}


def same_week_band(points: list[tuple[int, float]]) -> dict:
    """Compare the newest weekly filling against the SAME ISO week in prior years.

    Reservoir levels are hard seasonal — a trailing window would flag every
    spring melt as an anomaly. So the band is built from the nearest point
    within ±4 days of (newest − i·52 weeks) for each prior year i. Shared by
    the /hydro route and the hydro_deviation radar detector (one derivation).
    """
    import bisect

    ts, value = points[-1]
    keys = [t for t, _ in points]
    band: list[float] = []
    i = 1
    while True:
        target = ts - i * 364 * 86_400
        if target < keys[0] - 4 * 86_400:
            break
        j = bisect.bisect_left(keys, target)
        best = None
        for k in (j - 1, j):
            if 0 <= k < len(keys) and abs(keys[k] - target) <= 4 * 86_400:
                if best is None or abs(keys[k] - target) < abs(keys[best] - target):
                    best = k
        if best is not None:
            band.append(points[best][1])
        i += 1

    vs_band = None
    if band:
        vs_band = "below" if value < min(band) else "above" if value > max(band) else "within"
    return {
        "band_min_twh": round(min(band) / 1e6, 2) if band else None,
        "band_max_twh": round(max(band) / 1e6, 2) if band else None,
        "band_mean_twh": round(sum(band) / len(band) / 1e6, 2) if band else None,
        "band_n": len(band),
        "vs_band": vs_band,
    }
