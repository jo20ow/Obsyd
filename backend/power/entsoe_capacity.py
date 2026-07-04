"""ENTSO-E installed generation capacity per production type (A68 / processType A33).

Annual, per bidding zone — reference/context data (how much wind/solar/gas/etc.
capacity a zone has), exposed at /api/v1/capacity. Same document shape as A75
generation (TimeSeries → psrType → Period/Point in MW), but one value per year.
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
from backend.models.energy import InstalledCapacity
from backend.power.entsoe_grid import PSR_LABELS

logger = logging.getLogger(__name__)


def parse_installed_capacity(xml_text: str) -> dict[str, float]:
    """Parse an A68 GL_MarketDocument into {psrType_code: capacity_MW}.

    One value per production type (annual); if a type carries several points, they
    are averaged (defensive — normally there's exactly one).
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ValueError(f"ENTSO-E A68 XML parse error: {exc}") from exc

    out: dict[str, float] = {}
    for ts in root.iter():
        if _localname(ts.tag) != "TimeSeries":
            continue
        psr = next((e.text for e in ts.iter() if _localname(e.tag) == "psrType"), None)
        if psr is None:
            continue
        vals: list[float] = []
        for point in (e for e in ts.iter() if _localname(e.tag) == "Point"):
            qty = next((e.text for e in point if _localname(e.tag) == "quantity"), None)
            if qty is not None:
                try:
                    vals.append(float(qty))
                except (TypeError, ValueError):
                    continue
        if vals:
            out[psr] = sum(vals) / len(vals)
    return out


async def _fetch_capacity_year(eic: str, year: int, *, overwrite: bool = False) -> str:
    async def _do() -> dict:
        params = {
            "securityToken": _token(),
            "documentType": "A68",
            "processType": "A33",
            "in_Domain": eic,
            "periodStart": f"{year}01010000",
            "periodEnd": f"{year + 1}01010000",
        }
        async with httpx.AsyncClient(timeout=90) as client:
            resp = await client.get(ENTSOE_BASE, params=params)
            resp.raise_for_status()
            return {"xml": resp.text}

    payload = await raw_cache.fetch_or_cache(
        "entsoe_capacity", f"{eic}_{year}", date(year, 1, 1), _do, overwrite=overwrite
    )
    return payload.get("xml", "") if isinstance(payload, dict) else ""


async def ingest_installed_capacity(
    db: Session, year: int, *, eic: str, zone: str = "DE_LU", overwrite: bool = False
) -> dict:
    """Fetch + upsert installed capacity per production type for one zone-year."""
    if not settings.entsoe_api_token:
        return {"skipped": "no token"}
    try:
        xml = await _fetch_capacity_year(eic, year, overwrite=overwrite)
    except httpx.HTTPError as exc:
        logger.warning("capacity ingest [%s %s] fetch failed: %s", zone, year, exc)
        return {"skipped": "fetch failed"}
    if not xml:
        return {"year": year, "written": 0}

    caps = parse_installed_capacity(xml)
    written = 0
    for code, mw in caps.items():
        label = PSR_LABELS.get(code, code)
        row = (
            db.query(InstalledCapacity)
            .filter_by(zone=zone, year=year, psr_type=label)
            .first()
        )
        if row is None:
            db.add(InstalledCapacity(zone=zone, year=year, psr_type=label, capacity_mw=round(mw, 2)))
            written += 1
        elif overwrite:
            row.capacity_mw = round(mw, 2)
            written += 1
    db.commit()
    return {"year": year, "written": written}
