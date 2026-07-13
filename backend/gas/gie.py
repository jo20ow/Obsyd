"""GIE ingestion: AGSI (EU gas storage) + ALSI (EU LNG send-out).

Field names verified against the live API (they differ from older clients —
ALSI uses `inventory` as a {lng, gwh} dict, not `lngInventory`; the day is
`gasDayStart`). One free GIE key (x-key header) covers both services.

Access pattern: GET {base}?date=YYYY-MM-DD returns a TREE — `data[]` holds two or three
ROOTS (`eu`, `ne`, and for ALSI `ai`), each with country children, each of those with
operators, each of those with individual facilities. We keep the `eu` aggregate row (the
EU headline every panel has always shown) AND, since the country layer landed, one row per
country under the whitelisted roots. See COUNTRY_ROOTS for why that whitelist exists and
what reading only `eu` had been quietly deleting.

Still on the floor, deliberately: 78 operators and 137 individual storages with
coordinates, type and injection/withdrawal capacity, under `children[].children[]`.

Raw payloads are disk-cached so a re-run never re-hits the API — which is what makes the
country layer a pure reprocessing job over 2023→today at zero API calls. AGSI gasInStorage
is already TWh; ALSI inventory.gwh → TWh.
"""

from __future__ import annotations

import logging
from datetime import date, datetime

import httpx
from sqlalchemy.orm import Session

from backend.config import settings
from backend.gas import raw_cache
from backend.gas.units import coerce_float
from backend.models.gas import GasLng, GasLngCountry, GasStorage, GasStorageCountry

logger = logging.getLogger(__name__)

AGSI_BASE = "https://agsi.gie.eu/api"
ALSI_BASE = "https://alsi.gie.eu/api"
EU_CODE = "eu"

#: The payload roots whose children are REAL countries.
#:
#: `data[]` has never been one row. It has always been two or three, and we only ever read
#: the first. `eu` carries the 20 member states; `ne` carries Non-EU — which is where UKRAINE
#: (77 TWh in storage) and the real post-Brexit GB* live. The `GB` sitting under `eu` is
#: "United Kingdom (Pre-Brexit)" and is nothing but dashes. Reading only `eu` therefore threw
#: away the two most trade-relevant non-member countries in the file, every day, since 2023.
#:
#: And the obvious fix — "just walk data[] instead of the eu row" — is ALSO wrong, which is
#: why this is a whitelist and not a loop. ALSI carries a third root, `ai` ("Additional
#: Information"), holding `ES*` "Spain (1)": a DUPLICATE of Spain, which already appears
#: under `eu`. On 2026-06-20 that is eu→ES sendOut 343.8 AND ai→ES* sendOut 395.7. Walking
#: every root double-counts Spain in every LNG total. `ai` is excluded on purpose.
COUNTRY_ROOTS = ("eu", "ne")


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


def country_rows(payload: dict) -> list[tuple[str, dict]]:
    """[(region, country_row)] for every country under a whitelisted root. Pure.

    One level down only: the operator and facility levels (78 operators, 137 storages with
    coordinates, type and injection/withdrawal capacity) sit under `children[].children[]`
    and are deliberately left there for a follow-on PR.
    """
    out: list[tuple[str, dict]] = []
    for root in payload.get("data", []):
        region = root.get("code")
        if region not in COUNTRY_ROOTS:
            continue  # `ai` is a duplicate of Spain — see COUNTRY_ROOTS
        for child in root.get("children", []) or []:
            if child.get("code"):
                out.append((region, child))
    return out


async def ingest_storage(db: Session, days: list[str], *, overwrite: bool = False) -> dict:
    """AGSI storage → gas_storage (EU aggregate) AND gas_storage_country, one payload."""
    written = countries = 0
    for day in days:
        payload = await _fetch_day(AGSI_BASE, "agsi", day, overwrite=overwrite)
        eu = _eu_row(payload)
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
        countries += upsert_storage_countries(db, day, payload)
        written += 1
    db.commit()
    logger.info("gie.ingest_storage: %d/%d days, %d country rows", written, len(days), countries)
    return {"days": len(days), "written": written, "countries": countries}


async def ingest_lng(db: Session, days: list[str], *, overwrite: bool = False) -> dict:
    """ALSI LNG → gas_lng (EU aggregate) AND gas_lng_country, one payload."""
    written = countries = 0
    for day in days:
        payload = await _fetch_day(ALSI_BASE, "alsi", day, overwrite=overwrite)
        eu = _eu_row(payload)
        if not eu:
            continue
        _upsert_lng(
            db,
            day,
            send_out_gwh=coerce_float(eu.get("sendOut")),   # GWh/d (primary LNG supply)
            inventory_twh=_inventory_twh(eu),
        )
        countries += upsert_lng_countries(db, day, payload)
        written += 1
    db.commit()
    logger.info("gie.ingest_lng: %d/%d days, %d country rows", written, len(days), countries)
    return {"days": len(days), "written": written, "countries": countries}


def _inventory_twh(row: dict) -> float | None:
    """ALSI reports inventory as a {lng, gwh} dict, not a scalar."""
    inv = row.get("inventory")
    gwh = coerce_float(inv.get("gwh")) if isinstance(inv, dict) else None
    return (gwh / 1000.0) if gwh is not None else None


def upsert_storage_countries(db: Session, day: str, payload: dict) -> int:
    """Per-country AGSI rows from an already-fetched payload. No network.

    Flushes before returning: `Session.get()` does not see rows that are still pending, so
    without it a second pass over the same day inserts duplicates instead of updating —
    which is the difference between a re-readable history and a corrupt one.
    """
    n = 0
    for region, row in country_rows(payload):
        code = row["code"]
        existing = db.get(GasStorageCountry, (day, code))
        values = {
            "region": region,
            "name": row.get("name"),
            "stock_twh": coerce_float(row.get("gasInStorage")),
            "injection_gwh": coerce_float(row.get("injection")),
            "withdrawal_gwh": coerce_float(row.get("withdrawal")),
            "fill_pct": coerce_float(row.get("full")),
            "working_gas_twh": coerce_float(row.get("workingGasVolume")),
            "injection_capacity_gwh": coerce_float(row.get("injectionCapacity")),
            "withdrawal_capacity_gwh": coerce_float(row.get("withdrawalCapacity")),
            "trend": coerce_float(row.get("trend")),
            "status": row.get("status"),
        }
        if existing:
            for k, v in values.items():
                setattr(existing, k, v)
        else:
            db.add(GasStorageCountry(date=day, country=code, **values))
        n += 1
    db.flush()
    return n


def upsert_lng_countries(db: Session, day: str, payload: dict) -> int:
    """Per-country ALSI rows from an already-fetched payload. No network."""
    n = 0
    for region, row in country_rows(payload):
        code = row["code"]
        dtmi = row.get("dtmi")
        dtmi_gwh = coerce_float(dtmi.get("gwh")) if isinstance(dtmi, dict) else None
        existing = db.get(GasLngCountry, (day, code))
        values = {
            "region": region,
            "name": row.get("name"),
            "send_out_gwh": coerce_float(row.get("sendOut")),
            "inventory_twh": _inventory_twh(row),
            "dtmi_twh": (dtmi_gwh / 1000.0) if dtmi_gwh is not None else None,
        }
        if existing:
            for k, v in values.items():
                setattr(existing, k, v)
        else:
            db.add(GasLngCountry(date=day, country=code, **values))
        n += 1
    db.flush()
    return n


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
