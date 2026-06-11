"""GIE ingestion: AGSI (EU gas storage) + ALSI (EU LNG send-out).

Field names verified against the live API (they differ from older clients —
ALSI uses `inventory` as a {lng, gwh} dict, not `lngInventory`; the day is
`gasDayStart`). One free GIE key (x-key header) covers both services.

Access pattern: GET {base}?date=YYYY-MM-DD returns one row per aggregate code;
we keep code == "eu". Raw payloads are disk-cached so a re-run never re-hits
the API. AGSI gasInStorage is already TWh; ALSI inventory.gwh → TWh.
"""

from __future__ import annotations

import logging
from datetime import date, datetime

import httpx
from sqlalchemy.orm import Session

from backend.config import settings
from backend.gas import raw_cache
from backend.gas.units import coerce_float
from backend.models.gas import GasLng, GasStorage

logger = logging.getLogger(__name__)

AGSI_BASE = "https://agsi.gie.eu/api"
ALSI_BASE = "https://alsi.gie.eu/api"
EU_CODE = "eu"


def _gie_headers() -> dict:
    key = settings.gie_api_key
    if not key:
        raise RuntimeError("GIE_API_KEY not configured — set it in .env")
    return {"x-key": key.get_secret_value()}  # never logged


async def _fetch_day(base: str, source: str, day: str, *, overwrite: bool) -> dict:
    dt = datetime.strptime(day, "%Y-%m-%d").date()

    async def _do() -> dict:
        async with httpx.AsyncClient(timeout=40) as client:
            resp = await client.get(base, params={"date": day}, headers=_gie_headers())
            resp.raise_for_status()
            return resp.json()

    return await raw_cache.fetch_or_cache(source, f"{source}_{day}", dt, _do, overwrite=overwrite)


def _eu_row(payload: dict) -> dict | None:
    for row in payload.get("data", []):
        if row.get("code") == EU_CODE:
            return row
    return None


async def ingest_storage(db: Session, days: list[str], *, overwrite: bool = False) -> dict:
    """AGSI EU storage → gas_storage."""
    written = 0
    for day in days:
        eu = _eu_row(await _fetch_day(AGSI_BASE, "agsi", day, overwrite=overwrite))
        if not eu:
            continue  # data gap — no silent fill
        _upsert_storage(
            db,
            day,
            stock_twh=coerce_float(eu.get("gasInStorage")),   # already TWh
            injection_gwh=coerce_float(eu.get("injection")),  # GWh/d
            withdrawal_gwh=coerce_float(eu.get("withdrawal")),
            fill_pct=coerce_float(eu.get("full")),
        )
        written += 1
    db.commit()
    logger.info("gie.ingest_storage: %d/%d days", written, len(days))
    return {"days": len(days), "written": written}


async def ingest_lng(db: Session, days: list[str], *, overwrite: bool = False) -> dict:
    """ALSI EU LNG send-out + inventory → gas_lng."""
    written = 0
    for day in days:
        eu = _eu_row(await _fetch_day(ALSI_BASE, "alsi", day, overwrite=overwrite))
        if not eu:
            continue
        inv = eu.get("inventory")
        inv_gwh = coerce_float(inv.get("gwh")) if isinstance(inv, dict) else None
        _upsert_lng(
            db,
            day,
            send_out_gwh=coerce_float(eu.get("sendOut")),                       # GWh/d (primary LNG supply)
            inventory_twh=(inv_gwh / 1000.0) if inv_gwh is not None else None,  # GWh → TWh
        )
        written += 1
    db.commit()
    logger.info("gie.ingest_lng: %d/%d days", written, len(days))
    return {"days": len(days), "written": written}


def _upsert_storage(db, day, *, stock_twh, injection_gwh, withdrawal_gwh, fill_pct):
    existing = db.get(GasStorage, day)
    if existing:
        existing.stock_twh = stock_twh
        existing.injection_gwh = injection_gwh
        existing.withdrawal_gwh = withdrawal_gwh
        existing.fill_pct = fill_pct
    else:
        db.add(GasStorage(date=day, stock_twh=stock_twh, injection_gwh=injection_gwh, withdrawal_gwh=withdrawal_gwh, fill_pct=fill_pct))


def _upsert_lng(db, day, *, send_out_gwh, inventory_twh):
    existing = db.get(GasLng, day)
    if existing:
        existing.send_out_gwh = send_out_gwh
        existing.inventory_twh = inventory_twh
    else:
        db.add(GasLng(date=day, send_out_gwh=send_out_gwh, inventory_twh=inventory_twh))


def daterange(start: date, end: date) -> list[str]:
    """Inclusive list of YYYY-MM-DD strings."""
    out, d = [], start
    while d <= end:
        out.append(d.isoformat())
        d = date.fromordinal(d.toordinal() + 1)
    return out
