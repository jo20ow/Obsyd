"""ENTSO-E production units (A71 / processType A33) — names for the EICs on the outage board.

`PowerOutage.unit_eic` has been written since the outage ingest was built and read by nothing.
This is the table it was waiting for: the outage board can now say "CATTENOM 3" where it said
`17W100P100P0001A`, and every one of the 37 zones answers — including the 18 that have no A68
installed-capacity data at all.

WHAT IT IS NOT
--------------
It is NOT the installed fleet, and the temptation to use it as one is the whole risk of this
module. Measured against prod:

    DE-LU   A71/A33:  133 units,  65,193 MW      FR   A71/A33:  174 units,  93,903 MW
            A68    :             294,941 MW           A68    :             163,611 MW
                                 ──────────                                ──────────
                                  factor 4.5                                factor 1.7

And the ratio is not even CONSTANT (NL: 2.7), so no correction factor could turn one into the
other.

A71/A33 lists only production UNITS above ENTSO-E's ~100 MW publication threshold. It is a
different population, not a smaller sample of the same one. See ProductionUnit's docstring and
`published_unit_capacity_mw` for what it may honestly be used for, and
docs/findings/2026-07-13-entsoe-a09-a25-a71.md for the measurement.

CACHE
-----
`entsoe_units`, and it MUST be its own. `entsoe_gen_total_forecast` is already taken by A71 +
processType **A01** (the day-ahead generation forecast). Same document type, different process
type, completely different document — sharing the cache would serve back the wrong one, and it
would look like a data bug rather than a wiring bug.
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from datetime import date

import httpx
from sqlalchemy.orm import Session

from backend.config import settings
from backend.gas import raw_cache
from backend.gas.entsoe import ENTSOE_BASE, _localname, _token
from backend.models.energy import ProductionUnit
from backend.power.zones import ZONE_REGISTRY

logger = logging.getLogger(__name__)

UNIT_REGISTRY_DOCTYPE = "A71"
UNIT_REGISTRY_PROCESS_TYPE = "A33"
CACHE_SOURCE = "entsoe_units"  # NOT entsoe_gen_total_forecast — see module docstring


def parse_production_units(xml_text: str) -> list[dict]:
    """A71/A33 → [{unit_eic, name, psr_type, nominal_mw}]. Pure.

    One TimeSeries per unit.

    The nominal power arrives under TWO different tag names in the SAME document —
    `nominalIP_PowerSystemResources.nominalP` on most units and a bare `nominalP` on others (NL
    2026: 51 of one, 56 of the other). Matching only one and falling through to the Period's
    quantity for the rest is how a parser looks like it works: the quantity carries the same
    figure, so nothing errors and half the units are read by accident. Both are matched.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ValueError(f"ENTSO-E A71 XML parse error: {exc}") from exc

    units: list[dict] = []
    for ts in root.iter():
        if _localname(ts.tag) != "TimeSeries":
            continue
        eic = next((e.text for e in ts.iter()
                    if _localname(e.tag) == "registeredResource.mRID"), None)
        if not eic:
            continue
        # The nominal power comes under TWO different tag names in the SAME document —
        # `nominalIP_PowerSystemResources.nominalP` on most units and a bare `nominalP` on
        # others. Matching one of them and silently falling through to the Period's quantity
        # for the rest is how a parser looks like it works: the quantity happens to carry the
        # same figure, so nothing breaks and half the units are read by accident.
        nominal = next(
            (e.text for e in ts.iter() if _localname(e.tag).endswith("nominalP")), None
        )
        if nominal is None:
            nominal = next((e.text for e in ts.iter() if _localname(e.tag) == "quantity"), None)
        units.append({
            "unit_eic": eic,
            "name": next((e.text for e in ts.iter()
                          if _localname(e.tag) == "registeredResource.name"), None),
            # RAW code. B03 is real and is NOT in PSR_LABELS — label at read time, never here.
            "psr_type": next((e.text for e in ts.iter()
                              if _localname(e.tag) == "psrType"), None),
            "nominal_mw": _float(nominal),
        })
    return units


def _float(value: str | None) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


async def _fetch_units_year(zone: str, year: int, *, overwrite: bool = False) -> str:
    eic = ZONE_REGISTRY[zone]["eic"]

    async def _do() -> dict:
        params = {
            "securityToken": _token(),
            "documentType": UNIT_REGISTRY_DOCTYPE,
            "processType": UNIT_REGISTRY_PROCESS_TYPE,
            "in_Domain": eic,
            "periodStart": f"{year}01010000",
            "periodEnd": f"{year}01020000",
        }
        async with httpx.AsyncClient(timeout=180) as client:  # up to ~9 s per zone
            resp = await client.get(ENTSOE_BASE, params=params)
            if resp.status_code >= 400:
                return {"xml": ""}
            return {"xml": resp.text}

    payload = await raw_cache.fetch_or_cache(
        CACHE_SOURCE, f"{zone}_{year}", date(year, 1, 1), _do, overwrite=overwrite,
    )
    return payload.get("xml", "")


async def ingest_production_units(
    db: Session, year: int, *, zones: list[str] | None = None, overwrite: bool = False,
) -> dict:
    """Refresh the production-unit registry for `year`."""
    if not settings.entsoe_api_token:
        return {"skipped": "no token"}

    zones = zones or list(ZONE_REGISTRY)
    written = covered = 0
    for zone in zones:
        try:
            xml = await _fetch_units_year(zone, year, overwrite=overwrite)
        except httpx.HTTPError as exc:
            logger.warning("units %s %s: %s", zone, year, exc)
            continue
        if not xml:
            continue
        units = parse_production_units(xml)
        if not units:
            continue
        for u in units:
            existing = (
                db.query(ProductionUnit)
                .filter(ProductionUnit.unit_eic == u["unit_eic"], ProductionUnit.year == year)
                .one_or_none()
            )
            if existing:
                existing.zone = zone
                existing.name = u["name"]
                existing.psr_type = u["psr_type"]
                existing.nominal_mw = u["nominal_mw"]
            else:
                db.add(ProductionUnit(zone=zone, year=year, **u))
            written += 1
        db.flush()  # Session.get/query cannot see pending rows; a re-run would duplicate
        covered += 1
    db.commit()
    return {"year": year, "zones": len(zones), "with_data": covered, "units": written}
